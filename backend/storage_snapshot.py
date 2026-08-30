"""Create a privileged storage-capacity and per-user home usage snapshot."""

import argparse
import json
import os
import pwd
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

from storage_monitor import collect_filesystem_usage


def get_minimum_login_uid(login_defs_path: str = '/etc/login.defs') -> int:
    """读取系统普通登录用户的最小 UID。

    Args:
        login_defs_path: Linux ``login.defs`` 配置文件路径。

    Returns:
        配置中的 ``UID_MIN``；无法读取时返回常见默认值 ``1000``。
    """
    try:
        with open(login_defs_path, 'r', encoding='utf-8') as handle:
            for raw_line in handle:
                line = raw_line.split('#', 1)[0].strip()
                parts = line.split()
                if len(parts) >= 2 and parts[0] == 'UID_MIN':
                    return max(int(parts[1]), 1)
    except (OSError, TypeError, ValueError):
        pass
    return 1000


def list_user_homes(minimum_uid: int | None = None) -> list[dict[str, Any]]:
    """列出具有真实主目录的普通本地用户。

    Args:
        minimum_uid: 普通用户最小 UID；为空时读取 ``/etc/login.defs``。

    Returns:
        按 UID 排序的用户名、UID 与主目录列表。
    """
    uid_min = get_minimum_login_uid() if minimum_uid is None else max(int(minimum_uid), 1)
    users = []
    for account in pwd.getpwall():
        home = os.path.abspath(str(account.pw_dir or ''))
        if account.pw_uid < uid_min or account.pw_uid == 65534:
            continue
        if home == '/' or not os.path.isdir(home):
            continue
        users.append({
            'username': str(account.pw_name),
            'uid': int(account.pw_uid),
            'home': home,
        })
    return sorted(users, key=lambda item: (item['uid'], item['username']))


def measure_directory_usage(home_path: str, timeout_seconds: int = 600) -> int | None:
    """使用系统 ``du`` 统计目录实际占用的磁盘块字节数。

    Args:
        home_path: 需要统计的绝对主目录路径。
        timeout_seconds: 单个目录允许的最长统计时间。

    Returns:
        目录实际占用字节数；命令不可用、超时或统计失败时返回 ``None``。
    """
    if not os.path.isabs(home_path) or not os.path.isdir(home_path):
        return None
    du_path = shutil.which('du')
    if not du_path:
        return None
    try:
        result = subprocess.run(
            [du_path, '-s', '-B1', '--', home_path],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=max(int(timeout_seconds), 1),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return max(int(result.stdout.split(None, 1)[0]), 0)
    except (TypeError, ValueError, IndexError):
        return None


def collect_storage_snapshot() -> dict[str, Any]:
    """采集本地磁盘容量以及各普通用户主目录占用。

    Args:
        无。

    Returns:
        可供非特权 Agent 安全读取的存储快照字典。
    """
    filesystems = collect_filesystem_usage()
    total = int(filesystems['summary'].get('total') or 0)
    users = []
    for account in list_user_homes():
        used_bytes = measure_directory_usage(account['home'])
        users.append({
            **account,
            'used_bytes': used_bytes,
            'percent': round(used_bytes / total * 100, 2) if used_bytes is not None and total else None,
            'available': used_bytes is not None,
        })
    users.sort(
        key=lambda item: (
            item['used_bytes'] is not None,
            int(item['used_bytes'] or 0),
            item['username'],
        ),
        reverse=True,
    )
    timestamp = time.time()
    return {
        'version': 1,
        'timestamp': timestamp,
        'generated_at': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        'summary': {
            **filesystems['summary'],
            'user_count': len(users),
            'scanned_user_count': sum(1 for user in users if user['available']),
            'users_used': sum(int(user['used_bytes'] or 0) for user in users),
        },
        'mounts': filesystems['mounts'],
        'users': users,
        'user_usage_scope': 'home_directories',
    }


def write_snapshot(output_path: str, snapshot: dict[str, Any]) -> None:
    """把存储快照原子写入指定 JSON 文件。

    Args:
        output_path: 最终快照文件路径。
        snapshot: 需要落盘的存储快照。

    Returns:
        无返回值。
    """
    target_path = os.path.abspath(output_path)
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, mode=0o755, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix='.storage-',
        suffix='.json',
        dir=target_dir,
    )
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(snapshot, handle, ensure_ascii=False, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def main() -> None:
    """解析命令行参数并生成一次特权存储快照。

    Args:
        无。

    Returns:
        无返回值。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output',
        default='/run/gpuwebmonitor-storage/storage.json',
        help='Atomic JSON snapshot destination',
    )
    args = parser.parse_args()
    write_snapshot(args.output, collect_storage_snapshot())


if __name__ == '__main__':
    main()
