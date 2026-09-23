#!/usr/bin/env python3
"""Passive SSH metadata collector. Never writes packets or credentials to disk."""
import collections, hashlib, ipaddress, json, os, queue, re, sqlite3, subprocess, threading, time, urllib.request, uuid
from pathlib import Path
CONFIG=Path('/etc/frp-ssh-features/agent.json')
FIELDS=['frame.time_epoch','ip.src','ipv6.src','tcp.srcport','ip.dst','ipv6.dst','tcp.dstport','tcp.stream','tcp.flags.syn','tcp.flags.ack','tcp.flags.fin','tcp.flags.reset','ssh.protocol','ssh.cookie','ssh.kex.hassh','ssh.kex.hassh_algorithms']

def flag(value):
    return value.lower() in ('1','true','0x1')

def auth_event(entry):
    if entry.get('_SYSTEMD_UNIT') not in ('ssh.service','sshd.service'):return None
    m=entry.get('MESSAGE','')
    patterns=[('failure',r'Failed (?:password|publickey|keyboard-interactive(?:/pam)?) for (invalid user )?(\S+) from ([\da-fA-F:.]+) port (\d+)'),('success',r'Accepted (?:password|publickey|keyboard-interactive(?:/pam)?) for ()(\S+) from ([\da-fA-F:.]+) port (\d+)'),('invalid_user',r'Invalid user ()(\S+) from ([\da-fA-F:.]+) port (\d+)')]
    for kind,pat in patterns:
        x=re.search(pat,m)
        if x:
            return dict(kind='auth',outcome=kind,invalid=bool(x[1]) or kind=='invalid_user',username=x[2][:64],peer=str(ipaddress.ip_address(x[3])),peer_port=int(x[4]),ts=int(entry['__REALTIME_TIMESTAMP'])/1e6,id=hashlib.sha256(entry.get('__CURSOR',m+str(entry['__REALTIME_TIMESTAMP'])).encode()).hexdigest(),pid=str(entry.get('_PID','')),method=(re.search(r'^(?:Failed|Accepted) (\S+)',m).group(1) if re.search(r'^(?:Failed|Accepted) (\S+)',m) else 'unknown'))

