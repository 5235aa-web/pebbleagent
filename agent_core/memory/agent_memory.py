"""
Agent Memory - Agent 级别记忆

1. 为特定 Agent 类型提供独立的记忆空间
2. 支持三种作用域（user, project, local）
3. 记忆提示词构建
4. 记忆持久化

作用域：
- user: ~/.claude/agent-memory/<agent-type>/MEMORY.md （跨项目共享）
- project: <project>/.claude/agent-memory/<agent-type>/MEMORY.md （项目内共享）
- local: <project>/.claude/agent-memory-local/<agent-type>/MEMORY.md （本地专用）

用途：
- 为特定类型的 Agent（如 code_agent, explore）提供持久化的专业知识存储
- 不同 Agent 类型可以有独立的记忆空间
- 支持跨项目共享或项目特定记忆
"""

import os
import logging
from typing import Optional, List, Dict, Any
from enum import Enum
from pathlib import Path
from threading import Lock

from .auto_memory_paths import (
    get_agent_memory_dir,
    get_agent_memory_file,
    is_agent_memory_path,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Agent Memory Scope
# ============================================================================

class AgentMemoryScope(Enum):
    """Agent 记忆作用域"""
    USER = 'user'       # 跨项目共享
    PROJECT = 'project' # 项目内共享
    LOCAL = 'local'     # 本地专用


# ============================================================================
# Agent Memory Manager
# ============================================================================

class AgentMemoryManager:
    """
    Agent 记忆管理器
    
    为特定 Agent 类型提供独立的记忆空间
    
    功能：
    1. 记忆读写
    2. 作用域管理
    3. 记忆提示词构建
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
        
        # 记忆缓存（agent_type -> scope -> content）
        self._memory_cache: Dict[str, Dict[str, str]] = {}
        
        logger.info("[AgentMemoryManager] Initialized")
    
    # ========================================================================
    # 项目设置
    # ========================================================================
    
    def set_project(self, project_path: str) -> None:
        """
        设置当前项目
        
        Args:
            project_path: 项目路径
        """
        if project_path != self._project_path:
            self._project_path = project_path
            self._memory_cache.clear()
            logger.debug(f"[AgentMemoryManager] Project set: {project_path}")
    
    def get_project_path(self) -> Optional[str]:
        """获取当前项目路径"""
        return self._project_path
    
    def _check_project(self) -> None:
        """检查项目是否已设置"""
        if not self._project_path:
            raise ValueError("Project path not set. Call set_project() first.")
    
    # ========================================================================
    # 记忆读写
    # ========================================================================
    
    def get_memory_dir(
        self,
        agent_type: str,
        scope: AgentMemoryScope
    ) -> str:
        """
        获取 Agent 记忆目录
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
        
        Returns:
            记忆目录路径
        """
        if scope != AgentMemoryScope.USER:
            self._check_project()
        
        return get_agent_memory_dir(agent_type, scope.value, self._project_path)
    
    def get_memory_file(
        self,
        agent_type: str,
        scope: AgentMemoryScope
    ) -> str:
        """
        获取 Agent 记忆文件路径
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
        
        Returns:
            记忆文件路径
        """
        if scope != AgentMemoryScope.USER:
            self._check_project()
        
        return get_agent_memory_file(agent_type, scope.value, self._project_path)
    
    def read_memory(
        self,
        agent_type: str,
        scope: AgentMemoryScope
    ) -> Optional[str]:
        """
        读取 Agent 记忆
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
        
        Returns:
            记忆内容，或 None
        """
        # 检查缓存
        if agent_type in self._memory_cache:
            if scope.value in self._memory_cache[agent_type]:
                return self._memory_cache[agent_type][scope.value]
        
        file_path = self.get_memory_file(agent_type, scope)
        
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 缓存
            if agent_type not in self._memory_cache:
                self._memory_cache[agent_type] = {}
            self._memory_cache[agent_type][scope.value] = content
            
            return content
            
        except Exception as e:
            logger.error(f"[AgentMemoryManager] Failed to read memory: {e}")
            return None
    
    def write_memory(
        self,
        agent_type: str,
        scope: AgentMemoryScope,
        content: str
    ) -> bool:
        """
        写入 Agent 记忆
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
            content: 记忆内容
        
        Returns:
            是否成功
        """
        try:
            file_path = self.get_memory_file(agent_type, scope)
            memory_dir = os.path.dirname(file_path)
            
            # 确保目录存在
            os.makedirs(memory_dir, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # 更新缓存
            if agent_type not in self._memory_cache:
                self._memory_cache[agent_type] = {}
            self._memory_cache[agent_type][scope.value] = content
            
            logger.info(f"[AgentMemoryManager] Written memory: {agent_type} ({scope.value})")
            
            return True
            
        except Exception as e:
            logger.error(f"[AgentMemoryManager] Failed to write memory: {e}")
            return False
    
    def append_memory(
        self,
        agent_type: str,
        scope: AgentMemoryScope,
        content: str
    ) -> bool:
        """
        追加内容到 Agent 记忆
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
            content: 要追加的内容
        
        Returns:
            是否成功
        """
        existing = self.read_memory(agent_type, scope) or ""
        
        new_content = existing + "\n\n" + content if existing else content
        
        return self.write_memory(agent_type, scope, new_content)
    
    def clear_memory(
        self,
        agent_type: str,
        scope: AgentMemoryScope
    ) -> bool:
        """
        清除 Agent 记忆
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
        
        Returns:
            是否成功
        """
        try:
            file_path = self.get_memory_file(agent_type, scope)
            
            if os.path.exists(file_path):
                os.remove(file_path)
            
            # 清除缓存
            if agent_type in self._memory_cache:
                self._memory_cache[agent_type].pop(scope.value, None)
            
            logger.info(f"[AgentMemoryManager] Cleared memory: {agent_type} ({scope.value})")
            
            return True
            
        except Exception as e:
            logger.error(f"[AgentMemoryManager] Failed to clear memory: {e}")
            return False
    
    # ========================================================================
    # 记忆提示词构建
    # ========================================================================
    
    def build_memory_prompt(
        self,
        agent_type: str,
        scope: AgentMemoryScope,
        extra_guidelines: Optional[List[str]] = None
    ) -> str:
        """
        构建 Agent 记忆提示词
        
        Args:
            agent_type: Agent 类型
            scope: 作用域
            extra_guidelines: 额外指导
        
        Returns:
            记忆提示词
        """
        memory_dir = self.get_memory_dir(agent_type, scope)
        memory_content = self.read_memory(agent_type, scope)
        
        # 作用域说明
        scope_notes = {
            AgentMemoryScope.USER: "此记忆跨项目共享，应保持通用性",
            AgentMemoryScope.PROJECT: "此记忆在项目内共享，可通过版本控制与团队共享",
            AgentMemoryScope.LOCAL: "此记忆仅限本地使用，不会同步到其他机器",
        }
        
        lines = [
            f"# Agent Memory ({agent_type})",
            "",
            f"记忆位置: `{memory_dir}`",
            f"作用域: {scope_notes[scope]}",
            "",
            "此目录已存在，你可以直接写入文件。",
            "",
            "## 记忆内容",
            "",
        ]
        
        if memory_content:
            lines.append(memory_content)
        else:
            lines.append("（暂无记忆）")
        
        # 添加额外指导
        if extra_guidelines:
            lines.append("")
            lines.append("## 额外指导")
            lines.append("")
            for guideline in extra_guidelines:
                lines.append(f"- {guideline}")
        
        return "\n".join(lines)
    
    # ========================================================================
    # 工具方法
    # ========================================================================
    
    def is_agent_memory_path(self, file_path: str) -> bool:
        """检查文件路径是否在任何 Agent Memory 目录内"""
        return is_agent_memory_path(file_path, self._project_path)
    
    def get_scope_display(self, scope: Optional[AgentMemoryScope]) -> str:
        """获取作用域的显示名称"""
        if scope == AgentMemoryScope.USER:
            return f"User ({self.get_memory_dir('...', AgentMemoryScope.USER)})"
        elif scope == AgentMemoryScope.PROJECT:
            return "Project (.claude/agent-memory/)"
        elif scope == AgentMemoryScope.LOCAL:
            return "Local (.claude/agent-memory-local/)"
        else:
            return "None"
    
    def reset(self) -> None:
        """重置管理器状态"""
        self._project_path = None
        self._memory_cache.clear()


# ============================================================================
# 全局实例
# ============================================================================

def get_agent_memory_manager() -> AgentMemoryManager:
    """获取全局 AgentMemoryManager 实例"""
    return AgentMemoryManager()
