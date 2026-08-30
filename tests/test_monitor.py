import os
import sys
import json
import tempfile
import time
import unittest
from collections import namedtuple
from typing import Any
from unittest import mock


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import app as agent_app
import dashboard
import deployment_mode
import gpu_monitor
import storage_monitor
import storage_snapshot


CpuTimes = namedtuple("CpuTimes", "user system")
MemoryInfo = namedtuple("MemoryInfo", "rss")
VirtualMemory = namedtuple("VirtualMemory", "total available used percent")
NetIO = namedtuple("NetIO", "bytes_sent bytes_recv")
CpuFreq = namedtuple("CpuFreq", "current")
Partition = namedtuple("Partition", "device mountpoint fstype opts")
DiskUsage = namedtuple("DiskUsage", "total used free percent")


class FakeProcess:
    def __init__(
        self,
        pid: int,
        name: str,
        cpu_time: float,
        rss: int,
        username: str = "user",
        cmdline: list[str] | None = None,
    ) -> None:
        """创建具有固定 psutil 信息的测试进程。

        Args:
            pid: 模拟进程 ID。
            name: 模拟进程名称。
            cpu_time: 模拟累计 CPU 时间。
            rss: 模拟 RSS 内存字节数。
            username: 模拟进程所属用户。
            cmdline: 模拟完整命令行。

        Returns:
            无返回值。
        """
        self.info = {
            "pid": pid,
            "name": name,
            "username": username,
            "cmdline": cmdline or ["python", f"job-{pid}.py"],
            "create_time": 1000 + pid,
            "cpu_times": CpuTimes(cpu_time, 0),
            "memory_info": MemoryInfo(rss),
        }
        self._username = username
        self._cmdline = self.info["cmdline"]

    def oneshot(self) -> Any:
        """返回与 psutil oneshot 兼容的上下文管理器。

        Args:
            无。

        Returns:
            可用于 ``with`` 语句的模拟上下文管理器。
        """
        return mock.MagicMock(__enter__=lambda value: value, __exit__=lambda *args: None)

    def username(self) -> str:
        """返回模拟进程用户名。

        Args:
            无。

        Returns:
            初始化时设置的用户名。
        """
        return self._username

    def cmdline(self) -> list[str]:
        """返回模拟进程命令行。

        Args:
            无。

        Returns:
            初始化时设置的命令行参数列表。
        """
        return self._cmdline


class ProcessCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        """在每个测试前清空进程 CPU 采样缓存。

        Args:
            无。

        Returns:
            无返回值。
        """
        gpu_monitor._process_cpu_samples = {}

    def test_process_union_supports_cpu_and_memory_sorting(self) -> None:
        """验证结果同时保留 CPU 和内存排名靠前的进程。

        Args:
            无。

        Returns:
            无返回值。
        """
        cpu_heavy = FakeProcess(101, "cpu-heavy", 1, 100)
        memory_heavy = FakeProcess(202, "memory-heavy", 1, 10_000)

        with mock.patch.object(gpu_monitor.psutil, "process_iter", return_value=[cpu_heavy, memory_heavy]), mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=4), mock.patch.object(gpu_monitor.time, "monotonic", return_value=10):
            gpu_monitor.get_system_processes(limit=1, total_memory=40_000)

        cpu_heavy.info["cpu_times"] = CpuTimes(5, 0)
        memory_heavy.info["cpu_times"] = CpuTimes(1.2, 0)
        with mock.patch.object(gpu_monitor.psutil, "process_iter", return_value=[cpu_heavy, memory_heavy]), mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=4), mock.patch.object(gpu_monitor.time, "monotonic", return_value=12):
            processes = gpu_monitor.get_system_processes(limit=1, total_memory=40_000)

        self.assertEqual({process["pid"] for process in processes}, {101, 202})
        self.assertEqual(processes[0]["pid"], 101)
        self.assertEqual(processes[0]["cpu_percent"], 50.0)
        self.assertEqual(next(process for process in processes if process["pid"] == 202)["memory_rss"], 10_000)
        self.assertEqual(next(process for process in processes if process["pid"] == 202)["memory_percent"], 25.0)

    def test_identical_commands_are_grouped_and_totals_match(self) -> None:
        """验证相同命令实例会合并且资源总量保持一致。

        Args:
            无。

        Returns:
            无返回值。
        """
        first = FakeProcess(301, "python", 1, 1_000, cmdline=["python", "train.py"])
        second = FakeProcess(302, "python", 2, 3_000, cmdline=["python", "train.py"])

        with mock.patch.object(gpu_monitor.psutil, "process_iter", return_value=[first, second]), mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=4), mock.patch.object(gpu_monitor.time, "monotonic", return_value=20):
            gpu_monitor.get_system_processes(total_memory=100_000)

        first.info["cpu_times"] = CpuTimes(2, 0)
        second.info["cpu_times"] = CpuTimes(3, 0)
        with mock.patch.object(gpu_monitor.psutil, "process_iter", return_value=[first, second]), mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=4), mock.patch.object(gpu_monitor.time, "monotonic", return_value=22):
            processes = gpu_monitor.get_system_processes(total_memory=100_000)

        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["pids"], [301, 302])
        self.assertEqual(processes[0]["instance_count"], 2)
        self.assertEqual(processes[0]["cpu_percent"], 25.0)
        self.assertEqual(processes[0]["memory_bytes"], 4_000)
        self.assertEqual(processes[0]["memory_rss"], 4_000)
        self.assertEqual(processes[0]["memory_percent"], 4.0)

    def test_pss_prevents_shared_memory_double_counting_and_builds_user_totals(self) -> None:
        """验证 PSS 不重复计算共享内存并正确生成用户汇总。

        Args:
            无。

        Returns:
            无返回值。
        """
        first = FakeProcess(401, "worker", 1, 2_000, username="alice", cmdline=["python", "train.py"])
        second = FakeProcess(402, "worker", 1, 2_000, username="alice", cmdline=["python", "train.py"])
        other = FakeProcess(403, "server", 1, 1_000, username="bob", cmdline=["server"])
        snapshot = {
            "401": {"create_time": 1401, "pss": 600},
            "402": {"create_time": 1402, "pss": 700},
            "403": {"create_time": 1403, "pss": 900},
        }

        with mock.patch.object(gpu_monitor, "_load_memory_snapshot", return_value=snapshot), \
                mock.patch.object(gpu_monitor.psutil, "process_iter", return_value=[first, second, other]), \
                mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=4), \
                mock.patch.object(gpu_monitor.time, "monotonic", return_value=30):
            gpu_monitor.get_system_process_usage(total_memory=10_000)

        first.info["cpu_times"] = CpuTimes(2, 0)
        second.info["cpu_times"] = CpuTimes(2, 0)
        other.info["cpu_times"] = CpuTimes(1.4, 0)
        with mock.patch.object(gpu_monitor, "_load_memory_snapshot", return_value=snapshot), \
                mock.patch.object(gpu_monitor.psutil, "process_iter", return_value=[first, second, other]), \
                mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=4), \
                mock.patch.object(gpu_monitor.time, "monotonic", return_value=32):
            usage = gpu_monitor.get_system_process_usage(total_memory=10_000)

        alice_process = next(process for process in usage["processes"] if process["username"] == "alice")
        self.assertEqual(alice_process["memory_bytes"], 1_300)
        self.assertEqual(alice_process["memory_rss"], 4_000)
        self.assertEqual(alice_process["memory_metric"], "pss")
        alice = next(user for user in usage["users"] if user["username"] == "alice")
        self.assertEqual(alice["cpu_percent"], 25.0)
        self.assertEqual(alice["memory_bytes"], 1_300)
        self.assertEqual(alice["memory_percent"], 13.0)
        self.assertEqual(alice["process_group_count"], 1)
        self.assertEqual(alice["instance_count"], 2)

    def test_system_totals_use_consistent_cpu_and_memory_percentages(self) -> None:
        """验证系统总量与进程 CPU、内存百分比使用一致口径。

        Args:
            无。

        Returns:
            无返回值。
        """
        memory = VirtualMemory(total=1_000, available=350, used=500, percent=50)
        with mock.patch.object(gpu_monitor.psutil, "cpu_percent", return_value=125), \
                mock.patch.object(gpu_monitor.psutil, "virtual_memory", return_value=memory), \
                mock.patch.object(gpu_monitor.psutil, "net_io_counters", return_value=NetIO(10, 20)), \
                mock.patch.object(gpu_monitor.psutil, "cpu_freq", return_value=CpuFreq(2_200)), \
                mock.patch.object(gpu_monitor.psutil, "cpu_count", return_value=8), \
                mock.patch.object(gpu_monitor, "collect_filesystem_usage", return_value={"summary": {"total": 2_000, "used": 500, "free": 1_500, "percent": 25.0, "mount_count": 1}, "mounts": []}), \
                mock.patch.object(gpu_monitor, "get_system_process_usage", return_value={"processes": [], "users": [], "memory_metric": "pss"}) as get_usage:
            info = gpu_monitor.get_system_info()

        self.assertEqual(info["cpu"]["percent"], 100.0)
        self.assertEqual(info["memory"]["used"], 650)
        self.assertEqual(info["memory"]["percent"], 65.0)
        self.assertEqual(info["users"], [])
        self.assertEqual(info["process_memory_metric"], "pss")
        self.assertEqual(info["storage"]["percent"], 25.0)
        get_usage.assert_called_once_with(total_memory=1_000)


