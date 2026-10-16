"""
Permission Enforcer - 独立权限执行器

从 tool_executor_impl.py 中抽离出来的完整权限系统。
为未来的多 Agent 协作做准备——每个 Agent 实例可以共享同一个权限执行器。

职责：
- 工具权限检查（是否有权执行）
- 路径边界检查（是否越权访问）
- 敏感文件检测
- 危险命令检测
- 审计日志记录
"""

import asyncio
import logging
import os
import re
import time
from typing import Dict, Any, Optional, Tuple, List

from .query_state_machine import ToolExecutionContext

logger = logging.getLogger(__name__)

class PermissionCheckError(Exception):
    """权限检查错误"""
    pass

class PathBoundaryError(Exception):
    """路径边界违规错误"""
    pass

class PermissionEnforcer:
    """
    独立权限执行器
    
    可在多个 Agent 实例间共享。线程不安全——每个 event loop 创建独立实例。
    
    使用方式:
        enforcer = PermissionEnforcer(project_path="E:\\agentspace")
        allowed, reason, audit = await enforcer.check("write_file", {"path": "..."}, context)
    """
    
    def __init__(self, project_path: str = "E:\\agentspace"):
        self._current_project_path = project_path
        self._permission_manager = None
        self._audit_logger = None
        self._path_whitelist: List[str] = []
    
    def set_project_path(self, path: str):
        """设置项目路径"""
        self._current_project_path = path
    
    def set_path_whitelist(self, paths: List[str]) -> None:
        """设置额外白名单路径并同步到 PathBoundaryChecker"""
        self._path_whitelist = paths
        try:
            from .tool_permission import PathBoundaryChecker
            PathBoundaryChecker.set_user_whitelist(paths)
        except ImportError:
            pass
    
    # ==================== 公用：路径解析 ====================
    
    def resolve_path(self, path: str) -> str:
        """解析路径，支持相对路径"""
        if not path:
            return path
        if os.path.isabs(path):
            return path
        return os.path.join(self._current_project_path, path)
    
    # ==================== 延迟加载 ====================
    
    def _get_permission_manager(self):
        """获取权限管理器（延迟加载）"""
        if self._permission_manager is None:
            try:
                from gateway.permission_manager import get_permission_manager
                self._permission_manager = get_permission_manager()
            except ImportError:
                logger.warning("[PermissionEnforcer] permission_manager not available")
        return self._permission_manager
    
    def _get_audit_logger(self):
        """获取审计日志记录器（延迟加载）"""
        if self._audit_logger is None:
            try:
                from gateway.permission_audit import get_audit_logger
                self._audit_logger = get_audit_logger()
            except ImportError:
                logger.warning("[PermissionEnforcer] permission_audit not available")
        return self._audit_logger
    
    # ==================== 路径边界检查 ====================
    
    def is_path_in_project(self, resolved_path: str) -> bool:
        """检查解析后的路径是否在项目目录内"""
        if not resolved_path:
            return True
        try:
            abs_path = os.path.abspath(resolved_path)
            abs_project = os.path.abspath(self._current_project_path)
            return abs_path.lower().startswith(abs_project.lower())
        except Exception:
            return False
    
    async def check_path_boundary(self, path: str) -> Tuple[bool, str]:
        """
        检查路径边界
        
        Returns:
            (是否允许, 原因)
        """
        if not path:
            return True, "Empty path"
        
        try:
            from .tool_permission import PathBoundaryChecker
            PathBoundaryChecker.set_user_whitelist(self._path_whitelist)
            return PathBoundaryChecker.check_path_boundary(
                requested_path=path,
                project_path=self._current_project_path,
                allow_system_paths=False
            )
        except ImportError:
            resolved = self.resolve_path(path)
            if not self.is_path_in_project(resolved):
                return False, f"Path '{resolved}' is outside project boundary"
            return True, "Path is within project boundary"
    
    # ==================== 敏感文件检测 ====================
    
    def check_sensitive_file(self, file_path: str) -> Tuple[bool, str]:
        """
        检查是否是敏感文件
        
        Returns:
            (是否敏感, 原因)
        """
        if not file_path:
            return False, ""
        
        try:
            from .tool_permission import PathBoundaryChecker
            return PathBoundaryChecker.check_sensitive_file(file_path)
        except ImportError:
            filename = os.path.basename(file_path).lower()
            sensitive_patterns = [
                r'\.env$', r'\.pem$', r'\.key$', r'\.p12$', r'\.pfx$',
                r'credentials', r'secret', r'password', r'token',
                r'id_rsa', r'id_dsa', r'id_ecdsa', r'id_ed25519',
            ]
            for pattern in sensitive_patterns:
                if re.search(pattern, filename):
                    return True, f"Sensitive file detected: matches pattern '{pattern}'"
            return False, ""
    
    # ==================== 危险命令检测 ====================
    
    def check_dangerous_command(self, command: str) -> Tuple[bool, str]:
        """
        检查是否是危险命令
        
        委托给 BashValidator.destructive_check() 执行完整的破坏性检测。
        
        Returns:
            (是否危险, 原因)
        """
        from .bash_validator import BashValidator
        validator = BashValidator(self._current_project_path)
        validator.set_path_whitelist(self._path_whitelist)
        report = validator.validate(command)
        
        if report.blocked:
            return True, report.block_reason
        
        if report.is_destructive and report.warnings:
            return True, report.warnings[0]
        
        return False, ""
    
    # ==================== 主入口：完整权限检查 ====================
    
    async def check(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: Optional[ToolExecutionContext] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        执行完整的权限检查
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文
        
        Returns:
            (是否允许, 原因, 审计信息)
        """
        audit_info = {
            "tool_name": tool_name,
            "permission_type": "",
            "permission_level": "",
            "target": "",
            "content": "",
            "decision_source": "",
            "rule_matched": "",
            "path_violation": False,
            "requested_path": "",
            "resolved_path": ""
        }
        
        # 1. 检查是否需要权限检查
        try:
            from .tool_permission import is_tool_restricted, get_tool_permission_info, needs_path_boundary_check
            
            if not is_tool_restricted(tool_name):
                audit_info["decision_source"] = "no_restriction"
                return True, "Tool does not require permission check", audit_info
            
            perm_info = get_tool_permission_info(tool_name, args)
            audit_info["permission_type"] = perm_info.permission_type
            audit_info["content"] = perm_info.content
            audit_info["target"] = perm_info.target
            
        except ImportError as e:
            logger.warning(f"[PermissionEnforcer] tool_permission not available: {e}")
            audit_info["decision_source"] = "module_unavailable"
            return True, "Permission module unavailable", audit_info
        
        # 2. 路径边界检查（如果需要）
        if needs_path_boundary_check(tool_name):
            from .tool_permission import get_path_from_tool_args
            
            paths = get_path_from_tool_args(tool_name, args)
            for path in paths:
                resolved_path = self.resolve_path(path)
                audit_info["requested_path"] = path
                audit_info["resolved_path"] = resolved_path
                
                allowed, reason = await self.check_path_boundary(path)
                if not allowed:
                    audit_info["path_violation"] = True
                    audit_info["decision_source"] = "path_violation"
                    
                    audit_logger = self._get_audit_logger()
                    if audit_logger:
                        audit_logger.log_path_violation(
                            tool_name=tool_name,
                            requested_path=path,
                            resolved_path=resolved_path,
                            project_path=self._current_project_path,
                            session_id="",
                            conversation_uuid=context.conversation_uuid if context else "",
                            agent_name=context.agent_name if context else ""
                        )
                    
                    return False, f"⚠️ 路径边界违规: {reason}", audit_info
        
        # 3. 权限管理器检查
        permission_manager = self._get_permission_manager()
        if permission_manager is None:
            audit_info["decision_source"] = "manager_unavailable"
            return True, "Permission manager unavailable", audit_info

        from .tool_permission import get_tool_permission
        perm_result = get_tool_permission(tool_name, args)
        
        if perm_result is None:
            audit_info["decision_source"] = "no_permission_type"
            return True, "No permission type defined", audit_info
        
        perm_type, content = perm_result
                
        try:
            from gateway.permission_service import PERMISSION_LEVELS, PermissionLevel
            perm_level = PERMISSION_LEVELS.get(perm_type, PermissionLevel.MEDIUM)
            audit_info["permission_level"] = perm_level.value
        except ImportError:
            audit_info["permission_level"] = "medium"
        
        check_start_time = time.time()
        
        allowed, message = await permission_manager.check_permission(
            tool_name=tool_name,
            content=content,
            target=content,
            reason=f"Agent 请求执行 {tool_name}"
        )

        response_time_ms = int((time.time() - check_start_time) * 1000)
        
        # 解析决策来源
        if "Bypass mode" in message:
            audit_info["decision_source"] = "bypass_mode"
        elif "rule" in message.lower():
            audit_info["decision_source"] = "auto_allow_rule" if allowed else "auto_deny_rule"
            audit_info["rule_matched"] = message
        elif "Auto-allowed" in message or "low risk" in message:
            audit_info["decision_source"] = "low_risk"
        elif "User" in message:
            audit_info["decision_source"] = "user_approve" if allowed else "user_deny"
        elif "timed out" in message:
            audit_info["decision_source"] = "timeout"
        else:
            audit_info["decision_source"] = "unknown"
        
        # 记录审计日志
        audit_logger = self._get_audit_logger()
        if audit_logger:
            audit_logger.log_permission_decision(
                tool_name=tool_name,
                allowed=allowed,
                decision_source=audit_info["decision_source"],
                rule_matched=str(audit_info["rule_matched"]),
                user_response_time_ms=response_time_ms,
                session_id="",
                conversation_uuid=context.conversation_uuid if context else "",
                agent_name=context.agent_name if context else "",
                target=content,
                reason=message
            )
        
        if not allowed:
            return False, f"⚠️ 权限被拒绝: {message}", audit_info
        
        return True, message, audit_info
