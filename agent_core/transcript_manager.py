"""
Transcript Manager - 会话转录记录管理器

1. write_session_transcript_segment - 压缩时写入被压缩的消息段
2. get_transcript_path - 获取转录文件路径
3. read_transcript - 读取转录文件
4. search_transcript - 搜索转录内容
5. insert_message_chain - 插入消息链
6. load_transcript_file - 加载转录文件

转录文件格式：JSONL (每行一个 JSON 对象)
存储路径：~/.pebble/projects/{project_hash}/{session_id}.jsonl

核心功能：
- 压缩前保存被压缩的消息，以便后续查阅
- 支持会话恢复时加载历史
- 支持搜索历史内容
- 支持消息链追踪 (parentUuid)
"""

import os
import json
import logging
import re
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple, Generator
from dataclasses import dataclass, field, asdict

from .safe_json import safe_json_loads, safe_json_dumps
from threading import Lock
import glob as glob_module

logger = logging.getLogger(__name__)

# ============================================================================
# 常量配置
# ============================================================================

# 默认配置目录
DEFAULT_CONFIG_HOME = os.path.expanduser("~/.pebble")

# 最大读取字节数 (50MB)
MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024

# 最大写入块大小 (100MB)
MAX_CHUNK_BYTES = 100 * 1024 * 1024

# 刷新间隔 (毫秒)
FLUSH_INTERVAL_MS = 100

# 轻量读取缓冲区大小 (64KB)
LITE_READ_BUF_SIZE = 64 * 1024

# 跳过预压缩阈值
SKIP_PRECOMPACT_THRESHOLD = 1000


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class TranscriptEntry:
    """转录条目基类"""
    uuid: str
    type: str
    parent_uuid: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: Optional[str] = None
    version: str = "1.0.0"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = asdict(self)
        # 移除 None 值
        return {k: v for k, v in result.items() if v is not None}
    
    def to_jsonl(self) -> str:
        """转换为 JSONL 行"""
        return json.dumps(self.to_dict(), ensure_ascii=False) + "\n"


@dataclass
class UserTranscriptEntry(TranscriptEntry):
    """用户消息转录条目"""
    type: str = "user"
    content: Any = None  # str 或 List[Dict]
    is_sidechain: bool = False
    agent_id: Optional[str] = None
    prompt_id: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None
    is_compact_summary: bool = False
    is_meta: bool = False


@dataclass
class AssistantTranscriptEntry(TranscriptEntry):
    """助手消息转录条目"""
    type: str = "assistant"
    content: Any = None
    message_id: Optional[str] = None  # API 返回的消息 ID
    is_sidechain: bool = False
    agent_id: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None


@dataclass
class SystemTranscriptEntry(TranscriptEntry):
    """系统消息转录条目"""
    type: str = "system"
    subtype: Optional[str] = None
    content: Optional[str] = None
    compact_metadata: Optional[Dict[str, Any]] = None


@dataclass
class AttachmentTranscriptEntry(TranscriptEntry):
    """附件消息转录条目"""
    type: str = "attachment"
    attachment: Optional[Dict[str, Any]] = None
    is_sidechain: bool = False
    agent_id: Optional[str] = None


@dataclass
class CompactBoundaryTranscriptEntry(TranscriptEntry):
    """压缩边界转录条目"""
    type: str = "system"
    subtype: str = "compact_boundary"
    compact_metadata: Optional[Dict[str, Any]] = None


@dataclass
class SummaryTranscriptEntry(TranscriptEntry):
    """摘要转录条目"""
    type: str = "summary"
    summary: Optional[str] = None
    is_compaction: bool = True


@dataclass
class CustomTitleTranscriptEntry(TranscriptEntry):
    """自定义标题转录条目"""
    type: str = "custom-title"
    custom_title: Optional[str] = None


@dataclass
class TagTranscriptEntry(TranscriptEntry):
    """标签转录条目"""
    type: str = "tag"
    tag: Optional[str] = None


@dataclass
class LastPromptTranscriptEntry(TranscriptEntry):
    """最后提示转录条目"""
    type: str = "last-prompt"
    last_prompt: Optional[str] = None


