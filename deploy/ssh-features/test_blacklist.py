import json,tempfile,unittest
from pathlib import Path
from dashboard import blacklist
class BlacklistTests(unittest.TestCase):
 def test_complete_active_ipv4_ipv6_and_admin(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'state.json';bans={}
   for i in range(150):bans[str(i)]={'ip':f'8.8.0.{i+1}','scope':'forward','tier':'OTHER','until':2000,'history':[500]}
   bans['v6']={'ip':'2606:4700::1111','scope':'admin','tier':'OTHER','until':0,'history':[500]}
   bans['expired']={'ip':'1.1.1.1','scope':'forward','tier':'OTHER','until':999,'history':[]}
   p.write_text(json.dumps({'bans':bans}));r=blacklist(1000,p)
   self.assertTrue(r['available']);self.assertEqual(r['count'],151);self.assertNotIn('1.1.1.1',[b['ip'] for b in r['items']]);self.assertTrue(any(b['remaining_seconds'] is None for b in r['items']))
 def test_missing_is_not_empty_success(self):self.assertFalse(blacklist(1000,Path('/nonexistent-state'))['available'])
 def test_evidence_and_empty(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'s';p.write_text('{"bans":{}}');self.assertEqual(blacklist(1000,p)['count'],0)
   p.write_text(json.dumps({'bans':{'x':{'ip':'1.1.1.1','until':2000,'scope':'forward','tier':'OTHER','evidence':{'counts':5}}}}));self.assertEqual(blacklist(1000,p)['items'][0]['reason'],'authentication')
if __name__=='__main__':unittest.main()
