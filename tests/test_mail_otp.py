import sys,tempfile,unittest,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from mail_otp import OTPStore
class OTPTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'a.db';self.s=OTPStore(self.path,b'x'*32);self.now=1800000000
 def tearDown(self):self.tmp.cleanup()
 def req(self,student='y123',ip='1.1.1.1',browser='browser',now=None):return self.s.request(student,ip,browser,self.now if now is None else now)
 def test_valid_variable_length_and_strict_format(self):
  for i,v in enumerate(['y1','y001234567890','abc','Y123','123','y１２３','y123@students.example.edu','y123\n','']):
   r=self.req(v,ip=str(i));self.assertEqual(bool(r),i<2)
 def test_invalid_consumes_ip_cooldown(self):
  self.assertIsNone(self.req('wrong'));self.assertIsNone(self.req());self.assertIsNotNone(self.req(now=self.now+60))
 def test_cooldown_survives_restart(self):
  self.req();self.s=OTPStore(self.path,b'x'*32);self.assertIsNone(self.req('y456'))
 def test_atomic_race(self):
  out=[];threads=[threading.Thread(target=lambda:out.append(self.req())) for _ in range(8)]
  [t.start() for t in threads];[t.join() for t in threads];self.assertEqual(sum(r is not None for r in out),1)
 def test_one_time_and_browser_bound(self):
  r=self.req();self.s.sent(r[0],{});self.assertFalse(self.s.verify('y123',r[2],'other','other',self.now+1)[0]);self.assertTrue(self.s.verify('y123',r[2].lower(),'1.1.1.1','browser',self.now+2)[0]);self.assertFalse(self.s.verify('y123',r[2],'1.1.1.1','browser',self.now+3)[0]);self.assertTrue(self.s.verified('y123'))
 def test_expiry(self):
  r=self.req();self.s.sent(r[0],{});self.assertFalse(self.s.verify('y123',r[2],'1.1.1.1','browser',self.now+301)[0])
 def test_five_attempt_limit(self):
  r=self.req();self.s.sent(r[0],{})
  for _ in range(5):self.assertFalse(self.s.verify('y123','BAD','1.1.1.1','browser',self.now+1)[0])
  self.assertFalse(self.s.verify('y123',r[2],'1.1.1.1','browser',self.now+2)[0])
 def test_invalid_suppressed_and_temporary_failure_retried(self):
  r=self.req();self.s.sent(r[0],error='temporary');self.assertIsNotNone(self.req(now=self.now+61));self.s.mark_invalid(['y123@students.example.edu']);self.assertIsNone(self.req(now=self.now+122))
 def test_sent_is_not_verified(self):
  r=self.req();self.s.sent(r[0],{});self.assertFalse(self.s.verified('y123'))
 def test_verified_not_overwritten_by_historical_invalid(self):
  r=self.req();self.s.sent(r[0],{});self.s.verify('y123',r[2],'1.1.1.1','browser',self.now+1);self.s.mark_invalid(['y123@students.example.edu']);self.assertTrue(self.s.verified('y123'))
 def test_mailbox_quota_across_ips(self):
  for i in range(5):self.assertIsNotNone(self.req(ip=str(i),now=self.now+i*61))
  self.assertIsNone(self.req(ip='different',now=self.now+6*61))
 def test_resend_same_code_without_extending_expiry(self):
  first=self.req();second=self.req(now=self.now+61);self.assertEqual(first[2],second[2])
  with self.s.db() as d:self.assertEqual(d.execute('select expires from challenges where id=?',(second[0],)).fetchone()[0],self.now+300)
  third=self.req(now=self.now+301);self.assertNotEqual(first[2],third[2])
 def test_success_invalidates_all_browser_copies(self):
  first=self.req();self.s.sent(first[0],{});second=self.req(ip='2.2.2.2',browser='second',now=self.now+61);self.s.sent(second[0],{})
  self.assertEqual(first[2],second[2]);self.assertTrue(self.s.verify('y123',first[2],'1.1.1.1','browser',self.now+62)[0]);self.assertFalse(self.s.verify('y123',second[2],'2.2.2.2','second',self.now+63)[0])
  third=self.req(now=self.now+122);self.assertNotEqual(first[2],third[2])
if __name__=='__main__':unittest.main()
