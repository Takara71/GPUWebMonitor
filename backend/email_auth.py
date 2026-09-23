"""School email OTP login; the legacy password endpoint is not served."""
import argparse,json,re,secrets,time
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from session_auth import AuthRequestHandler,AuthSettings,Credential,create_session_token,verify_session_token,parse_cookie_token,decode_base64url,load_secret,SHORT_SESSION_SECONDS,REMEMBER_SESSION_SECONDS
from mail_otp import OTPStore,DirectMail,MailWorker,STUDENT

VERSION='school-email-v1'
FLOW_COOKIE='__Host-lab_login_flow'

class EmailHandler(AuthRequestHandler):
    store: OTPStore
    worker: MailWorker
    def authenticated(self):
        token=parse_cookie_token(self.headers.get('Cookie',''))
        try:
            payload=json.loads(decode_base64url(token.split('.')[0]));student=payload.get('u','')
            if not isinstance(student,str) or not STUDENT.fullmatch(student):return False
            return verify_session_token(token,Credential(student,'',VERSION),self.settings.secret) and self.store.verified(student)
        except (ValueError,TypeError,KeyError,UnicodeError):return False
    def flow(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''));value=cookie[FLOW_COOKIE].value
        except Exception:value=''
        return value if re.fullmatch(r'[A-Za-z0-9_-]{43}',value) else secrets.token_urlsafe(32)
    def do_POST(self):
        path=urlsplit(self.path).path
        if path=='/logout':return super().do_POST()
        if path not in ('/request-code','/login'):
            return self.send_json(404,{'error':'not_found'})
        if not self.write_origin_is_valid():return self.send_json(403,{'error':'invalid_origin'})
        try:doc=self.read_json_body()
        except (ValueError,UnicodeError):return self.send_json(400,{'error':'invalid_request'})
        student=doc.get('username','');flow=self.flow()
        if path=='/request-code':
            job=self.store.request(student,self.client_ip(),flow)
            self.worker.submit(job)
            return self.send_json(200,{'ok':True,'retry_after':60},{'Set-Cookie':f'{FLOW_COOKIE}={flow}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=600'})
        remember=doc.get('remember',False)
        if not isinstance(remember,bool):return self.send_json(400,{'error':'invalid_request'})
        valid,retry=self.store.verify(student,doc.get('code',''),self.client_ip(),flow)
        if not valid:return self.send_json(429 if retry else 401,{'error':'too_many_attempts' if retry else 'invalid_credentials','retry_after':retry})
        lifetime=REMEMBER_SESSION_SECONDS if remember else SHORT_SESSION_SECONDS
        token=create_session_token(student,VERSION,self.settings.secret,lifetime)
        self.send_json(200,{'authenticated':True,'expires_in':lifetime},{'Set-Cookie':self.set_session_cookie(token,remember)})

def create_server(host,port,secret,store,worker):
    if host not in ('127.0.0.1','::1'):raise ValueError('loopback only')
    settings=AuthSettings(host,port,'',secret,True)
    handler=type('ConfiguredEmailHandler',(EmailHandler,),dict(settings=settings,store=store,worker=worker))
    server=ThreadingHTTPServer((host,port),handler);server.daemon_threads=True
    return server

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=28458);p.add_argument('--secret-file',default='/etc/lab-status/session-secret');p.add_argument('--mail-config',default='/etc/lab-status/aliyun-mail.json');p.add_argument('--database',default='/var/lib/lab-email-auth/auth.sqlite');a=p.parse_args()
    secret=load_secret(a.secret_file);config=json.loads(Path(a.mail_config).read_text());store=OTPStore(a.database,secret,config['recipient_domain']);provider=DirectMail(config);worker=MailWorker(store,provider)
    create_server(a.host,a.port,secret,store,worker).serve_forever()
if __name__=='__main__':main()
