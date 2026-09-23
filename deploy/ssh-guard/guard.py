#!/usr/bin/env python3
"""FRP SSH ingress controls; no SSH credentials are inspected on forwarded ports."""
import argparse, collections, datetime, ipaddress, json, os, re, subprocess, time, signal, queue, threading
from pathlib import Path
ROOT = Path('/var/lib/frp-ssh-guard')
TABLE = 'inet frp_ssh_guard'
PORTS = '51022, 51023, 51024'

def nft(script, check=False):
    subprocess.run(['nft', *(['-c'] if check else []), '-f', '-'], input=script, text=True, check=True, capture_output=True)

def rules(geo):
    lines = [f'table {TABLE} {{']
    for v, typ in [(4, 'ipv4_addr'), (6, 'ipv6_addr')]:
        elements = ', '.join(geo.get(str(v), []))
        lines += [f'set hefei{v} {{ type {typ}; flags interval; auto-merge; '+(f'elements = {{ {elements} }}; ' if elements else '')+'}',
                  f'set banned{v} {{ type {typ}; flags timeout; size 65536; }}',
                  f'set permanent{v} {{ type {typ}; size 65536; }}',
                  f'set admin_banned{v} {{ type {typ}; flags timeout; size 65536; }}',
                  f'set connections{v} {{ type {typ}; flags dynamic; size 65536; }}']
    lines += ['set strict { type inet_service; flags timeout; }',
              'chain hf_drop { ct mark set ct mark | 0x20000000; limit rate 50/second burst 100 packets log prefix "FRPG HF "; counter drop; }',
              'chain other_drop { ct mark set ct mark | 0x20000000; limit rate 50/second burst 100 packets log prefix "FRPG OTHER "; counter drop; }',
              'chain global_drop { ct mark set ct mark | 0x20000000; limit rate 10/second burst 20 packets log prefix "FRPG GLOBAL "; counter drop; }',
              'chain global_normal { limit rate over 36/minute burst 18 packets goto global_drop; counter accept; }',
              'chain global_hf_strict { limit rate over 18/minute burst 6 packets goto global_drop; counter accept; }',
              'chain global_other_strict { limit rate over 3/minute burst 1 packets goto global_drop; counter accept; }']
    for v, ip in [(4, 'ip'), (6, 'ip6')]:
        for tier, rate, burst, count, drop in [('hf',18,6,54,'hf_drop'),('other',1,1,6,'other_drop')]:
            lines += [f'chain {tier}{v} {{',
                      f'add @connections{v} {{ {ip} saddr ct count over {count} }} goto {drop}',
                      f'meter rate_{tier}{v} size 65536 {{ {ip} saddr timeout 5m limit rate over {rate}/minute burst {burst} packets }} goto {drop}',
                      f'tcp dport @strict goto global_{"hf" if tier=="hf" else "other"}_strict',
                      'goto global_normal', '}']
    lines += ['chain input { type filter hook input priority -5; policy accept;',
              'iifname "lo" accept',
              'ct state established,related accept',
              'tcp dport 22 ip saddr @admin_banned4 counter drop',
              'tcp dport 22 ip6 saddr @admin_banned6 counter drop',
              f'tcp dport != {{ {PORTS} }} return',
              'meta l4proto != tcp return',
              'ip saddr @banned4 counter drop', 'ip6 saddr @banned6 counter drop',
              'ip saddr @permanent4 counter drop', 'ip6 saddr @permanent6 counter drop',
              'ct state invalid counter drop',
              'ct mark & 0x20000000 != 0 counter drop',
              'ct mark & 0x10000000 != 0 accept',
              'tcp flags & (fin|syn|rst|ack) != syn counter drop',
              'ct mark set ct mark | 0x10000000',
              'ip saddr @hefei4 goto hf4', 'ip6 saddr @hefei6 goto hf6',
              'meta nfproto ipv4 goto other4', 'meta nfproto ipv6 goto other6', '}', '}']
    return '\n'.join(lines)+'\n'

