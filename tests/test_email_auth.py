import sys,tempfile,threading,unittest,http.client,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import email_auth,session_auth
from mail_otp import OTPStore
class Worker:
 def __init__(self,store):self.store=store;self.jobs=[]
 def submit(self,job):
  if job:self.jobs.append(job);self.store.sent(job[0],{})
class HTTPTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.secret=b'z'*32;self.store=OTPStore(Path(self.tmp.name)/'db',self.secret);self.worker=Worker(self.store);self.server=email_auth.create_server('127.0.0.1',0,self.secret,self.store,self.worker);threading.Thread(target=self.server.serve_forever,daemon=True).start()
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
 def req(self,path,body=None,cookie='',ip='1.1.1.1',origin='https://lab.test'):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port);h={'Origin':origin,'X-Original-Host':'lab.test','X-Real-IP':ip,'Cookie':cookie,'Content-Type':'application/json'};c.request('GET' if body is None else 'POST',path,json.dumps(body) if body is not None else None,h);r=c.getresponse();data=r.read();result=(r.status,json.loads(data) if data else {},r.getheader('Set-Cookie',''));c.close();return result
 def test_uniform_response_and_persistent_cooldown(self):
  a=self.req('/request-code',{'username':'bad'});b=self.req('/request-code',{'username':'y123'});self.assertEqual(a[:2],b[:2]);self.assertEqual(len(self.worker.jobs),0)
  c=self.req('/request-code',{'username':'y123'},ip='2.2.2.2');self.assertEqual(a[:2],c[:2]);self.assertEqual(len(self.worker.jobs),1)
 def test_login_and_replay_and_old_token_rejected(self):
  a=self.req('/request-code',{'username':'y123'});flow=a[2].split(';')[0];code=self.worker.jobs[-1][2]
  b=self.req('/login',{'username':'y123','code':code},flow);self.assertEqual(b[0],200);cookie=b[2].split(';')[0];self.assertEqual(self.req('/verify',cookie=cookie)[0],204)
  self.assertEqual(self.req('/login',{'username':'y123','code':code},flow)[0],401)
  old=session_auth.create_session_token('monitor','old',self.secret,600);self.assertEqual(self.req('/verify',cookie='__Host-lab_session='+old)[0],401)
 def test_password_cannot_login_and_csrf(self):
  self.assertEqual(self.req('/login',{'username':'monitor','password':'legacy'})[0],401)
  self.assertEqual(self.req('/request-code',{'username':'y123'},origin='https://evil.test')[0],403);self.assertFalse(self.worker.jobs)
 def test_other_browser_cannot_use_code(self):
  self.req('/request-code',{'username':'y123'});code=self.worker.jobs[-1][2];self.assertEqual(self.req('/login',{'username':'y123','code':code})[0],401)
if __name__=='__main__':unittest.main()
