"""生成轻量级实验室节点可用性状态数据。"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence


LATENCY_TREND_BUCKET_SECONDS = 15 * 60


@dataclass(frozen=True)
class NodeConfig:
    """描述一个需要探测的计算节点。"""

    node_id: str
    name: str
    host: str
    port: int
    detail_url: str
    diagnostic_url: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    """保存一次 TCP 可用性探测结果。"""

    node_id: str
    online: bool
    latency_ms: float | None
    error: str | None
    checked_at: int


def load_config(config_path: str) -> tuple[list[NodeConfig], dict[str, Any]]:
    """读取并校验状态探测配置。

    Args:
        config_path: JSON 配置文件的绝对或相对路径。

    Returns:
        节点配置列表以及完整的全局配置字典。

    Raises:
        ValueError: 配置中没有节点、节点重复或端口不合法。
        OSError: 配置文件无法读取。
        json.JSONDecodeError: 配置文件不是有效 JSON。
    """
    with open(config_path, "r", encoding="utf-8") as handle:
        raw_config = json.load(handle)

    raw_nodes = raw_config.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("状态探测配置必须包含至少一个节点")

    nodes: list[NodeConfig] = []
    node_ids: set[str] = set()
    for raw_node in raw_nodes:
        node_id = str(raw_node.get("id", "")).strip()
        port = int(raw_node.get("port", 0))
        if not node_id or node_id in node_ids:
            raise ValueError(f"节点 ID 为空或重复：{node_id!r}")
        if not 1 <= port <= 65535:
            raise ValueError(f"节点 {node_id!r} 的端口不合法：{port}")
        node_ids.add(node_id)
        nodes.append(
            NodeConfig(
                node_id=node_id,
                name=str(raw_node.get("name") or node_id),
                host=str(raw_node.get("host") or "127.0.0.1"),
                port=port,
                detail_url=str(raw_node.get("detail_url") or "/monitor/"),
                diagnostic_url=(
                    str(raw_node.get("diagnostic_url") or "").strip() or None
                ),
            )
        )
    return nodes, raw_config


def describe_socket_error(error: OSError) -> str:
    """把底层网络异常转换为适合公开展示的简短中文说明。

    Args:
        error: TCP 连接过程中捕获的操作系统异常。

    Returns:
        不包含内部地址和端口的中文错误说明。
    """
    if isinstance(error, TimeoutError) or error.errno in {60, 110}:
        return "连接超时"
    if error.errno in {61, 111}:
        return "连接被拒绝"
    if error.errno in {51, 101, 113}:
        return "网络不可达"
    return "连接失败"


def probe_node(node: NodeConfig, timeout_seconds: float) -> ProbeResult:
    """检测节点对应的 FRP TCP 入口能否建立连接。

    Args:
        node: 目标节点的地址、端口和展示信息。
        timeout_seconds: TCP 建立连接的最大等待秒数。

    Returns:
        包含在线状态、连接耗时、错误和检测时间的结果。
    """
    started_at = time.monotonic()
    checked_at = int(time.time())
    try:
        with socket.create_connection((node.host, node.port), timeout=timeout_seconds):
            latency_ms = round((time.monotonic() - started_at) * 1000, 1)
        return ProbeResult(node.node_id, True, latency_ms, None, checked_at)
    except OSError as error:
        return ProbeResult(
            node.node_id,
            False,
            None,
            describe_socket_error(error),
            checked_at,
        )


def probe_nodes(
    nodes: Sequence[NodeConfig],
    timeout_seconds: float,
) -> list[ProbeResult]:
    """并发检测全部节点，避免离线节点叠加等待时间。

    Args:
        nodes: 需要探测的节点序列。
        timeout_seconds: 每个节点单独使用的连接超时秒数。

    Returns:
        与输入节点顺序一致的探测结果列表。
    """
    worker_count = max(1, min(len(nodes), 8))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(lambda node: probe_node(node, timeout_seconds), nodes))


def load_private_token(token_path: str | None) -> str | None:
    """从仅服务端可读文件加载 Agent Bearer Token。

    Args:
        token_path: Token 文件路径；为空时跳过诊断 API 获取。

    Returns:
        去除首尾空白后的 Token；文件不可用或为空时返回 ``None``。
    """
    if not token_path:
        return None
    try:
        token = Path(token_path).read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def fetch_node_diagnostic(
    node: NodeConfig,
    bearer_token: str | None,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """通过受保护的 Agent API 获取节点最近链路归因。

    Args:
        node: 包含诊断 API 地址的节点配置。
        bearer_token: Agent API 使用的 Bearer Token。
        timeout_seconds: 单次 HTTP 请求超时时间。

    Returns:
        Agent 返回的安全诊断状态；不可用或响应非法时返回 ``None``。
    """
    if not node.diagnostic_url or not bearer_token:
        return None
    request = urllib.request.Request(
        node.diagnostic_url,
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload_bytes = response.read(65537)
        if len(payload_bytes) > 65536:
            return None
        payload = json.loads(payload_bytes.decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else None
    except (
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ):
        return None


def fetch_node_diagnostics(
    nodes: Sequence[NodeConfig],
    bearer_token: str | None,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    """并发获取所有当前可达节点的链路诊断状态。

    Args:
        nodes: 需要获取诊断状态的节点序列。
        bearer_token: Agent API 使用的 Bearer Token。
        timeout_seconds: 每个节点独立使用的请求超时。

    Returns:
        按节点 ID 索引的有效诊断状态。
    """
    if not bearer_token:
        return {}
    worker_count = max(1, min(len(nodes), 8))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        states = list(
            executor.map(
                lambda node: fetch_node_diagnostic(
                    node,
                    bearer_token,
                    timeout_seconds,
                ),
                nodes,
            )
        )
    return {
        node.node_id: state
        for node, state in zip(nodes, states)
        if state is not None
    }


def open_database(database_path: str) -> sqlite3.Connection:
    """打开状态历史数据库并确保表结构存在。

    Args:
        database_path: SQLite 数据库文件路径。

    Returns:
        已设置行工厂并初始化表结构的数据库连接。
    """
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA cache_size=-1000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
            node_id TEXT NOT NULL,
            checked_at INTEGER NOT NULL,
            online INTEGER NOT NULL,
            latency_ms REAL,
            error TEXT,
            PRIMARY KEY (node_id, checked_at)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_checks_time ON checks (checked_at)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_annotations (
            node_id TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            reason TEXT NOT NULL,
            classification TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'node',
            correlated_node_ids TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (node_id, started_at)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_incident_annotations_time
        ON incident_annotations (started_at)
        """
    )
    connection.commit()
    return connection


