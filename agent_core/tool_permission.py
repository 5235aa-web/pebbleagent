"""
工具权限映射 - AgentChat 工具调用权限检查

定义每个工具对应的权限类型，用于在工具执行前进行权限检查。
保持 AgentChat 独立性：权限检查完全在后端进行，前端无感知。

功能：
1. 工具权限类型映射
2. 路径边界检查（防止越权访问项目目录外文件）
3. 权限检查结果封装
"""

import json
import os
import re
from typing import Tuple, Optional, Dict, Any, Callable, List
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class PermissionCheckResult:
    """
    权限检查结果
    
    封装权限检查的所有信息，便于统一处理和审计
    """
    
    def __init__(
        self,
        allowed: bool,
        reason: str = "",
        permission_type: str = "",
        permission_level: str = "",
        decision_source: str = "",
        rule_matched: str = "",
        target: str = "",
        content: str = "",
        path_violation: bool = False,
        requested_path: str = "",
        resolved_path: str = ""
    ):
        self.allowed = allowed
        self.reason = reason
        self.permission_type = permission_type
        self.permission_level = permission_level
        self.decision_source = decision_source
        self.rule_matched = rule_matched
        self.target = target
        self.content = content
        self.path_violation = path_violation
        self.requested_path = requested_path
        self.resolved_path = resolved_path
    
    def __bool__(self) -> bool:
        return self.allowed
    
    def __repr__(self) -> str:
        status = "✅ ALLOWED" if self.allowed else "❌ DENIED"
        return f"PermissionCheckResult({status}, reason={self.reason})"


