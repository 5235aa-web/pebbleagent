"""
snapshot_undo.py — 撤销回退引擎

功能:
- 加载回退操作日志
- 从 undo 快照恢复文件状态
- 恢复 SQLite 中的 revert_status
- 更新 manifest 回退历史

撤销回退流程:
  1. 加载 RevertOperation
  2. 逐文件从 undo_snapshots 或原始快照恢复
  3. 标记快照为 active
  4. 清理 undo 快照目录
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Dict, Any

from .snapshot_types import FileSnapshotRecord
from .snapshot_manager import SnapshotManager

logger = logging.getLogger(__name__)


class UndoEngine:
    """撤销回退引擎"""

    def __init__(self, manager: SnapshotManager):
        self._manager = manager

    def execute(self, revert_id: str) -> Dict[str, Any]:
        """
        执行撤销回退

        Args:
            revert_id: 要撤销的回退操作 ID

        Returns:
            { success, reverted_files, failed_files, message }
        """
        # 1. 加载回退操作日志
        log = self._manager.load_revert_log(revert_id)
        if not log:
            return {'success': False, 'reverted_files': 0, 'failed_files': 0,
                    'message': f'找不到回退操作 {revert_id}'}

        if log.get('status') != 'completed':
            return {'success': False, 'reverted_files': 0, 'failed_files': 0,
                    'message': f'回退操作未完成，当前状态: {log.get("status")}'}

        # 2. 获取被回退的所有快照记录
        records = self._manager.query_by_revert_id(revert_id)
        if not records:
            return {'success': False, 'reverted_files': 0, 'failed_files': 0,
                    'message': '找不到回退关联的快照记录'}

        # 3. 按文件去重
        seen: Dict[str, FileSnapshotRecord] = {}
        for r in records:
            if r.relative_path not in seen:
                seen[r.relative_path] = r

        # 4. 逐文件恢复
        reverted = []
        failed = []

        for rel_path, record in seen.items():
            result = self._undo_single_file(record, revert_id)
            if result['success']:
                reverted.append(rel_path)
            else:
                failed.append({'file': rel_path, 'reason': result.get('message', '未知错误')})

        # 5. 更新快照状态
        snapshot_ids = list(set(r.snapshot_id for r in records))
        self._manager.mark_as_unreverted(snapshot_ids)

        # 6. 清理 undo 快照
        self._manager.cleanup_undo_snapshots(revert_id)

        # 7. 更新 manifest 回退历史
        self._remove_from_revert_history(revert_id)

        logger.info(
            f"[UndoEngine] Unreverted {revert_id}: "
            f"{len(reverted)} restored, {len(failed)} failed"
        )

        return {
            'success': len(failed) == 0,
            'reverted_files': len(reverted),
            'failed_files': len(failed),
            'reverted': reverted,
            'failed': failed,
            'message': f'已恢复 {len(reverted)} 个文件' + 
                       (f', {len(failed)} 个失败' if failed else ''),
        }

    def _undo_single_file(self, record: FileSnapshotRecord, revert_id: str) -> Dict[str, Any]:
        """
        撤销单个文件的回退

        决策树:
          1. undo_snapshots 中有备份 → 使用 undo 快照恢复
          2. undo_snapshots 中无备份 → 说明回退时文件是原始备份直接恢复的，
             再次从备份恢复即可

        Args:
            record: 快照记录
            revert_id: 回退操作 ID

        Returns:
            { success, message }
        """
        abs_path = record.absolute_path

        # delete 操作的 undo：回退时恢复了被删除的文件，undo 应该再次删除
        if record.operation_type == 'delete':
            if os.path.isfile(abs_path):
                try:
                    os.unlink(abs_path)
                    return {'success': True, 'message': f'undo 删除文件: {record.relative_path}'}
                except Exception as e:
                    return {'success': False, 'message': str(e)}
            return {'success': True, 'message': f'文件已不存在，无需操作: {record.relative_path}'}

        # create 操作的 undo：回退时删除了新建的文件，undo 应重新创建
        if record.operation_type == 'create':
            if record.backup_path and os.path.isfile(record.backup_path):
                try:
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    shutil.copy2(record.backup_path, abs_path)
                    return {'success': True, 'message': f'undo 重建文件: {record.relative_path}'}
                except Exception as e:
                    return {'success': False, 'message': str(e)}
            return {'success': False, 'message': f'备份不存在，无法重建: {record.relative_path}'}

        # 尝试从 undo 快照恢复
        undo_path = self._manager.get_undo_snapshot(revert_id, record.relative_path)

        if undo_path and os.path.isfile(undo_path):
            # undo 快照存在 → 有文件被回退时覆盖了，现在恢复回去
            try:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                shutil.copy2(undo_path, abs_path)
                return {'success': True, 'message': f'从 undo 快照恢复: {record.relative_path}'}
            except Exception as e:
                return {'success': False, 'message': str(e)}

        # undo 快照不存在 → 直接使用原始备份
        if record.backup_path and os.path.isfile(record.backup_path):
            try:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                shutil.copy2(record.backup_path, abs_path)
                return {'success': True, 'message': f'从原始快照恢复: {record.relative_path}'}
            except Exception as e:
                return {'success': False, 'message': str(e)}

        # 新建文件的恢复：回退时删除了文件，undo 应该重新创建
        if not record.file_existed_before and not os.path.isfile(abs_path):
            return {'success': True, 'message': f'新建文件无需恢复: {record.relative_path}'}

        return {'success': False, 'message': f'无法找到可恢复的源: {record.relative_path}'}

    def _remove_from_revert_history(self, revert_id: str):
        """从 manifest 中移除回退历史记录"""
        with self._manager._manifest_lock:
            history = self._manager._manifest.get("revert_history", [])
            self._manager._manifest["revert_history"] = [
                h for h in history if h.get("revert_id") != revert_id
            ]
        self._manager._save_manifest()