def record_results(
    connection: sqlite3.Connection,
    results: Sequence[ProbeResult],
    retention_days: int,
) -> None:
    """写入最新探测结果并删除保留期外的数据。

    Args:
        connection: 已初始化的状态历史数据库连接。
        results: 本轮全部节点的探测结果。
        retention_days: 历史检查记录的保留天数。

    Returns:
        无返回值。
    """
    connection.executemany(
        """
        INSERT OR REPLACE INTO checks
            (node_id, checked_at, online, latency_ms, error)
        VALUES
            (:node_id, :checked_at, :online, :latency_ms, :error)
        """,
        [asdict(result) for result in results],
    )
    cutoff = int(time.time()) - max(retention_days, 1) * 86400
    connection.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
    connection.commit()


def calculate_availability(
    connection: sqlite3.Connection,
    node_id: str,
    since_timestamp: int,
) -> float | None:
    """计算节点在指定时间之后的检测可用率。

    Args:
        connection: 状态历史数据库连接。
        node_id: 需要统计的节点 ID。
        since_timestamp: 纳入统计的最早 Unix 时间戳。

    Returns:
        百分比形式的可用率；没有样本时返回 ``None``。
    """
    row = connection.execute(
        """
        SELECT COUNT(*) AS total, COALESCE(SUM(online), 0) AS online_count
        FROM checks
        WHERE node_id = ? AND checked_at >= ?
        """,
        (node_id, since_timestamp),
    ).fetchone()
    if row is None or int(row["total"]) == 0:
        return None
    return round(int(row["online_count"]) / int(row["total"]) * 100, 2)


