"""
snapshot_executor.py — SSE 流式回退执行引擎

核心逻辑:
- 接收回退请求 (session_uuid, target_message_id, conflict_strategy)
- 七阶段执行流程
- SSE 事件流推送进度
- 冲突决策分支处理
- undo 快照创建
- RevertOperation 记录生成
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Any, AsyncGenerator

from .snapshot_types import (
    FileSnapshotRecord,
    RevertOperation,
    RevertFileResult,
)
from .snapshot_manager import SnapshotManager
from .snapshot_conflict import ConflictDetector

logger = logging.getLogger(__name__)


class RevertExecutor:
    """SSE 流式回退执行引擎"""

    def __init__(self, manager: SnapshotManager):
        self._manager = manager

    async def execute_stream(
        self,
        message_id: int,
        conflict_strategy: str = "abort",
        selected_files: list[str] | None = None
    ) -> AsyncGenerator[str, None]:
        """
        流式执行回退，yield SSE 事件字符串

        Args:
            message_id: 回退目标消息 ID
            conflict_strategy: abort / skip_conflicts / overwrite_all

        Yields:
            SSE 格式的字符串行 (data: {...})
        """
        session_uuid = self._manager.session_uuid

        # 获取互斥锁
        async with self._manager.revert_mutex:
            # 创建回退操作记录
            operation = RevertOperation.create(
                session_uuid=session_uuid,
                target_message_id=message_id,
                conflict_strategy=conflict_strategy,
            )

            try:
                # ── 阶段一: 验证 ──
                yield self._sse_event("revert_phase", {"phase": "validating"})
                records = self._validate_and_collect(message_id, operation)
                if isinstance(records, str):
                    yield self._sse_event("revert_error", {"message": records, "phase": "validating"})
                    return
                if not records:
                    yield self._sse_event("revert_completed", {
                        "restored": 0, "skipped": 0, "failed": 0,
                        "total": 0, "revert_id": operation.revert_id,
                        "message": "没有需要回退的文件"
                    })
                    return

                # ── 阶段二: 收集快照 ──
                yield self._sse_event("revert_phase", {"phase": "collecting"})
                deduped = self._deduplicate_files(records)
                operation.total_files = len(deduped)

                # ── 阶段三: 冲突检测 ──
                yield self._sse_event("revert_phase", {"phase": "conflict_check"})
                detector = ConflictDetector(self._manager)
                conflict_report = detector.detect(message_id)

                if conflict_report['has_conflicts']:
                    if conflict_strategy == "abort":
                        yield self._sse_event("revert_conflict_detected", {
                            "message": f"检测到 {conflict_report['conflict_count']} 个文件冲突",
                            "conflicts": conflict_report['conflicts'],
                            "action": "aborted"
                        })
                        operation.status = 'failed'
                        operation.error_message = 'Aborted due to conflicts'
                        operation.conflicts = conflict_report['conflicts']
                        self._manager.save_revert_log(operation)
                        yield self._sse_event("revert_completed", {
                            "restored": 0, "skipped": 0, "failed": 0,
                            "total": operation.total_files,
                            "revert_id": operation.revert_id,
                            "conflicts": conflict_report['conflicts'],
                            "message": "检测到冲突，已中止"
                        })
                        return

                    operation.conflicts = conflict_report['conflicts']

                # ── 选择性过滤 ──
                if selected_files and len(selected_files) > 0:
                    selected_set = set(selected_files)
                    deduped = [r for r in deduped if r.relative_path in selected_set]
                    operation.total_files = len(deduped)

                # ── 阶段四: 标记回退中（只标记实际要恢复的记录） ──
                record_ids_to_revert = [r.record_id for r in deduped]
                self._manager.mark_records_as_reverting(record_ids_to_revert)

                # ── 阶段五: 文件恢复 ──
                yield self._sse_event("revert_phase", {"phase": "restoring"})
                yield self._sse_event("revert_started", {"total_files": operation.total_files})

                file_results: List[RevertFileResult] = []
                conflict_files = {c.get('relative_path', c.get('file', '')) 
                                  for c in conflict_report.get('conflicts', [])}

                for index, record in enumerate(deduped, 1):
                    # 冲突处理
                    if record.relative_path in conflict_files:
                        if conflict_strategy == "skip_conflicts":
                            result = RevertFileResult(
                                relative_path=record.relative_path,
                                absolute_path=record.absolute_path,
                                operation_type=record.operation_type,
                                status="skipped",
                                message="因冲突跳过",
                            )
                            file_results.append(result)
                            operation.skipped_count += 1
                            yield self._sse_event("revert_file_progress", {
                                "index": index, "total": operation.total_files,
                                "file": record.relative_path,
                                "operation_type": record.operation_type,
                                "status": "skipped",
                                "message": "因冲突跳过"
                            })
                            continue

                    # 创建 undo 快照
                    if os.path.isfile(record.absolute_path):
                        current_sha = self._manager._sha256_file(record.absolute_path)
                        if current_sha != record.backup_sha256:
                            self._manager.create_undo_snapshot(
                                operation.revert_id,
                                record.absolute_path,
                                record.relative_path
                            )

                    # 执行恢复
                    result = self._restore_single_file(record)
                    file_results.append(result)

                    if result.status == "success":
                        operation.restored_count += 1
                    elif result.status == "failed":
                        operation.failed_count += 1
                    else:
                        operation.skipped_count += 1

                    yield self._sse_event("revert_file_progress", {
                        "index": index, "total": operation.total_files,
                        "file": record.relative_path,
                        "operation_type": record.operation_type,
                        "status": result.status,
                        "message": result.message
                    })

                # ── 阶段六: 更新状态 ──
                yield self._sse_event("revert_phase", {"phase": "finalizing"})
                self._manager.mark_records_as_reverted(record_ids_to_revert, operation.revert_id)

                operation.file_results = [
                    {
                        "relative_path": r.relative_path,
                        "absolute_path": r.absolute_path,
                        "operation_type": r.operation_type,
                        "status": r.status,
                        "message": r.message,
                    }
                    for r in file_results
                ]
                operation.status = 'completed'
                operation.completed_at = time.time()

                self._manager.save_revert_log(operation)

                # 更新 manifest 回退历史
                self._manager._manifest_add_revert({
                    "revert_id": operation.revert_id,
                    "reverted_at": operation.completed_at,
                    "target_message_id": operation.target_message_id,
                    "restored_files": operation.restored_count,
                    "skipped_files": operation.skipped_count,
                    "failed_files": operation.failed_count,
                    "conflict_files": [c.get('relative_path', c.get('file', '')) for c in conflict_report.get('conflicts', [])],
                })

                # ── 阶段七: 完成 ──
                yield self._sse_event("revert_completed", {
                    "restored": operation.restored_count,
                    "skipped": operation.skipped_count,
                    "failed": operation.failed_count,
                    "total": operation.total_files,
                    "revert_id": operation.revert_id,
                    "conflicts": conflict_report.get('conflicts', []),
                    "message": (
                        f"已恢复 {operation.restored_count} 个文件"
                        f"{', ' + str(operation.skipped_count) + ' 个跳过' if operation.skipped_count > 0 else ''}"
                        f"{', ' + str(operation.failed_count) + ' 个失败' if operation.failed_count > 0 else ''}"
                    )
                })

            except Exception as e:
                logger.error(f"[RevertExecutor] Execution failed: {e}", exc_info=True)
                operation.status = 'failed'
                operation.error_message = str(e)
                operation.completed_at = time.time()
                self._manager.save_revert_log(operation)
                yield self._sse_event("revert_error", {
                    "message": str(e),
                    "phase": "unknown"
                })

    # ── 阶段一: 验证 ────────────────────────────────────────────

    def restore_batch(self, message_id: int, conflict_strategy: str = "abort") -> Dict[str, Any]:
        """同步批量回退（session.file_revert_all）：按策略处理冲突。"""
        records = self._manager.query_by_message(message_id)
        if not records:
            return {"success": False, "message": "No snapshots found for this message",
                    "restored": 0, "skipped": 0, "failed": 0}
        deduped = self._deduplicate_files(records)
        report = ConflictDetector(self._manager).detect(message_id)
        conflict_paths = {
            c.get("relative_path", c.get("file", ""))
            for c in report.get("conflicts", [])
        }
        if report.get("has_conflicts") and conflict_strategy == "abort":
            return {"success": False,
                    "message": f"{report.get('conflict_count', len(conflict_paths))} file conflicts detected, aborted",
                    "conflicts": report.get("conflicts", []),
                    "restored": 0, "skipped": 0, "failed": 0}
        restored = skipped = failed = 0
        for record in deduped:
            if record.relative_path in conflict_paths and conflict_strategy == "skip_conflicts":
                skipped += 1
                continue
            self._manager.mark_records_as_reverting([record.record_id])
            res = self._restore_single_file(record)
            if res.status == "success":
                self._manager.mark_records_as_reverted([record.record_id], self._manager.session_uuid + "_batch")
                restored += 1
            else:
                failed += 1
        return {"success": failed == 0, "restored": restored, "skipped": skipped,
                "failed": failed,
                "message": f"Restored {restored} files, skipped {skipped}, failed {failed}"}

    def _validate_and_collect(
        self, message_id: int, operation: RevertOperation
    ) -> List[FileSnapshotRecord] | str:
        """验证请求并收集快照记录"""
        records = self._manager.query_by_message(message_id)
        if not records:
            return "目标消息没有关联的快照记录"
        return records

    # ── 阶段二: 去重 ───────────────────────────────────────────

    def _deduplicate_files(
        self, records: List[FileSnapshotRecord]
    ) -> List[FileSnapshotRecord]:
        # 去重: 优先保留 active 记录; 全是 reverted 则保留最早
        priority = {'delete': 0, 'move_source': 1, 'move_dest': 1, 'modify': 2, 'create': 3}
        seen_active: Dict[str, FileSnapshotRecord] = {}
        seen_any: Dict[str, FileSnapshotRecord] = {}
        for r in records:
            if r.revert_status == 'active':
                if r.relative_path not in seen_active:
                    seen_active[r.relative_path] = r
                elif r.created_at < seen_active[r.relative_path].created_at:
                    seen_active[r.relative_path] = r
            if r.relative_path not in seen_any:
                seen_any[r.relative_path] = r
            elif r.created_at < seen_any[r.relative_path].created_at:
                seen_any[r.relative_path] = r

        result = []
        for key in set(list(seen_active.keys()) + list(seen_any.keys())):
            rec = seen_active.get(key, seen_any[key])
            result.append(rec)
        result.sort(key=lambda r: priority.get(r.operation_type, 99))
        return result

    # ── 文件恢复 ───────────────────────────────────────────────

    def _restore_single_file(self, record: FileSnapshotRecord) -> RevertFileResult:
        """恢复单个文件"""
        abs_path = record.absolute_path

        try:
            if record.operation_type in ('modify', 'move_dest'):
                # 修改操作: 从备份复制回原位置
                if record.backup_path and os.path.isfile(record.backup_path):
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    self._manager._atomic_copy(record.backup_path, abs_path)
                    return RevertFileResult(
                        relative_path=record.relative_path,
                        absolute_path=abs_path,
                        operation_type=record.operation_type,
                        status="success",
                        message=f"已恢复 {record.relative_path}",
                        restored_from=record.backup_path,
                    )
                return RevertFileResult(
                    relative_path=record.relative_path,
                    absolute_path=abs_path,
                    operation_type=record.operation_type,
                    status="failed",
                    message="备份文件不存在",
                )

            elif record.operation_type == 'delete':
                # 删除操作: 从备份恢复文件
                if record.backup_path and os.path.isfile(record.backup_path):
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    self._manager._atomic_copy(record.backup_path, abs_path)
                    return RevertFileResult(
                        relative_path=record.relative_path,
                        absolute_path=abs_path,
                        operation_type=record.operation_type,
                        status="success",
                        message=f"已恢复 {record.relative_path}（从删除中恢复）",
                        restored_from=record.backup_path,
                    )
                return RevertFileResult(
                    relative_path=record.relative_path,
                    absolute_path=abs_path,
                    operation_type=record.operation_type,
                    status="failed",
                    message="备份文件不存在，无法恢复删除的文件",
                )

            elif record.operation_type == 'create':
                # 新建文件: 回退时删除
                if os.path.isfile(abs_path):
                    os.unlink(abs_path)
                    return RevertFileResult(
                        relative_path=record.relative_path,
                        absolute_path=abs_path,
                        operation_type=record.operation_type,
                        status="success",
                        message=f"已删除新建文件 {record.relative_path}",
                    )
                return RevertFileResult(
                    relative_path=record.relative_path,
                    absolute_path=abs_path,
                    operation_type=record.operation_type,
                    status="success",
                    message=f"{record.relative_path} 已不存在，无需删除",
                )

            elif record.operation_type == 'move_source':
                # 源文件被移走: 从备份恢复到原位置
                if record.backup_path and os.path.isfile(record.backup_path):
                    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                    self._manager._atomic_copy(record.backup_path, abs_path)
                    return RevertFileResult(
                        relative_path=record.relative_path,
                        absolute_path=abs_path,
                        operation_type=record.operation_type,
                        status="success",
                        message=f"已恢复移动操作的源文件 {record.relative_path}",
                        restored_from=record.backup_path,
                    )
                return RevertFileResult(
                    relative_path=record.relative_path,
                    absolute_path=abs_path,
                    operation_type=record.operation_type,
                    status="failed",
                    message="源文件备份不存在",
                )

            return RevertFileResult(
                relative_path=record.relative_path,
                absolute_path=abs_path,
                operation_type=record.operation_type,
                status="failed",
                message=f"未知操作类型: {record.operation_type}",
            )

        except Exception as e:
            logger.error(f"[RevertExecutor] Restore failed for {record.relative_path}: {e}")
            return RevertFileResult(
                relative_path=record.relative_path,
                absolute_path=abs_path,
                operation_type=record.operation_type,
                status="failed",
                message=str(e),
            )

    # ── SSE 事件构建 ────────────────────────────────────────────

    @staticmethod
    def _sse_event(event_type: str, data: Dict[str, Any]) -> str:
        """构建 SSE 事件字符串"""
        event = {"type": event_type, **data}
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
