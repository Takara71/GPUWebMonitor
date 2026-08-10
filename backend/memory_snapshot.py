"""Collect privileged per-process memory counters for the unprivileged Agent.

Linux protects ``/proc/<pid>/smaps_rollup`` across user boundaries.  This
small, network-free helper is intended to run as a hardened root systemd
oneshot service.  It writes only PID, process start time, and memory counters
to an atomically replaced JSON file under ``/run``.
"""

import argparse
import json
import os
import tempfile
import time
from typing import Any

import psutil


def collect_snapshot() -> dict[str, Any]:
    """采集全部可访问进程的 PSS、USS 与 RSS 内存数据。

    Args:
        无。

    Returns:
        包含采集时间和按 PID 索引的进程内存快照。
    """
    processes = {}
    for process in psutil.process_iter(attrs=['pid', 'create_time'], ad_value=None):
        try:
            info = process.info
            pid = int(info['pid'])
            full = process.memory_full_info()
            processes[str(pid)] = {
                'create_time': float(info.get('create_time') or 0),
                'pss': max(int(getattr(full, 'pss', 0) or 0), 0),
                'uss': max(int(getattr(full, 'uss', 0) or 0), 0),
                'rss': max(int(getattr(full, 'rss', 0) or 0), 0),
            }
        except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied, KeyError, TypeError, ValueError):
            continue

    return {
        'version': 1,
        'timestamp': time.time(),
        'processes': processes,
    }


def write_snapshot(output_path: str, snapshot: dict[str, Any]) -> None:
    """把内存快照安全地原子替换到目标路径。

    Args:
        output_path: 最终 JSON 快照文件路径。
        snapshot: 需要写入的进程内存快照。

    Returns:
        无返回值。
    """
    output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, mode=0o755, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix='.process-memory-', suffix='.json', dir=output_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(snapshot, handle, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, output_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def main() -> None:
    """解析输出路径参数并生成一次特权内存快照。

    Args:
        无。

    Returns:
        无返回值。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output',
        default='/run/gpuwebmonitor-memory/process-memory.json',
        help='Atomic JSON snapshot destination',
    )
    args = parser.parse_args()
    write_snapshot(args.output, collect_snapshot())


if __name__ == '__main__':
    main()
