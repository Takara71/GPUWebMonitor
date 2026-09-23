"""Tiny off-screen OpenGL probe: no display server, packages, or privileged access."""
import ctypes as C,json,os
E=C.CDLL('libEGL.so.1');G=C.CDLL('libGL.so.1');P=C.c_void_p;I=C.c_int;U=C.c_uint
E.eglGetProcAddress.restype=P;E.eglGetProcAddress.argtypes=[C.c_char_p]
def extension(name,result,args):return C.CFUNCTYPE(result,*args)(E.eglGetProcAddress(name.encode()))
def fn(lib,name,result,args):
 f=getattr(lib,name);f.restype=result;f.argtypes=args;return f
query=extension('eglQueryDevicesEXT',U,[I,C.POINTER(P),C.POINTER(I)])
platform=extension('eglGetPlatformDisplayEXT',P,[U,P,C.POINTER(I)])
initialize=fn(E,'eglInitialize',U,[P,C.POINTER(I),C.POINTER(I)])
choose=fn(E,'eglChooseConfig',U,[P,C.POINTER(I),C.POINTER(P),I,C.POINTER(I)])
fn(E,'eglBindAPI',U,[U]);fn(E,'eglCreatePbufferSurface',P,[P,P,C.POINTER(I)])
fn(E,'eglCreateContext',P,[P,P,P,C.POINTER(I)]);fn(E,'eglMakeCurrent',U,[P,P,P,P])
fn(E,'eglDestroyContext',U,[P,P]);fn(E,'eglDestroySurface',U,[P,P]);fn(E,'eglTerminate',U,[P])
fn(G,'glGetString',C.c_char_p,[U]);fn(G,'glViewport',None,[I,I,I,I]);fn(G,'glClearColor',None,[C.c_float]*4);fn(G,'glClear',None,[U]);fn(G,'glBegin',None,[U]);fn(G,'glEnd',None,[]);fn(G,'glColor3f',None,[C.c_float]*3);fn(G,'glVertex2f',None,[C.c_float]*2);fn(G,'glFinish',None,[]);fn(G,'glReadPixels',None,[I,I,I,I,U,U,P]);fn(G,'glGetError',U,[])
count=I();devices=(P*16)();assert query(16,devices,C.byref(count))
results=[]
for index in range(count.value):
 d=platform(0x313F,devices[index],None);major=I();minor=I();context=None;surface=None
 try:
  if not initialize(d,C.byref(major),C.byref(minor)):raise RuntimeError('EGL initialization denied')
  assert E.eglBindAPI(0x30A2)
  attrs=(I*9)(0x3033,1,0x3040,8,0x3024,8,0x3025,16,0x3038);cfg=P();n=I();assert choose(d,attrs,C.byref(cfg),1,C.byref(n)) and n.value
  surface=E.eglCreatePbufferSurface(d,cfg,(I*5)(0x3057,32,0x3056,32,0x3038));context=E.eglCreateContext(d,cfg,None,(I*1)(0x3038));assert surface and context and E.eglMakeCurrent(d,surface,surface,context)
  renderer=G.glGetString(0x1F01).decode();vendor=G.glGetString(0x1F00).decode();version=G.glGetString(0x1F02).decode()
  G.glViewport(0,0,32,32);G.glClearColor(0,0,0,1);G.glClear(0x4000);G.glColor3f(1,0,0);G.glBegin(4)
  for x,y in [(-1,-1),(1,-1),(0,1)]:G.glVertex2f(x,y)
  G.glEnd();G.glFinish();pixel=(C.c_ubyte*4)();G.glReadPixels(16,16,1,1,0x1908,0x1401,pixel)
  err=G.glGetError();results.append(dict(device=index,vendor=vendor,renderer=renderer,opengl=version,pixel=list(pixel),render_pass=err==0 and pixel[0]>240 and pixel[1]<10 and pixel[2]<10))
 except Exception as e:results.append(dict(device=index,error=type(e).__name__+': '+str(e)))
 finally:
  E.eglMakeCurrent(d,None,None,None)
  if context:E.eglDestroyContext(d,context)
  if surface:E.eglDestroySurface(d,surface)
  E.eglTerminate(d)
print(json.dumps({'uid':os.getuid(),'display_set':bool(os.environ.get('DISPLAY')),'devices':results},ensure_ascii=False))
