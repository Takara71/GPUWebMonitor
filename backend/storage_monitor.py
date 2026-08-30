"""Collect local filesystem capacity and read privileged storage snapshots."""

import json
import os
import time
from typing import Any

import psutil


STORAGE_SNAPSHOT_PATH = os.environ.get(
    'GPU_MONITOR_STORAGE_SNAPSHOT',
    '/run/gpuwebmonitor-storage/storage.json',
)
STORAGE_SNAPSHOT_MAX_AGE = int(os.environ.get('GPU_MONITOR_STORAGE_MAX_AGE', '3600'))
_IGNORED_FILESYSTEMS = {
    '9p',
    'autofs',
    'binfmt_misc',
    'cgroup',
    'cgroup2',
    'cifs',
    'configfs',
    'debugfs',
    'devpts',
    'devtmpfs',
    'efivarfs',
    'fusectl',
    'fuse.sshfs',
    'hugetlbfs',
    'mqueue',
    'nfs',
    'nfs4',
    'nsfs',
    'overlay',
    'proc',
    'pstore',
    'securityfs',
    'smbfs',
    'squashfs',
    'sysfs',
    'tmpfs',
    'tracefs',
}


def _partition_identity(device: str, mountpoint: str) -> str:
    """生成挂载分区的稳定去重标识。

    Args:
        device: psutil 返回的设备路径或设备名称。
        mountpoint: 分区挂载点。

    Returns:
        优先基于真实设备路径生成的去重字符串。
    """
    if device:
        return os.path.realpath(device) if device.startswith('/') else device
    return os.path.realpath(mountpoint)


def _is_local_partition(device: str, fstype: str, mountpoint: str) -> bool:
    """判断一个挂载项是否属于需要展示的本地持久化磁盘。

    Args:
        device: 挂载项设备路径。
        fstype: 文件系统类型。
        mountpoint: 文件系统挂载点。

    Returns:
        本地持久化磁盘返回 ``True``，虚拟或网络文件系统返回 ``False``。
    """
    normalized_type = (fstype or '').strip().lower()
    if normalized_type in _IGNORED_FILESYSTEMS:
        return False
    if normalized_type.startswith('fuse.') and normalized_type != 'fuseblk':
        return False
    if not mountpoint or not os.path.isabs(mountpoint):
        return False
    if mountpoint == '/boot' or mountpoint.startswith('/boot/'):
        return False
    if device.startswith('//') or ':' in device and not device.startswith('/dev/'):
        return False
    return True


def collect_filesystem_usage() -> dict[str, Any]:
    """采集本机各本地文件系统及合计容量。

    同一块设备被重复或绑定挂载时只统计一次，并排除内存文件系统、容器
    overlay、网络盘和其他内核虚拟文件系统。

    Args:
        无。

    Returns:
        包含 ``summary`` 与 ``mounts`` 的磁盘容量字典。
    """
    mounts = []
    seen_devices = set()
    partitions = list(psutil.disk_partitions(all=False))

    for partition in partitions:
        device = str(getattr(partition, 'device', '') or '')
        mountpoint = str(getattr(partition, 'mountpoint', '') or '')
        fstype = str(getattr(partition, 'fstype', '') or '')
        if not _is_local_partition(device, fstype, mountpoint):
            continue
        identity = _partition_identity(device, mountpoint)
        if identity in seen_devices:
            continue
        try:
            usage = psutil.disk_usage(mountpoint)
        except (OSError, PermissionError):
            continue
        total = max(int(usage.total or 0), 0)
        if total <= 0:
            continue
        used = min(max(int(usage.used or 0), 0), total)
        free = min(max(int(usage.free or 0), 0), total)
        seen_devices.add(identity)
        mounts.append({
            'device': device or identity,
            'mountpoint': mountpoint,
            'fstype': fstype or 'unknown',
            'total': total,
            'used': used,
            'free': free,
            'percent': round(used / total * 100, 1),
        })

    if not mounts:
        try:
            usage = psutil.disk_usage('/')
            total = max(int(usage.total or 0), 0)
            used = min(max(int(usage.used or 0), 0), total)
            mounts.append({
                'device': '/',
                'mountpoint': '/',
                'fstype': 'unknown',
                'total': total,
                'used': used,
                'free': min(max(int(usage.free or 0), 0), total),
                'percent': round(used / total * 100, 1) if total else 0.0,
            })
        except (OSError, PermissionError):
            pass

    mounts.sort(key=lambda item: (item['mountpoint'] != '/', item['mountpoint']))
    total = sum(item['total'] for item in mounts)
    used = sum(item['used'] for item in mounts)
    free = sum(item['free'] for item in mounts)
    return {
        'summary': {
            'total': total,
            'used': used,
            'free': free,
            'percent': round(used / total * 100, 1) if total else 0.0,
            'mount_count': len(mounts),
        },
        'mounts': mounts,
    }


def load_storage_snapshot(
    snapshot_path: str = STORAGE_SNAPSHOT_PATH,
    max_age_seconds: int = STORAGE_SNAPSHOT_MAX_AGE,
) -> dict[str, Any] | None:
    """读取仍在有效期内的特权磁盘与用户占用快照。

    Args:
        snapshot_path: 快照 JSON 文件路径。
        max_age_seconds: 允许的最大快照年龄秒数。

    Returns:
        结构有效且未过期的快照；文件不可用时返回 ``None``。
    """
    try:
        if os.path.getsize(snapshot_path) > 2 * 1024 * 1024:
            return None
        with open(snapshot_path, 'r', encoding='utf-8') as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(snapshot, dict):
        return None
    try:
        timestamp = float(snapshot.get('timestamp') or 0)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0 or time.time() - timestamp > max(max_age_seconds, 1):
        return None
    if not isinstance(snapshot.get('summary'), dict):
        return None
    if not isinstance(snapshot.get('mounts'), list) or not isinstance(snapshot.get('users'), list):
        return None
    return snapshot
