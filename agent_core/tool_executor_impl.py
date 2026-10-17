"""
Tool Executor Implementation - 工具执行器实现

权限检查集成：
- 所有工具执行前进行权限检查
- 路径边界检查（防止越权访问）
- 完整的审计日志记录
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
import uuid
from typing import Dict, Any, List, Optional
from pathlib import Path

from .query_state_machine import ToolExecutionContext
from .tool_orchestrator import ToolExecutor
from .mcp.mcp_integration import is_mcp_tool
from .agent_loader import check_agent_tool_policy
from .permission_enforcer import PermissionEnforcer, PermissionCheckError, PathBoundaryError
from .tool_impl.filesystem import FileToolsMixin
from .tool_impl.execution import ExecutionToolsMixin
from .tool_impl.documents import DocumentToolsMixin
from .tool_impl.web import WebToolsMixin, _sanitize_download_filename
from .tool_impl.system import SystemToolsMixin
from .tool_impl.workspace import WorkspaceToolsMixin
from .tool_impl.lsp import LspToolsMixin
from .tool_impl.mcp_bridge import McpToolsMixin
from .tool_impl.registry import TOOL_HANDLERS

logger = logging.getLogger(__name__)

# 写操作工具：执行后回填写入结果 SHA，供回退冲突检测使用
WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete_file", "copy_file", "move_file"})


class AgentToolExecutorImpl(
    FileToolsMixin,
    ExecutionToolsMixin,
    DocumentToolsMixin,
    WebToolsMixin,
    SystemToolsMixin,
    WorkspaceToolsMixin,
    LspToolsMixin,
    McpToolsMixin,
    ToolExecutor,
):
    """
    Agent 工具执行器实现
    
    实现所有工具的具体执行逻辑
    
    权限检查特性：
    - 所有工具执行前进行权限检查
    - 路径边界检查（防止越权访问项目目录外文件）
    - 完整的审计日志记录
    - 统一的错误处理
    """
    
    def __init__(self, project_path: str = ""):
        project_path = project_path or os.getcwd()
        self._current_project_path = project_path
        self._enforcer = PermissionEnforcer(project_path)
        self._path_whitelist: List[str] = []
        from .truncation_manager import set_truncation_dir
        set_truncation_dir(project_path)
    
    def set_project_path(self, path: str):
        """设置项目路径"""
        self._current_project_path = path
        self._enforcer.set_project_path(path)
        from .truncation_manager import set_truncation_dir
        set_truncation_dir(path)
    
    def set_path_whitelist(self, paths: List[str]) -> None:
        """设置额外白名单路径"""
        self._path_whitelist = paths
        self._enforcer.set_path_whitelist(paths)
    
    def _resolve_path(self, path: str) -> str:
        """解析路径，支持相对路径"""
        if not path:
            return path
        if os.path.isabs(path):
            logger.debug(f"[ToolExecutor] _resolve_path: {path} is absolute, returning as-is")
            return path
        resolved = os.path.join(self._current_project_path, path)
        logger.debug(f"[ToolExecutor] _resolve_path: {path} -> {resolved} (project_path={self._current_project_path})")
        return resolved
    
    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolExecutionContext = None
    ) -> str:
        """
        执行工具
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文
        
        Returns:
            执行结果字符串
        """
        # ==================== Agent 工具策略检查（enforce 白名单/黑名单） ====================
        # 即使 LLM 幻觉调用了未注入 schema 的工具，也在执行入口被拒绝
        agent_name = context.agent_name if context else ""
        policy_denied = check_agent_tool_policy(agent_name, tool_name)
        if policy_denied:
            logger.warning(f"[ToolExecutor] Agent tool policy denied for {tool_name}: {policy_denied}")
            return f"Error: {policy_denied}"

        # ==================== 权限检查（统一入口 - 独立 PermissionEnforcer） ====================
        allowed, reason, audit_info = await self._enforcer.check(tool_name, args, context)

        if not allowed:
            logger.warning(f"[ToolExecutor] Permission denied for {tool_name}: {reason}")
            return f"Error: {reason}"
        
        # ==================== 快照（写操作前备份文件） ====================
        if tool_name in ('write_file', 'edit_file', 'delete_file', 'move_file', 'copy_file'):
            if (context and context.snapshot_session_uuid and 
                context.snapshot_message_id is not None):
                try:
                    from .snapshot.snapshot_manager import get_snapshot_manager
                    manager = get_snapshot_manager(
                        context.snapshot_session_uuid, 
                        self._current_project_path
                    )
                    if manager:
                        await manager.create_snapshot(
                            tool_name=tool_name,
                            tool_args=args,
                            message_id=context.snapshot_message_id,
                            tool_call_id=context.snapshot_tool_call_id or "unknown"
                        )
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning(f"[ToolExecutor] Snapshot creation failed for {tool_name}: {e}")
        
        # ==================== 执行工具 ====================
        try:
            # ==================== 工具调度（声明式注册表） ====================
            handler = TOOL_HANDLERS.get(tool_name)
            if handler is not None:
                result = await handler(self, args, context)
                if tool_name in WRITE_TOOLS:
                    await self._snapshot_record_write_result(tool_name, args, context)
                return result

            # ==================== MCP 工具路由 ====================
            if is_mcp_tool(tool_name):
                return await self._execute_mcp_tool(tool_name, args, context)

            # ==================== 扩展工具回退 ====================
            try:
                from agent_core.extensions_loader import try_execute_extension
                ext_result = try_execute_extension(tool_name, args, context)
                if ext_result is not None:
                    return ext_result
            except ImportError:
                pass
            except Exception as e:
                logger.error(f"[ToolExecutor] Extension tool error for {tool_name}: {e}")

            return f"Error: Unknown tool '{tool_name}'"
        
        except asyncio.CancelledError:
            logger.info(f"[ToolExecutor] Cancelled while executing {tool_name}")
            raise
        
        except Exception as e:
            logger.error(f"[ToolExecutor] Error executing {tool_name}: {e}")
            return f"Error: {str(e)}"
    
    async def _snapshot_record_write_result(self, tool_name: str, args: Dict[str, Any],
                                            context: ToolExecutionContext = None) -> None:
        """工具执行后回填"写入结果 SHA"（用于回退时的外部修改检测）

        与写前快照（execute 开头的 create_snapshot）配对：写前记录备份状态，
        写后记录结果状态，使回退冲突检测能区分"AI 修改"与"外部修改"。
        快照系统不可用/未启用时静默跳过（绝不影响工具执行结果）。
        """
        if not (context and context.snapshot_session_uuid and context.snapshot_message_id is not None):
            return
        try:
            from .snapshot.snapshot_manager import get_snapshot_manager
            manager = get_snapshot_manager(context.snapshot_session_uuid, self._current_project_path)
            if manager:
                await manager.record_write_result(
                    tool_name=tool_name,
                    tool_args=args,
                    message_id=context.snapshot_message_id,
                    tool_call_id=context.snapshot_tool_call_id or "unknown",
                )
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[ToolExecutor] Snapshot write-result record failed for {tool_name}: {e}")
    
    # ==================== 工具实现 ====================
    

    
    
    
    
    
    
    
    
    
    
    
    
    # ================================================================
    # 模糊搜索-替换块编辑（新增：搜索-替换块模式）
    # ================================================================
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    
    # ==================== 文件搜索工具 ====================
    
    

    
    
    # ==================== 网络搜索工具 ====================
    
    
    
    
    # ==================== 进程管理工具 ====================
    
    
    # ==================== 环境变量工具 ====================
    
    
    # ==================== 技能管理工具 ====================
    
    
    
    
    
    # ==================== Plan Mode 工具 ====================
    
    
    
    
    
    # ==================== Tool Search 工具 ====================
    
    
    
    # ==================== LSP 代码智能工具（9 种操作） ====================
    
    
    
    
    
    
    
    
    
    
    
    
    # ==================== LSP 结果格式化 ====================
    
    
    
    
    
    
