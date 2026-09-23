import unittest,tempfile,time,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from receiver import connect,ingest,correlate,evaluate,summary,score,valid_event,tier_for
from agent import auth_event,flag
NOW=1000000000

def flow(node='5090',id='f',**kw):
 d=dict(kind='flow',id=id,start=NOW-10,ts=NOW-5,peer='127.0.0.1' if node!='vps' else '198.51.100.9',peer_port=44444,server_port=22 if node!='vps' else 51022,client_cookie='a'*32,server_cookie='b'*32,syn=True,end=None,banner='SSH-2.0-test',hassh='c'*32,algorithms='a;b;c;d');d.update(kw);return d

def auth(**kw):
 d=dict(kind='auth',id='a',ts=NOW-4,peer='127.0.0.1',peer_port=44444,username='test',outcome='failure',invalid=False);d.update(kw);return d

class Tests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/'test.db')
 def tearDown(self):self.db.close();self.temp.cleanup()
 def add(self,node,events):ingest(self.db,node,events,NOW)
 def result(self):correlate(self.db,NOW);return self.db.execute('SELECT * FROM auth').fetchone()
 def matched(self):self.add('5090',[flow(),auth()]);self.add('vps',[flow('vps')])
 def test_tshark_boolean_versions(self):
  for s in ["1","True","true"]:self.assertTrue(flag(s))
  for s in ["0","False","false",""]:self.assertFalse(flag(s))
 def test_exact_pair(self):self.matched();self.assertEqual(self.result()['source'],'198.51.100.9')
 def test_time_alone_is_never_enough(self):self.add('5090',[flow(),auth()]);self.add('vps',[flow('vps',server_cookie='d'*32)]);self.assertIsNone(self.result()['source'])
 def test_requires_both_cookies(self):self.add('5090',[flow(client_cookie=''),auth()]);self.add('vps',[flow('vps')]);self.assertIsNone(self.result()['source'])
 def test_duplicate_external_is_ambiguous(self):self.matched();self.add('vps',[flow('vps','other',peer='198.51.100.10')]);self.assertEqual(self.result()['status'],'ambiguous')
 def test_late_collision_revokes_match(self):self.matched();self.result();self.add('vps',[flow('vps','other')]);self.assertIsNone(self.result()['source'])
 def test_reused_local_port_is_ambiguous(self):self.matched();self.add('5090',[flow(id='second')]);self.assertEqual(self.result()['status'],'ambiguous')
 def test_closed_local_flow_excluded(self):self.matched();self.add('5090',[flow(id='old',start=NOW-100,ts=NOW-80,end=NOW-80)]);self.assertEqual(self.result()['status'],'exact_cookie_pair')
 def test_missing_syn_unmatched(self):self.add('5090',[flow(syn=False),auth()]);self.add('vps',[flow('vps')]);self.assertIsNone(self.result()['source'])
 def test_wrong_target_unmatched(self):self.add('5090',[flow(),auth()]);self.add('vps',[flow('vps',server_port=51023)]);self.assertIsNone(self.result()['source'])
 def test_auth_dedup(self):self.add('5090',[auth(),auth()]);self.assertEqual(self.db.execute('SELECT count(*) FROM auth').fetchone()[0],1)
 def test_auth_parsing(self):
  d={'_SYSTEMD_UNIT':'ssh.service','MESSAGE':'Failed password for invalid user admin from 127.0.0.1 port 44444 ssh2','__REALTIME_TIMESTAMP':str(NOW*1000000)}
  e=auth_event(d);self.assertEqual(e['username'],'admin');self.assertTrue(e['invalid']);d['_SYSTEMD_UNIT']='evil.service';self.assertIsNone(auth_event(d))
 def test_nan_rejected(self):
  with self.assertRaises(ValueError):valid_event(flow(ts=float('nan')),NOW)
 def test_one_typo_no_penalty(self):self.assertEqual(score([dict(outcome='failure',invalid=False,node='5090')],[],'OTHER')['score'],0)
 def test_hefei_more_tolerant(self):
  rows=[dict(outcome='failure',invalid=True,username='u'+str(i),node='5090') for i in range(10)]
  self.assertEqual(score(rows,[],'OTHER')['recommended_action'],'would_temporarily_ban');self.assertNotEqual(score(rows,[],'HF')['recommended_action'],'would_temporarily_ban')
 def test_geodata_expiry(self):self.assertEqual(tier_for('198.51.100.9',{'updated_at':NOW-50*86400,'4':['198.51.100.0/24']},NOW),'OTHER')
 def test_identical_fingerprint_alone_no_group(self):
  self.add('vps',[flow('vps',str(i),peer='198.51.100.'+str(i)) for i in range(1,5)]);evaluate(self.db,NOW,{})
  for r in summary(self.db)['risks']:self.assertEqual(r['groups'],[]);self.assertEqual(r['score'],0)
 def test_exact_multi_source_failures_group(self):
  for i in range(1,4):
   f=flow(id=str(i),peer_port=44000+i,server_cookie=str(i)*32);self.add('5090',[f,auth(id=str(i)+'a',peer_port=44000+i),auth(id=str(i)+'b',peer_port=44000+i)])
   self.add('vps',[flow('vps',str(i),peer='198.51.100.'+str(i),server_cookie=str(i)*32)])
  correlate(self.db,NOW);evaluate(self.db,NOW,{})
  self.assertTrue(all(r['groups'][0]['members']==3 for r in summary(self.db)['risks']))
if __name__=='__main__':unittest.main()
