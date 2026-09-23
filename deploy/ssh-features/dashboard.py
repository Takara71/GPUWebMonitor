"""Authenticated dashboard projection; nginx must enforce /auth/verify."""
import datetime,json,time,ipaddress
from pathlib import Path
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Asia/Shanghai')
NODES={'gpu-node-a':'5090','gpu-node-b':'4090-1','gpu-node-c':'4090-2'}

def blacklist(now, path=Path('/var/lib/frp-ssh-guard/state.json')):
    try:
        state=json.loads(path.read_text())
        try: historical=json.loads(path.with_name('ban-evidence.json').read_text())
        except (OSError,ValueError): historical={}
        items=[]
        for b in state['bans'].values():
            address=str(ipaddress.ip_address(b['ip']))
            until=float(b['until'])
            if until and until<=now:continue
            if b['scope'] not in ('forward','admin'):continue
            banned_at=max(b.get('history',[]),default=None)
            evidence=b.get('evidence') or historical.get(b['scope']+':'+address+':'+str(banned_at),{})
            safe_evidence={k:evidence[k] for k in ('kind','window_seconds','count','counts','invalid_usernames_1h') if k in evidence}
            items.append({'ip':address,'scope':b['scope'],'tier':b['tier'],
                'until':until,'remaining_seconds':max(0,int(until-now)) if until else None,
                'reason':'authentication' if evidence.get('counts') else 'guard',
                'evidence':safe_evidence,'ban_count_24h':sum(banned_at-86400<=t<=banned_at for t in b.get('history',[])) if banned_at else 1,'ban_seconds':until-banned_at if until and banned_at else None,
                'banned_at':max(b.get('history',[]),default=None)})
        items.sort(key=lambda b:(b['scope'],ipaddress.ip_address(b['ip']).version,int(ipaddress.ip_address(b['ip']))))
        return {'available':True,'count':len(items),'items':items,'updated_at':now}
    except (OSError,ValueError,KeyError,TypeError):
        return {'available':False,'count':None,'items':[],'updated_at':now}

def dashboard(db,now=None):
    now=time.time() if now is None else now
    date=datetime.datetime.fromtimestamp(now,TZ);start=date.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    health={r['node']:r for r in db.execute('SELECT * FROM health')}
    result={'date':date.strftime('%Y-%m-%d'),'timezone':'Asia/Shanghai','generated_at':now,'mode':'authentication_enforcement','nodes':{},'blacklist':blacklist(now)}
    for public,node in NODES.items():
        rows=db.execute('SELECT * FROM auth WHERE node=? AND ts>=? AND ts<=? ORDER BY ts DESC',(node,min(start,now-600),now)).fetchall()
        all_failures=[r for r in rows if r['outcome']=='failure' and r['method']=='password']
        failures=[r for r in all_failures if r['ts']>=start]
        recent=[r for r in all_failures if r['ts']>=now-600]
        matched=[r for r in failures if r['status']=='exact_cookie_pair']
        unknown=[r for r in rows if r['outcome']=='failure' and r['method']=='unknown' and r['ts']>=start]
        invalid=[r for r in rows if r['outcome']=='invalid_user' and r['ts']>=start]
        sources={r['source'] for r in matched if r['source']}
        recent_sources={r['source'] for r in recent if r['status']=='exact_cookie_pair' and r['source']}
        findings=[]
        for ip in recent_sources:
            rec=db.execute('SELECT body FROM reports WHERE ip=? AND updated>=?',(ip,now-90)).fetchone()
            if rec:findings.append(json.loads(rec[0]))
        h=health.get(node);vps=health.get('vps');fresh=bool(h and now-h['received']<=90)
        synchronized=bool(h and json.loads(h['body']).get('health',{}).get('auth_backfill_complete'))
        handshake_fresh=bool(vps and now-vps['received']<=90)
        score=max([x['score'] for x in findings],default=0)
        level='low';reasons=[]
        if recent:reasons.append({'code':'recent_failures','count':len(recent)})
        if len(recent)>=100 or score>=75:level='high';reasons.append({'code':'high_failure_activity'})
        elif len(recent)>=20 or score>=25:level='medium';reasons.append({'code':'elevated_activity'})
        group_count=sum(bool(x.get('groups')) for x in findings)
        if group_count:level='high' if level=='high' else 'medium';reasons.append({'code':'related_sources','count':group_count})
        if not recent:reasons.append({'code':'no_recent_anomaly'})
        if not handshake_fresh:reasons.append({'code':'source_collection_stale'})
        if not fresh:level='unknown';reasons=[{'code':'collection_stale'}]
        elif not synchronized:level='unknown';reasons=[{'code':'syncing'}]
        result['nodes'][public]={'name':node,'password_failures_today':len(failures),'password_failures_recent':len(recent),'matched_password_failures':len(matched),'unmatched_password_failures':len(failures)-len(matched),'unique_public_sources':len(sources),'invalid_user_events':len(invalid),'unknown_method_failures':len(unknown),'risk':level,'risk_score':score,'reasons':reasons,'updated_at':h['received'] if h else None,'fresh':fresh,'synchronized':synchronized,'source_collection_fresh':handshake_fresh,'recent_events':[{'time':r['ts'],'source':r['source'] if r['status']=='exact_cookie_pair' else None,'attribution':r['status']} for r in failures[:5]]}
    return result
