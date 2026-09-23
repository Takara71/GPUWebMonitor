import unittest
from auth_policy import candidates
class AuthPolicyTests(unittest.TestCase):
 def setUp(self):self.now=1800000000;self.geo={'updated_at':self.now,'4':[],'6':[]}
 def rows(self,n,step=190,invalid=False):
  return [dict(source='8.8.4.4',ts=self.now-60-i*step,status='exact_cookie_pair',outcome='failure',method='password',username=str(i) if invalid else 'user',invalid=invalid) for i in range(n)]
 def get(self,r,prior=None):return list(candidates(r,self.now,self.geo,prior or {}))
 def test_slow_attack(self):self.assertTrue(self.get(self.rows(12)))
 def test_single_typo(self):self.assertFalse(self.get(self.rows(1)))
 def test_invalid_spray(self):self.assertTrue(self.get(self.rows(3,invalid=True)))
 def test_hefei_tolerance_and_limit(self):
  self.geo['4']=['8.8.4.0/24'];self.assertFalse(self.get(self.rows(12)));self.assertTrue(self.get(self.rows(8,invalid=True)))
 def test_unmatched_publickey_success_excluded(self):
  for k,v in [('status','unmatched'),('method','publickey'),('outcome','success'),('source','127.0.0.1')]:
   rows=self.rows(30);[r.update({k:v}) for r in rows];self.assertFalse(self.get(rows))
 def test_old_evidence_not_rebanned(self):
  prior={'forward:8.8.4.4':{'until':self.now-20}};self.assertFalse(self.get(self.rows(30),prior))
 def test_daily_window(self):self.assertTrue(self.get(self.rows(20,step=3000)))
 def test_settling_and_future_excluded(self):
  rows=self.rows(30);[r.update(ts=self.now-5) for r in rows];self.assertFalse(self.get(rows))
 def test_expired_geo_strict(self):
  self.geo.update(updated_at=self.now-46*86400,**{'4':['8.8.4.0/24']});self.assertTrue(self.get(self.rows(5,step=60)))
if __name__=='__main__':unittest.main()
