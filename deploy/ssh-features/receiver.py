#!/usr/bin/env python3
"""Observe-only SSH feature correlation. Contains no firewall mutation path."""
import argparse, collections, datetime, hmac, ipaddress, json, math, os, re, sqlite3, statistics, threading, time
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
PORTS={'5090':51022,'4090-1':51023,'4090-2':51024}
BASE=Path('/var/lib/frp-ssh-features')
SCHEMA='''
CREATE TABLE IF NOT EXISTS flows(node TEXT,id TEXT,start REAL,ts REAL,peer TEXT,peer_port INTEGER,server_port INTEGER,client_cookie TEXT,server_cookie TEXT,syn INTEGER,end REAL,banner TEXT,hassh TEXT,algorithms TEXT,PRIMARY KEY(node,id));
CREATE INDEX IF NOT EXISTS flow_local ON flows(node,peer,peer_port,start);
CREATE INDEX IF NOT EXISTS flow_cookies ON flows(client_cookie,server_cookie,node);
CREATE TABLE IF NOT EXISTS auth(node TEXT,id TEXT,ts REAL,peer TEXT,peer_port INTEGER,username TEXT,outcome TEXT,invalid INTEGER,status TEXT DEFAULT 'pending',source TEXT,hassh TEXT,flow_id TEXT,PRIMARY KEY(node,id));
CREATE INDEX IF NOT EXISTS auth_time ON auth(ts);
CREATE TABLE IF NOT EXISTS health(node TEXT PRIMARY KEY,received REAL,body TEXT);
CREATE TABLE IF NOT EXISTS reports(ip TEXT PRIMARY KEY,updated REAL,body TEXT);
CREATE TABLE IF NOT EXISTS findings(ip TEXT,bucket INTEGER,body TEXT,PRIMARY KEY(ip,bucket));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
'''

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);
    if 'method' not in {r[1] for r in db.execute('PRAGMA table_info(auth)')}:db.execute("ALTER TABLE auth ADD COLUMN method TEXT NOT NULL DEFAULT 'unknown'");db.commit()
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA busy_timeout=5000');return db

def valid_event(e,now):
    if not isinstance(e,dict) or e.get('kind') not in ['flow','auth','health']:raise ValueError('invalid kind')
    if not isinstance(e.get('id'),str) or not 1<=len(e['id'])<=150:raise ValueError('id')
    if not isinstance(e.get('ts'),(int,float)) or not math.isfinite(e['ts']) or abs(now-e['ts'])>86400:raise ValueError('timestamp')
    if e['kind']=='health':return
    ipaddress.ip_address(e['peer'])
    if type(e['peer_port']) is not int or not 1<=e['peer_port']<=65535:raise ValueError('peer port')
    if e['kind']=='flow':
        if e['server_port'] not in [22,51022,51023,51024]:raise ValueError('server port')
        if not isinstance(e['start'],(int,float)) or not math.isfinite(e['start']) or not 0<=e['ts']-e['start']<=86400:raise ValueError('flow time')
        if e['end'] is not None and (not isinstance(e['end'],(int,float)) or not math.isfinite(e['end']) or e['end']<e['start']):raise ValueError('end')
        for k in ['client_cookie','server_cookie','hassh']:
            if not isinstance(e[k],str) or (e[k] and not re.fullmatch('[0-9a-f]{32}',e[k])):raise ValueError(k)
        for k,maximum in [('banner',256),('algorithms',4096)]:
            if not isinstance(e[k],str) or len(e[k])>maximum:raise ValueError(k)
    else:
        if e.get('method','unknown') not in ['password','publickey','keyboard-interactive','keyboard-interactive/pam','unknown']:raise ValueError('method')
        if e['outcome'] not in ['success','failure','invalid_user'] or not isinstance(e['username'],str) or len(e['username'])>64:raise ValueError('auth')

def ingest(db,node,events,now):
    for e in events:valid_event(e,now)
    with db:
        for e in events:
            if e['kind']=='health':db.execute('INSERT OR REPLACE INTO health VALUES (?,?,?)',(node,now,json.dumps(e)))
            elif e['kind']=='flow':
                if (node=='vps' and e['server_port'] not in PORTS.values()) or (node!='vps' and e['server_port']!=22):raise ValueError('node scope')
                keys=['id','start','ts','peer','peer_port','server_port','client_cookie','server_cookie','syn','end','banner','hassh','algorithms']
                db.execute('INSERT INTO flows VALUES ('+','.join('?'*14)+') ON CONFLICT(node,id) DO UPDATE SET ts=excluded.ts,syn=MAX(flows.syn,excluded.syn),client_cookie=excluded.client_cookie,server_cookie=excluded.server_cookie,end=excluded.end,banner=excluded.banner,hassh=excluded.hassh,algorithms=excluded.algorithms WHERE excluded.ts>=flows.ts',[node]+[e[k] for k in keys])
            elif e['kind']=='auth':
                if node=='vps':raise ValueError('node scope')
                db.execute("INSERT INTO auth(node,id,ts,peer,peer_port,username,outcome,invalid,method) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(node,id) DO UPDATE SET method=excluded.method WHERE excluded.method!='unknown'",[node]+[e[k] for k in ['id','ts','peer','peer_port','username','outcome','invalid']]+[e.get('method','unknown')])

