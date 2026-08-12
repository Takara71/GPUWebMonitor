import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import status_probe


class TemporaryTcpServer:
    """提供测试期间可以接受连接的临时 TCP 服务。"""

    def __init__(self) -> None:
        """创建监听套接字但暂不启动后台线程。

        Args:
            无。

        Returns:
            无返回值。
        """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.socket.settimeout(0.2)
        self.port = int(self.socket.getsockname()[1])
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        """持续接受连接，直到监听套接字被关闭。

        Args:
            无。

        Returns:
            无返回值。
        """
        while True:
            try:
                connection, _ = self.socket.accept()
                connection.close()
            except TimeoutError:
                continue
            except OSError:
                return

    def __enter__(self) -> "TemporaryTcpServer":
        """启动临时 TCP 服务并返回自身。

        Args:
            无。

        Returns:
            已经开始接受连接的临时服务实例。
        """
        self.thread.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> None:
        """关闭临时监听套接字并结束后台线程。

        Args:
            exception_type: 上下文内异常的类型。
            exception: 上下文内捕获的异常。
            traceback: 异常对应的回溯对象。

        Returns:
            无返回值。
        """
        self.socket.close()
        self.thread.join(timeout=1)


class StatusProbeTests(unittest.TestCase):
    """验证轻量状态探测、历史计算和原子输出。"""

    def test_probe_node_reports_online_and_offline(self) -> None:
        """验证可连接和被拒绝端口会产生不同状态。

        Args:
            无。

        Returns:
            无返回值。
        """
        with TemporaryTcpServer() as server:
            online_node = status_probe.NodeConfig(
                "online", "在线节点", "127.0.0.1", server.port, "/monitor/"
            )
            online_result = status_probe.probe_node(online_node, 1)
        offline_node = status_probe.NodeConfig(
            "offline", "离线节点", "127.0.0.1", server.port, "/monitor/"
        )
        offline_result = status_probe.probe_node(offline_node, 0.2)

        self.assertTrue(online_result.online)
        self.assertIsNotNone(online_result.latency_ms)
        self.assertFalse(offline_result.online)
        self.assertIn(offline_result.error, {"连接被拒绝", "连接失败"})

    def test_history_and_incidents_match_recorded_checks(self) -> None:
        """验证可用率、状态条和故障区间使用相同检查记录。

        Args:
            无。

        Returns:
            无返回值。
        """
        now = int(datetime.now().timestamp())
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                CREATE TABLE checks (
                    node_id TEXT NOT NULL,
                    checked_at INTEGER NOT NULL,
                    online INTEGER NOT NULL,
                    latency_ms REAL,
                    error TEXT,
                    PRIMARY KEY (node_id, checked_at)
                )
                """
            )
            rows = [
                ("node", now - 180, 1, 3.0, None),
                ("node", now - 120, 0, None, "连接超时"),
                ("node", now - 60, 0, None, "连接超时"),
                ("node", now, 1, 2.0, None),
            ]
            connection.executemany("INSERT INTO checks VALUES (?, ?, ?, ?, ?)", rows)

            availability = status_probe.calculate_availability(
                connection, "node", now - 300
            )
            incidents = status_probe.build_incidents(connection, "node", now - 300)
            history = status_probe.build_daily_history(
                connection, "node", 1, datetime.now()
            )
            latency = status_probe.build_latency_profile(
                connection, "node", now - 300, bucket_seconds=60
            )

        self.assertEqual(availability, 50.0)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["duration_seconds"], 120)
        self.assertEqual(history[0]["state"], "degraded")
        self.assertEqual(latency["average_ms"], 2.5)
        self.assertEqual(latency["samples"], 2)
        self.assertEqual(latency["failed_samples"], 2)
        self.assertEqual(latency["total_samples"], 4)
        self.assertEqual(latency["availability"], 50.0)
        self.assertEqual(len(latency["points"]), 4)

    def test_incident_reason_is_enriched_by_matching_client_diagnostic(self) -> None:
        """验证恢复后的客户端诊断能够替换模糊 TCP 错误。

        Args:
            无。

        Returns:
            无返回值。
        """
        incidents = [
            {
                "started_at": 1_000,
                "ended_at": 1_120,
                "duration_seconds": 120,
                "reason": "连接被拒绝",
            }
        ]
        diagnostic = {
            "last_incident": {
                "started_at": 980,
                "ended_at": 1_100,
                "classification": "school_vps_inter_network_route",
            }
        }

        enriched = status_probe.enrich_incidents(incidents, diagnostic, None)

        self.assertEqual(enriched[0]["reason"], "学校与 VPS 之间的跨网路由异常")
        self.assertEqual(enriched[0]["raw_reason"], "连接被拒绝")
        self.assertEqual(
            enriched[0]["diagnostic_classification"],
            "school_vps_inter_network_route",
        )
        self.assertEqual(incidents[0]["reason"], "连接被拒绝")

    def test_vps_diagnostic_takes_precedence_over_client_route_guess(self) -> None:
        """验证同一故障期内 VPS 明确上游异常优先于客户端推测。

        Args:
            无。

        Returns:
            无返回值。
        """
        incidents = [
            {
                "started_at": 2_000,
                "ended_at": 2_060,
                "duration_seconds": 60,
                "reason": "连接被拒绝",
            }
        ]
        node_diagnostic = {
            "last_incident": {
                "started_at": 1_990,
                "ended_at": 2_050,
                "classification": "school_vps_inter_network_route",
            }
        }
        server_diagnostic = {
            "last_incident": {
                "started_at": 2_005,
                "ended_at": 2_055,
                "classification": "vps_upstream_network",
            }
        }

        enriched = status_probe.enrich_incidents(
            incidents,
            node_diagnostic,
            server_diagnostic,
        )

        self.assertEqual(enriched[0]["reason"], "VPS 上游网络异常")
        self.assertEqual(
            enriched[0]["diagnostic_classification"],
            "vps_upstream_network",
        )

    def test_frpc_session_failure_has_specific_public_reason(self) -> None:
        """验证 FRP 控制会话丢失不会退化为模糊连接错误。

        Args:
            无。

        Returns:
            无返回值。
        """
        incidents = [
            {
                "started_at": 3_000,
                "ended_at": 3_060,
                "duration_seconds": 60,
                "reason": "连接被拒绝",
            }
        ]
        diagnostic = {
            "last_incident": {
                "started_at": 2_995,
                "ended_at": 3_055,
                "classification": "node_frpc_session_failure",
            }
        }

        enriched = status_probe.enrich_incidents(incidents, diagnostic, None)

        self.assertEqual(enriched[0]["reason"], "节点 FRP 控制会话异常")
        self.assertEqual(
            enriched[0]["diagnostic_classification"],
            "node_frpc_session_failure",
        )

    def test_incident_annotation_survives_newer_diagnostic_incident(self) -> None:
        """验证出现新故障后，旧故障的精细原因仍从数据库恢复。

        Args:
            无。

        Returns:
            无返回值。
        """
        incident_started_at = int(datetime.now().timestamp()) - 120
        with tempfile.TemporaryDirectory() as directory:
            connection = status_probe.open_database(
                str(Path(directory) / "status.db")
            )
            original = {
                "node": [
                    {
                        "started_at": incident_started_at,
                        "ended_at": incident_started_at + 60,
                        "duration_seconds": 120,
                        "reason": "学校与 VPS 之间的跨网路由异常",
                        "raw_reason": "连接被拒绝",
                        "diagnostic_classification": (
                            "school_vps_inter_network_route"
                        ),
                    }
                ]
            }
            status_probe.store_incident_annotations(connection, original, 30)
            rebuilt = [
                {
                    "started_at": incident_started_at,
                    "ended_at": incident_started_at + 60,
                    "duration_seconds": 120,
                    "reason": "连接被拒绝",
                }
            ]

            restored = status_probe.apply_incident_annotations(
                connection,
                "node",
                rebuilt,
            )
            connection.close()

        self.assertEqual(
            restored[0]["reason"],
            "学校与 VPS 之间的跨网路由异常",
        )
        self.assertEqual(
            restored[0]["diagnostic_classification"],
            "school_vps_inter_network_route",
        )

    def test_simultaneous_node_incidents_share_one_public_reason(self) -> None:
        """验证同一时刻多节点中断不会显示互相矛盾的原因。

        Args:
            无。

        Returns:
            无返回值。
        """
        incidents_by_node = {
            "node-a": [
                {
                    "started_at": 5_000,
                    "ended_at": 5_060,
                    "duration_seconds": 60,
                    "reason": "节点 FRP 控制会话异常",
                    "diagnostic_classification": "node_frpc_session_failure",
                }
            ],
            "node-b": [
                {
                    "started_at": 5_000,
                    "ended_at": 5_060,
                    "duration_seconds": 60,
                    "reason": "FRP 服务端口或端口策略异常",
                    "diagnostic_classification": "frp_port_or_policy",
                }
            ],
            "node-c": [
                {
                    "started_at": 5_060,
                    "ended_at": 5_120,
                    "duration_seconds": 60,
                    "reason": "FRP 服务端口或端口策略异常",
                    "diagnostic_classification": "frp_port_or_policy",
                }
            ],
        }

        correlated = status_probe.correlate_incident_reasons(incidents_by_node)

        reasons = {
            incidents[0]["reason"] for incidents in correlated.values()
        }
        classifications = {
            incidents[0]["diagnostic_classification"]
            for incidents in correlated.values()
        }
        self.assertEqual(reasons, {"学校侧公共网络短时中断"})
        self.assertEqual(classifications, {"school_shared_network_disruption"})
        self.assertEqual(
            correlated["node-a"][0]["correlated_nodes"],
            ["node-a", "node-b", "node-c"],
        )

    def test_latency_profile_is_weighted_and_bucketed(self) -> None:
        """验证响应时间平均值按原始样本加权且趋势按时间桶压缩。

        Args:
            无。

        Returns:
            无返回值。
        """
        now = int(datetime.now().timestamp())
        first_bucket = now - (now % 900) - 1800
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                CREATE TABLE checks (
                    node_id TEXT NOT NULL,
                    checked_at INTEGER NOT NULL,
                    online INTEGER NOT NULL,
                    latency_ms REAL,
                    error TEXT,
                    PRIMARY KEY (node_id, checked_at)
                )
                """
            )
            connection.executemany(
                "INSERT INTO checks VALUES (?, ?, ?, ?, ?)",
                [
                    ("node", first_bucket + 60, 1, 10.0, None),
                    ("node", first_bucket + 120, 1, 20.0, None),
                    ("node", first_bucket + 960, 1, 60.0, None),
                    ("node", first_bucket + 1020, 0, None, "连接超时"),
                ],
            )
            profile = status_probe.build_latency_profile(
                connection, "node", first_bucket, bucket_seconds=900
            )

        self.assertEqual(profile["average_ms"], 30.0)
        self.assertEqual(profile["samples"], 3)
        self.assertEqual(profile["failed_samples"], 1)
        self.assertEqual(profile["total_samples"], 4)
        self.assertEqual(profile["availability"], 75.0)
        self.assertEqual(profile["bucket_seconds"], 900)
        self.assertEqual(len(profile["points"]), 2)
        self.assertEqual(profile["points"][0]["latency_ms"], 15.0)
        self.assertEqual(profile["points"][0]["samples"], 2)
        self.assertEqual(profile["points"][1]["latency_ms"], 60.0)
        self.assertEqual(profile["points"][1]["failed_samples"], 1)
        self.assertEqual(profile["points"][1]["availability"], 50.0)
        self.assertEqual(profile["points"][1]["state"], "degraded")

    def test_run_probe_writes_public_document(self) -> None:
        """验证完整探测流程会创建数据库和公开 JSON。

        Args:
            无。

        Returns:
            无返回值。
        """
        with TemporaryTcpServer() as server, tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "status.db")
            output_path = str(Path(directory) / "status.json")
            config_path = Path(directory) / "nodes.json"
            config_path.write_text(
                json.dumps(
                    {
                        "database_path": database_path,
                        "output_path": output_path,
                        "nodes": [
                            {
                                "id": "node",
                                "name": "测试节点",
                                "host": "127.0.0.1",
                                "port": server.port,
                                "detail_url": "/monitor/?node=node",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            document = status_probe.run_probe(str(config_path))
            stored_document = json.loads(Path(output_path).read_text(encoding="utf-8"))

        self.assertEqual(document["summary"]["online"], 1)
        self.assertEqual(stored_document["nodes"][0]["status"], "online")
        self.assertEqual(stored_document["nodes"][0]["detail_url"], "/monitor/?node=node")
        self.assertEqual(stored_document["version"], 2)
        self.assertGreaterEqual(stored_document["nodes"][0]["latency_24h"]["samples"], 1)


if __name__ == "__main__":
    unittest.main()
