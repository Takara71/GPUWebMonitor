#!/usr/bin/env python3
"""Outbound-only desktop tunnel. No public or local VNC listener."""
import asyncio,collections,json,os,pwd,re,secrets,socket,subprocess,time,sys,shutil
from pathlib import Path
import aiohttp

class Denied(Exception):
    def __init__(self,code,status=403,**info):self.code=code;self.status=status;self.info=info

def pam_authenticate(username,password):
    import PAM
    try:
        user=pwd.getpwnam(username)
        if user.pw_uid<1000 or user.pw_shell.endswith(('nologin','false')):return False
        def conversation(auth,queries,data):
            replies=[]
            for prompt,kind in queries:
                if kind==PAM.PAM_PROMPT_ECHO_OFF:replies.append((password,0))
                elif kind==PAM.PAM_PROMPT_ECHO_ON:replies.append((username,0))
                elif kind in (PAM.PAM_TEXT_INFO,PAM.PAM_ERROR_MSG):replies.append(('',0))
                else:raise ValueError('unsupported authentication challenge')
            return replies
        pam=PAM.pam();pam.start('lab-web-desktop');pam.set_item(PAM.PAM_USER,username);pam.set_item(PAM.PAM_CONV,conversation);pam.authenticate();pam.acct_mgmt();return True
    except (PAM.error,KeyError,ValueError):return False

def authenticate(username,password):
    # PAM modules may change process capabilities. Isolate each authentication.
    try:
        result=subprocess.run([sys.executable,__file__,'--authenticate'],input=json.dumps([username,password]),text=True,capture_output=True,timeout=15)
        return result.returncode==0 and result.stdout.strip()=='ok'
    except (OSError,subprocess.TimeoutExpired):return False

def session_info(username):
    user=pwd.getpwnam(username)
    if user.pw_uid<1000:raise Denied('invalid_credentials',401)
    unit='lab-desktop-session@'+str(user.pw_uid)+'.service'
    runtime=Path('/run/lab-desktop-'+str(user.pw_uid))
    active=subprocess.run(['systemctl','is-active','--quiet',unit],timeout=5).returncode==0
    if not active:raise Denied('desktop_unavailable',503)
    try:data=json.loads((runtime/'state.json').read_text())
    except (OSError,ValueError):raise Denied('desktop_starting',503)
    expected={'uid':user.pw_uid,'account':username,'display':':'+str(10000+user.pw_uid),'authority':str(runtime/'Xauthority')}
    if any(data.get(k)!=v for k,v in expected.items()):raise Denied('desktop_unavailable',503)
    return {**expected,'session':unit}

def ensure_session(username):
    user=pwd.getpwnam(username)
    if user.pw_uid<1000:raise Denied('invalid_credentials',401)
    unit='lab-desktop-session@'+str(user.pw_uid)+'.service'
    subprocess.run(['systemctl','start',unit],check=True,timeout=15)
    deadline=time.monotonic()+14
    while time.monotonic()<deadline:
        try:return session_info(username)
        except Denied:time.sleep(.2)
    raise Denied('desktop_start_failed',503)

def end_session(username):
    user=pwd.getpwnam(username)
    if user.pw_uid<1000:raise Denied('invalid_credentials',401)
    unit='lab-desktop-session@'+str(user.pw_uid)+'.service'
    subprocess.run(['systemctl','stop',unit],check=True,timeout=25)
    # Some desktop portal helpers need systemd's final SIGKILL. The user has
    # explicitly requested ending this desktop, so normalize the stopped unit.
    if subprocess.run(['systemctl','is-active','--quiet',unit],timeout=5).returncode==0:raise Denied('desktop_stop_failed',503)
    if subprocess.run(['systemctl','is-failed','--quiet',unit],timeout=5).returncode==0:
        subprocess.run(['systemctl','reset-failed',unit],check=False,timeout=5)

