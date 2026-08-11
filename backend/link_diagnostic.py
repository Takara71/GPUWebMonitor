"""低流量、自适应的 FRP 双端链路诊断服务。"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


STATE_VERSION = 1
DEFAULT_STATE_PATH = "/var/lib/lab-link-diagnostic/public-state.json"
DEFAULT_EVENTS_PATH = "/var/lib/lab-link-diagnostic/events.jsonl"
DEFAULT_MAX_EVENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Target:
    """描述一个仅建立 TCP 连接的诊断目标。"""

    name: str
    host: str
    port: int


@dataclass(frozen=True)
class ProbeOutcome:
    """保存一次轻量 TCP 连接检测结果。"""

    name: str
    online: bool
    latency_ms: float | None
    error: str | None


@dataclass(frozen=True)
class DiagnosticSettings:
    """保存链路诊断服务经过校验的运行配置。"""

    role: str
    node_id: str
    primary: Target
    secondary: Target | None
    references: tuple[Target, ...]
    local_service: str | None
    observe_service_connection: bool
    normal_interval_seconds: float
    degraded_interval_seconds: float
    recovery_successes: int
    timeout_seconds: float
    trace_cooldown_seconds: int
    heartbeat_interval_seconds: int
    state_path: str
    events_path: str
    max_event_bytes: int


def _parse_target(raw_target: Any, field_name: str) -> Target:
    """把 JSON 字典转换为经过校验的 TCP 目标。

    Args:
        raw_target: 配置文件中的目标字典。
        field_name: 错误信息中使用的配置字段名称。

    Returns:
        经过校验的目标对象。

    Raises:
        ValueError: 目标名称、主机或端口不合法。
    """
    if not isinstance(raw_target, dict):
        raise ValueError(f"{field_name} 必须是对象")
    name = str(raw_target.get("name") or field_name).strip()
    host = str(raw_target.get("host") or "").strip()
    port = int(raw_target.get("port") or 0)
    if not name or not host or not 1 <= port <= 65535:
        raise ValueError(f"{field_name} 的名称、主机或端口不合法")
    return Target(name=name, host=host, port=port)


def load_config(config_path: str) -> DiagnosticSettings:
    """读取并校验自适应链路诊断配置。

    Args:
        config_path: JSON 配置文件路径。

    Returns:
        可以直接用于诊断循环的配置对象。

    Raises:
        ValueError: 配置角色、目标或时间参数不合法。
        OSError: 配置文件无法读取。
        json.JSONDecodeError: 配置文件不是有效 JSON。
    """
    with open(config_path, "r", encoding="utf-8") as handle:
        raw_config = json.load(handle)

    role = str(raw_config.get("role") or "").strip().lower()
    if role not in {"client", "server"}:
        raise ValueError("role 必须是 client 或 server")
    node_id = str(raw_config.get("node_id") or "").strip()
    if not node_id:
        raise ValueError("node_id 不能为空")

    primary = _parse_target(raw_config.get("primary"), "primary")
    secondary_raw = raw_config.get("secondary")
    secondary = (
        _parse_target(secondary_raw, "secondary")
        if secondary_raw is not None
        else None
    )
    raw_references = raw_config.get("references") or []
    if not isinstance(raw_references, list):
        raise ValueError("references 必须是数组")
    references = tuple(
        _parse_target(item, f"references[{index}]")
        for index, item in enumerate(raw_references)
    )
    if not references:
        raise ValueError("至少需要一个公网参考目标")

    normal_interval = max(float(raw_config.get("normal_interval_seconds", 30)), 10)
    degraded_interval = max(float(raw_config.get("degraded_interval_seconds", 5)), 2)
    recovery_successes = max(int(raw_config.get("recovery_successes", 3)), 1)
    timeout_seconds = min(max(float(raw_config.get("timeout_seconds", 2)), 0.2), 10)
    trace_cooldown = max(int(raw_config.get("trace_cooldown_seconds", 600)), 60)
    heartbeat_interval = max(int(raw_config.get("heartbeat_interval_seconds", 900)), 60)
    max_event_bytes = max(int(raw_config.get("max_event_bytes", DEFAULT_MAX_EVENT_BYTES)), 65536)

    return DiagnosticSettings(
        role=role,
        node_id=node_id,
        primary=primary,
        secondary=secondary,
        references=references,
        local_service=(str(raw_config.get("local_service") or "").strip() or None),
        observe_service_connection=bool(
            raw_config.get("observe_service_connection", role == "client")
        ),
        normal_interval_seconds=normal_interval,
        degraded_interval_seconds=degraded_interval,
        recovery_successes=recovery_successes,
        timeout_seconds=timeout_seconds,
        trace_cooldown_seconds=trace_cooldown,
        heartbeat_interval_seconds=heartbeat_interval,
        state_path=str(raw_config.get("state_path") or DEFAULT_STATE_PATH),
        events_path=str(raw_config.get("events_path") or DEFAULT_EVENTS_PATH),
        max_event_bytes=max_event_bytes,
    )


def describe_socket_error(error: OSError) -> str:
    """把底层 TCP 异常转换为稳定且不泄露地址的错误代码。

    Args:
        error: 建立 TCP 连接时捕获的异常。

    Returns:
        适合结构化日志使用的简短错误代码。
    """
    if isinstance(error, TimeoutError) or error.errno in {60, 110}:
        return "timeout"
    if error.errno in {61, 111}:
        return "refused"
    if error.errno in {51, 101, 113}:
        return "unreachable"
    return "connect_error"


def probe_target(target: Target, timeout_seconds: float) -> ProbeOutcome:
    """仅通过 TCP 建连和立即关闭检测目标，不发送应用层数据。

    Args:
        target: 需要检测的目标。
        timeout_seconds: 单次连接最大等待时间。

    Returns:
        目标是否在线、连接耗时及错误代码。
    """
    started_at = time.monotonic()
    try:
        with socket.create_connection(
            (target.host, target.port),
            timeout=timeout_seconds,
        ):
            latency_ms = round((time.monotonic() - started_at) * 1000, 1)
        return ProbeOutcome(target.name, True, latency_ms, None)
    except OSError as error:
        return ProbeOutcome(
            target.name,
            False,
            None,
            describe_socket_error(error),
        )


def probe_targets(
    targets: Sequence[Target],
    timeout_seconds: float,
) -> dict[str, ProbeOutcome]:
    """并发检测若干 TCP 目标，避免超时串行叠加。

    Args:
        targets: 需要检测的目标序列。
        timeout_seconds: 每个目标独立使用的连接超时。

    Returns:
        按目标名称索引的检测结果。
    """
    if not targets:
        return {}
    worker_count = max(1, min(len(targets), 8))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(
                lambda target: probe_target(target, timeout_seconds),
                targets,
            )
        )
    return {result.name: result for result in results}


def service_is_active(service_name: str | None) -> bool | None:
    """查询本机 systemd 服务是否处于 active 状态。

    Args:
        service_name: systemd 服务名称；为空时跳过检测。

    Returns:
        服务活动状态；没有配置服务时返回 ``None``。
    """
    if not service_name:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def get_service_main_pid(service_name: str | None) -> int | None:
    """读取 systemd 服务当前主进程 PID。

    Args:
        service_name: systemd 服务名称；为空时无法查询。

    Returns:
        大于零的主进程 PID；服务未运行或查询失败时返回 ``None``。
    """
    if not service_name:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "show", service_name, "--property=MainPID", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        main_pid = int(result.stdout.strip() or 0)
        return main_pid if result.returncode == 0 and main_pid > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _process_socket_inodes(process_id: int) -> set[int]:
    """收集指定进程持有的全部套接字 inode。

    Args:
        process_id: 需要检查的 Linux 进程 ID。

    Returns:
        进程文件描述符中出现的套接字 inode 集合。
    """
    inodes: set[int] = set()
    descriptor_directory = Path(f"/proc/{process_id}/fd")
    try:
        descriptors = list(descriptor_directory.iterdir())
    except OSError:
        return inodes
    for descriptor in descriptors:
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if not target.startswith("socket:[") or not target.endswith("]"):
            continue
        try:
            inodes.add(int(target[8:-1]))
        except ValueError:
            continue
    return inodes


def _decode_proc_ipv4(hex_address: str) -> str | None:
    """把 ``/proc/net/tcp`` 的小端十六进制地址转换为 IPv4。

    Args:
        hex_address: 八位小端十六进制 IPv4 字符串。

    Returns:
        标准点分十进制地址；格式非法时返回 ``None``。
    """
    try:
        return socket.inet_ntoa(struct.pack("<L", int(hex_address, 16)))
    except (OSError, ValueError, struct.error):
        return None


def service_has_established_connection(
    service_name: str | None,
    target: Target,
) -> bool:
    """检查指定服务是否持有连接目标的 ESTABLISHED TCP 会话。

    该检查只读取本机 ``/proc``，正常状态下不会额外消耗公网流量，也不会
    因运营商限制新建连接而误判已有 FRP 控制会话。

    Args:
        service_name: FRP 客户端 systemd 服务名称。
        target: FRP 服务端控制端口目标。

    Returns:
        找到匹配的已建立 TCP 会话时返回 ``True``。
    """
    process_id = get_service_main_pid(service_name)
    if process_id is None:
        return False
    socket_inodes = _process_socket_inodes(process_id)
    if not socket_inodes:
        return False
    try:
        target_address = socket.gethostbyname(target.host)
    except OSError:
        return False

    tcp_path = Path(f"/proc/{process_id}/net/tcp")
    try:
        lines = tcp_path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return False
    for line in lines:
        fields = line.split()
        if len(fields) < 10 or fields[3] != "01":
            continue
        try:
            remote_hex, remote_port_hex = fields[2].split(":", 1)
            remote_port = int(remote_port_hex, 16)
            inode = int(fields[9])
        except (ValueError, IndexError):
            continue
        remote_address = _decode_proc_ipv4(remote_hex)
        if (
            inode in socket_inodes
            and remote_address == target_address
            and remote_port == target.port
        ):
            return True
    return False


def read_default_gateway() -> str | None:
    """从 Linux 路由表读取 IPv4 默认网关。

    Args:
        无。

    Returns:
        默认网关 IPv4 地址；无法确定时返回 ``None``。
    """
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) < 4 or fields[1] != "00000000":
                    continue
                flags = int(fields[3], 16)
                if not flags & 0x2:
                    continue
                packed = struct.pack("<L", int(fields[2], 16))
                return socket.inet_ntoa(packed)
    except (OSError, ValueError, struct.error):
        return None
    return None


def gateway_is_reachable(gateway: str | None, timeout_seconds: float) -> bool | None:
    """使用单个 ICMP Echo 检测默认网关是否可达。

    Args:
        gateway: 默认网关地址；为空时跳过检测。
        timeout_seconds: Ping 最大等待时间。

    Returns:
        网关是否响应；无法确定网关时返回 ``None``。
    """
    if not gateway:
        return None
    ping_binary = shutil.which("ping")
    if not ping_binary:
        return None
    try:
        result = subprocess.run(
            [
                ping_binary,
                "-n",
                "-c",
                "1",
                "-W",
                str(max(1, math.ceil(timeout_seconds))),
                gateway,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(timeout_seconds + 1, 2),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def classify_snapshot(
    role: str,
    outcomes: dict[str, ProbeOutcome],
    primary_name: str,
    active_primary_name: str | None,
    secondary_name: str | None,
    reference_names: Sequence[str],
    local_service_active: bool | None,
    gateway_reachable: bool | None,
) -> str:
    """根据双端证据生成稳定的链路故障分类。

    Args:
        role: 当前诊断点角色，值为 ``client`` 或 ``server``。
        outcomes: 按名称索引的 TCP 检测结果。
        primary_name: 主链路目标名称。
        active_primary_name: 故障模式下主动探测的主端口名称。
        secondary_name: 次要服务目标名称。
        reference_names: 公网参考目标名称列表。
        local_service_active: 本地 FRP 服务活动状态。
        gateway_reachable: 默认网关可达状态。

    Returns:
        可以安全写入公开状态的故障分类代码。
    """
    primary = outcomes[primary_name]
    active_primary = outcomes.get(active_primary_name) if active_primary_name else None
    references = [outcomes[name] for name in reference_names if name in outcomes]
    any_reference_online = any(item.online for item in references)
    all_references_offline = bool(references) and not any_reference_online
    secondary = outcomes.get(secondary_name) if secondary_name else None

    if role == "server":
        if local_service_active is False:
            return "vps_frps_service_failure"
        if not primary.online:
            return "vps_frps_port_unavailable"
        if all_references_offline:
            return "vps_upstream_network"
        return "healthy"

    if local_service_active is False:
        return "node_frpc_service_failure"
    if primary.online:
        return "healthy"
    if active_primary is not None and active_primary.online:
        return "node_frpc_session_failure"
    if gateway_reachable is False and all_references_offline:
        return "school_local_network_or_gateway"
    if all_references_offline:
        return "school_outbound_or_isp"
    if secondary is not None and secondary.online:
        return "frp_port_or_policy"
    if any_reference_online:
        return "school_vps_inter_network_route"
    return "frp_link_unavailable"


def collect_snapshot(
    settings: DiagnosticSettings,
    previous_status: str,
) -> dict[str, Any]:
    """按照正常或故障模式采集一轮最小必要诊断证据。

    正常客户端只读取本机 ``/proc`` 中的 FRP 控制会话，不发送网络探测；
    故障或恢复期间才并发探测 FRP 端口、HTTPS、公网参考目标和默认网关。
    服务端正常时额外检测一个公网参考目标，以便识别 VPS 自身的上游网络
    异常。

    Args:
        settings: 诊断服务配置。
        previous_status: 上一轮状态机状态。

    Returns:
        包含检测结果、分类和安全证据的字典。
    """
    session_probe_name = "frpc-control-session"
    if settings.role == "client" and settings.observe_service_connection:
        session_online = service_has_established_connection(
            settings.local_service,
            settings.primary,
        )
        primary = ProbeOutcome(
            session_probe_name,
            session_online,
            0.0 if session_online else None,
            None if session_online else "no_established_connection",
        )
    else:
        primary = probe_target(settings.primary, settings.timeout_seconds)
    outcomes = {primary.name: primary}
    detailed_mode = not primary.online or previous_status in {"degraded", "recovering"}

    if settings.role == "server":
        first_reference = probe_target(settings.references[0], settings.timeout_seconds)
        outcomes[first_reference.name] = first_reference
        detailed_mode = detailed_mode or not first_reference.online

    extra_targets: list[Target] = []
    if detailed_mode:
        if settings.role == "client" and settings.observe_service_connection:
            extra_targets.append(settings.primary)
        if settings.secondary is not None:
            extra_targets.append(settings.secondary)
        extra_targets.extend(
            target for target in settings.references if target.name not in outcomes
        )
        outcomes.update(probe_targets(extra_targets, settings.timeout_seconds))

    gateway = read_default_gateway() if detailed_mode and settings.role == "client" else None
    gateway_reachable = (
        gateway_is_reachable(gateway, settings.timeout_seconds)
        if gateway is not None
        else None
    )
    local_service_active = (
        service_is_active(settings.local_service) if detailed_mode else None
    )
    classification = classify_snapshot(
        role=settings.role,
        outcomes=outcomes,
        primary_name=primary.name,
        active_primary_name=(
            settings.primary.name
            if settings.role == "client" and settings.observe_service_connection
            else None
        ),
        secondary_name=settings.secondary.name if settings.secondary else None,
        reference_names=[target.name for target in settings.references],
        local_service_active=local_service_active,
        gateway_reachable=gateway_reachable,
    )
    safe_outcomes = {
        name: {
            "online": outcome.online,
            "latency_ms": outcome.latency_ms,
            "error": outcome.error,
        }
        for name, outcome in outcomes.items()
    }
    return {
        "classification": classification,
        "outcomes": safe_outcomes,
        "local_service_active": local_service_active,
        "gateway_reachable": gateway_reachable,
    }


def load_state(state_path: str) -> dict[str, Any]:
    """读取上一轮公开状态，文件损坏时安全回退为空状态。

    Args:
        state_path: 状态 JSON 文件路径。

    Returns:
        上一轮状态字典；文件不可用时返回空字典。
    """
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _safe_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """从原始快照提取不包含主机地址的公开证据。

    Args:
        snapshot: 当前诊断快照。

    Returns:
        仅包含目标名称、布尔状态和错误代码的安全证据。
    """
    return {
        "targets": snapshot.get("outcomes", {}),
        "local_service_active": snapshot.get("local_service_active"),
        "gateway_reachable": snapshot.get("gateway_reachable"),
    }


def advance_state(
    settings: DiagnosticSettings,
    previous: dict[str, Any],
    snapshot: dict[str, Any],
    now_timestamp: int,
) -> tuple[dict[str, Any], bool, bool]:
    """推进降频、故障和连续恢复状态机。

    Args:
        settings: 诊断服务配置。
        previous: 上一轮持久化状态。
        snapshot: 当前诊断快照。
        now_timestamp: 当前 Unix 时间戳。

    Returns:
        ``(新状态, 是否写事件, 是否执行路由追踪)`` 元组。
    """
    previous_status = str(previous.get("status") or "unknown")
    previous_classification = str(previous.get("classification") or "unknown")
    classification = str(snapshot["classification"])
    healthy = classification == "healthy"
    consecutive_successes = int(previous.get("consecutive_successes") or 0)
    last_incident = previous.get("last_incident")
    if not isinstance(last_incident, dict):
        last_incident = None

    if healthy and previous_status in {"degraded", "recovering"}:
        consecutive_successes += 1
        if consecutive_successes >= settings.recovery_successes:
            status = "healthy"
            classification = "healthy"
            if last_incident and last_incident.get("ended_at") is None:
                last_incident["ended_at"] = now_timestamp
        else:
            status = "recovering"
            classification = "recovering"
    elif healthy:
        status = "healthy"
        classification = "healthy"
        consecutive_successes = settings.recovery_successes
    else:
        status = "degraded"
        consecutive_successes = 0
        if previous_status not in {"degraded", "recovering"}:
            last_incident = {
                "started_at": now_timestamp,
                "ended_at": None,
                "classification": classification,
                "evidence": _safe_evidence(snapshot),
            }
        elif last_incident:
            last_incident["ended_at"] = None
            last_incident["classification"] = classification
            last_incident["evidence"] = _safe_evidence(snapshot)

    last_event_at = int(previous.get("last_event_at") or 0)
    state_changed = (
        status != previous_status or classification != previous_classification
    )
    heartbeat_due = now_timestamp - last_event_at >= settings.heartbeat_interval_seconds
    emit_event = state_changed or heartbeat_due or not previous
    last_trace_at = int(previous.get("last_trace_at") or 0)
    trace_due = (
        status == "degraded"
        and previous_status != "degraded"
        and (
            last_trace_at == 0
            or now_timestamp - last_trace_at >= settings.trace_cooldown_seconds
        )
    )

    state = {
        "version": STATE_VERSION,
        "node_id": settings.node_id,
        "role": settings.role,
        "generated_at": now_timestamp,
        "status": status,
        "classification": classification,
        "consecutive_successes": consecutive_successes,
        "current_interval_seconds": (
            settings.normal_interval_seconds
            if status == "healthy"
            else settings.degraded_interval_seconds
        ),
        "normal_interval_seconds": settings.normal_interval_seconds,
        "degraded_interval_seconds": settings.degraded_interval_seconds,
        "evidence": _safe_evidence(snapshot),
        "last_incident": last_incident,
        "last_event_at": now_timestamp if emit_event else last_event_at,
        "last_trace_at": now_timestamp if trace_due else last_trace_at,
    }
    return state, emit_event, trace_due


def collect_route_trace(target: Target) -> dict[str, Any]:
    """在故障首次出现时收集一次有冷却限制的路由诊断。

    优先使用 TCP MTR，其次使用 tracepath；都不可用时至少保存内核选路。

    Args:
        target: 主链路目标。

    Returns:
        诊断工具、退出码和截断后的输出。
    """
    mtr_binary = shutil.which("mtr")
    tracepath_binary = shutil.which("tracepath")
    ip_binary = shutil.which("ip")
    if mtr_binary:
        command = [
            mtr_binary,
            "-n",
            "-r",
            "-w",
            "-c",
            "5",
            "-T",
            "-P",
            str(target.port),
            target.host,
        ]
        tool_name = "mtr-tcp"
        timeout = 25
    elif tracepath_binary:
        command = [tracepath_binary, "-n", "-p", str(target.port), target.host]
        tool_name = "tracepath"
        timeout = 20
    elif ip_binary:
        command = [ip_binary, "route", "get", target.host]
        tool_name = "ip-route"
        timeout = 5
    else:
        return {"tool": "unavailable", "returncode": None, "output": ""}

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()[:12000]
        return {
            "tool": tool_name,
            "returncode": result.returncode,
            "output": output,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "tool": tool_name,
            "returncode": None,
            "output": type(error).__name__,
        }


def write_json_atomic(path: str, document: dict[str, Any], mode: int = 0o644) -> None:
    """原子写入 JSON 状态并设置明确文件权限。

    Args:
        path: 最终状态文件路径。
        document: 需要序列化的字典。
        mode: 最终文件权限，默认允许本机 Agent 只读。

    Returns:
        无返回值。
    """
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".link-state-",
        suffix=".json",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, destination)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def append_event(
    path: str,
    event: dict[str, Any],
    max_event_bytes: int,
) -> None:
    """追加结构化事件并在文件过大时保留最近一半内容。

    Args:
        path: 私有 JSONL 事件文件路径。
        event: 需要追加的诊断事件。
        max_event_bytes: 文件允许达到的最大字节数。

    Returns:
        无返回值。
    """
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size >= max_event_bytes:
        content = destination.read_bytes()
        retained = content[len(content) // 2 :]
        first_newline = retained.find(b"\n")
        if first_newline >= 0:
            retained = retained[first_newline + 1 :]
        destination.write_bytes(retained)
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    os.chmod(destination, 0o600)


def run_iteration(settings: DiagnosticSettings) -> tuple[dict[str, Any], float]:
    """执行一轮检测、状态推进、日志与路由诊断。

    Args:
        settings: 诊断服务配置。

    Returns:
        最新公开状态以及下一轮等待秒数。
    """
    previous = load_state(settings.state_path)
    previous_status = str(previous.get("status") or "unknown")
    snapshot = collect_snapshot(settings, previous_status)
    now_timestamp = int(time.time())
    state, emit_event, trace_due = advance_state(
        settings,
        previous,
        snapshot,
        now_timestamp,
    )
    event: dict[str, Any] | None = None
    if emit_event:
        event = {
            "timestamp": now_timestamp,
            "node_id": settings.node_id,
            "role": settings.role,
            "status": state["status"],
            "classification": state["classification"],
            "interval_seconds": state["current_interval_seconds"],
            "evidence": state["evidence"],
        }
    if trace_due:
        trace = collect_route_trace(settings.primary)
        if event is None:
            event = {
                "timestamp": now_timestamp,
                "node_id": settings.node_id,
                "role": settings.role,
                "status": state["status"],
                "classification": state["classification"],
                "interval_seconds": state["current_interval_seconds"],
                "evidence": state["evidence"],
            }
        event["route_trace"] = trace
    write_json_atomic(settings.state_path, state)
    if event is not None:
        append_event(settings.events_path, event, settings.max_event_bytes)
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)
    return state, float(state["current_interval_seconds"])


def run_forever(settings: DiagnosticSettings) -> None:
    """持续运行自适应诊断循环，单轮异常时自动重试。

    Args:
        settings: 诊断服务配置。

    Returns:
        无返回值；进程由 systemd 负责生命周期管理。
    """
    while True:
        delay = settings.degraded_interval_seconds
        try:
            _, delay = run_iteration(settings)
        except Exception as error:
            event = {
                "timestamp": int(time.time()),
                "node_id": settings.node_id,
                "role": settings.role,
                "status": "internal_error",
                "classification": "diagnostic_internal_error",
                "error": type(error).__name__,
            }
            print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)
        time.sleep(max(float(delay), 1))


def main() -> None:
    """解析命令行参数并启动单次或持续诊断。

    Args:
        无。

    Returns:
        无返回值。
    """
    parser = argparse.ArgumentParser(description="运行低流量自适应 FRP 链路诊断")
    parser.add_argument("--config", required=True, help="诊断 JSON 配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    arguments = parser.parse_args()
    settings = load_config(arguments.config)
    if arguments.once:
        state, _ = run_iteration(settings)
        print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        return
    run_forever(settings)


if __name__ == "__main__":
    main()