@dataclass
class FileHistorySnapshotTranscriptEntry(TranscriptEntry):
    """文件历史快照转录条目"""
    type: str = "file-history-snapshot"
    message_id: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None
    is_snapshot_update: bool = False


@dataclass
class ContentReplacementTranscriptEntry(TranscriptEntry):
    """内容替换转录条目"""
    type: str = "content-replacement"
    replacements: Optional[List[Dict[str, Any]]] = None
    agent_id: Optional[str] = None


# ============================================================================
# 路径工具函数
# ============================================================================

def get_config_home() -> str:
    """获取配置主目录"""
    env_path = os.environ.get("PEBBLE_CONFIG_DIR")
    if env_path:
        return env_path
    return DEFAULT_CONFIG_HOME


def get_projects_dir() -> str:
    """获取项目目录"""
    return os.path.join(get_config_home(), "projects")


def sanitize_path(path: str) -> str:
    """
    清理路径，使其可以安全地用作文件名
    
    将路径中的特殊字符替换为下划线
    """
    if not path:
        return "default"
    
    # Windows 驱动器号处理
    path = path.replace(":", "_")
    
    # 替换特殊字符
    sanitized = re.sub(r'[<>:"/\\|?*\s]', '_', path)
    
    # 移除连续下划线
    sanitized = re.sub(r'_+', '_', sanitized)
    
    # 移除首尾下划线
    sanitized = sanitized.strip('_')
    
    # 限制长度
    if len(sanitized) > 200:
        # 使用哈希缩短
        hash_suffix = hashlib.md5(path.encode()).hexdigest()[:8]
        sanitized = sanitized[:190] + '_' + hash_suffix
    
    return sanitized or "default"


def get_project_dir(project_path: str) -> str:
    """
    获取项目目录路径
    
    Args:
        project_path: 项目路径 (通常是工作目录)
    
    Returns:
        项目存储目录路径
    """
    return os.path.join(get_projects_dir(), sanitize_path(project_path))


def get_transcript_path(session_id: str, project_path: Optional[str] = None) -> str:
    """
    获取转录文件路径
    
    Args:
        session_id: 会话 ID
        project_path: 项目路径 (可选，默认使用当前工作目录)
    
    Returns:
        转录文件完整路径
    """
    if project_path is None:
        project_path = os.getcwd()
    
    project_dir = get_project_dir(project_path)
    return os.path.join(project_dir, f"{session_id}.jsonl")


def get_agent_transcript_path(
    session_id: str,
    agent_id: str,
    project_path: Optional[str] = None,
    subdir: Optional[str] = None
) -> str:
    """
    获取子 Agent 转录文件路径
    
    Args:
        session_id: 会话 ID
        agent_id: Agent ID
        project_path: 项目路径
        subdir: 子目录 (如 'workflows/{run_id}')
    
    Returns:
        Agent 转录文件完整路径
    """
    if project_path is None:
        project_path = os.getcwd()
    
    project_dir = get_project_dir(project_path)
    
    if subdir:
        base = os.path.join(project_dir, session_id, "subagents", subdir)
    else:
        base = os.path.join(project_dir, session_id, "subagents")
    
    return os.path.join(base, f"agent-{agent_id}.jsonl")


def get_transcript_dir(session_id: str, project_path: Optional[str] = None) -> str:
    """
    获取转录目录路径
    
    Args:
        session_id: 会话 ID
        project_path: 项目路径
    
    Returns:
        转录目录路径
    """
    if project_path is None:
        project_path = os.getcwd()
    
    project_dir = get_project_dir(project_path)
    return os.path.join(project_dir, session_id)


# ============================================================================
# 核心类：TranscriptManager
# ============================================================================