class StorageCollectionTests(unittest.TestCase):
    def test_filesystem_totals_exclude_virtual_network_and_duplicate_mounts(self) -> None:
        """验证总容量只累加本地持久化磁盘且不会重复统计设备。

        Args:
            无。

        Returns:
            无返回值。
        """
        partitions = [
            Partition('/dev/sda2', '/', 'ext4', 'rw'),
            Partition('/dev/sda1', '/boot/efi', 'vfat', 'rw'),
            Partition('/dev/sda2', '/srv/bind', 'ext4', 'rw,bind'),
            Partition('/dev/nvme0n1p1', '/data', 'xfs', 'rw'),
            Partition('tmpfs', '/run', 'tmpfs', 'rw'),
            Partition('storage:/share', '/mnt/share', 'nfs4', 'rw'),
        ]
        usages = {
            '/': DiskUsage(1_000, 400, 600, 40),
            '/srv/bind': DiskUsage(1_000, 400, 600, 40),
            '/data': DiskUsage(2_000, 500, 1_500, 25),
        }
        with mock.patch.object(storage_monitor.psutil, 'disk_partitions', return_value=partitions), \
                mock.patch.object(storage_monitor.psutil, 'disk_usage', side_effect=lambda path: usages[path]):
            storage = storage_monitor.collect_filesystem_usage()

        self.assertEqual(storage['summary']['total'], 3_000)
        self.assertEqual(storage['summary']['used'], 900)
        self.assertEqual(storage['summary']['mount_count'], 2)
        self.assertEqual([mount['mountpoint'] for mount in storage['mounts']], ['/', '/data'])

    def test_storage_snapshot_sorts_users_and_marks_failed_scans(self) -> None:
        """验证用户主目录统计按占用排序并保留无法读取状态。

        Args:
            无。

        Returns:
            无返回值。
        """
        filesystems = {
            'summary': {'total': 10_000, 'used': 4_000, 'free': 6_000, 'percent': 40.0, 'mount_count': 1},
            'mounts': [],
        }
        accounts = [
            {'username': 'alice', 'uid': 1001, 'home': '/home/alice'},
            {'username': 'bob', 'uid': 1002, 'home': '/home/bob'},
            {'username': 'carol', 'uid': 1003, 'home': '/home/carol'},
        ]
        usage_by_home = {'/home/alice': 1_000, '/home/bob': 3_000, '/home/carol': None}
        with mock.patch.object(storage_snapshot, 'collect_filesystem_usage', return_value=filesystems), \
                mock.patch.object(storage_snapshot, 'list_user_homes', return_value=accounts), \
                mock.patch.object(storage_snapshot, 'measure_directory_usage', side_effect=lambda path: usage_by_home[path]):
            snapshot = storage_snapshot.collect_storage_snapshot()

        self.assertEqual([user['username'] for user in snapshot['users']], ['bob', 'alice', 'carol'])
        self.assertEqual(snapshot['summary']['user_count'], 3)
        self.assertEqual(snapshot['summary']['scanned_user_count'], 2)
        self.assertEqual(snapshot['summary']['users_used'], 4_000)
        self.assertFalse(snapshot['users'][-1]['available'])

    def test_storage_snapshot_loader_rejects_expired_data(self) -> None:
        """验证 Agent 不会返回超过最大年龄的存储快照。

        Args:
            无。

        Returns:
            无返回值。
        """
        payload = {'timestamp': time.time() - 120, 'summary': {}, 'mounts': [], 'users': []}
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = os.path.join(directory, 'storage.json')
            with open(snapshot_path, 'w', encoding='utf-8') as handle:
                json.dump(payload, handle)
            self.assertIsNone(storage_monitor.load_storage_snapshot(snapshot_path, max_age_seconds=60))
            self.assertIsNotNone(storage_monitor.load_storage_snapshot(snapshot_path, max_age_seconds=180))


