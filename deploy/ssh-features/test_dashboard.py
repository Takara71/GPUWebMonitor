import datetime,json,tempfile,unittest
from pathlib import Path
from receiver import connect,ingest
from dashboard import dashboard,TZ
NOW=datetime.datetime(2026,9,24,1,0,tzinfo=TZ).timestamp()
class DashboardTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/'db')
  for node in ['vps','5090','4090-1','4090-2']:ingest(self.db,node,[dict(kind='health',id='health',ts=NOW,health={'auth_backfill_complete':True})],NOW)
 def tearDown(self):self.db.close();self.temp.cleanup()
 def add(self,id,method='password',ts=NOW-10):
  ingest(self.db,'5090',[dict(kind='auth',id=id,ts=ts,peer='127.0.0.1',peer_port=12345,username='sensitive_name',outcome='failure',invalid=False,method=method)],NOW)
 def test_password_only(self):
  self.add('a');self.add('b','publickey');self.add('c','unknown');e=dashboard(self.db,NOW)['nodes']['gpu-node-a'];self.assertEqual(e['password_failures_today'],1);self.assertEqual(e['unknown_method_failures'],1)
 def test_recent_window_crosses_midnight(self):
  now=NOW-3500;self.add("before",ts=now-200);e=dashboard(self.db,now)["nodes"]["gpu-node-a"];self.assertEqual(e["password_failures_today"],0);self.assertEqual(e["password_failures_recent"],1)
 def test_china_midnight(self):
  self.add('before',ts=NOW-3601);self.add('after',ts=NOW-3599);d=dashboard(self.db,NOW);self.assertEqual(d['date'],'2026-09-24');self.assertEqual(d['nodes']['gpu-node-a']['password_failures_today'],1)
 def test_replay_fills_method_without_double_count(self):
  self.add('a','unknown');self.add('a');self.add('a');e=dashboard(self.db,NOW)['nodes']['gpu-node-a'];self.assertEqual(e['password_failures_today'],1);self.assertEqual(e['unknown_method_failures'],0)
 def test_stale_is_not_green(self):self.assertEqual(dashboard(self.db,NOW+120)['nodes']['gpu-node-a']['risk'],'unknown')
 def test_backfill_unknown(self):
  ingest(self.db,'5090',[dict(kind='health',id='health',ts=NOW,health={'auth_backfill_complete':False})],NOW);self.assertEqual(dashboard(self.db,NOW)['nodes']['gpu-node-a']['risk'],'unknown')
 def test_usernames_not_exposed(self):self.add('a');self.assertNotIn('sensitive_name',json.dumps(dashboard(self.db,NOW)))
 def test_high_frequency(self):
  for i in range(100):self.add(str(i))
  self.assertEqual(dashboard(self.db,NOW)['nodes']['gpu-node-a']['risk'],'high');self.assertEqual(dashboard(self.db,NOW)['nodes']['gpu-node-b']['risk'],'low')
if __name__=='__main__':unittest.main()