def correlate(db,now):
    # Random cookies from BOTH sides must match; time proximity alone never attributes an IP.
    for a in db.execute("SELECT * FROM auth WHERE ts>?",(now-600,)).fetchall():
        locals_=db.execute('SELECT * FROM flows WHERE node=? AND peer=? AND peer_port=? AND start BETWEEN ? AND ? AND (end IS NULL OR end>=?) AND syn=1',(a['node'],a['peer'],a['peer_port'],a['ts']-180,a['ts']+1,a['ts']-1)).fetchall()
        status='pending';source=None;hassh=None;fid=None
        if not ipaddress.ip_address(a['peer']).is_loopback:
            db.execute("UPDATE auth SET status='outside_domestic_frp_scope' WHERE node=? AND id=?",(a['node'],a['id']));continue
        if len(locals_)>1:status='ambiguous'
        elif len(locals_)==1:
            f=locals_[0]
            if f['client_cookie'] and f['server_cookie']:
                externals=db.execute("SELECT * FROM flows WHERE node='vps' AND server_port=? AND client_cookie=? AND server_cookie=? AND ABS(start-?)<15 AND syn=1",(PORTS[a['node']],f['client_cookie'],f['server_cookie'],f['start'])).fetchall()
                if len(externals)>1:status='ambiguous'
                elif len(externals)==1:
                    ext=externals[0]
                    if not ipaddress.ip_address(ext['peer']).is_loopback:status='exact_cookie_pair';source=ext['peer'];hassh=ext['hassh'];fid=ext['id']
        if status=='pending' and now-a['ts']>120:status='unmatched'
        db.execute('UPDATE auth SET status=?,source=?,hassh=?,flow_id=? WHERE node=? AND id=?',(status,source,hassh,fid,a['node'],a['id']))
    db.execute("UPDATE auth SET status='unmatched' WHERE status='pending' AND ts<?",(now-600,));db.commit()

def tier_for(ip,geo,now):
    if now-geo.get('updated_at',0)>45*86400:return 'OTHER'
    addr=ipaddress.ip_address(ip)
    return 'HF' if any(addr in ipaddress.ip_network(n) for n in geo.get(str(addr.version),[])) else 'OTHER'

def score(rows,flowrows,tier):
    failures=[x for x in rows if x['outcome']=='failure'];badusers={x['username'] for x in rows if x['invalid']};nodes={x['node'] for x in failures};signals=[];points=0
    # One typo and shared client fingerprints produce no penalty.
    if len(failures)>=3:points+=min(40,(len(failures)-2)*5);signals.append('repeated_authentication_failures')
    if len(badusers)>=3:points+=25;signals.append('multiple_invalid_usernames')
    if len(nodes)>=2 and len(failures)>=4:points+=20;signals.append('failures_across_servers')
    starts=sorted({f['start'] for f in flowrows})
    if len(starts)>=6:
        intervals=[b-a for a,b in zip(starts,starts[1:])]
        if statistics.mean(intervals)>1 and statistics.pstdev(intervals)/statistics.mean(intervals)<0.15:signals.append('regular_connection_intervals');points+=5
    if len({f['server_port'] for f in flowrows})>=2:signals.append('connections_across_servers')
    threshold=75 if tier=='HF' else 50
    enough=len(failures)>=(18 if tier=='HF' else 6)
    action='would_temporarily_ban' if enough and points>=threshold else ('observe_elevated' if points>=25 else 'observe')
    return dict(score=min(points,100),tier=tier,signals=signals,failures=len(failures),invalid_usernames=len(badusers),targets=sorted(nodes),recommended_action=action,recommended_seconds=(600 if tier=='HF' else 3600) if action=='would_temporarily_ban' else 0)

