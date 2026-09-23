#!/usr/bin/env python3
"""Authenticated HTTPS desktop gateway with exclusive, browser-bound leases."""
import asyncio,collections,hashlib,hmac,json,secrets,time
from pathlib import Path
from aiohttp import web,ClientSession,ClientTimeout,WSMsgType
NODES=('gpu-node-a','gpu-node-b','gpu-node-c')
class Error(Exception):
    def __init__(self,status,code,**data):self.status=status;self.data={'error':code,**data}
class Tunnel:
    def __init__(self,ws):self.ws=ws;self.pending={};self.streams={}
    async def request(self,op,args=None):
        ident=secrets.token_hex(12);future=asyncio.get_running_loop().create_future();self.pending[ident]=future
        try:
            await self.ws.send_json({'id':ident,'op':op,'args':args or {}})
            result=await asyncio.wait_for(future,45 if op in ('acquire','end') else 20)
            if result['status']!=200:raise Error(result['status'],result['data']['error'],**{k:v for k,v in result['data'].items() if k!='error'})
            return result['data']
        except (asyncio.TimeoutError,ConnectionError):raise Error(503,'node_unavailable')
        finally:self.pending.pop(ident,None)

class Broker:
    def __init__(self,cfg):self.cfg=cfg;self.nodes={};self.leases={};self.session=None;self.failures=collections.defaultdict(collections.deque)
    async def startup(self,app):self.session=ClientSession(timeout=ClientTimeout(total=5));app['watchdog']=asyncio.create_task(self.watchdog())
    async def cleanup(self,app):
        app['watchdog'].cancel();await asyncio.gather(app['watchdog'],return_exceptions=True)
        for t in list(self.nodes.values()):await t.ws.close()
        await self.session.close()
    async def authenticated(self,request,write=False):
        if write and request.headers.get('Origin')!=self.cfg['origin']:raise Error(403,'invalid_origin')
        cookie=request.headers.get('Cookie','')
        if not cookie:raise Error(401,'login_required')
        try:
            async with self.session.get('http://127.0.0.1:28458/session',headers={'Cookie':cookie}) as r:
                if r.status!=200 or not (await r.json()).get('authenticated'):raise Error(401,'login_required')
        except (OSError,asyncio.TimeoutError):raise Error(503,'authentication_unavailable')
        return hashlib.sha256(cookie.encode()).hexdigest()
    def node(self,name):
        if name not in NODES:raise Error(404,'unknown_node')
        tunnel=self.nodes.get(name)
        if not tunnel or tunnel.ws.closed:raise Error(503,'node_unavailable')
        return tunnel
    async def node_tunnel(self,request):
        name=request.headers.get('X-Desktop-Node','');token=self.cfg['tokens'].get(name)
        if not token or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+token):raise Error(403,'invalid_node')
        ws=web.WebSocketResponse(heartbeat=15,compress=False,max_msg_size=1048576);await ws.prepare(request)
        if name in self.nodes:await self.nodes[name].ws.close(code=1012)
        tunnel=Tunnel(ws);self.nodes[name]=tunnel
        try:
            async for msg in ws:
                if msg.type==WSMsgType.TEXT:
                    doc=json.loads(msg.data)
                    if doc.get('id') in tunnel.pending and not tunnel.pending[doc['id']].done():tunnel.pending[doc['id']].set_result(doc)
                    elif doc.get('event')=='stream_end':
                        client=tunnel.streams.get(doc.get('stream_id'))
                        if client is not None and not client.closed:asyncio.create_task(client.close(code=1012,message=b'desktop_session_changed'))
                elif msg.type==WSMsgType.BINARY and len(msg.data)>32:
                    client=tunnel.streams.get(msg.data[:32].decode(errors='ignore'))
                    if client is not None and not client.closed:await client.send_bytes(msg.data[32:])
        finally:
            if self.nodes.get(name) is tunnel:self.nodes.pop(name,None)
            for f in tunnel.pending.values():
                if not f.done():f.set_exception(ConnectionError())
            for client in list(tunnel.streams.values()):await client.close(code=1012,message=b'node_disconnected')
            for ident,lease in list(self.leases.items()):
                if lease['tunnel'] is tunnel:self.leases.pop(ident,None)
        return ws
    async def status(self,request):
        await self.authenticated(request)
        async def one(name):
            try:return name,await self.node(name).request('status')
            except Error as e:return name,dict(online=False,available=False,error=e.data['error'],occupied=False)
        states=dict(await asyncio.gather(*(one(n) for n in NODES)))
        return web.json_response({'nodes':states},headers={'Cache-Control':'no-store, private'})
    def bound(self,request,identity,ticket):
        name=request.match_info['node'];lease=self.leases.get(ticket)
        if not lease or lease['node']!=name or not hmac.compare_digest(lease['identity'],identity):raise Error(409,'lease_expired')
        if time.monotonic()>lease['expires']:raise Error(409,'lease_expired')
        return lease
    async def api(self,request):
        identity=await self.authenticated(request,True);name=request.match_info['node'];tunnel=self.node(name)
        try:body=await request.json()
        except (ValueError,TypeError):raise Error(400,'invalid_request')
        if not isinstance(body,dict):raise Error(400,'invalid_request')
        op=request.match_info['op']
        if op=='acquire':
            source=request.headers.get('X-Real-IP','unknown');now=time.monotonic();q=self.failures[source]
            while q and now-q[0]>600:q.popleft()
            if len(q)>=5:raise Error(429,'rate_limited')
            try:data=await tunnel.request('acquire',dict(username=body.get('username'),password=body.get('password'),source=source))
            except Error as e:
                if e.status==401:q.append(now)
                raise
            body.pop('password',None)
            ticket=secrets.token_urlsafe(32);self.leases[ticket]=dict(node=name,agent_lease=data.pop('lease'),identity=identity,tunnel=tunnel,expires=now+35,active=False,account=data['account'])
            return web.json_response({'ticket':ticket,**data},headers={'Cache-Control':'no-store'})
        ticket=body.get('ticket','');lease=self.bound(request,identity,ticket)
        if op=='heartbeat':
            await tunnel.request('touch',{'lease':lease['agent_lease']});lease['expires']=time.monotonic()+120
            return web.json_response({'ok':True})
        if op in ('release','end'):
            await self.release(ticket,end=op=='end');return web.json_response({'released':True})
        raise Error(404,'unknown_operation')
    async def release(self,ticket,end=False):
        lease=self.leases.pop(ticket,None)
        if not lease:return
        if lease.get('client') is not None:await lease['client'].close(code=1000,message=b'released')
        try:await lease['tunnel'].request('end' if end else 'release',{'lease':lease['agent_lease']})
        except Error:
            if end:raise
    async def client_ws(self,request):
        identity=await self.authenticated(request,True);ticket=request.query.get('ticket','');lease=self.bound(request,identity,ticket)
        if lease['active']:raise Error(409,'occupied',account=lease['account'])
        # This transition has no await: two tabs cannot simultaneously claim a lease.
        lease['active']=True;tunnel=lease['tunnel'];sid=secrets.token_hex(16)
        client=web.WebSocketResponse(heartbeat=15,compress=False,max_msg_size=1048576,protocols=('binary',))
        try:
            await client.prepare(request);lease['client']=client;tunnel.streams[sid]=client
            await tunnel.request('stream',{'lease':lease['agent_lease'],'stream_id':sid})
            lease['expires']=time.monotonic()+120
            async for msg in client:
                if msg.type==WSMsgType.BINARY:await tunnel.ws.send_bytes(sid.encode()+msg.data)
                elif msg.type==WSMsgType.TEXT:await client.close(code=1003,message=b'binary_required')
        except Error as e:await client.close(code=1008,message=e.data['error'].encode())
        except (ConnectionError,asyncio.TimeoutError):await client.close(code=1012)
        finally:
            tunnel.streams.pop(sid,None);lease['active']=False;lease.pop('client',None)
            if self.leases.get(ticket) is lease:
                lease['expires']=min(lease['expires'],time.monotonic()+30)
                try:await tunnel.request('pause',{'lease':lease['agent_lease']})
                except Error:pass
        return client
    async def watchdog(self):
        while True:
            await asyncio.sleep(5);now=time.monotonic()
            for ticket,lease in list(self.leases.items()):
                if now>lease['expires']:await self.release(ticket)
            for source in list(self.failures):
                if not self.failures[source] or now-self.failures[source][-1]>600:del self.failures[source]

@web.middleware
async def errors(request,handler):
    try:return await handler(request)
    except Error as e:return web.json_response(e.data,status=e.status,headers={'Cache-Control':'no-store'})

def create_app(cfg):
    broker=Broker(cfg);app=web.Application(client_max_size=4096,middlewares=[errors]);app['broker']=broker
    app.router.add_get('/desktop/node-tunnel',broker.node_tunnel);app.router.add_get('/desktop/api/status',broker.status)
    app.router.add_post('/desktop/api/{node}/{op}',broker.api);app.router.add_get('/desktop/ws/{node}',broker.client_ws)
    app.on_startup.append(broker.startup);app.on_cleanup.append(broker.cleanup);return app
if __name__=='__main__':web.run_app(create_app(json.loads(Path('/etc/lab-desktop/broker.json').read_text())),host='127.0.0.1',port=28461,access_log=None)
