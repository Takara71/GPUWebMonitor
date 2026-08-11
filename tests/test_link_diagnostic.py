import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import link_diagnostic


class LinkDiagnosticTests(unittest.TestCase):
    """验证自适应 FRP 链路诊断的采样与故障分类。"""

    def _settings(self, role: str = "client") -> link_diagnostic.DiagnosticSettings:
        """创建使用临时状态目录的测试配置。

        Args:
            role: 需要模拟的诊断角色。

        Returns:
            不会访问真实部署路径的诊断配置。
        """
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        return link_diagnostic.DiagnosticSettings(
            role=role,
            node_id="test-node",
            primary=link_diagnostic.Target("primary", "127.0.0.1", 10001),
            secondary=link_diagnostic.Target("secondary", "127.0.0.1", 10002),
            references=(
                link_diagnostic.Target("reference-a", "127.0.0.1", 10003),
                link_diagnostic.Target("reference-b", "127.0.0.1", 10004),
            ),
            local_service="frpc-cn.service" if role == "client" else "frps.service",
            observe_service_connection=role == "client",
            normal_interval_seconds=30,
            degraded_interval_seconds=5,
            recovery_successes=3,
            timeout_seconds=1,
            trace_cooldown_seconds=600,
            heartbeat_interval_seconds=900,
            state_path=str(Path(temporary_directory.name) / "state.json"),
            events_path=str(Path(temporary_directory.name) / "events.jsonl"),
            max_event_bytes=65536,
        )

    def test_load_config_accepts_privacy_safe_example(self) -> None:
        """验证示例配置能够加载且保持确认后的自适应间隔。

        Args:
            无。

        Returns:
            无返回值。
        """
        config_path = os.path.join(ROOT_DIR, "deploy", "lab-link-diagnostic-client.json")
        settings = link_diagnostic.load_config(config_path)

        self.assertEqual(settings.role, "client")
        self.assertEqual(settings.normal_interval_seconds, 30)
        self.assertEqual(settings.degraded_interval_seconds, 5)
        self.assertEqual(settings.recovery_successes, 3)

    def test_client_classification_distinguishes_common_failures(self) -> None:
        """验证学校出口、跨网路由和 FRP 端口故障可以区分。

        Args:
            无。

        Returns:
            无返回值。
        """
        offline = link_diagnostic.ProbeOutcome("primary", False, None, "timeout")
        reference_online = link_diagnostic.ProbeOutcome("reference-a", True, 5.0, None)
        reference_offline = link_diagnostic.ProbeOutcome("reference-a", False, None, "timeout")
        secondary_online = link_diagnostic.ProbeOutcome("secondary", True, 3.0, None)
        secondary_offline = link_diagnostic.ProbeOutcome("secondary", False, None, "timeout")
        active_primary_online = link_diagnostic.ProbeOutcome(
            "active-primary", True, 2.0, None
        )

        session_failure = link_diagnostic.classify_snapshot(
            "client",
            {
                "primary": offline,
                "active-primary": active_primary_online,
                "secondary": secondary_online,
                "reference-a": reference_online,
            },
            "primary",
            "active-primary",
            "secondary",
            ["reference-a"],
            True,
            True,
        )

        port_failure = link_diagnostic.classify_snapshot(
            "client",
            {"primary": offline, "secondary": secondary_online, "reference-a": reference_online},
            "primary",
            None,
            "secondary",
            ["reference-a"],
            True,
            True,
        )
        route_failure = link_diagnostic.classify_snapshot(
            "client",
            {"primary": offline, "secondary": secondary_offline, "reference-a": reference_online},
            "primary",
            None,
            "secondary",
            ["reference-a"],
            True,
            True,
        )
        school_failure = link_diagnostic.classify_snapshot(
            "client",
            {"primary": offline, "secondary": secondary_offline, "reference-a": reference_offline},
            "primary",
            None,
            "secondary",
            ["reference-a"],
            True,
            False,
        )

        self.assertEqual(session_failure, "node_frpc_session_failure")
        self.assertEqual(port_failure, "frp_port_or_policy")
        self.assertEqual(route_failure, "school_vps_inter_network_route")
        self.assertEqual(school_failure, "school_local_network_or_gateway")

    def test_server_classification_detects_local_service_and_upstream(self) -> None:
        """验证 VPS 本机 FRP 故障与公网出口故障使用不同分类。

        Args:
            无。

        Returns:
            无返回值。
        """
        primary_online = link_diagnostic.ProbeOutcome("primary", True, 0.2, None)
        reference_offline = link_diagnostic.ProbeOutcome("reference-a", False, None, "timeout")
        service_failure = link_diagnostic.classify_snapshot(
            "server",
            {"primary": primary_online, "reference-a": reference_offline},
            "primary",
            None,
            None,
            ["reference-a"],
            False,
            None,
        )
        upstream_failure = link_diagnostic.classify_snapshot(
            "server",
            {"primary": primary_online, "reference-a": reference_offline},
            "primary",
            None,
            None,
            ["reference-a"],
            True,
            None,
        )

        self.assertEqual(service_failure, "vps_frps_service_failure")
        self.assertEqual(upstream_failure, "vps_upstream_network")

    def test_normal_client_uses_only_local_control_session_observation(self) -> None:
        """验证健康客户端只读取本机控制会话且不产生网络探测。

        Args:
            无。

        Returns:
            无返回值。
        """
        settings = self._settings("client")
        with mock.patch.object(link_diagnostic, "service_has_established_connection", return_value=True) as observe, \
                mock.patch.object(link_diagnostic, "probe_target") as probe, \
                mock.patch.object(link_diagnostic, "probe_targets") as probe_many, \
                mock.patch.object(link_diagnostic, "read_default_gateway") as read_gateway, \
                mock.patch.object(link_diagnostic, "service_is_active") as service_active:
            snapshot = link_diagnostic.collect_snapshot(settings, "healthy")

        self.assertEqual(snapshot["classification"], "healthy")
        observe.assert_called_once_with(settings.local_service, settings.primary)
        probe.assert_not_called()
        probe_many.assert_not_called()
        read_gateway.assert_not_called()
        service_active.assert_not_called()

    def test_established_connection_matches_service_socket_inode(self) -> None:
        """验证控制会话检查只接受目标端口与服务 inode 同时匹配的连接。

        Args:
            无。

        Returns:
            无返回值。
        """
        proc_tcp = (
            "sl local_address rem_address st tx_queue rx_queue retrnsmt uid "
            "timeout inode\n"
            "0: 0100007F:C350 0100007F:1F58 01 00000000:00000000 "
            "00:00000000 00000000 1000 0 424242 1\n"
        )
        target = link_diagnostic.Target("frps-control", "127.0.0.1", 8024)
        with mock.patch.object(
            link_diagnostic, "get_service_main_pid", return_value=42
        ), mock.patch.object(
            link_diagnostic, "_process_socket_inodes", return_value={424242}
        ), mock.patch.object(
            link_diagnostic.Path, "read_text", return_value=proc_tcp
        ):
            matched = link_diagnostic.service_has_established_connection(
                "frpc-cn.service", target
            )

        self.assertTrue(matched)

    def test_state_machine_uses_fast_recovery_then_returns_to_thirty_seconds(self) -> None:
        """验证故障后使用五秒检测并在连续三次成功后恢复降频。

        Args:
            无。

        Returns:
            无返回值。
        """
        settings = self._settings("client")
        failure_snapshot = {
            "classification": "school_vps_inter_network_route",
            "outcomes": {"primary": {"online": False, "latency_ms": None, "error": "timeout"}},
            "local_service_active": True,
            "gateway_reachable": True,
        }
        healthy_snapshot = {
            "classification": "healthy",
            "outcomes": {"primary": {"online": True, "latency_ms": 1.0, "error": None}},
            "local_service_active": None,
            "gateway_reachable": None,
        }

        degraded, emitted, trace_due = link_diagnostic.advance_state(
            settings,
            {},
            failure_snapshot,
            100,
        )
        recovering_one, _, _ = link_diagnostic.advance_state(
            settings,
            degraded,
            healthy_snapshot,
            105,
        )
        recovering_two, _, _ = link_diagnostic.advance_state(
            settings,
            recovering_one,
            healthy_snapshot,
            110,
        )
        recovered, _, _ = link_diagnostic.advance_state(
            settings,
            recovering_two,
            healthy_snapshot,
            115,
        )

        self.assertTrue(emitted)
        self.assertTrue(trace_due)
        self.assertEqual(degraded["current_interval_seconds"], 5)
        self.assertEqual(recovering_one["status"], "recovering")
        self.assertEqual(recovering_two["status"], "recovering")
        self.assertEqual(recovered["status"], "healthy")
        self.assertEqual(recovered["current_interval_seconds"], 30)
        self.assertEqual(recovered["last_incident"]["ended_at"], 115)

    def test_atomic_state_contains_no_configured_target_addresses(self) -> None:
        """验证 Agent 可读状态不会泄露诊断目标地址。

        Args:
            无。

        Returns:
            无返回值。
        """
        settings = self._settings("client")
        snapshot = {
            "classification": "healthy",
            "outcomes": {"primary": {"online": True, "latency_ms": 1.0, "error": None}},
            "local_service_active": None,
            "gateway_reachable": None,
        }
        state, _, _ = link_diagnostic.advance_state(settings, {}, snapshot, 100)
        link_diagnostic.write_json_atomic(settings.state_path, state)
        content = Path(settings.state_path).read_text(encoding="utf-8")

        self.assertNotIn("127.0.0.1", content)
        self.assertEqual(json.loads(content)["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
