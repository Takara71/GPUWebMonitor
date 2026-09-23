import importlib.util,tempfile,unittest,time,json
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('guard',Path(__file__).with_name('guard.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
class PolicyTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();g.ROOT=Path(self.tmp.name);self.calls=[];self.mock=patch.object(g,'nft',side_effect=lambda s,**kw:self.calls.append(s));self.mock.start();self.guard=g.Guard();self.now=1800000000
 def tearDown(self):self.mock.stop();self.tmp.cleanup()
 def test_outside_three_violations_then_escalation(self):
  for t in range(3):self.guard.event('forward','198.51.100.7','OTHER',self.now+t)
  b=self.guard.state['bans']['forward:198.51.100.7'];self.assertEqual(b['until'],self.now+2+3600)
  for t in range(3):self.guard.event('forward','198.51.100.7','OTHER',self.now+3603+t)
  self.assertEqual(self.guard.state['bans']['forward:198.51.100.7']['until'],self.now+3605+86400)
 def test_hefei_has_higher_tolerance(self):
  for t in range(5):self.guard.event('forward','203.0.113.8','HF',self.now+t)
  self.assertFalse(self.calls)
  self.guard.event('forward','203.0.113.8','HF',self.now+5);self.assertIn('timeout 600s',self.calls[-1])
 def test_sparse_violations_do_not_accumulate(self):
  for t in range(5):self.guard.event('forward','198.51.100.8','OTHER',self.now+121*t)
  self.assertFalse(self.calls)
 def test_admin_five_failures_and_ipv6(self):
  for t in range(5):self.guard.event('admin','2001:db8::1','OTHER',self.now+t)
  self.assertIn('admin_banned6',self.calls[-1]);self.assertIn('3600s',self.calls[-1])
 def test_loopback_never_banned(self):
  for t in range(20):self.guard.event('forward','127.0.0.1','OTHER',self.now+t)
  self.assertFalse(self.calls)
 def test_global_flood_strict_not_source_bans(self):
  for t in range(5):self.guard.global_event(self.now+t)
  self.assertEqual(self.guard.state['strict_until'],self.now+904);self.assertFalse(self.guard.state['bans'])
 def test_multiple_days_permanent_only_for_outside(self):
  h=[self.now-2*86400,self.now-86400];self.assertEqual(g.duration('OTHER',h,self.now),0);self.assertEqual(g.duration('HF',h,self.now),604800)
 def test_journal_trust_boundaries(self):
  self.guard.message({'MESSAGE':'FRPG OTHER SRC=198.51.100.9'});self.assertFalse(self.guard.events)
  for t in range(3):self.guard.message({'_TRANSPORT':'kernel','MESSAGE':'FRPG OTHER IN=eth0 SRC=198.51.100.9 DST=1.1.1.1'})
  self.assertIn('forward:198.51.100.9',self.guard.state['bans'])
 def test_expired_geo_fails_strict(self):
  p=g.ROOT/'geo.json';p.write_text(json.dumps({'updated_at':time.time()-46*86400,'4':['203.0.113.0/24'],'6':[]}));self.assertEqual(g.load_geo(p),{'4':[],'6':[]})
if __name__=='__main__':unittest.main()
