"""Persistent email login challenges and Alibaba DirectMail transport."""
import base64, datetime, hashlib, hmac, json, logging, queue, re, secrets, sqlite3, threading, time, urllib.parse, urllib.request, urllib.error, uuid
from pathlib import Path
from contextlib import contextmanager
from cryptography.fernet import Fernet

STUDENT = re.compile(r'y[0-9]{1,63}\Z', re.ASCII)
ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
COOLDOWN = 60
TTL = 300

class MailError(Exception):
    def __init__(self, code): self.code = code; super().__init__(code)

class DirectMail:
    def __init__(self, config): self.config = config
    def call(self, action, **params):
        c=self.config
        p=dict(Format='JSON',Version='2015-11-23',AccessKeyId=c['access_key_id'],SignatureMethod='HMAC-SHA1',SignatureVersion='1.0',SignatureNonce=str(uuid.uuid4()),Timestamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),RegionId=c.get('region','cn-hangzhou'),Action=action,**params)
        quote=lambda x:urllib.parse.quote(str(x),safe='~')
        canonical='&'.join(quote(k)+'='+quote(p[k]) for k in sorted(p))
        sign='POST&%2F&'+quote(canonical)
        p['Signature']=base64.b64encode(hmac.new((c['access_key_secret']+'&').encode(),sign.encode(),hashlib.sha1).digest()).decode()
        req=urllib.request.Request(c.get('endpoint','https://dm.aliyuncs.com/'),data=urllib.parse.urlencode(p).encode(),headers={'Content-Type':'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req,timeout=12) as response: result=json.load(response)
        except urllib.error.HTTPError as error:
            try: code=json.loads(error.read(65536)).get('Code','provider_error')
            except Exception: code='provider_error'
            raise MailError(code) from None
        except (OSError,ValueError): raise MailError('temporary_transport_failure') from None
        if result.get('Code'): raise MailError(result['Code'])
        return result
    def send(self, student, code, expires=None):
        expiry=datetime.datetime.fromtimestamp(expires or time.time()+TTL,datetime.timezone(datetime.timedelta(hours=8))).strftime("%H:%M:%S")
        return self.call('SingleSendMail',AccountName=self.config['sender'],AddressType='1',ReplyToAddress='false',ToAddress=student+'@'+self.config['recipient_domain'],Subject='GPU 实验室网站登录验证码',FromAlias='GPU实验室',TextBody=f'你正在登录 GPU 实验室状态网站。\n\n登录验证码：{code}\n\n验证码有效至北京时间 {expiry}，仅可使用一次。首次生成后有效 5 分钟，重发不会延长有效期。请在发起登录的同一浏览器中输入，字母不区分大小写。\n如果不是你本人操作，请忽略本邮件。请勿将验证码告知他人。')
    def invalid_addresses(self):
        result=[];cursor=''
        for _ in range(20):
            params={'KeyWord':'@'+self.config['recipient_domain'],'Length':'100'}
            if cursor: params['NextStart']=cursor
            data=self.call('QueryInvalidAddress',**params)
            result.extend(r['ToAddress'].lower() for r in data.get('data',data.get('Data',{})).get('mailDetail',[]) if isinstance(r.get('ToAddress'),str))
            nxt=data.get('NextStart')
            if not nxt or nxt==cursor:break
            cursor=nxt
        return result

class OTPStore:
    def __init__(self, path, secret, recipient_domain="students.example.edu"):
        self.path=str(path);self.secret=secret;self.recipient_domain=recipient_domain
        self.cipher=Fernet(base64.urlsafe_b64encode(hmac.new(secret,b"email-otp-encryption-v1",hashlib.sha256).digest()))
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as d:
            d.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS students(student TEXT PRIMARY KEY,status TEXT NOT NULL,updated REAL NOT NULL,verified_at REAL,last_error TEXT);
            CREATE TABLE IF NOT EXISTS codes(student TEXT PRIMARY KEY,value BLOB NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS limits(key TEXT PRIMARY KEY,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,student TEXT NOT NULL,browser TEXT NOT NULL,digest TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,used INTEGER NOT NULL DEFAULT 0,ready INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS challenge_lookup ON challenges(student,browser,expires);
            CREATE TABLE IF NOT EXISTS sends(id TEXT PRIMARY KEY,student TEXT NOT NULL,created REAL NOT NULL,state TEXT NOT NULL,provider_id TEXT,error TEXT);
            CREATE INDEX IF NOT EXISTS send_time ON sends(created);
            CREATE TABLE IF NOT EXISTS failures(key TEXT NOT NULL,created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS failure_time ON failures(key,created);
            ''')
    @contextmanager
    def db(self):
        d=sqlite3.connect(self.path,timeout=5);d.row_factory=sqlite3.Row
        try:
            with d:yield d
        finally:d.close()
    def key(self, value):return hmac.new(self.secret,value.encode(),hashlib.sha256).hexdigest()
    def request(self, student, ip, browser, now=None):
        now=time.time() if now is None else now
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            d.execute('DELETE FROM limits WHERE until<?',(now-86400,))
            d.execute('DELETE FROM challenges WHERE expires<?',(now-86400,))
            d.execute('DELETE FROM codes WHERE expires<?',(now-86400,))
            d.execute('DELETE FROM failures WHERE created<?',(now-900,))
            d.execute('DELETE FROM sends WHERE created<?',(now-7*86400,))
            ipkey='send:'+self.key(ip)
            limit=d.execute('SELECT until FROM limits WHERE key=?',(ipkey,)).fetchone()
            if limit and limit[0]>now:return None
            d.execute('INSERT OR REPLACE INTO limits VALUES (?,?)',(ipkey,now+COOLDOWN))
            if not isinstance(student,str) or not STUDENT.fullmatch(student):return None
            rec=d.execute('SELECT status FROM students WHERE student=?',(student,)).fetchone()
            if rec and rec[0]=='invalid':return None
            # Independent address and total quotas prevent multi-IP mailbox bombing.
            if d.execute('SELECT count(*) FROM sends WHERE student=? AND created>?',(student,now-60)).fetchone()[0]:return None
            if d.execute('SELECT count(*) FROM sends WHERE student=? AND created>?',(student,now-3600)).fetchone()[0]>=5:return None
            if d.execute('SELECT count(*) FROM sends WHERE student=? AND created>?',(student,now-86400)).fetchone()[0]>=20:return None
            if d.execute('SELECT count(*) FROM sends WHERE created>?',(now-3600,)).fetchone()[0]>=120:return None
            if d.execute('SELECT count(*) FROM sends WHERE created>?',(now-86400,)).fetchone()[0]>=500:return None
            current=d.execute('SELECT * FROM codes WHERE student=?',(student,)).fetchone()
            if current and not current['used'] and current['expires']>now:
                code=self.cipher.decrypt(current['value']).decode();expires=current['expires']
            else:
                code=''.join(secrets.choice(ALPHABET) for _ in range(8));expires=now+TTL
                d.execute('INSERT OR REPLACE INTO codes VALUES (?,?,?,0)',(student,self.cipher.encrypt(code.encode()),expires))
            cid=secrets.token_urlsafe(24)
            d.execute('UPDATE challenges SET used=1 WHERE student=? AND browser=?',(student,self.key(browser)))
            d.execute('INSERT INTO challenges(id,student,browser,digest,expires) VALUES (?,?,?,?,?)',(cid,student,self.key(browser),self.key(cid+':'+code),expires))
            d.execute('INSERT INTO sends VALUES (?,?,?,?,NULL,NULL)',(cid,student,now,'queued'))
            d.execute("INSERT OR IGNORE INTO students VALUES (?,'pending',?,NULL,NULL)",(student,now))
            return (cid,student,code)
    def sent(self,cid,result=None,error=None):
        now=time.time()
        with self.db() as d:
            r=d.execute('SELECT student FROM sends WHERE id=?',(cid,)).fetchone()
            if not r:return
            d.execute('UPDATE sends SET state=?,provider_id=?,error=? WHERE id=?',('failed' if error else 'accepted',(result or {}).get('EnvId'),error,cid))
            if error:
                d.execute('UPDATE challenges SET used=1 WHERE id=?',(cid,))
                d.execute('UPDATE students SET last_error=?,updated=? WHERE student=?',(error,now,r[0]))
            else:
                d.execute('UPDATE challenges SET ready=1 WHERE id=?',(cid,))
                d.execute("UPDATE students SET status=CASE WHEN status='pending' THEN 'accepted' ELSE status END,last_error=NULL,updated=? WHERE student=?",(now,r[0]))
    def mark_invalid(self, addresses, now=None):
        now=time.time() if now is None else now
        with self.db() as d:
            for address in addresses:
                suffix='@'+self.recipient_domain
                if not address.endswith(suffix):continue
                student=address[:-len(suffix)]
                if not STUDENT.fullmatch(student):continue
                # A historical suppression entry cannot override proof of current ownership.
                d.execute("UPDATE students SET status='invalid',updated=?,last_error='provider_invalid_address' WHERE student=? AND verified_at IS NULL",(now,student))
                d.execute("UPDATE challenges SET used=1 WHERE student=? AND EXISTS(SELECT 1 FROM students WHERE student=? AND status='invalid')",(student,student))
    def verify(self,student,code,ip,browser,now=None):
        now=time.time() if now is None else now
        if not isinstance(student,str):student=''
        if not isinstance(code,str):code=''
        code=code.strip().upper()
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            keys=['verify-ip:'+self.key(ip),'verify-student:'+self.key(student)]
            for k in keys:
                r=d.execute('SELECT until FROM limits WHERE key=?',(k,)).fetchone()
                if r and r[0]>now:return False,max(1,int(r[0]-now))
            r=d.execute('SELECT * FROM challenges WHERE student=? AND browser=? ORDER BY expires DESC LIMIT 1',(student,self.key(browser))).fetchone()
            valid=bool(r and not r['used'] and r['ready'] and r['expires']>now and r['attempts']<5 and hmac.compare_digest(r['digest'],self.key(r['id']+':'+code)))
            if valid:
                d.execute('UPDATE challenges SET used=1 WHERE student=?',(student,))
                d.execute('UPDATE codes SET used=1 WHERE student=?',(student,))
                d.execute("UPDATE students SET status='verified',verified_at=?,updated=?,last_error=NULL WHERE student=?",(now,now,student))
                return True,0
            if r:d.execute('UPDATE challenges SET attempts=attempts+1 WHERE id=?',(r['id'],))
            for k in keys:
                d.execute('INSERT INTO failures VALUES (?,?)',(k,now))
                n=d.execute('SELECT count(*) FROM failures WHERE key=? AND created>?',(k,now-600)).fetchone()[0]
                if n>=5:d.execute('INSERT OR REPLACE INTO limits VALUES (?,?)',(k,now+900))
            return False,0
    def verified(self,student):
        with self.db() as d:
            r=d.execute('SELECT status FROM students WHERE student=?',(student,)).fetchone()
            return bool(r and r[0]=='verified')

class MailWorker:
    def __init__(self,store,provider):
        self.store=store;self.provider=provider;self.jobs=queue.Queue(maxsize=128)
        threading.Thread(target=self.run,daemon=True).start()
        threading.Thread(target=self.poll_invalid,daemon=True).start()
    def submit(self,job):
        if job:
            try:self.jobs.put_nowait(job)
            except queue.Full:self.store.sent(job[0],error='temporary_queue_full')
    def run(self):
        while True:
            cid,student,code=self.jobs.get()
            try:
                with self.store.db() as d:r=d.execute('SELECT expires,used FROM challenges WHERE id=?',(cid,)).fetchone()
                if not r or r['used'] or r['expires']<=time.time():
                    self.store.sent(cid,error='expired_before_send')
                else:self.store.sent(cid,result=self.provider.send(student,code,r['expires']))
            except MailError as e:
                self.store.sent(cid,error=e.code);logging.warning('mail_send_failed code=%s',e.code)
            except Exception:
                self.store.sent(cid,error='temporary_internal_error');logging.warning('mail_send_failed internal')
            finally:self.jobs.task_done()
    def poll_invalid(self):
        while True:
            try:self.store.mark_invalid(self.provider.invalid_addresses())
            except MailError as e:logging.warning('mail_invalid_sync_failed code=%s',e.code)
            except Exception:logging.warning('mail_invalid_sync_failed internal')
            time.sleep(300)