def load_geo(path):
    d=json.loads(Path(path).read_text())
    if time.time()-d['updated_at'] > 45*86400:
        return {'4':[], '6':[]}
    for v in ['4','6']:
        for s in d[v]:
            assert ipaddress.ip_network(s).version==int(v)
    return d

def duration(tier, history, now):
    recent=[t for t in history if now-t<86400]
    days={datetime.datetime.fromtimestamp(t,datetime.timezone.utc).date() for t in history if now-t<30*86400}
    days.add(datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date())
    if len(days)>=3: return 604800 if tier=='HF' else 0
    return ([600,3600,86400] if tier=='HF' else [3600,86400,604800])[min(len(recent),2)]

class Guard:
    def __init__(self):
        ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.path=ROOT/'state.json'
        self.state=json.loads(self.path.read_text()) if self.path.exists() else {'bans':{},'strict_until':0}
        self.events=collections.defaultdict(collections.deque)
        self.global_events=collections.deque()
        self.processed=0
    def save(self):
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.state));tmp.chmod(0o600);tmp.replace(self.path)
    def emit(self, data):
        data={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),**data}
        with (ROOT/'events.jsonl').open('a') as f:f.write(json.dumps(data)+'\n')
        print(json.dumps(data),flush=True)
    def restore(self):
        now=time.time();cmd=[]
        for key,b in self.state['bans'].items():
            ip=ipaddress.ip_address(b['ip']);prefix='admin_banned' if b['scope']=='admin' else ('permanent' if b['until']==0 else 'banned')
            if b['until']==0 or b['until']>now:
                ttl='' if b['until']==0 else f' timeout {max(1,int(b["until"]-now))}s'
                cmd.append(f'add element {TABLE} {prefix}{ip.version} {{ {ip}{ttl} }}')
        if self.state['strict_until']>now:
            ttl=max(1,int(self.state['strict_until']-now))
            cmd.append(f'add element {TABLE} strict {{ '+', '.join(f'{p} timeout {ttl}s' for p in [51022,51023,51024])+' }')
        if cmd:nft('\n'.join(cmd))
    def event(self, scope, address, tier, now=None):
        now=now or time.time();ip=ipaddress.ip_address(address)
        if ip.is_loopback:return
        key=scope+':'+str(ip);prior=self.state['bans'].get(key)
        if prior and (prior['until']==0 or prior['until']>now):return
        q=self.events[key];window=600 if scope=='admin' else (60 if tier=='HF' else 120)
        q.append(now)
        while q and now-q[0]>window:q.popleft()
        threshold=5 if scope=='admin' else (6 if tier=='HF' else 3)
        if len(q)<threshold:return
        evidence={'kind':'admin_password' if scope=='admin' else 'connection_limit','window_seconds':window,'count':len(q)}
        q.clear()
        self.ban(scope, address, tier, now,evidence)
    def ban(self, scope, address, tier, now, evidence=None):
        ip=ipaddress.ip_address(address)
        if ip.is_loopback:return
        key=scope+':'+str(ip);prior=self.state['bans'].get(key)
        if prior and (prior['until']==0 or prior['until']>now):return
        history=[t for t in (prior or {}).get('history',[]) if now-t<30*86400]
        seconds=duration(tier,history,now)
        # The VPS admin port uses timed bans even after repeated incidents.
        if scope=='admin' and seconds==0:seconds=604800
        prefix='admin_banned' if scope=='admin' else ('permanent' if seconds==0 else 'banned')
        ttl=f' timeout {seconds}s' if seconds else ''
        nft(f'add element {TABLE} {prefix}{ip.version} {{ {ip}{ttl} }}')
        self.state['bans'][key]={'ip':str(ip),'scope':scope,'tier':tier,'until':now+seconds if seconds else 0,'history':history+[now],'evidence':evidence}
        self.save();self.emit({'action':'ban','scope':scope,'ip':str(ip),'tier':tier,'seconds':seconds,'evidence':evidence})
    def global_event(self,now=None):
        now=now or time.time()
        if self.state['strict_until']>now:return
        self.global_events.append(now)
        while self.global_events and now-self.global_events[0]>60:self.global_events.popleft()
        if len(self.global_events)<5:return
        nft(f'add element {TABLE} strict {{ 51022 timeout 15m, 51023 timeout 15m, 51024 timeout 15m }}')
        self.state['strict_until']=now+900;self.save();self.global_events.clear();self.emit({'action':'strict_mode','seconds':900})
    def message(self, entry):
        self.processed+=1
        if self.processed%512==0:
            now=time.time()
            for key in list(self.events):
                if not self.events[key] or now-self.events[key][-1]>600:del self.events[key]
        m=entry.get('MESSAGE','')
        if entry.get('_TRANSPORT')=='kernel':
            found=re.search(r'FRPG (HF|OTHER|GLOBAL).*?SRC=([0-9a-fA-F:.]+)',m)
            if found:
                if found[1]=='GLOBAL':self.global_event()
                else:self.event('forward',found[2],found[1])
        elif entry.get('_SYSTEMD_UNIT')=='ssh.service':
            found=re.search(r'Failed password for (?:invalid user )?\S+ from ([0-9a-fA-F:.]+)',m)
            if found:self.event('admin',found[1],'OTHER')
    def follow(self):
        signal.signal(signal.SIGHUP,lambda *_:setattr(self,'state',json.loads(self.path.read_text()) if self.path.exists() else {'bans':{},'strict_until':0}))
        proc=subprocess.Popen(['journalctl','-f','-n','0','-o','json','_TRANSPORT=kernel','+','_SYSTEMD_UNIT=ssh.service'],stdout=subprocess.PIPE,text=True)
        messages=queue.Queue(maxsize=10000)
        def reader():
            for line in proc.stdout:messages.put(line)
            messages.put(None)
        threading.Thread(target=reader,daemon=True).start()
        next_scan=0
        try:
            while True:
                if time.monotonic()>=next_scan:
                    try:
                        import auth_policy
                        auth_policy.scan(self)
                    except Exception as e:
                        self.emit({'action':'authentication_scan_error','error':str(e)})
                    next_scan=time.monotonic()+15
                try:line=messages.get(timeout=1)
                except queue.Empty:continue
                if line is None:break
                try:self.message(json.loads(line))
                except (ValueError,KeyError) as e:self.emit({'action':'parse_error','error':str(e)})
        finally:proc.terminate();proc.wait()
        raise RuntimeError('journal follower stopped')

