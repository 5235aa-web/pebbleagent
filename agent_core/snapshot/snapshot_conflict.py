"""
snapshot_conflict.py — 三阶段冲突检测引擎

检测流程:
  阶段一: SHA256 完整性检测 — 逐文件对比当前磁盘 SHA256 与快照记录的 SHA256
  阶段二: 存在性验证 — 检查文件的存在状态是否与快照记录预期一致
  阶段三: move 操作完整性检查 — 配对验证 move_source 和 move_dest 的状态一致性

返回完整的 FileConflict 列表和汇总统计。
"""

from __future__ import annotations

import os
from typing import Dict, List, Any

from .snapshot_types import FileSnapshotRecord, FileConflict
from .snapshot_manager import SnapshotManager


class ConflictDetector:
    """三阶段冲突检测引擎"""

    def __init__(self, manager: SnapshotManager):
        self._manager = manager

    def detect(self, message_id: int) -> Dict[str, Any]:
        """
        执行三阶段冲突检测

        Args:
            message_id: 回退目标消息 ID

        Returns:
            { has_conflicts, conflicts, safe_count, conflict_count,
              phase_one, phase_two, phase_three }
        """
        records = self._manager.query_by_message(message_id)
        if not records:
            return {'has_conflicts': False, 'conflicts': [], 'safe_count': 0,
                    'conflict_count': 0, 'phase_one': [], 'phase_two': [], 'phase_three': []}

        # 按文件去重
        seen: Dict[str, FileSnapshotRecord] = {}
        for r in records:
            if r.relative_path not in seen:
                seen[r.relative_path] = r
        unique_records = list(seen.values())

        # 阶段一: SHA256 检测
        phase_one = self._phase_one_sha256(unique_records)

        # 阶段二: 存在性验证
        phase_two = self._phase_two_existence(unique_records)

        # 阶段三: move 完整性
        phase_three = self._phase_three_move_integrity(unique_records)

        # 汇总
        all_conflicts = []
        all_conflicts.extend(p['conflict'] for p in phase_one if p.get('conflict'))
        all_conflicts.extend(p['conflict'] for p in phase_two if p.get('conflict'))
        all_conflicts.extend(p['conflict'] for p in phase_three if p.get('conflict'))

        safe_records = [r for r in unique_records
                        if not any(c.relative_path == r.relative_path for c in all_conflicts)]

        return {
            'has_conflicts': len(all_conflicts) > 0,
            'conflicts': [c.to_dict() for c in all_conflicts],
            'safe_count': len(safe_records),
            'conflict_count': len(all_conflicts),
            'phase_one': phase_one,
            'phase_two': phase_two,
            'phase_three': phase_three,
        }

    # ── 阶段一: SHA256 完整性检测 ─────────────────────────────

    def _phase_one_sha256(self, records: List[FileSnapshotRecord]) -> List[Dict[str, Any]]:
        """逐文件对比当前磁盘 SHA256 与快照记录的 SHA256

        SHA 不同时不再一律判为"将被覆盖"。
        若当前 SHA 不在该文件的"AI 掌控已知集合"（所有 backup_sha256 ∪
        write_result_sha256）中，说明回退点之后的内容可能来自外部修改
        （别的程序/人工），报 external_modification 冲突而非静默覆盖。
        兼容：无 write_result 数据的旧记录回退到原行为（will_overwrite）。
        """
        results = []
        known_cache: Dict[str, tuple] = {}
        for record in records:
            result = {'file': record.relative_path, 'has_conflict': False, 'conflict': None}

            if not record.file_existed_before:
                # 新建文件，无需 SHA256 检测
                results.append(result)
                continue

            if not os.path.isfile(record.absolute_path):
                # 文件已不存在，留给阶段二处理
                results.append(result)
                continue

            current_sha = self._manager._sha256_file(record.absolute_path)

            if current_sha == record.backup_sha256:
                result['current_sha'] = current_sha
                result['snapshot_sha'] = record.backup_sha256
                results.append(result)
                continue

            # 收集该文件的已知 SHA 集合（缓存，文件级）
            known = known_cache.get(record.relative_path)
            if known is None:
                known = self._manager.collect_known_sha256(record.relative_path)
                known_cache[record.relative_path] = known
            known_set, has_write_result = known

            result['current_sha'] = current_sha
            result['snapshot_sha'] = record.backup_sha256

            if (not has_write_result) or (current_sha in known_set):
                # 正常：当前内容在 AI 掌控内（这正是回退操作要恢复的），标记"将被覆盖"
                result['will_overwrite'] = True
            else:
                # 外部修改：当前内容不在任何已知状态中
                stat = os.stat(record.absolute_path)
                result['has_conflict'] = True
                result['conflict'] = FileConflict(
                    relative_path=record.relative_path,
                    absolute_path=record.absolute_path,
                    snapshot_record_id=record.record_id,
                    snapshot_sha256=record.backup_sha256,
                    current_sha256=current_sha,
                    conflict_type='external_modification',
                    expectation='回退时此文件将被恢复为快照版本',
                    actual='文件当前内容不在 AI 的已知修改记录中（可能被外部程序或人工修改）',
                    last_modified_at=stat.st_mtime,
                )
            results.append(result)

        return results

    # ── 阶段二: 存在性验证 ────────────────────────────────────

    def _phase_two_existence(self, records: List[FileSnapshotRecord]) -> List[Dict[str, Any]]:
        """检查文件的存在状态是否与快照记录预期一致"""
        results = []
        for record in records:
            result = {'file': record.relative_path, 'has_conflict': False, 'conflict': None}

            file_exists_now = os.path.isfile(record.absolute_path)

            if record.file_existed_after and not file_exists_now:
                # 预期文件应该存在，但当前不存在
                conflict = FileConflict(
                    relative_path=record.relative_path,
                    absolute_path=record.absolute_path,
                    snapshot_record_id=record.record_id,
                    snapshot_sha256=record.backup_sha256,
                    current_sha256='',
                    conflict_type='file_missing',
                    expectation='回退后此文件应该存在',
                    actual='文件当前不存在于磁盘上',
                )
                result['has_conflict'] = True
                result['conflict'] = conflict

            elif not record.file_existed_after and file_exists_now:
                # 预期文件不存在（被删除了），但当前存在
                current_sha = self._manager._sha256_file(record.absolute_path)
                stat = os.stat(record.absolute_path)
                conflict = FileConflict(
                    relative_path=record.relative_path,
                    absolute_path=record.absolute_path,
                    snapshot_record_id=record.record_id,
                    snapshot_sha256=record.backup_sha256,
                    current_sha256=current_sha,
                    conflict_type='unexpected_file_exists',
                    expectation='回退后此文件应被删除/不存在',
                    actual='文件当前存在于磁盘上（可能是外部重建的）',
                    last_modified_at=stat.st_mtime,
                )
                result['has_conflict'] = True
                result['conflict'] = conflict

            results.append(result)

        return results

    # ── 阶段三: move 操作完整性检查 ─────────────────────────────

    def _phase_three_move_integrity(self, records: List[FileSnapshotRecord]) -> List[Dict[str, Any]]:
        """配对验证 move_source 和 move_dest 的状态一致性"""
        results = []

        move_source = [r for r in records if r.operation_type == 'move_source']
        move_dest = [r for r in records if r.operation_type == 'move_dest']

        # 为每个 move_source 查找配对的 move_dest
        for source_record in move_source:
            counterpart_path = source_record.move_counterpart_path
            if not counterpart_path:
                continue

            counterpart_abs = self._manager._resolve_path(counterpart_path)
            if not counterpart_abs:
                continue

            counterpart_rel = self._manager._to_relative(counterpart_abs)

            # 查找配对的 move_dest 记录
            dest_record = next(
                (r for r in move_dest if r.absolute_path == counterpart_abs),
                None
            )

            source_missing = not os.path.isfile(source_record.absolute_path)

            if source_missing and not dest_record:
                result = {
                    'file': source_record.relative_path,
                    'counterpart': counterpart_rel,
                    'has_conflict': True,
                    'conflict': FileConflict(
                        relative_path=source_record.relative_path,
                        absolute_path=source_record.absolute_path,
                        snapshot_record_id=source_record.record_id,
                        snapshot_sha256=source_record.backup_sha256,
                        current_sha256='',
                        conflict_type='move_integrity_broken',
                        expectation=f'源文件应可从备份恢复，目标 {counterpart_rel} 应可回退',
                        actual='move 操作完整性被破坏：缺少配对的目标文件快照',
                    ),
                }
                results.append(result)

        return results