class AuthenticationTests(unittest.TestCase):
    def test_storage_api_is_authenticated_and_returns_snapshot(self) -> None:
        """验证存储快照沿用 Agent 认证且不会并入常规刷新请求。

        Args:
            无。

        Returns:
            无返回值。
        """
        snapshot = {'timestamp': time.time(), 'summary': {}, 'mounts': [], 'users': []}
        with mock.patch.object(agent_app, 'DEPLOYMENT_MODE', deployment_mode.PUBLIC_MODE), \
                mock.patch.object(agent_app, 'AGENT_TOKEN', 'agent-secret'), \
                mock.patch.object(agent_app, 'load_storage_snapshot', return_value=snapshot):
            client = agent_app.app.test_client()
            denied = client.get('/api/storage')
            allowed = client.get('/api/storage', headers={'Authorization': 'Bearer agent-secret'})

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.get_json()['data'], snapshot)

    def test_link_diagnostic_api_is_authenticated_and_redacted(self) -> None:
        """验证链路诊断 API 复用 Agent 认证且只返回安全状态。

        Args:
            无。

        Returns:
            无返回值。
        """
        diagnostic = {
            "version": 1,
            "node_id": "gpu-node-a",
            "status": "degraded",
            "classification": "school_vps_inter_network_route",
            "evidence": {"targets": {"frps-control": {"online": False}}},
        }
        with mock.patch.object(agent_app, "DEPLOYMENT_MODE", deployment_mode.PUBLIC_MODE), \
                mock.patch.object(agent_app, "AGENT_TOKEN", "agent-secret"), \
                mock.patch.object(agent_app, "load_link_diagnostic_state", return_value=diagnostic):
            client = agent_app.app.test_client()
            denied = client.get("/api/link-diagnostic")
            allowed = client.get(
                "/api/link-diagnostic",
                headers={"Authorization": "Bearer agent-secret"},
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.get_json()["data"], diagnostic)

    def test_agent_api_requires_bearer_token(self) -> None:
        """验证公网 Agent API 必须提供正确 Bearer Token。

        Args:
            无。

        Returns:
            无返回值。
        """
        original_token = agent_app.AGENT_TOKEN
        original_mode = agent_app.DEPLOYMENT_MODE
        agent_app.AGENT_TOKEN = "agent-secret"
        agent_app.DEPLOYMENT_MODE = deployment_mode.PUBLIC_MODE
        self.addCleanup(setattr, agent_app, "AGENT_TOKEN", original_token)
        self.addCleanup(setattr, agent_app, "DEPLOYMENT_MODE", original_mode)

        client = agent_app.app.test_client()
        self.assertEqual(client.get("/api/status").status_code, 401)

        payload = {"system": {}, "gpu": {"gpus": [], "summary": {}}}
        with mock.patch.object(agent_app.gpu_monitor, "get_all_info", return_value=payload):
            response = client.get("/api/status", headers={"Authorization": "Bearer agent-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], payload)

    def test_public_agent_fails_closed_without_token(self) -> None:
        """验证公网 Agent 缺少服务端 Token 时拒绝开放 API。

        Args:
            无。

        Returns:
            无返回值。
        """
        with mock.patch.object(agent_app, "DEPLOYMENT_MODE", deployment_mode.PUBLIC_MODE), \
                mock.patch.object(agent_app, "AGENT_TOKEN", ""):
            response = agent_app.app.test_client().get("/api/status")
        self.assertEqual(response.status_code, 503)

    def test_lan_agent_does_not_require_token(self) -> None:
        """验证局域网 Agent 保持原项目的免 Token 行为。

        Args:
            无。

        Returns:
            无返回值。
        """
        payload = {"system": {}, "gpu": {"gpus": [], "summary": {}}}
        with mock.patch.object(agent_app, "DEPLOYMENT_MODE", deployment_mode.LAN_MODE), \
                mock.patch.object(agent_app, "AGENT_TOKEN", "agent-secret"), \
                mock.patch.object(agent_app.gpu_monitor, "get_all_info", return_value=payload):
            response = agent_app.app.test_client().get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], payload)

    def test_dashboard_auth_and_public_config_redaction(self) -> None:
        """验证公网 Dashboard 登录和 Agent 地址隐藏逻辑。

        Args:
            无。

        Returns:
            无返回值。
        """
        original_username = dashboard.DASHBOARD_USERNAME
        original_password = dashboard.DASHBOARD_PASSWORD
        original_token = dashboard.AGENT_TOKEN
        original_mode = dashboard.DEPLOYMENT_MODE
        dashboard.DASHBOARD_USERNAME = "monitor"
        dashboard.DASHBOARD_PASSWORD = "strong-password"
        dashboard.AGENT_TOKEN = "agent-secret"
        dashboard.DEPLOYMENT_MODE = deployment_mode.PUBLIC_MODE
        self.addCleanup(setattr, dashboard, "DASHBOARD_USERNAME", original_username)
        self.addCleanup(setattr, dashboard, "DASHBOARD_PASSWORD", original_password)
        self.addCleanup(setattr, dashboard, "AGENT_TOKEN", original_token)
        self.addCleanup(setattr, dashboard, "DEPLOYMENT_MODE", original_mode)

        client = dashboard.app.test_client()
        self.assertEqual(client.get("/api/config").status_code, 401)

        response = client.get(
            "/api/config",
            headers={"Authorization": "Basic bW9uaXRvcjpzdHJvbmctcGFzc3dvcmQ="},
        )
        self.assertEqual(response.status_code, 200)
        servers = response.get_json()["servers"]
        self.assertTrue(servers)
        self.assertNotIn("url", servers[0])

        blocked = client.get(
            "/config.json",
            headers={"Authorization": "Basic bW9uaXRvcjpzdHJvbmctcGFzc3dvcmQ="},
        )
        self.assertEqual(blocked.status_code, 403)

        blocked_named_config = client.get(
            "/config.public.json",
            headers={"Authorization": "Basic bW9uaXRvcjpzdHJvbmctcGFzc3dvcmQ="},
        )
        self.assertEqual(blocked_named_config.status_code, 403)

    def test_public_dashboard_fails_closed_without_credentials(self) -> None:
        """验证公网 Dashboard 缺少凭据时返回安全错误。

        Args:
            无。

        Returns:
            无返回值。
        """
        with mock.patch.object(dashboard, "DEPLOYMENT_MODE", deployment_mode.PUBLIC_MODE), \
                mock.patch.object(dashboard, "DASHBOARD_USERNAME", ""), \
                mock.patch.object(dashboard, "DASHBOARD_PASSWORD", ""):
            response = dashboard.app.test_client().get("/api/config")
        self.assertEqual(response.status_code, 503)

    def test_lan_dashboard_is_login_free_and_returns_full_config(self) -> None:
        """验证局域网 Dashboard 免登录并返回完整节点配置。

        Args:
            无。

        Returns:
            无返回值。
        """
        config = {
            "servers": [
                {"id": "lan-node", "name": "LAN node", "url": "http://192.168.1.10:15896"}
            ]
        }
        agent_response = mock.Mock(status_code=200)
        agent_response.json.return_value = {"code": 200, "data": {}, "msg": "success"}

        with mock.patch.object(dashboard, "DEPLOYMENT_MODE", deployment_mode.LAN_MODE), \
                mock.patch.object(dashboard, "DASHBOARD_USERNAME", "public-user"), \
                mock.patch.object(dashboard, "DASHBOARD_PASSWORD", "public-password"), \
                mock.patch.object(dashboard, "AGENT_TOKEN", "public-agent-token"), \
                mock.patch.object(dashboard, "load_config", return_value=config), \
                mock.patch.object(dashboard.requests, "get", return_value=agent_response) as request_agent:
            client = dashboard.app.test_client()
            response = client.get("/api/config")
            legacy_config = client.get("/config.json")
            proxied = client.get("/api/proxy?id=lan-node")
            proxied_node = client.get("/api/nodes/lan-node/status")
            proxied_storage = client.get("/api/nodes/lan-node/storage")
            proxied_history = client.get("/api/nodes/lan-node/history?limit=25")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["deployment_mode"], deployment_mode.LAN_MODE)
        self.assertEqual(response.get_json()["servers"][0]["url"], "http://192.168.1.10:15896")
        self.assertEqual(legacy_config.status_code, 200)
        self.assertEqual(proxied.status_code, 200)
        self.assertEqual(proxied_node.status_code, 200)
        self.assertEqual(proxied_storage.status_code, 200)
        self.assertEqual(proxied_history.status_code, 200)
        self.assertIn("/api/history?limit=25", request_agent.call_args.args[0])
        self.assertEqual(request_agent.call_args.kwargs["headers"], {})
        self.assertFalse(request_agent.call_args.kwargs["verify"])