def evaluate(db,now,geo):
    rows=db.execute("SELECT * FROM auth WHERE status='exact_cookie_pair' AND ts>?",(now-600,)).fetchall();byip=collections.defaultdict(list)
    for r in rows:byip[r['source']].append(r)
    flows=db.execute("SELECT * FROM flows WHERE node='vps' AND start>?",(now-600,)).fetchall();byflow=collections.defaultdict(list)
    for f in flows:byflow[f['peer']].append(f)
    # Distributed candidates require >=3 addresses, same HASSH AND username, >=2 actual failures per member.
    groups=collections.defaultdict(collections.Counter)
    for r in rows:
        if r['hassh'] and r['outcome']=='failure':groups[(r['hassh'],r['username'])][r['source']]+=1
    members=collections.defaultdict(list)
    for (fingerprint,username),counts in groups.items():
        ips=sorted(ip for ip,n in counts.items() if n>=2)
        if len(ips)>=3:
            for ip in ips:members[ip].append(dict(hassh=fingerprint,username=username,ips=ips[:50],members=len(ips)))
    for ip in set(byip)|set(byflow):
        report=score(byip[ip],byflow[ip],tier_for(ip,geo,now));report.update(ip=ip,updated=now,mode='observe',window_seconds=600,groups=members[ip],connections=len(byflow[ip]),fingerprints=sorted({f['hassh'] for f in byflow[ip] if f['hassh']}),banners=sorted({f['banner'] for f in byflow[ip] if f['banner']}))
        if members[ip]:report['signals'].append('multi_source_shared_fingerprint_and_failed_username');report['group_recommendation']='would_limit_group_new_connections';report['score']=min(100,report['score']+20)
        db.execute('INSERT OR REPLACE INTO reports VALUES (?,?,?)',(ip,now,json.dumps(report)))
        if report['score']>=25 or members[ip]:db.execute('INSERT OR REPLACE INTO findings VALUES (?,?,?)',(ip,int(now//300),json.dumps(report)))
    db.execute('DELETE FROM findings WHERE bucket<?',(int((now-7*86400)//300),))
    db.execute('DELETE FROM reports WHERE updated<?',(now-86400,));db.commit()

def summary(db):
    return dict(mode='observe',automatic_enforcement=False,health=[dict(node=r['node'],received=r['received'],age_seconds=round(time.time()-r['received'],1),details=json.loads(r['body']).get('health',{})) for r in db.execute('SELECT * FROM health')],auth=[dict(r) for r in db.execute('SELECT status,outcome,count(*) AS count FROM auth GROUP BY status,outcome')],flows=[dict(r) for r in db.execute('SELECT node,count(*) AS count,sum(client_cookie!="" AND server_cookie!="") AS complete_handshakes FROM flows GROUP BY node')],risks=[json.loads(r[0]) for r in db.execute('SELECT body FROM reports ORDER BY updated DESC LIMIT 30')])

def maintain(db,now):
    for table,column in [('flows','ts'),('auth','ts')]:
        db.execute(f'DELETE FROM {table} WHERE {column}<?',(now-7*86400,))
        db.execute(f'DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} ORDER BY {column} DESC LIMIT -1 OFFSET 200000)')
    db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

def serve():
    cfg=json.loads(Path('/etc/frp-ssh-features/receiver.json').read_text());db=connect(BASE/'events.sqlite');last=[0,0]
    db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('observation_started',str(time.time())));db.commit()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path!='/dashboard':self.send_error(404);return
            from dashboard import dashboard
            body=json.dumps(dashboard(db)).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store, private');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            self.connection.settimeout(5)
            node=self.headers.get('X-Feature-Node','');expected=cfg['tokens'].get(node,'')
            if self.path!='/ingest' or not expected or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+expected):self.send_error(403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=262144:raise ValueError('length')
                data=json.loads(self.rfile.read(length));events=data['events']
                if not isinstance(events,list) or len(events)>100:raise ValueError('events')
                now=time.time();ingest(db,node,events,now)
                if now-last[0]>5:
                    correlate(db,now)
                    try:geo=json.loads(Path('/var/lib/frp-ssh-guard/geo.json').read_text())
                    except (OSError,ValueError):geo={}
                    evaluate(db,now,geo);last[0]=now
                if now-last[1]>3600:maintain(db,now);last[1]=now
                self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'OK')
            except (ValueError,KeyError,TypeError):self.send_error(400)
    HTTPServer(('127.0.0.1',28460),Handler).serve_forever()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['serve','status','report']);args=parser.parse_args()
    if args.command=='serve':serve()
    else:
        db=connect(BASE/'events.sqlite');data=summary(db)
        if args.command=='report':
            data['last_24h_auth']=[dict(r) for r in db.execute('SELECT node,status,outcome,count(*) AS count FROM auth WHERE ts>? GROUP BY node,status,outcome',(time.time()-86400,))]
            data['elevated_findings']=[json.loads(r[0]) for r in db.execute('SELECT body FROM findings WHERE bucket>? ORDER BY bucket DESC LIMIT 1000',(int((time.time()-86400)//300),))]
            out=BASE/'reports';out.mkdir(mode=0o700,exist_ok=True);target=out/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')+'.json');target.write_text(json.dumps(data,indent=2));target.chmod(0o600)
            for p in out.glob('*.json'):
                if time.time()-p.stat().st_mtime>8*86400:p.unlink()
            print(str(target))
        else:print(json.dumps(data,indent=2,ensure_ascii=False))
