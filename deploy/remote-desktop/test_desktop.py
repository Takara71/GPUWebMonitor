import asyncio,sys,unittest,time
from unittest.mock import patch,AsyncMock
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import agent
from broker import Broker,Error,Tunnel
DISPLAY=dict(account=None,display=':0',authority='/test',uid=120,session='c1')
class AgentTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.a=agent.Agent({});self.auth=patch('agent.authenticate',return_value=True);self.display_patch=patch('agent.ensure_session',return_value=DISPLAY);self.auth.start();self.display=self.display_patch.start()
 async def asyncTearDown(self):self.auth.stop();self.display_patch.stop()
 async def acquire(self,user='alice'):return await self.a.acquire({'username':user,'password':'test','source':'192.0.2.1'})
 async def test_concurrent_users_one_winner(self):
  results=await asyncio.gather(self.acquire('alice'),self.acquire('bob'),return_exceptions=True)
  self.assertEqual(sum(isinstance(r,dict) for r in results),1);self.assertEqual(sum(isinstance(r,agent.Denied) and r.status==409 for r in results),1)
 async def test_same_account_second_tab_rejected(self):
  await self.acquire()
  with self.assertRaises(agent.Denied) as e:await self.acquire()
  self.assertEqual(e.exception.code,'occupied')
 async def test_session_starts_for_authenticated_account_only(self):
  await self.acquire('alice');self.display.assert_called_once_with('alice')
 async def test_session_failure_does_not_claim_server(self):
  self.display.side_effect=agent.Denied('desktop_start_failed',503)
  with self.assertRaises(agent.Denied):await self.acquire()
  self.assertIsNone(self.a.lease)
 async def test_disconnect_preserves_session(self):
  result=await self.acquire()
  with patch('agent.end_session') as end:
   await self.a.release({'lease':result['lease']});end.assert_not_called()
 async def test_end_only_stops_authenticated_account_session(self):
  result=await self.acquire('alice')
  with patch('agent.end_session') as end:
   await self.a.release({'lease':result['lease']},end=True);end.assert_called_once_with('alice')
 async def test_bad_credentials_no_lease(self):
  with patch('agent.authenticate',return_value=False):
   with self.assertRaises(agent.Denied):await self.acquire()
  self.assertIsNone(self.a.lease);self.display.assert_not_called()
 async def test_bruteforce_cooldown(self):
  with patch('agent.authenticate',return_value=False):
   for _ in range(5):
    with self.assertRaises(agent.Denied):await self.acquire()
   with self.assertRaises(agent.Denied) as e:await self.acquire()
  self.assertEqual(e.exception.status,429)
 async def test_release_requires_correct_ticket(self):
  result=await self.acquire()
  with self.assertRaises(agent.Denied):await self.a.release({'lease':'wrong'})
  self.assertIsNotNone(self.a.lease);await self.a.release({'lease':result['lease']});self.assertIsNone(self.a.lease)
 async def test_password_not_in_lease(self):
  await self.acquire();self.assertNotIn('password',self.a.lease)
 async def test_touch_requires_ticket(self):
  result=await self.acquire();old=self.a.lease['expires'];await self.a.command('touch',{'lease':result['lease']});self.assertGreater(self.a.lease['expires'],old)
 async def test_stream_requires_lease(self):
  with self.assertRaises(agent.Denied):await self.a.command('stream',{'lease':'fake','stream_id':'a'*32})
 async def test_status_does_not_expose_secrets(self):
  result=await self.acquire();state=await self.a.status();self.assertNotIn('lease',state);self.assertNotIn(result['lease'],str(state))
class BrokerTests(unittest.IsolatedAsyncioTestCase):
 async def test_tunnel_error_translation(self):
  ws=AsyncMock();t=Tunnel(ws)
  async def respond(data):t.pending[data['id']].set_result({'status':409,'data':{'error':'occupied','account':'alice'}})
  ws.send_json.side_effect=respond
  with self.assertRaises(Error) as e:await t.request('acquire')
  self.assertEqual(e.exception.status,409);self.assertEqual(e.exception.data['account'],'alice');self.assertEqual(t.pending,{})
 async def test_browser_binding(self):
  b=Broker({});b.leases['t']={'node':'gpu-node-a','identity':'owner','expires':time.monotonic()+10}
  class Request:match_info={'node':'gpu-node-a'}
  with self.assertRaises(Error):b.bound(Request(),'other','t')
  self.assertEqual(b.bound(Request(),'owner','t')['node'],'gpu-node-a')
 async def test_wrong_node_or_expired_ticket(self):
  b=Broker({});b.leases['t']={'node':'gpu-node-a','identity':'owner','expires':time.monotonic()-1}
  class Request:match_info={'node':'gpu-node-a'}
  with self.assertRaises(Error):b.bound(Request(),'owner','t')
 async def test_unknown_node_closed(self):
  with self.assertRaises(Error):Broker({}).node('untrusted')
if __name__=='__main__':unittest.main()
