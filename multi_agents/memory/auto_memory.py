"""
Auto Memory Manager - 自动记忆管理器
1. 记忆的 CRUD 操作
2. 记忆索引管理（MEMORY.md）
3. 记忆搜索和检索
4. 记忆加载（注入到系统提示词）
5. 会话级状态追踪

核心功能：
- create_memory: 创建记忆
- read_memory: 读取记忆
- update_memory: 更新记忆
- delete_memory: 删除记忆
- list_memories: 列出记忆
- search_memories: 搜索记忆
- build_memory_prompt: 构建记忆提示词
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path
from threading import Lock
import json
import re

from .auto_memory_types import (
    MemoryEntry,
    MemoryType,
    MemoryIndex,
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_LINES,
    MAX_ENTRYPOINT_BYTES,
    truncate_entrypoint_content,
)
from .auto_memory_paths import (
    get_auto_memory_dir,
    get_memory_index_path,
    get_memory_file_path,
    ensure_memory_dir_exists,
    is_auto_memory_path,
    get_canonical_project_path,
)
from .auto_memory_scan import (
    scan_memory_files,
    scan_memory_files_async,
    format_memory_manifest,
    find_relevant_memories,
    MemoryScanResult,
    ScannedMemory,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Auto Memory Manager
# ============================================================================

class AutoMemoryManager:
    """
    Auto Memory 管理器
    
    管理 ~/.claude/projects/<project>/memory/ 目录下的记忆文件
    
    功能：
    1. 记忆的 CRUD 操作
    2. 记忆索引管理
    3. 记忆搜索
    4. 记忆提示词构建
    
    线程安全：使用锁保护并发访问
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
        
        # 当前项目路径
        self._project_path: Optional[str] = None
        
        # 记忆缓存
        self._memory_cache: Dict[str, MemoryEntry] = {}
        
        # 索引缓存
        self._index_cache: Optional[MemoryIndex] = None
        
        # 扫描结果缓存
        self._scan_cache: Optional[MemoryScanResult] = None
        
        # 是否禁用持久化
        self._persistence_disabled = False
        
        logger.info("[AutoMemoryManager] Initialized")
    
    # ========================================================================
    # 项目设置
    # ========================================================================
    
    def set_project(self, project_path: str) -> None:
        """
        设置当前项目
        
        Args:
            project_path: 项目路径
        """
        # 使用规范化路径（Git 根目录）
        canonical_path = get_canonical_project_path(project_path)
        
        if canonical_path != self._project_path:
            self._project_path = canonical_path
            self._clear_cache()
            ensure_memory_dir_exists(canonical_path)
            logger.debug(f"[AutoMemoryManager] Project set: {canonical_path}")
    
    def get_project_path(self) -> Optional[str]:
        """获取当前项目路径"""
        return self._project_path
    
    def disable_persistence(self, disabled: bool = True) -> None:
        """禁用/启用持久化"""
        self._persistence_disabled = disabled
    
    def is_persistence_disabled(self) -> bool:
        """检查是否禁用持久化"""
        return self._persistence_disabled or os.environ.get('CLAUDE_CODE_SKIP_PROMPT_HISTORY') == '1'
    
    def _clear_cache(self) -> None:
        """清除缓存"""
        self._memory_cache.clear()
        self._index_cache = None
        self._scan_cache = None
    
    def _check_project(self) -> None:
        """检查项目是否已设置"""
        if not self._project_path:
            raise ValueError("Project path not set. Call set_project() first.")
    
    # ========================================================================
    # CRUD 操作
    # ========================================================================
    
    def create_memory(
        self,
        name: str,
        memory_type: MemoryType,
        description: str,
        content: str,
        tags: Optional[List[str]] = None
    ) -> MemoryEntry:
        """
        创建记忆
        
        Args:
            name: 记忆名称
            memory_type: 记忆类型
            description: 描述
            content: 内容
            tags: 标签
        
        Returns:
            创建的记忆条目
        """
        self._check_project()
        
        if self.is_persistence_disabled():
            logger.warning("[AutoMemoryManager] Persistence disabled, skipping create")
            raise RuntimeError("Persistence disabled")
        
        # 规范化名称
        safe_name = self._sanitize_name(name, memory_type)
        
        # 创建记忆条目
        entry = MemoryEntry(
            name=safe_name,
            memory_type=memory_type,
            description=description,
            content=content,
            tags=tags or [],
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        
        # 写入文件
        file_path = get_memory_file_path(self._project_path, safe_name)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(entry.to_frontmatter())
        
        # 更新索引
        self._update_index(entry)
        
        # 更新缓存
        self._memory_cache[safe_name] = entry
        self._scan_cache = None  # 使扫描缓存失效
        
        logger.info(f"[AutoMemoryManager] Created memory: {safe_name} ({memory_type.value})")
        
        return entry
    
    def read_memory(self, name: str) -> Optional[MemoryEntry]:
        """
        读取记忆
        
        Args:
            name: 记忆名称
        
        Returns:
            记忆条目，或 None
        """
        self._check_project()
        
        # 检查缓存
        if name in self._memory_cache:
            return self._memory_cache[name]
        
        # 读取文件
        file_path = get_memory_file_path(self._project_path, name)
        
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            entry = MemoryEntry.from_frontmatter(content, file_path)
            
            if entry:
                self._memory_cache[name] = entry
            
            return entry
            
        except Exception as e:
            logger.error(f"[AutoMemoryManager] Failed to read memory {name}: {e}")
            return None
    
    def update_memory(
        self,
        name: str,
        description: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> bool:
        """
        更新记忆
        
        Args:
            name: 记忆名称
            description: 新描述
            content: 新内容
            tags: 新标签
        
        Returns:
            是否成功
        """
        self._check_project()
        
        if self.is_persistence_disabled():
            return False
        
        entry = self.read_memory(name)
        if not entry:
            return False
        
        # 更新字段
        if description is not None:
            entry.description = description
        if content is not None:
            entry.content = content
        if tags is not None:
            entry.tags = tags
        
        entry.updated_at = datetime.now().isoformat()
        
        # 写入文件
        file_path = get_memory_file_path(self._project_path, name)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(entry.to_frontmatter())
        
        # 更新索引
        self._update_index(entry)
        
        # 更新缓存
        self._memory_cache[name] = entry
        self._scan_cache = None
        
        logger.info(f"[AutoMemoryManager] Updated memory: {name}")
        
        return True
    
    def delete_memory(self, name: str) -> bool:
        """
        删除记忆
        
        Args:
            name: 记忆名称
        
        Returns:
            是否成功
        """
        self._check_project()
        
        if self.is_persistence_disabled():
            return False
        
        file_path = get_memory_file_path(self._project_path, name)
        
        if not os.path.exists(file_path):
            return False
        
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(f"[AutoMemoryManager] Failed to delete memory {name}: {e}")
            return False
        
        # 从索引移除
        self._remove_from_index(name)
        
        # 从缓存移除
        self._memory_cache.pop(name, None)
        self._scan_cache = None
        
        logger.info(f"[AutoMemoryManager] Deleted memory: {name}")
        
        return True
    
    def list_memories(
        self,
        memory_type: Optional[MemoryType] = None,
        use_cache: bool = True
    ) -> List[MemoryEntry]:
        """
        列出所有记忆
        
        Args:
            memory_type: 可选的类型筛选
            use_cache: 是否使用缓存
        
        Returns:
            记忆列表
        """
        self._check_project()
        
        # 扫描记忆
        memory_dir = get_auto_memory_dir(self._project_path)
        
        if use_cache and self._scan_cache:
            scan_result = self._scan_cache
        else:
            scan_result = scan_memory_files(memory_dir)
            self._scan_cache = scan_result
        
        # 转换为 MemoryEntry
        entries = []
        
        for scanned in scan_result.memories:
            if memory_type and scanned.memory_type != memory_type:
                continue
            
            entry = MemoryEntry(
                name=scanned.name,
                memory_type=scanned.memory_type,
                description=scanned.description,
                content='',  # 不加载完整内容
                file_path=scanned.file_path,
                created_at=scanned.created_at,
                updated_at=scanned.updated_at,
                tags=scanned.tags,
            )
            entries.append(entry)
        
        return entries
    
    # ========================================================================
    # 搜索
    # ========================================================================
    
    def search_memories(
        self,
        query: str,
        max_results: int = 10
    ) -> List[MemoryEntry]:
        """
        搜索记忆
        
        Args:
            query: 搜索查询
            max_results: 最大结果数
        
        Returns:
            匹配的记忆列表
        """
        self._check_project()
        
        memory_dir = get_auto_memory_dir(self._project_path)
        
        # 扫描记忆
        if self._scan_cache:
            scan_result = self._scan_cache
        else:
            scan_result = scan_memory_files(memory_dir)
            self._scan_cache = scan_result
        
        # 查找相关记忆
        relevant = find_relevant_memories(query, scan_result.memories, max_results)
        
        # 转换为 MemoryEntry
        entries = []
        for scanned in relevant:
            entry = self.read_memory(scanned.name)
            if entry:
                entries.append(entry)
        
        return entries
    
    # ========================================================================
    # 索引管理
    # ========================================================================
    
    def _load_index(self) -> MemoryIndex:
        """加载索引"""
        index_path = get_memory_index_path(self._project_path)
        
        if not os.path.exists(index_path):
            return MemoryIndex()
        
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            return MemoryIndex.from_markdown(content)
            
        except Exception as e:
            logger.error(f"[AutoMemoryManager] Failed to load index: {e}")
            return MemoryIndex()
    
    def _save_index(self, index: MemoryIndex) -> None:
        """保存索引"""
        if self.is_persistence_disabled():
            return
        
        index_path = get_memory_index_path(self._project_path)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(index.to_markdown())
        
        self._index_cache = index
    
    def _update_index(self, entry: MemoryEntry) -> None:
        """更新索引条目"""
        index = self._load_index()
        index.add_entry(entry.name, entry.description)
        self._save_index(index)
    
    def _remove_from_index(self, name: str) -> None:
        """从索引移除条目"""
        index = self._load_index()
        index.remove_entry(name)
        self._save_index(index)
    
    def rebuild_index(self) -> int:
        """
        重建索引
        
        Returns:
            索引条目数
        """
        self._check_project()
        
        memories = self.list_memories()
        
        index = MemoryIndex()
        
        for entry in memories:
            index.add_entry(entry.name, entry.description)
        
        self._save_index(index)
        
        logger.info(f"[AutoMemoryManager] Rebuilt index with {len(memories)} entries")
        
        return len(memories)
    
    # ========================================================================
    # 提示词构建
    # ========================================================================
    
    def build_memory_prompt(self) -> str:
        """
        构建记忆提示词（注入到系统提示词）
        
        Returns:
            记忆系统提示词
        """
        self._check_project()
        
        memory_dir = get_auto_memory_dir(self._project_path)
        
        # 加载索引
        index = self._load_index()
        
        lines = [
            "# Auto Memory",
            "",
            f"你有一个持久化的记忆系统，位于 `{memory_dir}`。",
            "此目录已存在，你可以直接写入文件（无需运行 mkdir 或检查是否存在）。",
            "",
            "你应该随着时间建立这个记忆系统，以便未来的对话能够完整了解：",
            "- 用户是谁",
            "- 用户希望如何与你协作",
            "- 应该避免或重复哪些行为",
            "- 用户给你的工作的上下文",
            "",
            "如果用户明确要求你记住某些内容，立即保存为最合适的类型。",
            "如果用户要求你忘记某些内容，找到并移除相关条目。",
            "",
            "## 记忆类型",
            "",
            "有几种不同类型的记忆可以存储：",
            "",
            "### user（用户画像）",
            "包含关于用户角色、目标、职责和知识的信息。",
            "当你了解到用户的角色、偏好、职责或知识时保存。",
            "",
            "### feedback（行为指导）",
            "用户给你的关于如何工作的指导——包括应该避免什么和应该继续做什么。",
            "当用户纠正你的方法或确认非显而易见的方法有效时保存。",
            "",
            "### project（项目上下文）",
            "你了解到的关于项目中正在进行的工作、目标、计划、bug 或事件的信息。",
            "当你了解到谁在做什么、为什么、或何时完成时保存。",
            "",
            "### reference（外部引用）",
            "指向外部系统中信息位置的指针。",
            "当你了解到外部系统中的资源及其用途时保存。",
            "",
            "## 不应保存的内容",
            "",
            "- 代码模式、约定、架构、文件路径或项目结构——这些可以从当前项目状态推导",
            "- Git 历史、最近更改或谁改了什么——`git log` / `git blame` 是权威来源",
            "- 调试解决方案或修复配方——修复在代码中；提交消息有上下文",
            "- 已在 CLAUDE.md 文件中记录的任何内容",
            "- 临时任务细节：进行中的工作、临时状态、当前对话上下文",
            "",
            "## 何时访问记忆",
            "",
            "- 当记忆看起来相关时，或用户引用之前的对话工作时",
            "- 用户明确要求你检查、回忆或记住时",
            "",
            "## 记忆可能过时",
            "",
            "记忆记录可能随时间变得陈旧。在回答用户或基于记忆信息建立假设之前，",
            "验证记忆是否仍然正确和最新。如果回忆的记忆与当前信息冲突，",
            "相信你现在观察到的——并更新或移除陈旧的记忆。",
            "",
        ]
        
        # 添加索引内容
        if index.entries:
            lines.append(f"## {ENTRYPOINT_NAME}")
            lines.append("")
            
            for entry in index.entries:
                title = entry.get('title', entry.get('name', 'Untitled'))
                path = entry.get('path', f"{title}.md")
                hook = entry.get('hook', entry.get('description', ''))
                lines.append(f"- [{title}]({path}) — {hook}")
        else:
            lines.append(f"## {ENTRYPOINT_NAME}")
            lines.append("")
            lines.append("你的记忆目前为空。当你保存新记忆时，它们会出现在这里。")
        
        return "\n".join(lines)
    
    def get_memory_index_content(self) -> str:
        """获取记忆索引内容"""
        self._check_project()
        
        index_path = get_memory_index_path(self._project_path)
        
        if not os.path.exists(index_path):
            return ""
        
        with open(index_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    # ========================================================================
    # 工具方法
    # ========================================================================
    
    def _sanitize_name(self, name: str, memory_type: MemoryType) -> str:
        """
        规范化记忆名称
        
        Args:
            name: 原始名称
            memory_type: 记忆类型
        
        Returns:
            规范化后的名称
        """
        # 移除特殊字符
        safe = re.sub(r'[^\w\u4e00-\u9fff-]', '_', name)
        safe = re.sub(r'_+', '_', safe).strip('_')
        
        # 添加类型前缀
        prefix = memory_type.value
        if not safe.startswith(prefix + '_'):
            safe = f"{prefix}_{safe}"
        
        return safe
    
    def get_stats(self) -> Dict[str, Any]:
        """获取记忆统计信息"""
        self._check_project()
        
        memory_dir = get_auto_memory_dir(self._project_path)
        
        if self._scan_cache:
            scan_result = self._scan_cache
        else:
            scan_result = scan_memory_files(memory_dir)
            self._scan_cache = scan_result
        
        return {
            'total_count': scan_result.total_count,
            'by_type': scan_result.by_type,
            'errors': len(scan_result.errors),
        }
    
    def reset(self) -> None:
        """重置管理器状态"""
        self._project_path = None
        self._clear_cache()
        self._persistence_disabled = False


# ============================================================================
# 全局实例
# ============================================================================

def get_auto_memory_manager() -> AutoMemoryManager:
    """获取全局 AutoMemoryManager 实例"""
    return AutoMemoryManager()
