"""
snapshot_cleanup.py — 磁盘空间管理与过期清理

功能:
- 强制磁盘限制检查 (总数限制 + 容量限制)
- 孤儿文件清理
- 会话级全部清理
"""

from __future__ import annotations

import logging
import os
import shutil

from .snapshot_manager import SnapshotManager

logger = logging.getLogger(__name__)


async def enforce_disk_limits(manager: SnapshotManager):
    """
    强制检查磁盘限制

    三层策略:
      1. 总数限制: 超过 MAX_SNAPSHOTS 时从旧到新清理（跳过已回退的）
      2. 容量限制: 总备份大小超过 MAX_TOTAL_SIZE 时清理
      3. 孤儿清理: 删除文件系统中孤立的快照目录
    """
    if not os.path.exists(manager._base):
        return

    # 1. 总数限制
    await _enforce_count_limit(manager)

    # 2. 容量限制
    await _enforce_size_limit(manager)

    # 3. 孤儿清理
    await _cleanup_orphans(manager)


async def _enforce_count_limit(manager: SnapshotManager):
    """总数限制: 超过 MAX_SNAPSHOTS 时从旧到新清理"""
    all_active = manager.query_all_active()

    # 按 snapshot_id 聚合
    snapshot_groups: dict[str, list] = {}
    for r in all_active:
        snapshot_groups.setdefault(r.snapshot_id, []).append(r)

    total_snapshot_points = len(snapshot_groups)
    if total_snapshot_points <= manager.MAX_SNAPSHOTS:
        return

    # 按 created_at 升序排列（最旧的在前）
    sorted_ids = sorted(snapshot_groups.keys(),
                         key=lambda sid: min(r.created_at for r in snapshot_groups[sid]))

    to_remove_count = total_snapshot_points - manager.MAX_SNAPSHOTS
    removed = 0

    for sid in sorted_ids:
        if removed >= to_remove_count:
            break
        _remove_snapshot_directory(manager, sid)
        removed += 1

    if removed > 0:
        logger.info(f"[SnapshotCleanup] Removed {removed} old snapshot points (count limit)")


async def _enforce_size_limit(manager: SnapshotManager):
    """容量限制: 总备份大小超过 MAX_TOTAL_SIZE 时清理"""
    usage = manager.get_disk_usage()
    if usage['total_size'] <= manager.MAX_TOTAL_SIZE:
        return

    all_active = manager.query_all_active()
    # 按 created_at 升序
    all_active.sort(key=lambda r: r.created_at)

    # 按 snapshot_id 聚合
    snapshot_groups: dict[str, list] = {}
    for r in all_active:
        snapshot_groups.setdefault(r.snapshot_id, []).append(r)

    sorted_ids = sorted(snapshot_groups.keys(),
                         key=lambda sid: min(r.created_at for r in snapshot_groups[sid]))

    removed = 0
    current_usage = usage['total_size']

    for sid in sorted_ids:
        if current_usage <= manager.MAX_TOTAL_SIZE:
            break
        if len(snapshot_groups) - removed <= manager.MIN_RETAIN:
            break

        group_size = sum(r.backup_size for r in snapshot_groups[sid])
        _remove_snapshot_directory(manager, sid)
        current_usage -= group_size
        removed += 1

    if removed > 0:
        logger.info(f"[SnapshotCleanup] Removed {removed} snapshots to reclaim space")


async def _cleanup_orphans(manager: SnapshotManager):
    """清理文件系统中孤立的快照目录"""
    if not os.path.exists(manager._files_dir):
        return

    # 获取所有有效的 snapshot_id（active + reverted：reverted 备份仍被 UndoEngine 依赖）
    valid_ids = set()
    all_records = manager.query_all_retained()
    for r in all_records:
        valid_ids.add(r.snapshot_id)

    # 检查 files/ 下的子目录
    for entry in os.listdir(manager._files_dir):
        entry_path = os.path.join(manager._files_dir, entry)
        if os.path.isdir(entry_path) and entry not in valid_ids:
            try:
                shutil.rmtree(entry_path, ignore_errors=True)
                logger.info(f"[SnapshotCleanup] Removed orphan directory: {entry}")
            except Exception as e:
                logger.warning(f"[SnapshotCleanup] Failed to remove orphan {entry}: {e}")


def _remove_snapshot_directory(manager: SnapshotManager, snapshot_id: str):
    """删除某个快照点的文件目录和数据库记录"""
    # 删除文件备份
    snapshot_dir = os.path.join(manager._files_dir, snapshot_id)
    if os.path.exists(snapshot_dir):
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    # 删除数据库记录
    manager._db_execute(
        "DELETE FROM file_snapshots WHERE snapshot_id = ? AND session_uuid = ?",
        (snapshot_id, manager.session_uuid)
    )


def cleanup_session_snapshots(manager: SnapshotManager):
    """
    同步清理整个会话的快照目录 (在对话删除时调用)
    """
    SnapshotManager.cleanup_session(manager.session_uuid)
