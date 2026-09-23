import os,pathlib,subprocess,secrets,time,json
root=pathlib.Path('/var/tmp/lab-3d-validation');vgl=root/'vgl/opt/VirtualGL';auth=root/'test.xauth';display=':93'
assert not pathlib.Path('/tmp/.X11-unix/X93').exists(),'Test display already used'
auth.touch(mode=0o600);subprocess.run(['xauth','-f',str(auth),'add',display,'.',secrets.token_hex(16)],check=True)
env={**os.environ,'DISPLAY':display,'XAUTHORITY':str(auth),'LD_LIBRARY_PATH':str(root/'x/usr/lib/x86_64-linux-gnu')+':'+str(root/'vgl/usr/lib')}
log=(root/'xvfb.log').open('w');proc=subprocess.Popen([str(root/'x/usr/bin/Xvfb'),display,'-screen','0','640x480x24','-nolisten','tcp','-auth',str(auth)],env=env,stdout=log,stderr=log)
try:
 for _ in range(30):
  if pathlib.Path('/tmp/.X11-unix/X93').exists():break
  if proc.poll() is not None:raise RuntimeError((root/'xvfb.log').read_text())
  time.sleep(.1)
 for mode,cmd in [('baseline',[str(vgl/'bin/glxinfo'),'-B']),('gpu',[str(vgl/'bin/vglrun'),'-d','egl0','-ld',str(root/'vgl/usr/lib'),str(vgl/'bin/glxinfo'),'-B']),('render',[str(vgl/'bin/vglrun'),'-d','egl0','-ld',str(root/'vgl/usr/lib'),str(vgl/'bin/glxspheres64'),'-w','320x240','-n','4','-p','100','-f','60','-bt','0.1'])]:
  result=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=15);print(json.dumps({'mode':mode,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}))
finally:
 proc.terminate()
 try:proc.wait(timeout=5)
 except subprocess.TimeoutExpired:proc.kill();proc.wait()
 log.close();auth.unlink(missing_ok=True)
