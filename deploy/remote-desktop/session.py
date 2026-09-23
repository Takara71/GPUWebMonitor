#!/usr/bin/env python3
"""A user-owned virtual X11 desktop; lifecycle is restricted to its systemd unit."""
import json,os,pathlib,pwd,secrets,signal,subprocess,sys,time

def main():
    user=pwd.getpwuid(os.getuid());uid=user.pw_uid
    if uid<1000:raise RuntimeError('A regular Linux account is required')
    os.chdir(user.pw_dir)
    runtime=pathlib.Path('/run/lab-desktop-'+str(uid));runtime.mkdir(mode=0o700,exist_ok=True)
    os.umask(0o077);auth=runtime/'Xauthority';state=runtime/'state.json';state.unlink(missing_ok=True)
    display=':'+str(10000+uid)
    if pathlib.Path('/tmp/.X11-unix/X'+display[1:]).exists():raise RuntimeError('Display number is already in use')
    auth.touch(mode=0o600)
    subprocess.run(['xauth','-f',str(auth),'add',display,'.',secrets.token_hex(16)],check=True)
    config=pathlib.Path(user.pw_dir)/'.config/lab-remote-desktop';config.mkdir(parents=True,exist_ok=True)
    settings=config/'xfce4/xfconf/xfce-perchannel-xml';settings.mkdir(parents=True,exist_ok=True)
    defaults={
      'xfwm4.xml':'<channel name="xfwm4" version="1.0"><property name="general" type="empty"><property name="use_compositing" type="bool" value="false"/></property></channel>',
      'xfce4-session.xml':'<channel name="xfce4-session" version="1.0"><property name="general" type="empty"><property name="SaveOnExit" type="bool" value="false"/></property><property name="shutdown" type="empty"><property name="LockScreen" type="bool" value="false"/></property></channel>',
    }
    for name,text in defaults.items():
        path=settings/name
        if not path.exists():path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'+text+'\n')
    helpers=config/'xfce4/helpers.rc'
    if not helpers.exists():helpers.write_text('TerminalEmulator=xfce4-terminal\nFileManager=Thunar\n')
    autostart=config/'autostart';autostart.mkdir(exist_ok=True)
    for name in ('xfce4-screensaver','light-locker','xfce4-power-manager','gnome-keyring-pkcs11','gnome-keyring-secrets','gnome-keyring-ssh','org.gnome.Evolution-alarm-notify','tracker-miner-fs-3','org.gnome.DejaDup.Monitor','update-notifier','ubuntu-advantage-notification'):
        (autostart/(name+'.desktop')).write_text('[Desktop Entry]\nType=Application\nName='+name+'\nHidden=true\n')
    desktop=pathlib.Path(user.pw_dir)/'Desktop';desktop.mkdir(exist_ok=True)
    for name in ('lab-opengl.desktop','lab-desktop-help.desktop'):
        source=pathlib.Path('/opt/lab-desktop')/name;target=desktop/name
        if not target.exists():target.write_bytes(source.read_bytes());target.chmod(0o700)
    env={k:v for k,v in os.environ.items() if k not in ('DBUS_SESSION_BUS_ADDRESS','SESSION_MANAGER','WAYLAND_DISPLAY','LD_PRELOAD')}
    env.update(HOME=user.pw_dir,USER=user.pw_name,LOGNAME=user.pw_name,DISPLAY=display,XAUTHORITY=str(auth),XDG_RUNTIME_DIR=str(runtime),XDG_CONFIG_HOME=str(config),XDG_CACHE_HOME=str(pathlib.Path(user.pw_dir)/'.cache/lab-remote-desktop'),XDG_SESSION_TYPE='x11',XDG_SESSION_DESKTOP='xfce',XDG_CURRENT_DESKTOP='XFCE',DESKTOP_SESSION='xfce',LANG='zh_CN.UTF-8',LIBGL_ALWAYS_SOFTWARE='1')
    # The desktop compositor stays CPU-light. GPU application launchers remove
    # LIBGL_ALWAYS_SOFTWARE before using VirtualGL; native Vulkan uses its ICD.
    children=[]
    def stop(signum=None,frame=None):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        x=subprocess.Popen(['Xvfb',display,'-screen','0','1600x900x24','-nolisten','tcp','-auth',str(auth),'-noreset','+extension','RANDR'],env=env);children.append(x)
        for _ in range(100):
            if x.poll() is not None:raise RuntimeError('Virtual display exited')
            if subprocess.run(['xdpyinfo'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
            time.sleep(.1)
        else:raise RuntimeError('Virtual display did not start')
        subprocess.run(['xset','s','off'],env=env,check=False);subprocess.run(['xset','-dpms'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        session=subprocess.Popen(['dbus-run-session','--','xfce4-session'],env=env);children.append(session)
        time.sleep(1)
        if session.poll() is not None:raise RuntimeError('Desktop session exited during startup')
        temp=runtime/'state.new';temp.write_text(json.dumps({'uid':uid,'account':user.pw_name,'display':display,'authority':str(auth),'started':time.time(),'mode':'independent'}));temp.replace(state)
        print('Independent desktop ready for '+user.pw_name,flush=True)
        while x.poll() is None and session.poll() is None:time.sleep(1)
    finally:
        state.unlink(missing_ok=True)
        for child in reversed(children):
            if child.poll() is None:child.terminate()
        for child in reversed(children):
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        auth.unlink(missing_ok=True)
if __name__=='__main__':main()
