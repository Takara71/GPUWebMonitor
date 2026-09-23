"""Run as root inside `unshare -n`; never use the host network namespace."""
import os,sys,subprocess,socket,threading,json,time
from pathlib import Path
import guard
assert os.readlink('/proc/self/ns/net') != os.readlink('/proc/1/ns/net'), 'Must run inside unshare -n'
def cmd(*a):return subprocess.run(a,check=True,capture_output=True,text=True).stdout
def load(text):
 subprocess.run(['nft','delete','table','inet','frp_ssh_guard'],capture_output=True)
 guard.nft(text)
client=subprocess.Popen(['unshare','-n','sleep','240'])
try:
 time.sleep(.1);cmd('ip','link','set','lo','up');cmd('ip','link','add','guard-s','type','veth','peer','name','guard-c');cmd('ip','link','set','guard-c','netns',str(client.pid));cmd('ip','link','set','guard-s','up')
 for address in ['10.201.0.1/24','10.202.0.1/24']:cmd('ip','addr','add',address,'dev','guard-s')
 ns=['nsenter','-t',str(client.pid),'-n']
 cmd(*ns,'ip','link','set','lo','up');cmd(*ns,'ip','link','set','guard-c','up')
 for address in ['10.201.0.2/24','10.202.0.2/24']:cmd(*ns,'ip','addr','add',address,'dev','guard-c')
 cmd('ip','-6','addr','add','fd00:201::1/64','dev','guard-s','nodad');cmd(*ns,'ip','-6','addr','add','fd00:201::2/64','dev','guard-c','nodad')
 cmd('ip','-6','addr','add','fd00:202::1/64','dev','guard-s','nodad');cmd(*ns,'ip','-6','addr','add','fd00:202::2/64','dev','guard-c','nodad')
 listeners=[]
 def echo(c):
  try:
   while data:=c.recv(1024):c.sendall(data)
  except OSError:pass
  finally:c.close()
 def serve(s):
  while True:
   try:c,_=s.accept();threading.Thread(target=echo,args=(c,),daemon=True).start()
   except OSError:return
 for port in [51022,51023,51024,80]:
  s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',port));s.listen(100);listeners.append(s);threading.Thread(target=serve,args=(s,),daemon=True).start()
 s6=socket.socket(socket.AF_INET6);s6.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);s6.bind(('::',51022));s6.listen(100);listeners.append(s6);threading.Thread(target=serve,args=(s6,),daemon=True).start()
 def attempt(ip,n,ports=[51022],hold=False):
  code='''import socket,json,time
ok=[];ss=[]
for i in range(N):
 s=socket.socket();s.settimeout(.25);s.bind((IP,0))
 try:s.connect((IP.rsplit('.',1)[0]+'.1',PORTS[i%len(PORTS)]));s.sendall(b'x');assert s.recv(1)==b'x';ok.append(True);ss.append(s)
 except (OSError,AssertionError):ok.append(False);s.close()
print(json.dumps(ok),flush=True)
'''.replace('N',str(n)).replace('IP',repr(ip)).replace('PORTS',repr(ports))
  return json.loads(cmd(*ns,'python3','-c',code))
 base=guard.rules({'4':['10.201.0.0/24'],'6':[]})
 load(base);r=attempt('10.202.0.2',3,[51022,51023,51024]);assert r==[True,False,False],r;print('PASS outside one/minute shared across ports')
 load(base);r=attempt('10.201.0.2',7);assert r==[True]*6+[False],r;print('PASS Hefei burst six')
 load(base);r=attempt('10.202.0.2',4,[80]);assert all(r),r;print('PASS unrelated HTTP unaffected')
 load(base);guard.nft('add element inet frp_ssh_guard banned4 { 10.201.0.2 timeout 2s }');assert attempt('10.201.0.2',1)==[False];time.sleep(2.1);assert attempt('10.201.0.2',1)==[True];print('PASS timed ban expires')
 load(base);guard.nft('add element inet frp_ssh_guard strict { 51022 timeout 15m, 51023 timeout 15m, 51024 timeout 15m }');relaxed=base.replace('1/minute burst 1','1000/second burst 100').replace('18/minute burst 6','1000/second burst 100')
 # Isolate the global strict budget by removing the per-source rate only.
 load(relaxed);guard.nft('add element inet frp_ssh_guard strict { 51022 timeout 15m }');assert attempt('10.202.0.2',2)==[True,False];print('PASS strict outside shared budget')
 # Keep a connection open, then ban its address. Existing data must continue.
 load(base)
 code="import socket,sys;s=socket.create_connection(('10.201.0.1',51022));s.sendall(b'x');print(s.recv(1).decode(),flush=True);sys.stdin.readline();s.sendall(b'y');print(s.recv(1).decode(),flush=True)"
 p=subprocess.Popen([*ns,'python3','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True);assert p.stdout.readline().strip()=='x';guard.nft('add element inet frp_ssh_guard banned4 { 10.201.0.2 timeout 1m }');p.stdin.write('\n');p.stdin.flush();assert p.stdout.readline().strip()=='y';assert p.wait()==0;print('PASS established session survives ban')
 # Remove rate budgets to isolate concurrent-connection accounting.
 fast=base.replace('1/minute burst 1','1000/second burst 100').replace('18/minute burst 6','1000/second burst 100').replace('36/minute burst 18','1000/second burst 100')
 load(fast);r=attempt('10.202.0.2',7);assert r==[True]*6+[False],r;print('PASS outside concurrent cap six')
 load(fast);r=attempt('10.201.0.2',55);assert r==[True]*54+[False],r;print('PASS Hefei concurrent cap 54')
 global_test=base.replace('18/minute burst 6','1000/second burst 100')
 load(global_test);r=attempt('10.201.0.2',19);assert r==[True]*18+[False],r;print('PASS normal global burst 18')
 for subnet,expected in [('201',6),('202',1)]:
  load(guard.rules({'4':[],'6':['fd00:201::/64']}))
  code="import socket,json\nok=[];ss=[]\nfor i in range(7):\n s=socket.socket(socket.AF_INET6);s.settimeout(.15)\n try:s.connect(('fd00:SUBNET::1',51022));s.sendall(b'x');assert s.recv(1)==b'x';ok.append(True);ss.append(s)\n except (OSError,AssertionError):ok.append(False);s.close()\nprint(json.dumps(ok))".replace('SUBNET',subnet)
  r=json.loads(cmd(*ns,'python3','-c',code));assert sum(r)==expected,r;print('PASS IPv6 tier',subnet)
 print('All isolated kernel network tests passed')
finally:
 client.terminate();client.wait()