class Agent:
    def __init__(self,cfg):
        self.cfg=cfg; self.q=queue.Queue(10000); self.health={}; self.lock=threading.Lock()
        self.db=sqlite3.connect('/var/lib/frp-ssh-features/spool.sqlite');self.db.execute('CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY, body TEXT)');self.db.commit()
    def put(self,e):
        e.setdefault('id',uuid.uuid4().hex)
        try:self.q.put_nowait(e)
        except queue.Full:self.health['queue_overflow']=self.health.get('queue_overflow',0)+1
    def capture(self):
        cfg=self.cfg;ports=[51022,51023,51024] if cfg['node']=='vps' else [22]
        filt='tcp and ('+' or '.join('port '+str(x) for x in ports)+')'
        if cfg['node']!='vps':filt+=' and (host 127.0.0.1 or host ::1)'
        while True:
            epoch=uuid.uuid4().hex;flows={}
            capture=subprocess.Popen(['dumpcap','-q','-p','-i',cfg['interface'],'-f',filt,'-s','4096','-B','2','-a','duration:600','-w','-'],stdout=subprocess.PIPE)
            cmd=['tshark','-n','-l','-r','-','-o','tcp.desegment_tcp_streams:TRUE','-T','fields','-E','occurrence=f']
            for port in ports:cmd+=['-d',f'tcp.port=={port},ssh']
            cmd+=['-Y','tcp.flags.syn == 1 or tcp.flags.fin == 1 or tcp.flags.reset == 1 or ssh.protocol or ssh.cookie']
            for field in FIELDS:cmd+=['-e',field]
            p=subprocess.Popen(cmd,stdin=capture.stdout,stdout=subprocess.PIPE,text=True)
            capture.stdout.close()
            self.health['capture_started']=time.time()
            for line in p.stdout:
                try:
                    a=line.rstrip('\n').split('\t');a+=['']*(len(FIELDS)-len(a));v=dict(zip(FIELDS,a));ts=float(v['frame.time_epoch']);sp=int(v['tcp.srcport']);dp=int(v['tcp.dstport']);client=dp in ports
                    peer=(v['ip.src'] or v['ipv6.src']) if client else (v['ip.dst'] or v['ipv6.dst']);peerport=sp if client else dp;serverport=dp if client else sp
                    key=epoch+':'+v['tcp.stream'];f=flows.get(key)
                    if not f:
                        if len(flows)>=10000:self.health['flow_overflow']=self.health.get('flow_overflow',0)+1;continue
                        f=dict(kind='flow',id=key,ts=ts,start=ts,peer=peer,peer_port=peerport,server_port=serverport,client_cookie='',server_cookie='',banner='',hassh='',algorithms='',syn=False,end=None);flows[key]=f
                    f['ts']=ts
                    if flag(v['tcp.flags.syn']) and not flag(v['tcp.flags.ack']):f['syn']=True;f['start']=ts
                    if v['ssh.protocol'] and client:f['banner']=v['ssh.protocol'][:256]
                    cookie=v['ssh.cookie'].replace(':','').lower()
                    if re.fullmatch('[0-9a-f]{32}',cookie):
                        name='client_cookie' if client else 'server_cookie'
                        if not f[name]:f[name]=cookie
                    if client and v['ssh.kex.hassh']:f['hassh']=v['ssh.kex.hassh'];f['algorithms']=v['ssh.kex.hassh_algorithms'][:4096]
                    if flag(v['tcp.flags.fin']) or flag(v['tcp.flags.reset']):f['end']=ts
                    self.put(dict(f));self.health['last_packet']=ts
                except (ValueError,KeyError):self.health['parse_errors']=self.health.get('parse_errors',0)+1
            rc=p.wait();capture.terminate();capture_rc=capture.wait();self.health['capture_exit']=rc;self.health['capture_source_exit']=capture_rc
            time.sleep(10 if rc or capture_rc not in (0,124,-15) else 1)
    def journal(self):
        import datetime
        from zoneinfo import ZoneInfo
        cursor=None
        while True:
            start=int(datetime.datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
            args=['journalctl','-o','json','--no-pager']
            if cursor:args+=['-f','--after-cursor',cursor]
            else:
                self.health['auth_backfill_complete']=False
                args+=['--since','@'+str(start)]
            args+=['_SYSTEMD_UNIT=ssh.service','+','_SYSTEMD_UNIT=sshd.service']
            p=subprocess.Popen(args,stdout=subprocess.PIPE,text=True)
            for line in p.stdout:
                try:
                    entry=json.loads(line);cursor=entry.get('__CURSOR',cursor);e=auth_event(entry)
                    if e:self.q.put(e)  # Authentication counters must not drop under queue pressure.
                except (ValueError,KeyError):pass
            rc=p.wait()
            if not rc:
                self.health['auth_backfill_complete']=True
                self.health['auth_day_start']=start
                self.put(dict(kind='health',ts=time.time(),health=dict(self.health)))
            if not cursor or rc:time.sleep(5)
    def run(self):
        threading.Thread(target=self.capture,daemon=True).start()
        if self.cfg['node']!='vps':threading.Thread(target=self.journal,daemon=True).start()
        heartbeat=0;lastprint=0
        while True:
            now=time.time()
            if now-heartbeat>=30:self.put(dict(kind='health',ts=now,health=dict(self.health)));heartbeat=now
            for _ in range(1000):
                try:e=self.q.get_nowait()
                except queue.Empty:break
                self.db.execute('INSERT OR REPLACE INTO queue VALUES (?,?)',(e['id'],json.dumps(e)))
            self.db.execute('DELETE FROM queue WHERE rowid IN (SELECT rowid FROM queue ORDER BY rowid DESC LIMIT -1 OFFSET 10000)');self.db.commit()
            rows=self.db.execute('SELECT id,body FROM queue ORDER BY rowid LIMIT 40').fetchall()
            if rows:
                stale=[r[0] for r in rows if now-json.loads(r[1])['ts']>86000]
                if stale:
                    self.db.executemany('DELETE FROM queue WHERE id=?',[(x,) for x in stale]);self.db.commit();self.health['expired_events']=self.health.get('expired_events',0)+len(stale);continue
                body=json.dumps({'events':[json.loads(r[1]) for r in rows]}).encode()
                req=urllib.request.Request(self.cfg['url'],data=body,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.cfg['token'],'X-Feature-Node':self.cfg['node']})
                try:
                    with urllib.request.urlopen(req,timeout=10) as resp:
                        if resp.status!=200:raise RuntimeError('unexpected HTTP status')
                    self.db.executemany('DELETE FROM queue WHERE id=?',[(r[0],) for r in rows]);self.db.commit();self.health['last_upload']=now
                except Exception as exc:
                    if now-lastprint>60:print('metadata upload failed:',type(exc).__name__,flush=True);lastprint=now
                    time.sleep(5)
            time.sleep(1)
if __name__=='__main__':Agent(json.loads(CONFIG.read_text())).run()