def build_latency_profile(
    connection: sqlite3.Connection,
    node_id: str,
    since_timestamp: int,
    bucket_seconds: int = LATENCY_TREND_BUCKET_SECONDS,
) -> dict[str, Any]:
    """计算节点响应时间平均值并生成轻量趋势采样。

    仅统计成功建立连接且拥有延迟值的探测。趋势按固定时间桶聚合，避免把
    最近 24 小时的每分钟原始记录全部发送给浏览器。

    Args:
        connection: 状态历史数据库连接。
        node_id: 需要统计的节点 ID。
        since_timestamp: 纳入统计的最早 Unix 时间戳。
        bucket_seconds: 趋势聚合桶宽度，默认十五分钟。

    Returns:
        包含平均响应时间、样本数、桶宽度和趋势点的字典。
    """
    normalized_bucket_seconds = max(int(bucket_seconds), 60)
    rows = connection.execute(
        """
        SELECT
            CAST(checked_at / ? AS INTEGER) * ? AS bucket_start,
            AVG(latency_ms) AS average_latency,
            COUNT(*) AS sample_count
        FROM checks
        WHERE
            node_id = ?
            AND checked_at >= ?
            AND online = 1
            AND latency_ms IS NOT NULL
        GROUP BY bucket_start
        ORDER BY bucket_start ASC
        """,
        (
            normalized_bucket_seconds,
            normalized_bucket_seconds,
            node_id,
            since_timestamp,
        ),
    ).fetchall()

    points: list[dict[str, Any]] = []
    weighted_latency = 0.0
    total_samples = 0
    for row in rows:
        average_latency = float(row["average_latency"])
        sample_count = int(row["sample_count"])
        points.append(
            {
                "timestamp": int(row["bucket_start"]),
                "latency_ms": round(average_latency, 1),
                "samples": sample_count,
            }
        )
        weighted_latency += average_latency * sample_count
        total_samples += sample_count

    return {
        "average_ms": (
            round(weighted_latency / total_samples, 1) if total_samples else None
        ),
        "samples": total_samples,
        "bucket_seconds": normalized_bucket_seconds,
        "points": points,
    }


