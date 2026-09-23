"""Controlled VPS ingress test using an isolated, temporary source address."""
import subprocess,json,time,os
from pathlib import Path
import guard

def cmd(*args,check=True):return subprocess.run(args,check=check,capture_output=True,text=True).stdout
client=subprocess.Popen(['unshare','-n','sleep','120'])
try:
 time.sleep(.1);cmd('ip','link','add','frpg-test-s','type','veth','peer','name','frpg-test-c');cmd('ip','link','set','frpg-test-c','netns',str(client.pid));cmd('ip','addr','add','198.18.0.1/30','dev','frpg-test-s');cmd('ip','link','set','frpg-test-s','up')
 ns=['nsenter','-t',str(client.pid),'-n'];cmd(*ns,'ip','addr','add','198.18.0.2/30','dev','frpg-test-c');cmd(*ns,'ip','link','set','frpg-test-c','up');cmd(*ns,'ip','link','set','lo','up')
 code="""import socket,time
for p in [51022,51023,51024,51022]:
 s=socket.socket();s.settimeout(.5)
 try:s.connect(('198.18.0.1',p));print('accepted',p)
 except OSError:print('limited',p)
 finally:s.close()
 time.sleep(.3)
"""
 out=cmd(*ns,'python3','-c',code);print(out);assert out.count('accepted')==1 and out.count('limited')==3
 time.sleep(1)
 d=json.loads((guard.ROOT/'state.json').read_text());b=d['bans']['forward:198.18.0.2'];assert b['until']>time.time();print('PASS real kernel log -> running daemon -> one-hour ban')
 # The same source can still reach the management port before auth failure threshold.
 ask=Path('/tmp/frpg-invalid-askpass.sh');ask.write_text('#!/bin/sh\nprintf "invalid-test-password\\n"\n');ask.chmod(0o700)
 env=dict(os.environ,SSH_ASKPASS=str(ask),SSH_ASKPASS_REQUIRE='force',DISPLAY=':0')
 for i in range(5):
  r=subprocess.run([*ns,'ssh','-o','ConnectTimeout=3','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-o','PreferredAuthentications=password','-o','PubkeyAuthentication=no','-o','NumberOfPasswordPrompts=1','frpg-invalid-test@198.18.0.1','true'],env=env,stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
  assert r.returncode!=0
 time.sleep(1);d=json.loads((guard.ROOT/'state.json').read_text());assert d['bans']['admin:198.18.0.2']['until']>time.time();print('PASS real VPS SSH failure logs -> five failures -> management ban')
finally:
 cmd('python3','/opt/frp-ssh-guard/guard.py','unban','--ip','198.18.0.2',check=False)
 Path('/tmp/frpg-invalid-askpass.sh').unlink(missing_ok=True)
 client.terminate();client.wait();cmd('ip','link','del','frpg-test-s',check=False)
