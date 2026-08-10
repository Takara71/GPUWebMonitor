"""生成轻量级实验室节点可用性状态数据。"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import tempfile
import time
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


def build_status_document(
    connection: sqlite3.Connection,
    nodes: Sequence[NodeConfig],
    results: Sequence[ProbeResult],
    retention_days: int,
    history_days: int,
    check_interval_seconds: int,
) -> dict[str, Any]:
    """汇总当前状态、可用率、状态条和故障记录。

    Args:
        connection: 状态历史数据库连接。
        nodes: 全部节点展示配置。
        results: 本轮探测结果。
        retention_days: 可用率和故障记录的统计天数。
        history_days: 状态条展示的自然日数量。
        check_interval_seconds: systemd 定时探测间隔秒数。

    Returns:
        可以直接序列化为前端状态 JSON 的字典。
    """
    now = datetime.now().astimezone()
    since_24h = int(time.time()) - 86400
    since_retention = int(time.time()) - max(retention_days, 1) * 86400
    result_by_id = {result.node_id: result for result in results}
    node_documents: list[dict[str, Any]] = []
    availability_values: list[float] = []

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
                "incidents": build_incidents(
                    connection, node.node_id, since_retention
                ),
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