class DeploymentModeTests(unittest.TestCase):
    def test_modes_are_normalized_and_invalid_values_fail(self) -> None:
        """验证部署模式会规范化且非法值会被拒绝。

        Args:
            无。

        Returns:
            无返回值。
        """
        self.assertEqual(deployment_mode.load_deployment_mode(" LAN "), deployment_mode.LAN_MODE)
        self.assertEqual(deployment_mode.load_deployment_mode("PUBLIC"), deployment_mode.PUBLIC_MODE)
        with self.assertRaises(RuntimeError):
            deployment_mode.load_deployment_mode("internet")

    def test_unset_mode_preserves_original_lan_behavior(self) -> None:
        """验证未设置部署模式时保持原版局域网行为。

        Args:
            无。

        Returns:
            无返回值。
        """
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(deployment_mode.load_deployment_mode(), deployment_mode.LAN_MODE)

    def test_boolean_settings_are_validated(self) -> None:
        """验证布尔环境变量支持合法写法并拒绝非法值。

        Args:
            无。

        Returns:
            无返回值。
        """
        with mock.patch.dict(os.environ, {"SETTING": "yes"}):
            self.assertTrue(deployment_mode.load_boolean_setting("SETTING", False))
        with mock.patch.dict(os.environ, {"SETTING": "off"}):
            self.assertFalse(deployment_mode.load_boolean_setting("SETTING", True))
        with mock.patch.dict(os.environ, {"SETTING": "maybe"}):
            with self.assertRaises(RuntimeError):
                deployment_mode.load_boolean_setting("SETTING", True)


if __name__ == "__main__":
    unittest.main()
