"""
snapshot_diff.py — Diff 计算引擎

功能:
- 当前文件内容 vs 快照备份内容 的 unified diff 生成
- 支持三种文件状态: modified / created / deleted
- 返回结构化的 diff hunk 数据供前端渲染
"""

from __future__ import annotations

import difflib
import os
from typing import Dict, List, Any, Optional, Tuple

from .snapshot_types import FileSnapshotRecord


class DiffEngine:
    """Diff 计算引擎"""

    @staticmethod
    def compute_diff(
        records: List[FileSnapshotRecord],
        project_path: str
    ) -> Dict[str, Any]:
        """
        计算回退 diff

        按文件去重（只取最新快照版本），为每个文件生成 unified diff。

        Args:
            records: 按 created_at 降序排列的快照记录
            project_path: 项目根目录

        Returns:
            { changes: List, total_files: int }
        """
        # 按文件去重，保留最新版本
        seen: Dict[str, FileSnapshotRecord] = {}
        for r in records:
            if r.relative_path not in seen:
                seen[r.relative_path] = r

        changes = []
        for rel_path, record in seen.items():
            abs_path = record.absolute_path
            change = DiffEngine._diff_single_file(abs_path, rel_path, record)
            changes.append(change)

        return {
            'changes': changes,
            'total_files': len(changes),
        }

    @staticmethod
    def _diff_single_file(
        abs_path: str, rel_path: str, record: FileSnapshotRecord
    ) -> Dict[str, Any]:
        """为单个文件生成 diff"""

        # 文件当前不存在
        if not os.path.isfile(abs_path):
            if record.file_existed_before and record.backup_path:
                bp = os.path.normpath(record.backup_path)
                if os.path.isfile(bp):
                    # 文件被删除了，回退后将恢复
                    with open(bp, 'r', encoding='utf-8', errors='replace') as f:
                        backup_content = f.read()
                    return {
                        'file': rel_path,
                        'status': 'deleted',
                        'old_content_preview': backup_content[:500],
                        'new_content_preview': '',
                        'hunks': DiffEngine._generate_full_file_diff(
                            backup_content, '', 'deleted'
                        ),
                        'message': '文件已被删除，回退后将恢复',
                    }
            return {
                'file': rel_path,
                'status': 'noop',
                'old_content_preview': '',
                'new_content_preview': '',
                'hunks': [],
                'message': '文件不存在且原始也不存在',
            }

        # 文件当前存在
        try:
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                current_content = f.read()
        except Exception as e:
            return {
                'file': rel_path,
                'status': 'error',
                'old_content_preview': '',
                'new_content_preview': '',
                'hunks': [],
                'message': f'无法读取当前文件: {e}',
            }

        # 备份不存在（新建文件）
        if not record.file_existed_before:
            return {
                'file': rel_path,
                'status': 'created',
                'old_content_preview': '',
                'new_content_preview': current_content[:500],
                'hunks': DiffEngine._generate_full_file_diff(
                    '', current_content, 'created'
                ),
                'message': '这是新建的文件，回退后将删除',
            }

        # 对比差异
        bp = os.path.normpath(record.backup_path) if record.backup_path else ''
        if not bp or not os.path.isfile(bp):
            return {
                'file': rel_path,
                'status': 'error',
                'old_content_preview': '',
                'new_content_preview': current_content[:500],
                'hunks': [],
                'message': f'备份文件不存在: {bp or record.backup_path}',
            }

        try:
            with open(bp, 'r', encoding='utf-8', errors='replace') as f:
                backup_content = f.read()
        except Exception as e:
            return {
                'file': rel_path,
                'status': 'error',
                'old_content_preview': '',
                'new_content_preview': current_content[:500],
                'hunks': [],
                'message': f'无法读取备份文件: {e}',
            }

        if current_content == backup_content:
            return {
                'file': rel_path,
                'status': 'unchanged',
                'old_content_preview': backup_content[:500],
                'new_content_preview': current_content[:500],
                'hunks': [],
                'message': '当前内容与回退目标一致',
            }

        # 生成 unified diff
        hunks = DiffEngine._generate_hunks(backup_content, current_content)

        return {
            'file': rel_path,
            'status': 'modified',
            'old_content_preview': backup_content[:500],
            'new_content_preview': current_content[:500],
            'hunks': hunks,
            'message': f'{len(hunks)} 处变更',
        }

    @staticmethod
    def _generate_hunks(
        old_content: str, new_content: str
    ) -> List[Dict[str, Any]]:
        """生成结构化的 diff hunk 列表"""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diffs = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile='回退目标', tofile='当前版本',
            lineterm=''
        ))

        if not diffs:
            return []

        hunks: List[Dict[str, Any]] = []
        current_hunk: Optional[Dict[str, Any]] = None

        for line in diffs:
            if line.startswith('@@'):
                if current_hunk:
                    hunks.append(current_hunk)
                # 解析 @@ -old_start,old_count +new_start,new_count @@
                parts = line.split()
                old_info = parts[1].lstrip('-').split(',')
                new_info = parts[2].lstrip('+').split(',')
                current_hunk = {
                    'old_start': int(old_info[0]) if old_info[0] else 1,
                    'old_lines': int(old_info[1]) if len(old_info) > 1 else 1,
                    'new_start': int(new_info[0]) if new_info[0] else 1,
                    'new_lines': int(new_info[1]) if len(new_info) > 1 else 1,
                    'lines': [],
                }
            elif current_hunk is not None:
                prefix = line[0] if line else ' '
                text = line[1:] if line else ''
                current_hunk['lines'].append({
                    'prefix': prefix,
                    'text': text,
                })

        if current_hunk:
            hunks.append(current_hunk)

        return hunks

    @staticmethod
    def _generate_full_file_diff(
        old_content: str, new_content: str, status: str
    ) -> List[Dict[str, Any]]:
        """为完整文件变更生成 diff hunk"""
        if status == 'deleted':
            # 文件被删，回退后恢复 → 整个文件是新增的
            lines = [{'prefix': '+', 'text': line.rstrip('\n\r')}
                     for line in old_content.split('\n')]
        else:
            # 新建文件，回退后删除 → 整个文件是删除的
            lines = [{'prefix': '-', 'text': line.rstrip('\n\r')}
                     for line in new_content.split('\n')]

        if not lines:
            return []

        return [{
            'old_start': 1,
            'old_lines': 0,
            'new_start': 1,
            'new_lines': len(lines),
            'lines': lines,
        }]