class PathBoundaryChecker:
    """
    路径边界检查器
    
    确保所有文件操作都在项目目录范围内，防止越权访问
    """
    
    # 允许访问的系统路径（白名单，需 allow_system_paths=True 时检查）
    SYSTEM_PATH_WHITELIST = [
        # Windows
        os.path.expandvars(r"%TEMP%"),
        os.path.expandvars(r"%TMP%"),
        # Linux/Mac
        "/tmp",
        "/var/tmp",
    ]

    # 用户配置的额外白名单路径（始终检查，不依赖 allow_system_paths）
    USER_PATH_WHITELIST: List[str] = []

    @classmethod
    def set_user_whitelist(cls, paths: List[str]) -> None:
        """设置用户白名单路径（始终允许访问；解析为真实绝对路径）"""
        if not paths:
            return
        cls.USER_PATH_WHITELIST = [os.path.realpath(os.path.expandvars(os.path.expanduser(p))) for p in paths if p]
    
    # 禁止访问的敏感路径（黑名单）
    SENSITIVE_PATH_BLACKLIST = [
        # Windows
        os.path.expandvars(r"%SYSTEMROOT%\System32\config"),  # SAM, SYSTEM等
        os.path.expandvars(r"%APPDATA%\Microsoft\Credentials"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Credentials"),
        # 通用
        ".ssh",
        ".gnupg",
        ".password-store",
        "credentials",
        "secrets",
    ]
    
    @classmethod
    def normalize_path(cls, path: str) -> str:
        """规范化路径（展开环境变量/用户目录 + 折叠相对段；绝对路径再解析符号链接）"""
        if not path:
            return path
        expanded = os.path.expandvars(path)
        expanded = os.path.expanduser(expanded)
        normalized = os.path.normpath(expanded)
        # 绝对路径解析符号链接/junction；相对路径由调用方基于项目目录解析后再判断
        if os.path.isabs(normalized):
            normalized = os.path.realpath(normalized)
        return normalized
    
    @classmethod
    def is_subpath(cls, child_path: str, parent_path: str) -> bool:
        """
        检查 child_path 是否是 parent_path 的子路径
        
        Args:
            child_path: 子路径
            parent_path: 父路径
            
        Returns:
            True 如果是子路径
        """
        if not child_path or not parent_path:
            return False
        
        # 规范化路径
        child = os.path.abspath(cls.normalize_path(child_path))
        parent = os.path.abspath(cls.normalize_path(parent_path))
        
        # 检查是否是子路径
        # 使用 os.path.commonpath 比较更可靠
        try:
            common = os.path.commonpath([child, parent])
            return common.lower() == parent.lower()
        except ValueError:
            # 不同驱动器（Windows）
            return False
    
    @classmethod
    def check_path_boundary(
        cls,
        requested_path: str,
        project_path: str,
        allow_system_paths: bool = False
    ) -> Tuple[bool, str]:
        """
        检查路径是否在允许的边界内
        
        Args:
            requested_path: 请求的路径
            project_path: 项目目录路径
            allow_system_paths: 是否允许访问系统临时目录
            
        Returns:
            (是否允许, 原因)
        """
        if not requested_path:
            return True, "Empty path"

        # 规范化路径；相对路径按项目目录解析（与执行层一致），再统一折叠/解析符号链接
        normalized = cls.normalize_path(requested_path)
        if not os.path.isabs(normalized):
            normalized = os.path.join(project_path, normalized)
        normalized = os.path.realpath(normalized)

        # 检查是否在项目目录内
        if cls.is_subpath(normalized, project_path):
            return True, "Path is within project boundary"

        # 检查是否在用户配置的白名单中（始终检查）
        for allowed_path in cls.USER_PATH_WHITELIST:
            if allowed_path and cls.is_subpath(normalized, allowed_path):
                return True, f"Path is within user whitelist: {allowed_path}"
        
        # 检查是否在系统白名单中
        if allow_system_paths:
            for sys_path in cls.SYSTEM_PATH_WHITELIST:
                if sys_path and os.path.exists(sys_path) and cls.is_subpath(normalized, sys_path):
                    return True, f"Path is within allowed system path: {sys_path}"
        
        # 检查是否在敏感黑名单中
        normalized_lower = normalized.lower()
        for sensitive in cls.SENSITIVE_PATH_BLACKLIST:
            if sensitive and sensitive.lower() in normalized_lower:
                return False, f"Access to sensitive path denied: contains '{sensitive}'"
        
        # 不在项目目录内
        return False, f"Path '{normalized}' is outside project boundary '{project_path}'"
    
    @classmethod
    def check_sensitive_file(cls, file_path: str) -> Tuple[bool, str]:
        """
        检查是否是敏感文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            (是否敏感, 原因)
        """
        if not file_path:
            return False, ""
        
        filename = os.path.basename(file_path).lower()
        dirname = os.path.dirname(file_path).lower()
        
        # 敏感文件名模式
        sensitive_patterns = [
            r'\.env$',                    # .env
            r'\.env\.',                   # .env.local, .env.production
            r'\.pem$',                    # 证书
            r'\.key$',                    # 密钥
            r'\.p12$',                    # PKCS12
            r'\.pfx$',                    # PFX
            r'credentials',               # credentials
            r'secrets?',                  # secret, secrets
            r'password',                  # password
            r'token',                     # token
            r'api[_-]?key',               # api_key, api-key
            r'private[_-]?key',           # private_key
            r'id_rsa',                    # SSH私钥
            r'id_dsa',
            r'id_ecdsa',
            r'id_ed25519',
            r'\.gitconfig$',              # Git配置
            r'\.npmrc$',                  # NPM配置（可能包含token）
            r'\.pypirc$',                 # PyPI配置
        ]
        
        for pattern in sensitive_patterns:
            if re.search(pattern, filename):
                return True, f"Sensitive file detected: matches pattern '{pattern}'"
        
        # 敏感目录
        sensitive_dirs = [
            '.ssh',
            '.gnupg',
            '.password-store',
            '.config/credentials',
            '.aws',
            '.azure',
        ]
        
        for sensitive_dir in sensitive_dirs:
            if sensitive_dir in dirname:
                return True, f"Sensitive directory detected: '{sensitive_dir}'"
        
        return False, ""


def _get_permission_type_enum():
    """
    延迟导入 PermissionType，避免循环导入
    """
    from gateway.permission_service import PermissionType
    return PermissionType


# 工具名 → (权限类型, 内容提取函数, 是否需要路径边界检查)
# 内容提取函数用于从工具参数中提取关键信息（如文件路径、命令等）
TOOL_PERMISSION_MAPPING: Dict[str, Tuple[str, Callable[[Dict[str, Any]], str], bool]] = {
    # ==================== 代码执行 ====================
    "execute_code": ("EXEC_COMMAND", lambda a: a.get("code", ""), False),
    "execute_shell": ("EXEC_COMMAND", lambda a: a.get("command", ""), False),
    "execute_python": ("EXEC_COMMAND", lambda a: a.get("code", ""), False),
    "execute_javascript": ("EXEC_COMMAND", lambda a: a.get("code", ""), False),
    "powershell": ("EXEC_COMMAND", lambda a: a.get("command", ""), False),
    
    # ==================== 文件操作（需要路径边界检查） ====================
    "read_file": ("FILE_READ", lambda a: a.get("file_path", "") or a.get("path", ""), True),
    "write_file": ("FILE_WRITE", lambda a: a.get("file_path", "") or a.get("path", ""), True),
    "edit_file": ("FILE_WRITE", lambda a: a.get("file_path", "") or a.get("path", ""), True),
    "delete_file": ("FILE_DELETE", lambda a: a.get("path", ""), True),
    "copy_file": ("FILE_WRITE", lambda a: f"{a.get('source', '')} → {a.get('destination', '')}", True),
    "move_file": ("FILE_WRITE", lambda a: f"{a.get('source', '')} → {a.get('destination', '')}", True),
    
    # ==================== 目录操作（需要路径边界检查） ====================
    "list_directory": ("FILE_READ", lambda a: a.get("path", ""), True),
    "create_directory": ("FILE_WRITE", lambda a: a.get("path", ""), True),
    "get_file_info": ("FILE_READ", lambda a: a.get("path", ""), True),
    "ls": ("FILE_READ", lambda a: a.get("path", ""), True),
    
    # ==================== 搜索（需要路径边界检查） ====================
    "grep": ("FILE_READ", lambda a: a.get("path", ""), True),
    "glob": ("FILE_READ", lambda a: a.get("path", ""), True),
    "search_in_file": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "search_files": ("FILE_READ", lambda a: a.get("directory", ""), True),
    "find_files": ("FILE_READ", lambda a: a.get("directory", ""), True),
    
    # ==================== Git ====================
    "git_status": ("FILE_READ", lambda a: a.get("repo_path", "."), True),
    "git_log": ("FILE_READ", lambda a: a.get("repo_path", "."), True),
    "git_diff": ("FILE_READ", lambda a: a.get("repo_path", "."), True),
    
    # ==================== 网络 ====================
    "search_web": ("NETWORK_FETCH", lambda a: a.get("query", ""), False),
    "web_search": ("NETWORK_FETCH", lambda a: a.get("query", ""), False),
    "browser_navigate": ("NETWORK_FETCH", lambda a: a.get("url", ""), False),
    "browser_download": ("NETWORK_FETCH", lambda a: a.get("url", ""), False),
    "fetch_url": ("NETWORK_FETCH", lambda a: a.get("url", ""), False),
    
    # ==================== 进程管理 ====================
    "list_processes": ("EXEC_COMMAND", lambda a: "", False),
    "kill_process": ("EXEC_COMMAND", lambda a: f"PID: {a.get('pid', '')}", False),
    
    # ==================== 环境变量 ====================
    "get_env_var": ("FILE_READ", lambda a: a.get("name", ""), False),
    "set_env_var": ("EXEC_COMMAND", lambda a: a.get("name", ""), False),
    "get_system_info": ("FILE_READ", lambda a: "", False),
    
    # ==================== Notebook ====================
    "notebook_edit": ("FILE_WRITE", lambda a: a.get("notebook_path", ""), True),
    
    # ==================== 技能管理（无需权限检查） ====================
    "list_skills": ("", lambda a: "", False),
    "load_skill": ("", lambda a: "", False),
    
    # ==================== 用户交互（无需权限检查） ====================
    "ask_user_question": ("", lambda a: "", False),
    "brief": ("", lambda a: "", False),
    
    # ==================== Todo管理（无需权限检查） ====================
    "todo_write": ("", lambda a: "", False),
    "todo_read": ("", lambda a: "", False),
    
    # ==================== Plan Mode（无需权限检查） ====================
    "enter_plan_mode": ("", lambda a: "", False),
    "exit_plan_mode": ("", lambda a: "", False),
    "create_plan": ("", lambda a: "", False),
    "get_plan": ("", lambda a: "", False),
    
    # ==================== Tool Search（无需权限检查） ====================
    "tool_search": ("", lambda a: "", False),
    "list_tools": ("", lambda a: "", False),
    
    # ==================== LSP 代码智能（只读文件访问，需要路径边界检查） ====================
    "lsp_go_to_definition": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_find_references": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_hover": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_document_symbol": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_workspace_symbol": ("FILE_READ", lambda a: "", False),
    "lsp_go_to_implementation": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_prepare_call_hierarchy": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_incoming_calls": ("FILE_READ", lambda a: a.get("file_path", ""), True),
    "lsp_outgoing_calls": ("FILE_READ", lambda a: a.get("file_path", ""), True),
}

# 工具别名映射（兼容不同命名）
TOOL_ALIASES = {
    "ls": "list_directory",
    "cat": "read_file",
    "rm": "delete_file",
    "mkdir": "create_directory",
    "bash": "execute_shell",
    "shell": "execute_shell",
}


def get_tool_permission(tool_name: str, args: Dict[str, Any]) -> Optional[Tuple[Any, str]]:
    """
    获取工具的权限要求
    
    Args:
        tool_name: 工具名称
        args: 工具参数字典
    
    Returns:
        (权限类型枚举, 操作内容) 或 None（无需权限检查）
    """
    # 处理别名
    actual_tool_name = TOOL_ALIASES.get(tool_name, tool_name)
    
    if actual_tool_name not in TOOL_PERMISSION_MAPPING:
        # 未知工具（如 MCP 工具）：按 MCP_TOOL / UNKNOWN_TOOL 处理，交由权限层询问，不再默认放行
        PermissionType = _get_permission_type_enum()
        perm_type = PermissionType.MCP_TOOL if actual_tool_name.startswith("mcp__") else PermissionType.UNKNOWN_TOOL
        try:
            content = json.dumps(args, ensure_ascii=False)[:500]
        except Exception:
            content = ""
        return (perm_type, content)
    
    mapping_entry = TOOL_PERMISSION_MAPPING[actual_tool_name]
    
    # 兼容新旧格式
    if len(mapping_entry) == 3:
        perm_type_str, content_extractor, _ = mapping_entry
    else:
        perm_type_str, content_extractor = mapping_entry
    
    # 空权限类型表示无需权限检查
    if not perm_type_str:
        return None
    
    # 获取权限类型枚举
    PermissionType = _get_permission_type_enum()
    
    # 将字符串转换为枚举
    perm_type_map = {
        "FILE_READ": PermissionType.FILE_READ,
        "FILE_WRITE": PermissionType.FILE_WRITE,
        "FILE_DELETE": PermissionType.FILE_DELETE,
        "EXEC_COMMAND": PermissionType.EXEC_COMMAND,
        "NETWORK_FETCH": PermissionType.NETWORK_FETCH,
        "APP_LAUNCH": PermissionType.APP_LAUNCH,
        "CLIPBOARD_READ": PermissionType.CLIPBOARD_READ,
        "CLIPBOARD_WRITE": PermissionType.CLIPBOARD_WRITE,
        "SCREENSHOT": PermissionType.SCREENSHOT,
    }
    
    perm_type = perm_type_map.get(perm_type_str)
    if perm_type is None:
        logger.warning(f"[ToolPermission] Unknown permission type: {perm_type_str}")
        return None
    
    # 提取内容
    try:
        content = content_extractor(args)
    except Exception as e:
        logger.warning(f"[ToolPermission] Failed to extract content: {e}")
        content = ""
    
    return (perm_type, content)


def get_tool_permission_info(tool_name: str, args: Dict[str, Any]) -> PermissionCheckResult:
    """
    获取工具的完整权限信息
    
    Args:
        tool_name: 工具名称
        args: 工具参数字典
    
    Returns:
        PermissionCheckResult 包含完整权限信息
    """
    # 处理别名
    actual_tool_name = TOOL_ALIASES.get(tool_name, tool_name)
    
    if actual_tool_name not in TOOL_PERMISSION_MAPPING:
        # 未知工具（如 MCP 工具）：交由权限层询问（MCP_TOOL / UNKNOWN_TOOL）
        perm_type_str = "MCP_TOOL" if actual_tool_name.startswith("mcp__") else "UNKNOWN_TOOL"
        try:
            content = json.dumps(args, ensure_ascii=False)[:500]
        except Exception:
            content = ""
        return PermissionCheckResult(
            allowed=True,
            reason="Unknown tool - permission check required",
            permission_type=perm_type_str,
            content=content,
            target=content,
            decision_source="pending"
        )
    
    mapping_entry = TOOL_PERMISSION_MAPPING[actual_tool_name]
    
    # 兼容新旧格式
    if len(mapping_entry) == 3:
        perm_type_str, content_extractor, needs_path_check = mapping_entry
    else:
        perm_type_str, content_extractor = mapping_entry
        needs_path_check = False
    
    # 空权限类型表示无需权限检查
    if not perm_type_str:
        return PermissionCheckResult(
            allowed=True,
            reason="Tool does not require permission check",
            decision_source="no_restriction"
        )
    
    # 获取权限类型枚举
    PermissionType = _get_permission_type_enum()
    
    # 将字符串转换为枚举
    perm_type_map = {
        "FILE_READ": PermissionType.FILE_READ,
        "FILE_WRITE": PermissionType.FILE_WRITE,
        "FILE_DELETE": PermissionType.FILE_DELETE,
        "EXEC_COMMAND": PermissionType.EXEC_COMMAND,
        "NETWORK_FETCH": PermissionType.NETWORK_FETCH,
        "APP_LAUNCH": PermissionType.APP_LAUNCH,
        "CLIPBOARD_READ": PermissionType.CLIPBOARD_READ,
        "CLIPBOARD_WRITE": PermissionType.CLIPBOARD_WRITE,
        "SCREENSHOT": PermissionType.SCREENSHOT,
    }
    
    perm_type = perm_type_map.get(perm_type_str)
    
    # 提取内容
    try:
        content = content_extractor(args)
    except Exception as e:
        logger.warning(f"[ToolPermission] Failed to extract content: {e}")
        content = ""
    
    return PermissionCheckResult(
        allowed=True,  # 这里只返回信息，实际权限检查由 PermissionManager 完成
        permission_type=perm_type_str,
        content=content,
        target=content,
        decision_source="pending"  # 待后续检查
    )


def needs_path_boundary_check(tool_name: str) -> bool:
    """
    检查工具是否需要路径边界检查
    
    Args:
        tool_name: 工具名称
    
    Returns:
        True 如果需要路径边界检查
    """
    actual_tool_name = TOOL_ALIASES.get(tool_name, tool_name)
    
    if actual_tool_name not in TOOL_PERMISSION_MAPPING:
        return False
    
    mapping_entry = TOOL_PERMISSION_MAPPING[actual_tool_name]
    
    # 兼容新旧格式
    if len(mapping_entry) == 3:
        _, _, needs_check = mapping_entry
        return needs_check
    else:
        return False


def get_path_from_tool_args(tool_name: str, args: Dict[str, Any]) -> List[str]:
    """
    从工具参数中提取所有路径
    
    Args:
        tool_name: 工具名称
        args: 工具参数字典
    
    Returns:
        路径列表
    """
    actual_tool_name = TOOL_ALIASES.get(tool_name, tool_name)
    paths = []
    
    # 通用路径参数名
    path_keys = ["path", "file_path", "directory", "repo_path", "notebook_path", "source", "destination"]
    
    for key in path_keys:
        if key in args and args[key]:
            paths.append(args[key])
    
    # 特殊工具处理
    if actual_tool_name == "copy_file" or actual_tool_name == "move_file":
        if "source" in args:
            paths.append(args["source"])
        if "destination" in args:
            paths.append(args["destination"])
    
    return list(set(paths))  # 去重


def is_tool_restricted(tool_name: str) -> bool:
    """
    检查工具是否在已知权限映射表中

    （未知工具不再默认放行：由 get_tool_permission / get_tool_permission_info
    映射为 MCP_TOOL / UNKNOWN_TOOL，交由权限层询问。）
    """
    actual_tool_name = TOOL_ALIASES.get(tool_name, tool_name)
    return actual_tool_name in TOOL_PERMISSION_MAPPING


def get_all_restricted_tools() -> list:
    """
    获取所有需要权限检查的工具列表
    
    Returns:
        工具名称列表
    """
    return list(TOOL_PERMISSION_MAPPING.keys())
