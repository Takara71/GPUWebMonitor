import os,pathlib,subprocess,secrets,time,json
root=pathlib.Path('/var/tmp/lab-vulkan-validation');auth=root/'test.xauth';display=':94'
assert not pathlib.Path('/tmp/.X11-unix/X94').exists(),'Test display already used'
auth.touch(mode=0o600);subprocess.run(['xauth','-f',str(auth),'add',display,'.',secrets.token_hex(16)],check=True)
env={**os.environ,'DISPLAY':display,'XAUTHORITY':str(auth),'LD_LIBRARY_PATH':str(root/'root/usr/lib/x86_64-linux-gnu'),'VK_ICD_FILENAMES':'/usr/share/vulkan/icd.d/nvidia_icd.json'}
log=(root/'xvfb.log').open('w');proc=subprocess.Popen([str(root/'root/usr/bin/Xvfb'),display,'-screen','0','640x480x24','-nolisten','tcp','-auth',str(auth)],env=env,stdout=log,stderr=log)
try:
 for _ in range(30):
  if pathlib.Path('/tmp/.X11-unix/X94').exists():break
  if proc.poll() is not None:raise RuntimeError((root/'xvfb.log').read_text())
  time.sleep(.1)
 try:
  result=subprocess.run([str(root/'root/usr/bin/vkcube'),'--gpu_number','0','--width','320','--height','240','--c','60','--suppress_popups'],env=env,capture_output=True,text=True,timeout=15)
  print(json.dumps({'backend':'Xvfb','gpu':'NVIDIA ICD only','exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}))
 except subprocess.TimeoutExpired as e:print(json.dumps({'backend':'Xvfb','error':'Timed out after 15 seconds','stdout':str(e.stdout),'stderr':str(e.stderr)}))
finally:
 proc.terminate()
 try:proc.wait(timeout=5)
 except subprocess.TimeoutExpired:proc.kill();proc.wait()
 log.close();auth.unlink(missing_ok=True)