class TranscriptManager:
    """
    转录管理器
    
    管理会话转录文件的读写、搜索和消息链追踪
    
    功能：
    1. 写入消息到转录文件
    2. 读取转录文件
    3. 搜索转录内容
    4. 管理消息链 (parentUuid)
    5. 压缩时写入被压缩的消息段
    """
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        
        # 当前会话文件路径
        self._session_file: Optional[str] = None
        self._session_id: Optional[str] = None
        self._project_path: Optional[str] = None
        
        # 缓冲区
        self._pending_entries: List[TranscriptEntry] = []
        
        # 消息 UUID 集合 (用于去重)
        self._message_uuids: Set[str] = set()
        
        # 写入队列
        self._write_queue: List[Tuple[str, TranscriptEntry]] = []
        
        # 刷新计时器
        self._flush_timer: Optional[float] = None
        self._flush_interval_ms = FLUSH_INTERVAL_MS
        
        # 会话元数据缓存
        self._current_session_title: Optional[str] = None
        self._current_session_tag: Optional[str] = None
        self._current_session_last_prompt: Optional[str] = None
        self._current_session_agent_name: Optional[str] = None
        self._current_session_agent_color: Optional[str] = None
        
        # Agent 转录子目录映射
        self._agent_transcript_subdirs: Dict[str, str] = {}
        
        # 是否禁用持久化
        self._persistence_disabled = False
        
        logger.info("[TranscriptManager] Initialized")
    
    def set_session(
        self,
        session_id: str,
        project_path: Optional[str] = None
    ) -> None:
        """
        设置当前会话
        
        Args:
            session_id: 会话 ID
            project_path: 项目路径
        """
        self._session_id = session_id
        self._project_path = project_path or os.getcwd()
        self._session_file = get_transcript_path(session_id, self._project_path)
        self._message_uuids.clear()
        self._pending_entries.clear()
        
        logger.debug(f"[TranscriptManager] Session set: {session_id}, file: {self._session_file}")
    
    def set_agent_transcript_subdir(self, agent_id: str, subdir: str) -> None:
        """
        设置 Agent 转录子目录
        
        Args:
            agent_id: Agent ID
            subdir: 子目录路径
        """
        self._agent_transcript_subdirs[agent_id] = subdir
    
    def clear_agent_transcript_subdir(self, agent_id: str) -> None:
        """清除 Agent 转录子目录"""
        self._agent_transcript_subdirs.pop(agent_id, None)
    
    def disable_persistence(self, disabled: bool = True) -> None:
        """禁用/启用持久化"""
        self._persistence_disabled = disabled
    
    def should_skip_persistence(self) -> bool:
        """检查是否应该跳过持久化"""
        return self._persistence_disabled or os.environ.get("PEBBLE_SKIP_PROMPT_HISTORY") == "1"
    
    # ========================================================================
    # 写入操作
    # ========================================================================
    
    def write_session_transcript_segment(
        self,
        messages: List[Dict[str, Any]],
        session_id: Optional[str] = None,
        project_path: Optional[str] = None
    ) -> bool:
        """
        写入会话转录段 (压缩时调用)
        
        在压缩发生时，将被压缩的消息段写入转录文件，
        以便后续需要时可以查阅完整的历史内容。
        
        Args:
            messages: 要写入的消息列表
            session_id: 会话 ID (可选，使用当前会话)
            project_path: 项目路径 (可选)
        
        Returns:
            是否写入成功
        """
        if self.should_skip_persistence():
            return False
        
        if not messages:
            return True
        
        # 确定目标文件
        if session_id is None:
            session_id = self._session_id
        
        if session_id is None:
            logger.warning("[TranscriptManager] No session ID set")
            return False
        
        if project_path is None:
            project_path = self._project_path or os.getcwd()
        
        transcript_path = get_transcript_path(session_id, project_path)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(transcript_path), exist_ok=True)
        
        # 写入消息
        try:
            with open(transcript_path, 'a', encoding='utf-8') as f:
                for msg in messages:
                    entry = self._message_to_entry(msg)
                    if entry:
                        f.write(entry.to_jsonl())
            
            logger.info(f"[TranscriptManager] Wrote {len(messages)} messages to {transcript_path}")
            return True
            
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to write transcript segment: {e}")
            return False
    
    def insert_message_chain(
        self,
        messages: List[Dict[str, Any]],
        is_sidechain: bool = False,
        agent_id: Optional[str] = None,
        starting_parent_uuid: Optional[str] = None
    ) -> Optional[str]:
        """
        插入消息链
        
        将消息链写入转录文件，维护 parentUuid 关系
        
        Args:
            messages: 消息列表
            is_sidechain: 是否是侧链
            agent_id: Agent ID (子 Agent)
            starting_parent_uuid: 起始父 UUID
        
        Returns:
            最后一条消息的 UUID
        """
        if self.should_skip_persistence():
            return None
        
        if not messages:
            return None
        
        # 确保会话文件已初始化
        if self._session_file is None:
            self._ensure_session_file()
        
        if self._session_file is None:
            return None
        
        parent_uuid = starting_parent_uuid
        
        # 获取 git 分支
        git_branch = self._get_git_branch()
        
        # 获取当前工作目录
        cwd = os.getcwd()
        
        last_uuid = None
        
        for msg in messages:
            # 检查是否是压缩边界
            is_compact_boundary = msg.get("type") == "system" and msg.get("subtype") == "compact_boundary"
            
            # 构建转录条目
            entry = self._message_to_entry(
                msg,
                parent_uuid=parent_uuid if not is_compact_boundary else None,
                is_sidechain=is_sidechain,
                agent_id=agent_id,
                cwd=cwd,
                git_branch=git_branch
            )
            
            if entry is None:
                continue
            
            # 检查 UUID 是否已存在
            if entry.uuid in self._message_uuids:
                continue
            
            # 写入条目
            self._append_entry(entry, agent_id)
            
            # 更新 parent_uuid
            if msg.get("type") != "progress":
                parent_uuid = entry.uuid
                last_uuid = entry.uuid
            
            # 添加到 UUID 集合
            self._message_uuids.add(entry.uuid)
        
        # 缓存最后提示
        if not is_sidechain:
            self._cache_last_prompt(messages)
        
        return last_uuid
    
    def _message_to_entry(
        self,
        msg: Dict[str, Any],
        parent_uuid: Optional[str] = None,
        is_sidechain: bool = False,
        agent_id: Optional[str] = None,
        cwd: Optional[str] = None,
        git_branch: Optional[str] = None
    ) -> Optional[TranscriptEntry]:
        """
        将消息字典转换为转录条目
        
        Args:
            msg: 消息字典
            parent_uuid: 父 UUID
            is_sidechain: 是否是侧链
            agent_id: Agent ID
            cwd: 当前工作目录
            git_branch: Git 分支
        
        Returns:
            转录条目
        """
        msg_type = msg.get("type", "user")
        uuid = msg.get("uuid", self._generate_uuid())
        
        timestamp = msg.get("timestamp")
        if timestamp is None:
            timestamp = datetime.now().isoformat()
        elif isinstance(timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp).isoformat()
        
        common_args = {
            "uuid": uuid,
            "parent_uuid": parent_uuid,
            "session_id": self._session_id,
            "timestamp": timestamp,
        }
        
        if msg_type == "user":
            return UserTranscriptEntry(
                **common_args,
                content=msg.get("content"),
                is_sidechain=is_sidechain,
                agent_id=agent_id,
                cwd=cwd,
                git_branch=git_branch,
                is_compact_summary=msg.get("is_compact_summary", False),
                is_meta=msg.get("is_meta", False)
            )
        
        elif msg_type == "assistant":
            return AssistantTranscriptEntry(
                **common_args,
                content=msg.get("content"),
                message_id=msg.get("message_id"),
                is_sidechain=is_sidechain,
                agent_id=agent_id,
                cwd=cwd,
                git_branch=git_branch,
                usage=msg.get("usage"),
                model=msg.get("model")
            )
        
        elif msg_type == "system":
            return SystemTranscriptEntry(
                **common_args,
                subtype=msg.get("subtype"),
                content=msg.get("content"),
                compact_metadata=msg.get("compact_metadata")
            )
        
        elif msg_type == "attachment":
            return AttachmentTranscriptEntry(
                **common_args,
                attachment=msg.get("attachment"),
                is_sidechain=is_sidechain,
                agent_id=agent_id
            )
        
        else:
            # 未知类型，创建通用条目
            return TranscriptEntry(
                uuid=uuid,
                type=msg_type,
                parent_uuid=parent_uuid,
                session_id=self._session_id,
                timestamp=timestamp
            )
    
    def _append_entry(
        self,
        entry: TranscriptEntry,
        agent_id: Optional[str] = None
    ) -> None:
        """
        追加条目到转录文件
        
        Args:
            entry: 转录条目
            agent_id: Agent ID (用于确定目标文件)
        """
        if self._session_file is None:
            return
        
        # 确定目标文件
        if agent_id and entry.is_sidechain:
            target_file = get_agent_transcript_path(
                self._session_id,
                agent_id,
                self._project_path,
                self._agent_transcript_subdirs.get(agent_id)
            )
        else:
            target_file = self._session_file
        
        # 确保目录存在
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        
        # 写入
        try:
            with open(target_file, 'a', encoding='utf-8') as f:
                f.write(entry.to_jsonl())
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to append entry: {e}")
    
    def _ensure_session_file(self) -> None:
        """确保会话文件已初始化"""
        if self._session_file is None and self._session_id:
            self._session_file = get_transcript_path(self._session_id, self._project_path)
            os.makedirs(os.path.dirname(self._session_file), exist_ok=True)
    
    def _generate_uuid(self) -> str:
        """生成 UUID"""
        import uuid as uuid_module
        return str(uuid_module.uuid4())
    
    def _get_git_branch(self) -> Optional[str]:
        """获取当前 git 分支"""
        try:
            import subprocess
            result = subprocess.run(
                ['git', 'branch', '--show-current'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip() or None
        except Exception:
            pass
        return None
    
    def _cache_last_prompt(self, messages: List[Dict[str, Any]]) -> None:
        """缓存最后提示"""
        for msg in reversed(messages):
            if msg.get("type") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    flat = content.replace("\n", " ").strip()
                    self._current_session_last_prompt = (
                        flat[:200] + "…" if len(flat) > 200 else flat
                    )
                break
    
    # ========================================================================
    # 读取操作
    # ========================================================================
    
    def read_transcript(
        self,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        读取转录文件
        
        Args:
            session_id: 会话 ID (可选)
            project_path: 项目路径 (可选)
            limit: 最大条目数
            offset: 偏移量
        
        Returns:
            转录条目列表
        """
        if session_id is None:
            session_id = self._session_id
        
        if session_id is None:
            return []
        
        transcript_path = get_transcript_path(
            session_id,
            project_path or self._project_path
        )
        
        if not os.path.exists(transcript_path):
            return []
        
        entries = []
        
        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i < offset:
                        continue
                    if limit and len(entries) >= limit:
                        break
                    
                    line = line.strip()
                    if not line:
                        continue
                    
                    entry = safe_json_loads(line, default=None)
                    if entry is not None:
                        entries.append(entry)
        
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to read transcript: {e}")
        
        return entries
    
    def load_transcript_file(
        self,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None,
        skip_precompact: bool = True
    ) -> List[Dict[str, Any]]:
        """
        加载转录文件 (完整加载，支持跳过预压缩内容)
        
        Args:
            session_id: 会话 ID
            project_path: 项目路径
            skip_precompact: 是否跳过预压缩内容
        
        Returns:
            消息列表
        """
        entries = self.read_transcript(session_id, project_path)
        
        if not entries:
            return []
        
        # 如果跳过预压缩内容，找到最后一个压缩边界
        if skip_precompact:
            last_boundary_index = -1
            for i, entry in enumerate(entries):
                if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
                    last_boundary_index = i
            
            if last_boundary_index >= 0:
                entries = entries[last_boundary_index + 1:]
        
        # 过滤出消息类型的条目
        messages = []
        for entry in entries:
            if entry.get("type") in ("user", "assistant", "system", "attachment"):
                messages.append(entry)
        
        return messages
    
    def get_messages_after_compact_boundary(
        self,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取最后一个压缩边界之后的消息
        
        Args:
            session_id: 会话 ID
        
        Returns:
            消息列表
        """
        return self.load_transcript_file(session_id, skip_precompact=True)
    
    # ========================================================================
    # 搜索操作
    # ========================================================================
    
    def search_transcript(
        self,
        query: str,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None,
        case_sensitive: bool = False,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        搜索转录内容
        
        Args:
            query: 搜索查询
            session_id: 会话 ID
            project_path: 项目路径
            case_sensitive: 是否区分大小写
            limit: 最大结果数
        
        Returns:
            匹配的条目列表
        """
        if not query:
            return []
        
        entries = self.read_transcript(session_id, project_path)
        
        if not case_sensitive:
            query = query.lower()
        
        results = []
        
        for entry in entries:
            content = entry.get("content", "")
            
            if isinstance(content, str):
                search_text = content if case_sensitive else content.lower()
            elif isinstance(content, list):
                # 处理多块内容
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(json.dumps(block.get("input", {}), ensure_ascii=False))
                        elif block.get("type") == "tool_result":
                            text_parts.append(str(block.get("content", "")))
                search_text = " ".join(text_parts)
                if not case_sensitive:
                    search_text = search_text.lower()
            else:
                search_text = ""
            
            if query in search_text:
                results.append(entry)
                if len(results) >= limit:
                    break
        
        return results
    
    def search_by_uuid(
        self,
        uuid: str,
        session_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        通过 UUID 搜索条目
        
        Args:
            uuid: 要搜索的 UUID
            session_id: 会话 ID
        
        Returns:
            匹配的条目，如果没找到返回 None
        """
        entries = self.read_transcript(session_id)
        
        for entry in entries:
            if entry.get("uuid") == uuid:
                return entry
        
        return None
    
    def search_by_type(
        self,
        entry_type: str,
        session_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        通过类型搜索条目
        
        Args:
            entry_type: 条目类型
            session_id: 会话 ID
            limit: 最大结果数
        
        Returns:
            匹配的条目列表
        """
        entries = self.read_transcript(session_id)
        
        results = []
        for entry in entries:
            if entry.get("type") == entry_type:
                results.append(entry)
                if len(results) >= limit:
                    break
        
        return results
    
    # ========================================================================
    # 元数据操作
    # ========================================================================
    
    def re_append_session_metadata(self) -> None:
        """
        重新追加会话元数据
        
        在压缩后调用，确保元数据在文件末尾
        """
        if self._session_file is None or not os.path.exists(self._session_file):
            return
        
        if self._session_id is None:
            return
        
        # 写入最后提示
        if self._current_session_last_prompt:
            self._append_entry(LastPromptTranscriptEntry(
                uuid=self._generate_uuid(),
                session_id=self._session_id,
                last_prompt=self._current_session_last_prompt
            ))
        
        # 写入自定义标题
        if self._current_session_title:
            self._append_entry(CustomTitleTranscriptEntry(
                uuid=self._generate_uuid(),
                session_id=self._session_id,
                custom_title=self._current_session_title
            ))
        
        # 写入标签
        if self._current_session_tag:
            self._append_entry(TagTranscriptEntry(
                uuid=self._generate_uuid(),
                session_id=self._session_id,
                tag=self._current_session_tag
            ))
    
    def set_session_title(self, title: str) -> None:
        """设置会话标题"""
        self._current_session_title = title
    
    def set_session_tag(self, tag: str) -> None:
        """设置会话标签"""
        self._current_session_tag = tag
    
    def get_session_title(self) -> Optional[str]:
        """获取会话标题"""
        return self._current_session_title
    
    def get_session_tag(self) -> Optional[str]:
        """获取会话标签"""
        return self._current_session_tag
    
    def read_lite_metadata(
        self,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        读取轻量元数据
        
        只读取文件尾部，提取元数据字段
        
        Args:
            session_id: 会话 ID
        
        Returns:
            元数据字典
        """
        if session_id is None:
            session_id = self._session_id
        
        if session_id is None:
            return {}
        
        transcript_path = get_transcript_path(session_id, self._project_path)
        
        if not os.path.exists(transcript_path):
            return {}
        
        metadata = {}
        
        try:
            # 读取文件尾部
            file_size = os.path.getsize(transcript_path)
            chunk_len = min(file_size, LITE_READ_BUF_SIZE)
            
            with open(transcript_path, 'r', encoding='utf-8') as f:
                f.seek(file_size - chunk_len)
                tail = f.read()
            
            # 解析尾部行
            for line in tail.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                entry = safe_json_loads(line, default=None)
                if entry is None:
                    continue
                
                entry_type = entry.get("type")
                
                if entry_type == "custom-title":
                    metadata["title"] = entry.get("customTitle")
                elif entry_type == "tag":
                    metadata["tag"] = entry.get("tag")
                elif entry_type == "last-prompt":
                    metadata["last_prompt"] = entry.get("lastPrompt")
                elif entry_type == "agent-name":
                    metadata["agent_name"] = entry.get("agentName")
                elif entry_type == "agent-color":
                    metadata["agent_color"] = entry.get("agentColor")
        
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to read lite metadata: {e}")
        
        return metadata
    
    # ========================================================================
    # 文件操作
    # ========================================================================
    
    def remove_message_by_uuid(self, target_uuid: str) -> bool:
        """
        通过 UUID 移除消息
        
        Args:
            target_uuid: 要移除的消息 UUID
        
        Returns:
            是否成功移除
        """
        if self._session_file is None or not os.path.exists(self._session_file):
            return False
        
        try:
            # 读取文件尾部
            file_size = os.path.getsize(self._session_file)
            chunk_len = min(file_size, LITE_READ_BUF_SIZE)
            
            with open(self._session_file, 'r', encoding='utf-8') as f:
                f.seek(file_size - chunk_len)
                tail = f.read()
            
            # 搜索目标 UUID
            needle = f'"uuid":"{target_uuid}"'
            match_idx = tail.rfind(needle)
            
            if match_idx >= 0:
                # 找到行边界
                prev_nl = tail.rfind('\n', 0, match_idx)
                next_nl = tail.find('\n', match_idx + len(needle))
                
                line_start = prev_nl + 1 if prev_nl >= 0 else 0
                line_end = next_nl + 1 if next_nl >= 0 else len(tail)
                
                abs_line_start = (file_size - chunk_len) + line_start
                
                # 截断并重写尾部
                with open(self._session_file, 'r+', encoding='utf-8') as f:
                    f.truncate(abs_line_start)
                    if line_end < len(tail):
                        f.write(tail[line_end:])
                
                # 从 UUID 集合中移除
                self._message_uuids.discard(target_uuid)
                
                return True
            
            # 不在尾部，需要全文件扫描
            if file_size > MAX_TRANSCRIPT_READ_BYTES:
                logger.warning(f"[TranscriptManager] File too large for full scan: {file_size}")
                return False
            
            # 全文件扫描
            with open(self._session_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            lines = content.split('\n')
            new_lines = []
            
            for line in lines:
                if not line.strip():
                    new_lines.append(line)
                    continue
                
                entry = safe_json_loads(line, default=None)
                if entry is None:
                    new_lines.append(line)
                elif entry.get("uuid") != target_uuid:
                    new_lines.append(line)
            
            with open(self._session_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(new_lines))
            
            self._message_uuids.discard(target_uuid)
            return True
        
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to remove message: {e}")
            return False
    
    def session_exists(self, session_id: str) -> bool:
        """
        检查会话是否存在
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否存在
        """
        transcript_path = get_transcript_path(session_id, self._project_path)
        return os.path.exists(transcript_path)
    
    def list_sessions(
        self,
        project_path: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        列出所有会话
        
        Args:
            project_path: 项目路径
            limit: 最大数量
        
        Returns:
            会话列表
        """
        if project_path is None:
            project_path = self._project_path or os.getcwd()
        
        project_dir = get_project_dir(project_path)
        
        if not os.path.exists(project_dir):
            return []
        
        sessions = []
        
        try:
            for filename in os.listdir(project_dir):
                if filename.endswith('.jsonl'):
                    session_id = filename[:-6]  # 移除 .jsonl
                    transcript_path = os.path.join(project_dir, filename)
                    
                    stat = os.stat(transcript_path)
                    
                    sessions.append({
                        "session_id": session_id,
                        "path": transcript_path,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
                    
                    if len(sessions) >= limit:
                        break
        
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to list sessions: {e}")
        
        # 按修改时间排序
        sessions.sort(key=lambda x: x.get("modified", ""), reverse=True)
        
        return sessions
    
    def delete_session(self, session_id: str) -> bool:
        """
        删除会话
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否成功删除
        """
        transcript_path = get_transcript_path(session_id, self._project_path)
        
        try:
            if os.path.exists(transcript_path):
                os.remove(transcript_path)
            
            # 删除会话目录（如果存在）
            session_dir = get_transcript_dir(session_id, self._project_path)
            if os.path.exists(session_dir):
                import shutil
                shutil.rmtree(session_dir)
            
            return True
        
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to delete session: {e}")
            return False
    
    def flush(self) -> None:
        """刷新缓冲区"""
        # 当前实现是同步的，无需刷新
        pass
    
    def reset(self) -> None:
        """重置管理器状态"""
        self._session_file = None
        self._session_id = None
        self._project_path = None
        self._pending_entries.clear()
        self._message_uuids.clear()
        self._write_queue.clear()
        self._current_session_title = None
        self._current_session_tag = None
        self._current_session_last_prompt = None


# ============================================================================
# 全局实例
# ============================================================================

_transcript_manager: Optional[TranscriptManager] = None


def get_transcript_manager() -> TranscriptManager:
    """获取全局 TranscriptManager 实例"""
    global _transcript_manager
    if _transcript_manager is None:
        _transcript_manager = TranscriptManager()
    return _transcript_manager


# ============================================================================
# 便捷函数
# ============================================================================

def write_session_transcript_segment(
    messages: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    project_path: Optional[str] = None
) -> bool:
    """
    写入会话转录段 (压缩时调用)
    
    便捷函数，使用全局 TranscriptManager 实例
    """
    return get_transcript_manager().write_session_transcript_segment(
        messages, session_id, project_path
    )


def get_transcript_path_for_session(
    session_id: str,
    project_path: Optional[str] = None
) -> str:
    """获取转录文件路径"""
    return get_transcript_path(session_id, project_path)


def read_transcript_for_session(
    session_id: str,
    project_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """读取会话转录"""
    return get_transcript_manager().load_transcript_file(session_id, project_path)


def search_transcript_content(
    query: str,
    session_id: Optional[str] = None,
    limit: int = 20
) -> List[Dict[str, Any]]:
    """搜索转录内容"""
    return get_transcript_manager().search_transcript(query, session_id, limit=limit)


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    # 类
    'TranscriptManager',
    'TranscriptEntry',
    'UserTranscriptEntry',
    'AssistantTranscriptEntry',
    'SystemTranscriptEntry',
    'AttachmentTranscriptEntry',
    'CompactBoundaryTranscriptEntry',
    'SummaryTranscriptEntry',
    'CustomTitleTranscriptEntry',
    'TagTranscriptEntry',
    'LastPromptTranscriptEntry',
    'FileHistorySnapshotTranscriptEntry',
    'ContentReplacementTranscriptEntry',
    
    # 路径函数
    'get_config_home',
    'get_projects_dir',
    'sanitize_path',
    'get_project_dir',
    'get_transcript_path',
    'get_agent_transcript_path',
    'get_transcript_dir',
    
    # 便捷函数
    'get_transcript_manager',
    'write_session_transcript_segment',
    'get_transcript_path_for_session',
    'read_transcript_for_session',
    'search_transcript_content',
    
    # 常量
    'MAX_TRANSCRIPT_READ_BYTES',
    'MAX_CHUNK_BYTES',
    'FLUSH_INTERVAL_MS',
    'LITE_READ_BUF_SIZE',
]