def main():
    a=argparse.ArgumentParser();a.add_argument('command',choices=['render','apply','run','status','unban','maintenance']);a.add_argument('--geo',default='/var/lib/frp-ssh-guard/geo.json');a.add_argument('--ip');args=a.parse_args()
    if args.command=='render':print(rules(load_geo(args.geo)));return
    if args.command=='apply':
        script=rules(load_geo(args.geo))
        exists=subprocess.run(['nft','list','table','inet','frp_ssh_guard'],capture_output=True).returncode==0
        script=(f'delete table {TABLE}\n' if exists else '')+script
        nft(script,check=True);nft(script);Guard().restore();return
    if args.command=='maintenance':
        geo=load_geo(args.geo)
        if not geo['4'] and not geo['6']:
            nft(f'flush set {TABLE} hefei4\nflush set {TABLE} hefei6')
            print('Expired geographic data: all sources now use strict tier',flush=True)
        return
    g=Guard()
    if args.command=='run':g.follow()
    elif args.command=='status':print(json.dumps(g.state,indent=2))
    elif args.command=='unban':
        ip=ipaddress.ip_address(args.ip)
        for prefix in ['admin_banned','banned','permanent']:
            subprocess.run(['nft','delete','element','inet','frp_ssh_guard',prefix+str(ip.version),'{',str(ip),'}'],capture_output=True)
        for scope in ['admin','forward']:g.state['bans'].pop(scope+':'+str(ip),None)
        g.save();g.emit({'action':'unban','ip':str(ip)})
        subprocess.run(['systemctl','kill','--kill-who=main','--signal=HUP','frp-ssh-guard.service'],check=True)
if __name__=='__main__':main()