class Agent:
    def __init__(self,cfg):
        self.cfg=cfg;self.ws=None;self.lease=None;self.mutex=asyncio.Lock();self.stream_task=None;self.writer=None;self.proc=None;self.stream_id=None;self.capture_session=None;self.failures=collections.defaultdict(collections.deque)
    def check_lease(self,lease):
        if not self.lease or not secrets.compare_digest(lease or '',self.lease['id']):raise Denied('lease_expired',409)
    async def status(self):
        available=all(shutil.which(x) for x in ('Xvfb','xfce4-session','x11vnc','dbus-run-session'))
        return dict(online=True,available=available,mode='independent',desktop_account=None,occupied=bool(self.lease),account=self.lease['username'] if self.lease else None,since=self.lease['since'] if self.lease else None,connected=bool(self.stream_task and not self.stream_task.done()),error=None if available else 'desktop_unavailable')
    async def acquire(self,args):
        async with self.mutex:
            if self.lease:raise Denied('occupied',409,account=self.lease['username'])
            name=args.get('username','');password=args.get('password','');source=args.get('source','unknown')
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}',name) or not isinstance(password,str) or not 1<=len(password)<=512:raise Denied('invalid_credentials',401)
            now=time.monotonic()
            for key in ['ip:'+source,'user:'+name]:
                q=self.failures[key]
                while q and now-q[0]>600:q.popleft()
                if len(q)>=(5 if key.startswith('ip:') else 10):raise Denied('rate_limited',429,retry_after=600)
            good=await asyncio.to_thread(authenticate,name,password)
            args.pop('password',None);password=None
            if not good:
                for key in ['ip:'+source,'user:'+name]:self.failures[key].append(now)
                raise Denied('invalid_credentials',401)
            await asyncio.to_thread(ensure_session,name)
            self.lease=dict(id=secrets.token_hex(16),username=name,since=time.time(),expires=time.monotonic()+35)
            print(json.dumps({'event':'acquired','account':name}),flush=True)
            return {'lease':self.lease['id'],**await self.status()}
    async def stop_stream(self):
        t=self.stream_task;self.stream_task=None
        if t and t is not asyncio.current_task():t.cancel();await asyncio.gather(t,return_exceptions=True)
    async def release(self,args,end=False):
        async with self.mutex:
            self.check_lease(args.get('lease'));await self.stop_stream()
            if end:await asyncio.to_thread(end_session,self.lease['username'])
            self.lease=None
        return {'released':True}
    async def command(self,op,args):
        if op=='status':return await self.status()
        if op=='acquire':return await self.acquire(args)
        if op=='release':return await self.release(args)
        if op=='end':return await self.release(args,end=True)
        async with self.mutex:
            self.check_lease(args.get('lease'))
            if op=='touch':self.lease['expires']=time.monotonic()+120;return {'ok':True}
            if op=='pause':await self.stop_stream();self.lease['expires']=min(self.lease['expires'],time.monotonic()+30);return {'ok':True}
            if op=='stream':
                if self.stream_task and not self.stream_task.done():raise Denied('occupied',409,account=self.lease['username'])
                d=await asyncio.to_thread(session_info,self.lease['username'])
                sid=args.get('stream_id','')
                if not re.fullmatch('[0-9a-f]{32}',sid):raise Denied('invalid_stream',400)
                self.stream_id=sid;self.capture_session=d['session'];self.lease['expires']=time.monotonic()+120
                self.stream_task=asyncio.create_task(self.stream(d,sid));return {'ok':True}
            raise Denied('unknown_operation',400)
    async def stream(self,d,sid):
        parent,child=socket.socketpair();parent.setblocking(False);proc=None;writer=None
        try:
            args=['x11vnc','-inetd','-q','-display',d['display'],'-auth',d['authority'],'-once','-nopw','-noremote','-nocmds','-nosel','-xkb','-repeat','-wait','16','-defer','12','-nap']
            env=os.environ.copy();env.update(DISPLAY=d['display'],XAUTHORITY=d['authority'])
            try:
                dimensions=await asyncio.to_thread(subprocess.run,['xdpyinfo'],env=env,capture_output=True,text=True,timeout=5)
                match=re.search(r'dimensions:\s+(\d+)x(\d+)',dimensions.stdout)
                if match:
                    ratio=min(1,1600/int(match[1]),900/int(match[2]))
                    if ratio<1:args+=['-scale',f'{ratio:.4f}']
            except subprocess.TimeoutExpired:pass
            # inetd transports RFB exclusively over the private socketpair, never a listening port.
            proc=await asyncio.create_subprocess_exec(*args,stdin=child,stdout=child,stderr=None,user=d['uid'],group=pwd.getpwuid(d['uid']).pw_gid,extra_groups=[])
            self.proc=proc;child.close();reader,writer=await asyncio.open_connection(sock=parent);self.writer=writer
            while data:=await reader.read(65536):await self.ws.send_bytes(sid.encode()+data)
        except (ConnectionError,aiohttp.ClientError):pass
        except Exception:
            import traceback;traceback.print_exc()
        finally:
            child.close()
            if writer:writer.close()
            else:parent.close()
            self.writer=None;self.proc=None
            if proc and proc.returncode is None:
                try:proc.terminate()
                except ProcessLookupError:pass
                try:await asyncio.wait_for(proc.wait(),3)
                except asyncio.TimeoutError:proc.kill();await proc.wait()
            if self.ws and not self.ws.closed:
                await self.ws.send_json({'event':'stream_end','stream_id':sid})
    async def dispatch(self,doc):
        ident=doc.get('id');op=doc.get('op');args=doc.get('args',{})
        try:payload=await self.command(op,args);status=200
        except Denied as e:payload={'error':e.code,**e.info};status=e.status
        except Exception:payload={'error':'desktop_unavailable'};status=503
        if self.ws and not self.ws.closed:await self.ws.send_json({'id':ident,'status':status,'data':payload})
    async def watchdog(self):
        while True:
            await asyncio.sleep(3)
            if self.lease and time.monotonic()>self.lease['expires']:
                try:await self.release({'lease':self.lease['id']})
                except Denied:pass
            # A user may log out of the independent desktop from inside XFCE.
            if self.lease and self.stream_task and not self.stream_task.done():
                try:await asyncio.to_thread(session_info,self.lease['username'])
                except Denied:await self.stop_stream()
            now=time.monotonic()
            for key in list(self.failures):
                if not self.failures[key] or now-self.failures[key][-1]>600:del self.failures[key]
    async def run(self):
        watch=asyncio.create_task(self.watchdog())
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None,sock_connect=15)) as session:
            while True:
                tasks=set()
                try:
                    async with session.ws_connect(self.cfg['url'],headers={'Authorization':'Bearer '+self.cfg['token'],'X-Desktop-Node':self.cfg['node']},heartbeat=15,compress=0,max_msg_size=1048576) as ws:
                        self.ws=ws;print('desktop tunnel connected',flush=True)
                        async for msg in ws:
                            if msg.type==aiohttp.WSMsgType.TEXT:
                                doc=json.loads(msg.data)
                                if len(tasks)>=20:continue
                                t=asyncio.create_task(self.dispatch(doc));tasks.add(t);t.add_done_callback(tasks.discard)
                            elif msg.type==aiohttp.WSMsgType.BINARY:
                                if self.writer and msg.data[:32].decode(errors='ignore')==self.stream_id:self.writer.write(msg.data[32:]);await self.writer.drain()
                except (aiohttp.ClientError,asyncio.TimeoutError,OSError,ValueError):print('desktop tunnel reconnecting',flush=True)
                finally:
                    for t in tasks:t.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True);await self.stop_stream();self.lease=None;self.ws=None
                await asyncio.sleep(5)
if __name__=='__main__':
    if sys.argv[1:]==['--authenticate']:
        username,password=json.load(sys.stdin);print('ok' if pam_authenticate(username,password) else 'denied')
    else:asyncio.run(Agent(json.loads(Path('/etc/lab-desktop/agent.json').read_text())).run())
