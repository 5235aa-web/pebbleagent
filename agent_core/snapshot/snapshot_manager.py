"""
snapshot_manager.py — 会话级快照核心管理器

职责:
- 管理 .pebble/snapshots/{session_uuid}/ 目录树
- SQLite 索引 CRUD
- manifest.json 维护
- 快照创建（写操作前触发）
- 快照查询
- 回退前预览
- 磁盘限制执行

线程安全: SQLite 写操作由 _db_lock 保护, manifest 由 _manifest_lock 保护
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from typing import Optional, Dict, List, Any

from .snapshot_types import (
    FileSnapshotRecord,
    RevertOperation,
    FILE_SNAPSHOTS_TABLE_DDL,
    FILE_SNAPSHOTS_INDEXES_DDL,
)

logger = logging.getLogger(__name__)

# ============================================================================
# 实例注册表 (按 session_uuid 索引)
# ============================================================================

_instances: Dict[str, 'SnapshotManager'] = {}
_instances_lock = threading.Lock()


def get_snapshot_manager(session_uuid: str, project_path: str = None) -> Optional['SnapshotManager']:
    """
    获取或创建会话级快照管理器

    Args:
        session_uuid: 会话 UUID
        project_path: 项目路径（创建时必需，获取已有实例时可选）

    Returns:
        SnapshotManager 实例或 None（project_path 为空且实例不存在时）
    """
    with _instances_lock:
        if session_uuid in _instances:
            return _instances[session_uuid]
        if project_path:
            instance = SnapshotManager(session_uuid, project_path)
            _instances[session_uuid] = instance
            return instance
    return None


def dispose_snapshot_manager(session_uuid: str):
    """销毁指定会话的快照管理器"""
    with _instances_lock:
        instance = _instances.pop(session_uuid, None)
        if instance:
            instance.dispose()


def dispose_all():
    """销毁所有快照管理器"""
    with _instances_lock:
        for instance in list(_instances.values()):
            instance.dispose()
        _instances.clear()


# ============================================================================
# SnapshotManager
# ============================================================================

class SnapshotManager:
    """
    会话级快照管理器

    每个 (session_uuid, project_path) 组合只有一个实例。
    管理该会话下的所有文件快照。

    Attributes:
        MAX_SNAPSHOTS: 最大快照点数量（默认200）
        MAX_TOTAL_SIZE: 最大备份总大小（默认500MB）
    """

    MAX_SNAPSHOTS: int = 200
    MAX_TOTAL_SIZE: int = 500_000_000  # 500MB
    MIN_RETAIN: int = 50  # 清理时至少保留最近50个快照点

    def __init__(self, session_uuid: str, project_path: str):
        self.session_uuid = session_uuid
        self.project_path = os.path.abspath(project_path)

        # 目录结构
        self._base = os.path.join(self.project_path, '.pebble', 'snapshots', session_uuid)
        self._manifest_path = os.path.join(self._base, 'manifest.json')
        self._db_path = os.path.join(self._base, 'index.db')
        self._files_dir = os.path.join(self._base, 'files')
        self._revert_log_dir = os.path.join(self._base, 'revert_log')
        self._undo_dir = os.path.join(self._base, 'undo_snapshots')

        # 确保目录树存在
        for d in [self._base, self._files_dir, self._revert_log_dir, self._undo_dir]:
            os.makedirs(d, exist_ok=True)

        # SQLite 连接 (WAL 模式 + 外键约束)
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db_lock = threading.Lock()

        # 初始化表
        self._init_tables()

        # manifest 读写互斥锁
        self._manifest_lock = threading.Lock()
        self._manifest: Dict[str, Any] = self._load_manifest()

        # 回退操作互斥锁 (asyncio 层面)
        self._revert_mutex = asyncio.Lock()

        # 清理计数器 (每 N 次快照触发一次清理)
        self._snapshot_count_since_cleanup: int = 0
        self._cleanup_interval: int = 10

        logger.info(f"[SnapshotManager] Initialized for session {session_uuid[:8]}... "
                     f"at {self._base}")

    # ────────────────────────────────────────────────────────────────
    # 生命周期
    # ────────────────────────────────────────────────────────────────

    def dispose(self):
        """关闭连接，释放资源"""
        if hasattr(self, '_db') and self._db:
            try:
                self._db.close()
            except Exception:
                pass
        logger.info(f"[SnapshotManager] Disposed session {self.session_uuid[:8]}...")

    # ────────────────────────────────────────────────────────────────
    # SQLite 表初始化
    # ────────────────────────────────────────────────────────────────

    def _init_tables(self):
        """初始化 SQLite 表结构"""
        with self._db_lock:
            try:
                self._db.execute(FILE_SNAPSHOTS_TABLE_DDL)
                for index_ddl in FILE_SNAPSHOTS_INDEXES_DDL:
                    self._db.execute(index_ddl)
                # 迁移：已有库补 write_result_sha256 列
                try:
                    self._db.execute(
                        "ALTER TABLE file_snapshots ADD COLUMN write_result_sha256 TEXT NOT NULL DEFAULT ''"
                    )
                except sqlite3.OperationalError:
                    pass  # 列已存在
                self._db.commit()
            except sqlite3.Error as e:
                logger.error(f"[SnapshotManager] Failed to init tables: {e}")
                self._db.rollback()

    # ────────────────────────────────────────────────────────────────
    # manifest.json 操作
    # ────────────────────────────────────────────────────────────────

    def _load_manifest(self) -> Dict[str, Any]:
        """加载或创建 manifest.json"""
        if os.path.exists(self._manifest_path):
            try:
                with open(self._manifest_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"[SnapshotManager] Corrupt manifest, rebuilding: {e}")

        default = {
            "session_uuid": self.session_uuid,
            "project_path": self.project_path,
            "created_at": time.time(),
            "updated_at": time.time(),
            "total_snapshots": 0,
            "total_size_bytes": 0,
            "snapshot_ids": [],
            "revert_history": [],
        }
        self._save_manifest(default)
        return default

    def _save_manifest(self, manifest: Optional[Dict] = None):
        """保存 manifest.json (原子写入)"""
        if manifest is None:
            manifest = self._manifest
        manifest["updated_at"] = time.time()
        with self._manifest_lock:
            tmp = self._manifest_path + '.tmp'
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._manifest_path)
            except Exception as e:
                logger.error(f"[SnapshotManager] Failed to save manifest: {e}")

    def _manifest_add_snapshot(self, snapshot_id: str, total_size: int):
        """向 manifest 添加新快照点记录"""
        with self._manifest_lock:
            self._manifest["total_snapshots"] += 1
            self._manifest["total_size_bytes"] += total_size
            if snapshot_id not in self._manifest["snapshot_ids"]:
                self._manifest["snapshot_ids"].append(snapshot_id)
        self._save_manifest()

    def _manifest_add_revert(self, revert_entry: Dict[str, Any]):
        """向 manifest 添加回退历史"""
        with self._manifest_lock:
            self._manifest["revert_history"].append(revert_entry)
        self._save_manifest()

    # ────────────────────────────────────────────────────────────────
    # 工具方法
    # ────────────────────────────────────────────────────────────────

    def _resolve_path(self, raw: str) -> Optional[str]:
        """解析路径：相对路径 → 项目根目录下绝对路径"""
        if not raw:
            return None
        if os.path.isabs(raw):
            return os.path.normpath(raw)
        return os.path.normpath(os.path.join(self.project_path, raw))

    def _to_relative(self, abs_path: str) -> str:
        """绝对路径 → 项目根目录下相对路径（对项目外文件使用扁平化路径避免 ..）"""
        try:
            rel = os.path.relpath(abs_path, self.project_path)
            # 规范化：消除 .. 并统一分隔符
            norm = os.path.normpath(rel).replace('\\', '/')
            if norm.startswith('../') or norm.startswith('..\\'):
                # 项目外文件：用绝对路径的「盘符剥离 + 下划线替换」作为扁平 key
                flat = abs_path.replace(':\\', '_').replace(':', '').replace('\\', '/').lstrip('/')
                return f'_external/{flat}'
            return norm
        except ValueError:
            return abs_path.replace('\\', '/')

    @staticmethod
    def _sha256_file(file_path: str) -> str:
        """计算文件 SHA256 哈希"""
        sha = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()

    def _atomic_copy(self, src: str, dst: str) -> None:
        """原子复制文件: 先写临时文件，SHA256 校验，再 rename"""
        tmp = dst + '.tmp.' + str(os.getpid())
        shutil.copy2(src, tmp)
        src_sha = self._sha256_file(src)
        dst_sha = self._sha256_file(tmp)
        if src_sha != dst_sha:
            os.unlink(tmp)
            raise IOError(f"原子复制 SHA256 不一致: {src}")
        os.replace(tmp, dst)

    # ────────────────────────────────────────────────────────────────
    # 数据库批量操作
    # ────────────────────────────────────────────────────────────────

    def _db_insert_batch(self, records: List[FileSnapshotRecord]):
        """批量插入快照记录"""
        with self._db_lock:
            try:
                rows = [r.to_db_row() for r in records]
                self._db.executemany("""
                    INSERT OR REPLACE INTO file_snapshots
                    (record_id, snapshot_id, session_uuid, message_id, tool_call_id,
                     tool_name, tool_input_json, absolute_path, relative_path,
                     backup_path, backup_sha256, backup_size, backup_mtime,
                     write_result_sha256,
                     file_existed_before, file_existed_after,
                     operation_type, move_counterpart_path,
                     created_at, revert_status, reverted_at, revert_id)
                    VALUES
                    (:record_id, :snapshot_id, :session_uuid, :message_id, :tool_call_id,
                     :tool_name, :tool_input_json, :absolute_path, :relative_path,
                     :backup_path, :backup_sha256, :backup_size, :backup_mtime,
                     :write_result_sha256,
                     :file_existed_before, :file_existed_after,
                     :operation_type, :move_counterpart_path,
                     :created_at, :revert_status, :reverted_at, :revert_id)
                """, rows)
                self._db.commit()
            except sqlite3.Error as e:
                logger.error(f"[SnapshotManager] Batch insert failed: {e}")
                self._db.rollback()

    def _db_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """执行查询并返回字典列表"""
        with self._db_lock:
            cursor = self._db.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def _db_execute(self, query: str, params: tuple = ()):
        """执行非查询 SQL"""
        with self._db_lock:
            try:
                self._db.execute(query, params)
                self._db.commit()
            except sqlite3.Error as e:
                logger.error(f"[SnapshotManager] Execute failed: {e}")
                self._db.rollback()

    def _db_update_revert_status(self, snapshot_id: str, status: str,
                                  revert_id: str = None):
        """批量更新快照点的回退状态"""
        with self._db_lock:
            try:
                if revert_id:
                    self._db.execute(
                        """UPDATE file_snapshots 
                           SET revert_status = ?, reverted_at = ?, revert_id = ?
                           WHERE snapshot_id = ? AND session_uuid = ?""",
                        (status, time.time(), revert_id, snapshot_id, self.session_uuid)
                    )
                else:
                    self._db.execute(
                        """UPDATE file_snapshots 
                           SET revert_status = ?, reverted_at = ?
                           WHERE snapshot_id = ? AND session_uuid = ?""",
                        (status, time.time(), snapshot_id, self.session_uuid)
                    )
                self._db.commit()
            except sqlite3.Error as e:
                logger.error(f"[SnapshotManager] Update revert status failed: {e}")
                self._db.rollback()

    def accept_all_active(self) -> int:
        """将所有活跃快照标记为已接受，返回受影响行数"""
        with self._db_lock:
            try:
                cur = self._db.execute(
                    """UPDATE file_snapshots
                       SET revert_status = 'accepted', reverted_at = ?
                       WHERE session_uuid = ? AND revert_status = 'active'""",
                    (time.time(), self.session_uuid)
                )
                self._db.commit()
                affected = cur.rowcount
                logger.info(f"[SnapshotManager] Accepted {affected} active snapshots for {self.session_uuid}")
                return affected
            except sqlite3.Error as e:
                logger.error(f"[SnapshotManager] Accept snapshots failed: {e}")
                self._db.rollback()
                raise e

    # ────────────────────────────────────────────────────────────────
    # 快照创建
    # ────────────────────────────────────────────────────────────────

    async def create_snapshot(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        message_id: int,
        tool_call_id: str
    ) -> str:
        """
        为一次工具调用创建快照

        Args:
            tool_name: 工具名称
            tool_args: 工具参数字典
            message_id: 触发消息 ID
            tool_call_id: 工具调用 ID

        Returns:
            snapshot_id 字符串

        流程:
          1. 解析工具参数 → 确定需要备份的文件列表
          2. 对每个文件: 计算 SHA256 → 原子复制到备份目录 → 写入 SQLite
          3. 更新 manifest.json
          4. 异步触发磁盘空间检查
        """
        snapshot_id = f"{message_id}_{tool_call_id}"
        files_to_backup = self._resolve_files_for_tool(tool_name, tool_args)

        if not files_to_backup:
            return snapshot_id

        records: List[FileSnapshotRecord] = []
        total_size: int = 0

        for file_info in files_to_backup:
            abs_path = file_info['absolute_path']
            rel_path = file_info['relative_path']
            op_type = file_info['operation_type']

            sha256 = ""
            size = 0
            mtime = 0.0
            backup_path = ""

            if file_info['file_existed_before'] and os.path.isfile(abs_path):
                sha256 = self._sha256_file(abs_path)
                stat = os.stat(abs_path)
                size = stat.st_size
                mtime = stat.st_mtime

                # 备份路径: files/{snapshot_id}/content/{relative_path}
                backup_rel = os.path.join(snapshot_id, 'content', rel_path).replace('\\', '/')
                backup_path = os.path.normpath(os.path.join(self._files_dir, backup_rel))
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)

                try:
                    self._atomic_copy(abs_path, backup_path)
                except IOError as e:
                    logger.error(f"[SnapshotManager] Atomic copy failed for {rel_path}: {e}")
                    continue

                total_size += size

            record = FileSnapshotRecord(
                record_id=str(uuid.uuid4()),
                snapshot_id=snapshot_id,
                session_uuid=self.session_uuid,
                message_id=message_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_input_json=json.dumps(tool_args, ensure_ascii=False),
                absolute_path=abs_path,
                relative_path=rel_path,
                backup_path=backup_path,
                backup_sha256=sha256,
                backup_size=size,
                backup_mtime=mtime,
                file_existed_before=file_info['file_existed_before'],
                file_existed_after=file_info['file_existed_after'],
                operation_type=op_type,
                move_counterpart_path=file_info.get('move_counterpart'),
                created_at=time.time(),
                revert_status='active',
            )
            records.append(record)

        if records:
            self._db_insert_batch(records)
            self._manifest_add_snapshot(snapshot_id, total_size)
            self._snapshot_count_since_cleanup += 1

            # 定期触发清理
            if self._snapshot_count_since_cleanup >= self._cleanup_interval:
                self._snapshot_count_since_cleanup = 0
                asyncio.create_task(self._enforce_disk_limits())

        logger.debug(
            f"[SnapshotManager] Created snapshot {snapshot_id}: "
            f"{len(records)} files, {total_size} bytes"
        )
        return snapshot_id

    async def record_write_result(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        message_id: int,
        tool_call_id: str
    ) -> None:
        """工具执行后回填"写入结果 SHA"

        回退冲突检测需要区分"AI 修改后的状态"与"外部修改"：仅有写前备份时，
        最后一次写入的结果不在任何 backup_sha256 中，无法区分。本方法在工具
        执行后记录文件的实际结果状态，detect 时据此判定 external_modification。
        """
        snapshot_id = f"{message_id}_{tool_call_id}"
        files = self._resolve_files_for_tool(tool_name, tool_args)
        for file_info in files:
            abs_path = file_info['absolute_path']
            rel_path = file_info['relative_path']
            sha = self._sha256_file(abs_path) if os.path.isfile(abs_path) else ""
            self._db_execute(
                """UPDATE file_snapshots SET write_result_sha256 = ?
                   WHERE session_uuid = ? AND snapshot_id = ? AND relative_path = ?""",
                (sha, self.session_uuid, snapshot_id, rel_path)
            )

    def collect_known_sha256(self, relative_path: str) -> tuple:
        """收集某文件在 AI 掌控内的全部已知 SHA

        Returns:
            (known: set[str], has_write_result: bool)
            known = 所有历史记录的 backup_sha256 ∪ write_result_sha256；
            has_write_result 表示该文件是否有回填数据（无则回退到兼容判定）。
        """
        rows = self._db_query(
            """SELECT backup_sha256, write_result_sha256 FROM file_snapshots
               WHERE session_uuid = ? AND relative_path = ?""",
            (self.session_uuid, relative_path)
        )
        known = set()
        has_write_result = False
        for r in rows:
            b = (r.get('backup_sha256') or '').strip()
            if b:
                known.add(b)
            w = (r.get('write_result_sha256') or '').strip()
            if w:
                known.add(w)
                has_write_result = True
        return known, has_write_result

    def _resolve_files_for_tool(self, tool_name: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """根据工具类型和参数，返回需要备份的文件列表"""
        if tool_name == 'write_file':
            return self._resolve_write_file(args)
        elif tool_name == 'edit_file':
            return self._resolve_edit_file(args)
        elif tool_name == 'delete_file':
            return self._resolve_delete_file(args)
        elif tool_name == 'move_file':
            return self._resolve_move_file(args)
        elif tool_name == 'copy_file':
            return self._resolve_copy_file(args)
        return []

    def _resolve_write_file(self, args: Dict) -> List[Dict]:
        file_path = self._resolve_path(args.get('file_path', ''))
        if not file_path:
            return []
        rel = self._to_relative(file_path)
        if os.path.isfile(file_path):
            return [{'absolute_path': file_path, 'relative_path': rel,
                     'operation_type': 'modify', 'file_existed_before': True,
                     'file_existed_after': True}]
        return [{'absolute_path': file_path, 'relative_path': rel,
                 'operation_type': 'create', 'file_existed_before': False,
                 'file_existed_after': True}]

    def _resolve_edit_file(self, args: Dict) -> List[Dict]:
        file_path = self._resolve_path(args.get('file_path', ''))
        if not file_path or not os.path.isfile(file_path):
            return []
        return [{'absolute_path': file_path, 'relative_path': self._to_relative(file_path),
                 'operation_type': 'modify', 'file_existed_before': True,
                 'file_existed_after': True}]

    def _resolve_delete_file(self, args: Dict) -> List[Dict]:
        target = self._resolve_path(args.get('path', ''))
        if not target or not os.path.exists(target):
            return []
        result = []
        if os.path.isfile(target):
            result.append({'absolute_path': target, 'relative_path': self._to_relative(target),
                           'operation_type': 'delete', 'file_existed_before': True,
                           'file_existed_after': False})
        elif os.path.isdir(target):
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for fname in sorted(files):
                    if fname.startswith('.'):
                        continue
                    full = os.path.join(root, fname)
                    result.append({'absolute_path': full, 'relative_path': self._to_relative(full),
                                   'operation_type': 'delete', 'file_existed_before': True,
                                   'file_existed_after': False})
        return result

    def _resolve_move_file(self, args: Dict) -> List[Dict]:
        result = []
        source = self._resolve_path(args.get('source', ''))
        dest = self._resolve_path(args.get('destination', ''))
        if source and os.path.isfile(source):
            result.append({'absolute_path': source, 'relative_path': self._to_relative(source),
                           'operation_type': 'move_source', 'file_existed_before': True,
                           'file_existed_after': False, 'move_counterpart': dest})
        if dest and os.path.isfile(dest):
            result.append({'absolute_path': dest, 'relative_path': self._to_relative(dest),
                           'operation_type': 'move_dest', 'file_existed_before': True,
                           'file_existed_after': True, 'move_counterpart': source})
        return result

    def _resolve_copy_file(self, args: Dict) -> List[Dict]:
        """copy_file 快照解析

        回退语义：目标文件被创建 → 回退删除；目标被覆盖 → 回退恢复旧内容。
        源文件不变，无需备份。
        """
        dest = self._resolve_path(args.get('destination', ''))
        if not dest:
            return []
        rel = self._to_relative(dest)
        if os.path.isfile(dest):
            return [{'absolute_path': dest, 'relative_path': rel,
                     'operation_type': 'modify', 'file_existed_before': True,
                     'file_existed_after': True}]
        return [{'absolute_path': dest, 'relative_path': rel,
                 'operation_type': 'create', 'file_existed_before': False,
                 'file_existed_after': True}]

    # ────────────────────────────────────────────────────────────────
    # 查询
    # ────────────────────────────────────────────────────────────────

    def query_by_message(self, message_id: int) -> List[FileSnapshotRecord]:
        """查询 >= message_id 的所有快照记录（含已回退）"""
        rows = self._db_query(
            """SELECT * FROM file_snapshots
               WHERE session_uuid = ? AND message_id >= ?
               ORDER BY created_at DESC""",
            (self.session_uuid, message_id)
        )
        return [FileSnapshotRecord.from_db_row(r) for r in rows]

    def query_by_file(self, relative_path: str) -> List[FileSnapshotRecord]:
        """查询某个文件的所有快照历史"""
        rows = self._db_query(
            """SELECT * FROM file_snapshots
               WHERE session_uuid = ? AND relative_path = ? AND revert_status = 'active'
               ORDER BY created_at DESC""",
            (self.session_uuid, relative_path)
        )
        return [FileSnapshotRecord.from_db_row(r) for r in rows]

    def query_by_revert_id(self, revert_id: str) -> List[FileSnapshotRecord]:
        """查询某次回退操作涉及的所有快照记录"""
        rows = self._db_query(
            """SELECT * FROM file_snapshots WHERE revert_id = ?""",
            (revert_id,)
        )
        return [FileSnapshotRecord.from_db_row(r) for r in rows]

    def query_all_active(self) -> List[FileSnapshotRecord]:
        """查询所有活跃快照记录"""
        rows = self._db_query(
            """SELECT * FROM file_snapshots
               WHERE session_uuid = ? AND revert_status = 'active'
               ORDER BY created_at DESC""",
            (self.session_uuid,)
        )
        return [FileSnapshotRecord.from_db_row(r) for r in rows]

    def query_all_retained(self) -> List[FileSnapshotRecord]:
        """查询所有需保留的快照记录（active + reverted）

        reverted 快照的备份文件仍被 UndoEngine（撤销回退）依赖，
        孤儿清理必须把它们视为有效，否则撤销回退会因备份缺失而失败。
        """
        rows = self._db_query(
            """SELECT * FROM file_snapshots
               WHERE session_uuid = ? AND revert_status IN ('active', 'reverted')
               ORDER BY created_at DESC""",
            (self.session_uuid,)
        )
        return [FileSnapshotRecord.from_db_row(r) for r in rows]

    def count_by_session(self) -> int:
        """统计活跃快照记录数"""
        rows = self._db_query(
            """SELECT COUNT(*) as cnt FROM file_snapshots
               WHERE session_uuid = ? AND revert_status = 'active'""",
            (self.session_uuid,)
        )
        return rows[0]['cnt'] if rows else 0

    def get_stats(self) -> Dict[str, Any]:
        """获取快照统计"""
        total = self._db_query(
            """SELECT COUNT(*) as cnt, COALESCE(SUM(backup_size), 0) as size
               FROM file_snapshots WHERE session_uuid = ? AND revert_status = 'active'""",
            (self.session_uuid,)
        )
        by_tool = self._db_query(
            """SELECT tool_name, COUNT(*) as cnt
               FROM file_snapshots WHERE session_uuid = ? AND revert_status = 'active'
               GROUP BY tool_name""",
            (self.session_uuid,)
        )
        return {
            'total_records': total[0]['cnt'] if total else 0,
            'total_size': total[0]['size'] if total else 0,
            'by_tool': {r['tool_name']: r['cnt'] for r in by_tool} if by_tool else {},
        }

    # ────────────────────────────────────────────────────────────────
    # 回退预览
    # ────────────────────────────────────────────────────────────────

    def preview_revert(self, message_id: int) -> Dict[str, Any]:
        """
        计算回退影响范围（不含冲突检测，仅统计）

        Returns:
            { target_message_id, affected_tools, affected_files, files: [...] }
        """
        records = self.query_by_message(message_id)
        if not records:
            return {
                'target_message_id': message_id,
                'affected_tools': 0,
                'affected_files': 0,
                'files': [],
            }

        # 统计工具调用数（按 snapshot_id 去重）
        snapshot_ids = set(r.snapshot_id for r in records)

        # 按文件去重，保留最早的快照版本（离回退目标点最近的"修改前"状态）
        seen_files: Dict[str, FileSnapshotRecord] = {}
        for r in records:
            if r.relative_path not in seen_files:
                seen_files[r.relative_path] = r
            elif r.created_at < seen_files[r.relative_path].created_at:
                seen_files[r.relative_path] = r

        files = []
        for r in seen_files.values():
            files.append({
                'file': r.relative_path,
                'tool_name': r.tool_name,
                'tool_call_id': r.tool_call_id,
                'operation_type': r.operation_type,
                'backup_size': r.backup_size,
                'existed_before': r.file_existed_before,
                'already_reverted': r.revert_status == 'reverted',
            })

        return {
            'target_message_id': message_id,
            'affected_tools': len(snapshot_ids),
            'affected_files': len(files),
            'files': files,
        }

    def detect_conflicts(self, message_id: int) -> Dict[str, Any]:
        """
        三阶段冲突检测

        Returns:
            { has_conflicts, conflicts: [...], safe_count, conflict_count }
        """
        from .snapshot_conflict import ConflictDetector
        detector = ConflictDetector(self)
        return detector.detect(message_id)

    # ────────────────────────────────────────────────────────────────
    # 回退状态更新
    # ────────────────────────────────────────────────────────────────

    def mark_records_as_reverting(self, record_ids: List[str]):
        """按 record_id 精确标记为回退中（不影响同快照下的其他文件）"""
        for rid in record_ids:
            self._db_execute(
                """UPDATE file_snapshots SET revert_status = 'reverting'
                   WHERE record_id = ? AND session_uuid = ?""",
                (rid, self.session_uuid)
            )

    def mark_records_as_reverted(self, record_ids: List[str], revert_id: str):
        """按 record_id 精确标记为已回退（不影响同快照下的其他文件）"""
        for rid in record_ids:
            self._db_execute(
                """UPDATE file_snapshots SET revert_status = 'reverted', 
                   reverted_at = ?, revert_id = ?
                   WHERE record_id = ? AND session_uuid = ?""",
                (time.time(), revert_id, rid, self.session_uuid)
            )

    def mark_as_reverting(self, snapshot_ids: List[str]):
        """标记快照点为回退中"""
        for sid in snapshot_ids:
            self._db_update_revert_status(sid, 'reverting')

    def mark_as_reverted(self, snapshot_ids: List[str], revert_id: str):
        """标记快照点为已回退"""
        for sid in snapshot_ids:
            self._db_update_revert_status(sid, 'reverted', revert_id)

    def mark_as_unreverted(self, snapshot_ids: List[str]):
        """标记快照点为未回退（撤销回退）"""
        for sid in snapshot_ids:
            self._db_execute(
                """UPDATE file_snapshots
                   SET revert_status = 'active', reverted_at = NULL, revert_id = NULL
                   WHERE snapshot_id = ? AND session_uuid = ?""",
                (sid, self.session_uuid)
            )

    # ────────────────────────────────────────────────────────────────
    # 回退日志
    # ────────────────────────────────────────────────────────────────

    def save_revert_log(self, operation: 'RevertOperation'):
        """保存回退操作日志"""
        log_path = os.path.join(self._revert_log_dir, f"{operation.revert_id}.json")
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(operation.to_dict(), f, ensure_ascii=False, indent=2)

    def load_revert_log(self, revert_id: str) -> Optional[Dict[str, Any]]:
        """加载回退操作日志"""
        log_path = os.path.join(self._revert_log_dir, f"{revert_id}.json")
        if not os.path.exists(log_path):
            return None
        with open(log_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_revert_history(self) -> List[Dict[str, Any]]:
        """获取所有回退历史"""
        with self._manifest_lock:
            return list(self._manifest.get("revert_history", []))

    # ────────────────────────────────────────────────────────────────
    # Undo 快照
    # ────────────────────────────────────────────────────────────────

    def create_undo_snapshot(self, revert_id: str, file_path: str, rel_path: str) -> str:
        """
        创建 undo 快照——回退前备份当前文件状态

        Returns:
            undo 备份路径
        """
        undo_base = os.path.join(self._undo_dir, revert_id, 'content')
        backup_path = os.path.join(undo_base, rel_path)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)

        if os.path.isfile(file_path):
            self._atomic_copy(file_path, backup_path)

        return backup_path

    def get_undo_snapshot(self, revert_id: str, rel_path: str) -> Optional[str]:
        """获取 undo 快照路径"""
        path = os.path.join(self._undo_dir, revert_id, 'content', rel_path)
        return path if os.path.isfile(path) else None

    def cleanup_undo_snapshots(self, revert_id: str):
        """清理撤销回退后的 undo 快照"""
        undo_dir = os.path.join(self._undo_dir, revert_id)
        if os.path.exists(undo_dir):
            shutil.rmtree(undo_dir, ignore_errors=True)

    # ────────────────────────────────────────────────────────────────
    # 磁盘空间管理
    # ────────────────────────────────────────────────────────────────

    async def _enforce_disk_limits(self):
        """异步执行磁盘限制检查"""
        from .snapshot_cleanup import enforce_disk_limits
        await enforce_disk_limits(self)

    def get_disk_usage(self) -> Dict[str, Any]:
        """获取磁盘使用统计"""
        total_size = 0
        file_count = 0
        for root, dirs, files in os.walk(self._files_dir):
            for f in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                    file_count += 1
                except OSError:
                    pass
        return {
            'total_size': total_size,
            'file_count': file_count,
            'max_size': self.MAX_TOTAL_SIZE,
            'usage_percent': round(total_size / self.MAX_TOTAL_SIZE * 100, 1) if self.MAX_TOTAL_SIZE > 0 else 0,
        }

    # ────────────────────────────────────────────────────────────────
    # 会话清理
    # ────────────────────────────────────────────────────────────────

    @classmethod
    def cleanup_session(cls, session_uuid: str, project_path: str = None):
        """
        删除整个会话的快照目录

        在对话删除时调用
        """
        if project_path:
            base = os.path.join(os.path.abspath(project_path), '.pebble', 'snapshots', session_uuid)
        else:
            # 尝试从实例获取路径
            with _instances_lock:
                instance = _instances.get(session_uuid)
                if instance:
                    base = instance._base
                    instance.dispose()
                else:
                    return

        if os.path.exists(base):
            shutil.rmtree(base, ignore_errors=True)
            logger.info(f"[SnapshotManager] Cleaned up session {session_uuid[:8]}...")

    @property
    def revert_mutex(self) -> asyncio.Lock:
        return self._revert_mutex