def build_daily_history(
    connection: sqlite3.Connection,
    node_id: str,
    days: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """构建最近若干天的状态条数据。

    Args:
        connection: 状态历史数据库连接。
        node_id: 需要统计的节点 ID。
        days: 返回的自然日数量。
        now: 用于确定“今天”的当前本地时间。

    Returns:
        按日期从旧到新排列的可用率和状态列表。
    """
    first_day = now.date() - timedelta(days=max(days, 1) - 1)
    first_timestamp = int(datetime.combine(first_day, datetime.min.time()).timestamp())
    rows = connection.execute(
        """
        SELECT checked_at, online
        FROM checks
        WHERE node_id = ? AND checked_at >= ?
        ORDER BY checked_at ASC
        """,
        (node_id, first_timestamp),
    ).fetchall()

    daily_samples: dict[str, list[int]] = {}
    for row in rows:
        date_key = datetime.fromtimestamp(int(row["checked_at"])).date().isoformat()
        daily_samples.setdefault(date_key, []).append(int(row["online"]))

    history: list[dict[str, Any]] = []
    for offset in range(max(days, 1)):
        date_value = first_day + timedelta(days=offset)
        date_key = date_value.isoformat()
        samples = daily_samples.get(date_key, [])
        availability = (
            round(sum(samples) / len(samples) * 100, 2) if samples else None
        )
        if availability is None:
            state = "unknown"
        elif availability >= 99.9:
            state = "online"
        elif availability <= 0:
            state = "offline"
        else:
            state = "degraded"
        history.append(
            {
                "date": date_key,
                "state": state,
                "availability": availability,
                "checks": len(samples),
            }
        )
    return history


def build_incidents(
    connection: sqlite3.Connection,
    node_id: str,
    since_timestamp: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """根据在线状态变化生成最近的故障区间。

    Args:
        connection: 状态历史数据库连接。
        node_id: 需要分析的节点 ID。
        since_timestamp: 故障扫描的最早 Unix 时间戳。
        limit: 最多返回的近期故障数量。

    Returns:
        按开始时间从新到旧排列的故障记录。
    """
    rows = connection.execute(
        """
        SELECT checked_at, online, error
        FROM checks
        WHERE node_id = ? AND checked_at >= ?
        ORDER BY checked_at ASC
        """,
        (node_id, since_timestamp),
    ).fetchall()
    incidents: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in rows:
        online = bool(row["online"])
        checked_at = int(row["checked_at"])
        if not online and current is None:
            current = {
                "started_at": checked_at,
                "ended_at": None,
                "duration_seconds": None,
                "reason": row["error"] or "连接失败",
            }
        elif online and current is not None:
            current["ended_at"] = checked_at
            current["duration_seconds"] = checked_at - int(current["started_at"])
            incidents.append(current)
            current = None
    if current is not None:
        current["duration_seconds"] = int(time.time()) - int(current["started_at"])
        incidents.append(current)
    return list(reversed(incidents[-max(limit, 1) :]))


DIAGNOSTIC_REASON_LABELS = {
    "node_frpc_service_failure": "节点 FRP 客户端服务异常",
    "node_frpc_session_failure": "节点 FRP 控制会话异常",
    "school_local_network_or_gateway": "学校本地网络或默认网关异常",
    "school_shared_network_disruption": "学校侧公共网络短时中断",
    "school_outbound_or_isp": "学校公网出口或运营商异常",
    "frp_port_or_policy": "FRP 服务端口或端口策略异常",
    "frp_control_plane_degraded": "FRP 服务端控制链路异常",
    "school_vps_inter_network_route": "学校与 VPS 之间的跨网路由异常",
    "frp_link_unavailable": "FRP 公网链路异常",
    "vps_frps_service_failure": "VPS 的 FRP 服务异常",
    "vps_frps_port_unavailable": "VPS 的 FRP 监听端口异常",
    "vps_upstream_network": "VPS 上游网络异常",
}


def load_local_diagnostic_state(state_path: str | None) -> dict[str, Any] | None:
    """读取国内 VPS 本机生成的安全诊断状态。

    Args:
        state_path: VPS 诊断公开状态文件路径。

    Returns:
        有效状态字典；文件缺失、过大或解析失败时返回 ``None``。
    """
    if not state_path:
        return None
    try:
        path = Path(state_path)
        if path.stat().st_size > 65536:
            return None
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _incident_matches_diagnostic(
    incident: dict[str, Any],
    diagnostic_incident: dict[str, Any],
    tolerance_seconds: int = 90,
) -> bool:
    """判断状态页故障与客户端诊断故障是否属于同一时间区间。

    Args:
        incident: 状态探针根据每分钟样本生成的故障区间。
        diagnostic_incident: 自适应诊断服务记录的故障区间。
        tolerance_seconds: 两种采样频率之间允许的时间偏差。

    Returns:
        两个区间在容差范围内重叠时返回 ``True``。
    """
    try:
        incident_start = int(incident["started_at"])
        diagnostic_start = int(diagnostic_incident["started_at"])
    except (KeyError, TypeError, ValueError):
        return False
    now_timestamp = int(time.time())
    incident_end = int(incident.get("ended_at") or now_timestamp)
    diagnostic_end = int(diagnostic_incident.get("ended_at") or now_timestamp)
    return (
        diagnostic_start <= incident_end + tolerance_seconds
        and diagnostic_end >= incident_start - tolerance_seconds
    )


def enrich_incidents(
    incidents: Sequence[dict[str, Any]],
    node_diagnostic: dict[str, Any] | None,
    server_diagnostic: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """使用双端自适应诊断为故障记录补充更精确的原因。

    VPS 本机明确检测到的 FRP 服务或上游故障优先级最高；否则使用对应
    GPU 节点恢复后上报的最近故障分类。原始 TCP 错误会保存在
    ``raw_reason`` 字段中，便于管理员复核。

    Args:
        incidents: 状态数据库生成的故障记录。
        node_diagnostic: GPU 节点最新安全诊断状态。
        server_diagnostic: 国内 VPS 最新安全诊断状态。

    Returns:
        不修改输入对象的增强故障记录列表。
    """
    node_incident = (
        node_diagnostic.get("last_incident")
        if isinstance(node_diagnostic, dict)
        else None
    )
    server_incident = (
        server_diagnostic.get("last_incident")
        if isinstance(server_diagnostic, dict)
        else None
    )
    enriched: list[dict[str, Any]] = []
    for incident in incidents:
        item = dict(incident)
        candidates = [
            ("server", server_incident),
            ("node", node_incident),
        ]
        for source, candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if not _incident_matches_diagnostic(item, candidate):
                continue
            classification = str(candidate.get("classification") or "")
            reason = DIAGNOSTIC_REASON_LABELS.get(classification)
            if not reason:
                continue
            item["raw_reason"] = item.get("reason")
            item["reason"] = reason
            item["diagnostic_classification"] = classification
            item["diagnostic_source"] = source
            break
        enriched.append(item)
    return enriched


COMMON_INCIDENT_CLASSIFICATION_PRIORITY = {
    "vps_frps_service_failure": 100,
    "vps_frps_port_unavailable": 95,
    "vps_upstream_network": 90,
    "frp_port_or_policy": 80,
    "frp_control_plane_degraded": 80,
    "frp_link_unavailable": 70,
    "school_vps_inter_network_route": 60,
    "school_outbound_or_isp": 50,
    "school_shared_network_disruption": 50,
}


def apply_incident_annotations(
    connection: sqlite3.Connection,
    node_id: str,
    incidents: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """恢复数据库中已经确认过的故障归因，避免旧记录退化为 TCP 错误。

    本轮诊断结果优先于历史注释；只有当前故障尚未获得精细分类时，才使用
    相同节点和开始时间对应的持久化结果。

    Args:
        connection: 状态历史数据库连接。
        node_id: 故障记录所属节点 ID。
        incidents: 已由当前诊断状态增强的近期故障记录。

    Returns:
        恢复历史精细原因后的新故障记录列表。
    """
    restored: list[dict[str, Any]] = []
    for incident in incidents:
        item = dict(incident)
        if item.get("diagnostic_classification"):
            restored.append(item)
            continue
        try:
            started_at = int(item["started_at"])
        except (KeyError, TypeError, ValueError):
            restored.append(item)
            continue
        row = connection.execute(
            """
            SELECT reason, classification, scope, correlated_node_ids
            FROM incident_annotations
            WHERE node_id = ? AND started_at = ?
            """,
            (node_id, started_at),
        ).fetchone()
        if row is None:
            restored.append(item)
            continue
        item["raw_reason"] = item.get("reason")
        item["reason"] = str(row["reason"])
        item["diagnostic_classification"] = str(row["classification"])
        item["diagnostic_scope"] = str(row["scope"])
        try:
            correlated_nodes = json.loads(row["correlated_node_ids"] or "[]")
        except (json.JSONDecodeError, TypeError):
            correlated_nodes = []
        if isinstance(correlated_nodes, list) and correlated_nodes:
            item["correlated_nodes"] = [str(value) for value in correlated_nodes]
        restored.append(item)
    return restored


def _incidents_are_simultaneous(
    first: dict[str, Any],
    second: dict[str, Any],
    tolerance_seconds: int,
) -> bool:
    """判断两个节点故障是否在同一采样窗口内开始且时间区间重叠。

    Args:
        first: 第一条节点故障记录。
        second: 第二条节点故障记录。
        tolerance_seconds: 允许的开始时间偏差秒数。

    Returns:
        两次故障可以视为同一公共事件时返回 ``True``。
    """
    try:
        first_start = int(first["started_at"])
        second_start = int(second["started_at"])
    except (KeyError, TypeError, ValueError):
        return False
    if abs(first_start - second_start) > max(tolerance_seconds, 0):
        return False
    now_timestamp = int(time.time())
    first_end = int(first.get("ended_at") or now_timestamp)
    second_end = int(second.get("ended_at") or now_timestamp)
    return first_start <= second_end and second_start <= first_end


def _select_shared_classification(
    group: Sequence[tuple[str, dict[str, Any]]],
) -> str:
    """从同一公共故障的多节点证据中选择统一且可解释的分类。

    节点自身的 FRP 会话异常不能单独解释多节点同时断开，因此优先采用能够
    解释公共中断的服务端、端口、跨网路由或运营商分类。若只有节点级判断，
    则保守归类为 FRP 公网链路异常，避免给出互相矛盾的结论。

    Args:
        group: 由节点 ID 与故障记录组成的公共事件组。

    Returns:
        适用于整个事件组的统一诊断分类。
    """
    server_counts: dict[str, int] = {}
    for _, incident in group:
        classification = str(incident.get("diagnostic_classification") or "")
        if (
            incident.get("diagnostic_source") == "server"
            and classification.startswith("vps_")
        ):
            server_counts[classification] = server_counts.get(classification, 0) + 1
    if not server_counts:
        return "school_shared_network_disruption"
    return max(
        server_counts,
        key=lambda value: (
            server_counts[value],
            COMMON_INCIDENT_CLASSIFICATION_PRIORITY[value],
        ),
    )


def correlate_incident_reasons(
    incidents_by_node: dict[str, list[dict[str, Any]]],
    tolerance_seconds: int = 90,
) -> dict[str, list[dict[str, Any]]]:
    """关联同一时段的多节点中断，并为公共事件统一故障原因。

    Args:
        incidents_by_node: 按节点 ID 索引的近期故障记录。
        tolerance_seconds: 不同节点采样时间允许的最大偏差。

    Returns:
        保留原有顺序、但公共事件原因已经统一的新字典。
    """
    correlated = {
        node_id: [dict(incident) for incident in incidents]
        for node_id, incidents in incidents_by_node.items()
    }
    entries = [
        (node_id, incident)
        for node_id, incidents in correlated.items()
        for incident in incidents
    ]
    entries.sort(key=lambda value: int(value[1].get("started_at") or 0))
    consumed: set[tuple[str, int]] = set()
    for node_id, incident in entries:
        try:
            started_at = int(incident["started_at"])
        except (KeyError, TypeError, ValueError):
            continue
        identity = (node_id, started_at)
        if identity in consumed:
            continue
        group = [
            (candidate_node_id, candidate)
            for candidate_node_id, candidate in entries
            if candidate_node_id != node_id
            and _incidents_are_simultaneous(
                incident,
                candidate,
                tolerance_seconds,
            )
        ]
        group.append((node_id, incident))
        unique_node_ids = sorted({candidate_node_id for candidate_node_id, _ in group})
        if len(unique_node_ids) < 2:
            continue
        classification = _select_shared_classification(group)
        reason = DIAGNOSTIC_REASON_LABELS[classification]
        for candidate_node_id, candidate in group:
            candidate_start = int(candidate.get("started_at") or 0)
            consumed.add((candidate_node_id, candidate_start))
            candidate.setdefault("raw_reason", candidate.get("reason"))
            candidate["reason"] = reason
            candidate["diagnostic_classification"] = classification
            candidate["diagnostic_scope"] = "shared"
            candidate["correlated_nodes"] = unique_node_ids
    return correlated


def store_incident_annotations(
    connection: sqlite3.Connection,
    incidents_by_node: dict[str, list[dict[str, Any]]],
    retention_days: int,
) -> None:
    """持久化已确认的精细故障原因，并清理保留期外的注释。

    Args:
        connection: 状态历史数据库连接。
        incidents_by_node: 按节点 ID 索引的已增强故障记录。
        retention_days: 注释与状态样本共同使用的保留天数。

    Returns:
        无返回值。
    """
    updated_at = int(time.time())
    rows: list[dict[str, Any]] = []
    for node_id, incidents in incidents_by_node.items():
        for incident in incidents:
            classification = str(incident.get("diagnostic_classification") or "")
            reason = str(incident.get("reason") or "")
            if not classification or not reason:
                continue
            rows.append(
                {
                    "node_id": node_id,
                    "started_at": int(incident["started_at"]),
                    "ended_at": incident.get("ended_at"),
                    "reason": reason,
                    "classification": classification,
                    "scope": str(incident.get("diagnostic_scope") or "node"),
                    "correlated_node_ids": json.dumps(
                        incident.get("correlated_nodes") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "updated_at": updated_at,
                }
            )
    if rows:
        connection.executemany(
            """
            INSERT INTO incident_annotations (
                node_id, started_at, ended_at, reason, classification,
                scope, correlated_node_ids, updated_at
            ) VALUES (
                :node_id, :started_at, :ended_at, :reason, :classification,
                :scope, :correlated_node_ids, :updated_at
            )
            ON CONFLICT(node_id, started_at) DO UPDATE SET
                ended_at = excluded.ended_at,
                reason = excluded.reason,
                classification = excluded.classification,
                scope = excluded.scope,
                correlated_node_ids = excluded.correlated_node_ids,
                updated_at = excluded.updated_at
            """,
            rows,
        )
    cutoff = updated_at - max(retention_days, 1) * 86400
    connection.execute(
        "DELETE FROM incident_annotations WHERE started_at < ?",
        (cutoff,),
    )
    connection.commit()


def build_status_document(
    connection: sqlite3.Connection,
    nodes: Sequence[NodeConfig],
    results: Sequence[ProbeResult],
    retention_days: int,
    history_days: int,
    check_interval_seconds: int,
    node_diagnostics: dict[str, dict[str, Any]] | None = None,
    server_diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总当前状态、可用率、状态条和故障记录。

    Args:
        connection: 状态历史数据库连接。
        nodes: 全部节点展示配置。
        results: 本轮探测结果。
        retention_days: 可用率和故障记录的统计天数。
        history_days: 状态条展示的自然日数量。
        check_interval_seconds: systemd 定时探测间隔秒数。
        node_diagnostics: 按节点 ID 索引的安全链路诊断状态。
        server_diagnostic: 国内 VPS 本机的安全链路诊断状态。

    Returns:
        可以直接序列化为前端状态 JSON 的字典。
    """
    now = datetime.now().astimezone()
    since_24h = int(time.time()) - 86400
    since_retention = int(time.time()) - max(retention_days, 1) * 86400
    result_by_id = {result.node_id: result for result in results}
    node_diagnostics = node_diagnostics or {}
    node_documents: list[dict[str, Any]] = []
    availability_values: list[float] = []

    incidents_by_node: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        incidents = enrich_incidents(
            build_incidents(connection, node.node_id, since_retention),
            node_diagnostics.get(node.node_id),
            server_diagnostic,
        )
        incidents_by_node[node.node_id] = apply_incident_annotations(
            connection,
            node.node_id,
            incidents,
        )
    incidents_by_node = correlate_incident_reasons(incidents_by_node)
    store_incident_annotations(connection, incidents_by_node, retention_days)

    for node in nodes:
        result = result_by_id[node.node_id]
        availability_24h = calculate_availability(
            connection, node.node_id, since_24h
        )
        availability_retention = calculate_availability(
            connection, node.node_id, since_retention
        )
        if availability_retention is not None:
            availability_values.append(availability_retention)
        node_documents.append(
            {
                "id": node.node_id,
                "name": node.name,
                "online": result.online,
                "status": "online" if result.online else "offline",
                "latency_ms": result.latency_ms,
                "error": result.error,
                "checked_at": result.checked_at,
                "availability_24h": availability_24h,
                "availability_30d": availability_retention,
                "latency_24h": build_latency_profile(
                    connection, node.node_id, since_24h
                ),
                "history": build_daily_history(
                    connection, node.node_id, history_days, now
                ),
                "incidents": incidents_by_node[node.node_id],
                "detail_url": node.detail_url,
            }
        )

    online_count = sum(1 for result in results if result.online)
    average_availability = (
        round(sum(availability_values) / len(availability_values), 2)
        if availability_values
        else None
    )
    return {
        "version": 2,
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_at_unix": int(now.timestamp()),
        "check_interval_seconds": check_interval_seconds,
        "summary": {
            "total": len(nodes),
            "online": online_count,
            "offline": len(nodes) - online_count,
            "availability_30d": average_availability,
        },
        "nodes": node_documents,
    }


def write_json_atomic(output_path: str, document: dict[str, Any]) -> None:
    """把状态文档原子写入目标 JSON 文件。

    Args:
        output_path: Nginx读取的最终 JSON 文件路径。
        document: 需要序列化的状态文档。

    Returns:
        无返回值。
    """
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=".status-",
        suffix=".json",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, destination)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def run_probe(config_path: str) -> dict[str, Any]:
    """执行一轮完整探测、历史入库和公开状态生成。

    Args:
        config_path: 状态探测 JSON 配置文件路径。

    Returns:
        本轮生成的公开状态文档。
    """
    nodes, config = load_config(config_path)
    timeout_seconds = max(float(config.get("timeout_seconds", 2.5)), 0.1)
    retention_days = max(int(config.get("retention_days", 30)), 1)
    history_days = max(int(config.get("history_days", 30)), 1)
    check_interval_seconds = max(int(config.get("check_interval_seconds", 60)), 10)
    database_path = str(config.get("database_path", "/var/lib/lab-status/status.db"))
    output_path = str(config.get("output_path", "/var/lib/lab-status/status.json"))
    diagnostic_token = load_private_token(
        str(config.get("diagnostic_token_file") or "").strip() or None
    )
    diagnostic_timeout_seconds = min(
        max(float(config.get("diagnostic_timeout_seconds", 2)), 0.2),
        10,
    )
    node_diagnostics = fetch_node_diagnostics(
        nodes,
        diagnostic_token,
        diagnostic_timeout_seconds,
    )
    server_diagnostic = load_local_diagnostic_state(
        str(config.get("server_diagnostic_state_path") or "").strip() or None
    )

    results = probe_nodes(nodes, timeout_seconds)
    connection = open_database(database_path)
    try:
        record_results(connection, results, retention_days)
        document = build_status_document(
            connection,
            nodes,
            results,
            retention_days,
            history_days,
            check_interval_seconds,
            node_diagnostics,
            server_diagnostic,
        )
    finally:
        connection.close()
    write_json_atomic(output_path, document)
    return document


def main() -> None:
    """解析命令行参数并执行一轮状态探测。

    Args:
        无。

    Returns:
        无返回值。
    """
    parser = argparse.ArgumentParser(description="生成实验室节点可用性状态数据")
    parser.add_argument(
        "--config",
        default="/etc/lab-status/nodes.json",
        help="状态探测配置文件路径",
    )
    arguments = parser.parse_args()
    document = run_probe(arguments.config)
    online = int(document["summary"]["online"])
    total = int(document["summary"]["total"])
    print(f"状态探测完成：{online}/{total} 个节点在线")


if __name__ == "__main__":
    main()
