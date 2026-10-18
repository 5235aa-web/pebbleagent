"""
权限管理器 - 规则驱动的工具授权

提供：
- 授权模式（manual / auto / readonly）
- 规则表（allow / deny / ask）
- 规则匹配（工具名 + 参数模式）
- 与 PermissionService 的会话信任集成

规则写法：``tool`` 或 ``tool:pattern``
- ``read_file``            整个工具
- ``execute_shell:git *``  命令前缀
- ``write_file:*.env``     路径或文件名匹配
"""

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from gateway.permission_service import (
    PermissionService,
    PermissionType,
    PermissionLevel,
    get_permission_service,
)

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    """授权模式"""
    MANUAL = "manual"        # 规则优先，未命中按风险等级决定
    AUTO = "auto"            # 全部放行
    READONLY = "readonly"    # 只读放行，写与执行一律拒绝


class PermissionBehavior(str, Enum):
    """规则效果"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# 旧配置值到新模式的映射
_LEGACY_MODES = {
    "default": PermissionMode.MANUAL,
    "autoAllow": PermissionMode.AUTO,
    "acceptEdits": PermissionMode.AUTO,
    "bypassPermissions": PermissionMode.AUTO,
    # dontAsk（旧版「不询问」）已废弃：不在映射表中，加载时回退 manual
}


@dataclass
class PermissionRule:
    """一条授权规则"""
    tool: str
    pattern: Optional[str] = None
    effect: PermissionBehavior = PermissionBehavior.ASK
    origin: str = "user"


@dataclass
class PermissionContext:
    """授权上下文"""
    mode: PermissionMode = PermissionMode.MANUAL
    allow_rules: List[PermissionRule] = field(default_factory=list)
    deny_rules: List[PermissionRule] = field(default_factory=list)
    ask_rules: List[PermissionRule] = field(default_factory=list)
    session_id: str = ""


class PermissionManager:
    """
    权限管理器

    规则优先级：deny → ask → allow → 模式默认
    """

    # 只读工具（readonly 模式下放行）
    # 网络只读取回放行；browser_download 会落盘，仍按写操作拒绝
    READONLY_TOOLS = {
        "read_file", "read_pdf", "list_directory", "get_file_info", "ls",
        "grep", "glob", "search_in_file", "search_files", "find_files",
        "git_status", "git_log", "git_diff", "get_env_var", "get_system_info",
        "list_processes", "list_skills", "load_skill", "todo_read", "get_plan",
        "tool_search", "list_tools",
        "web_search", "fetch_url", "browser_navigate",
        "lsp_go_to_definition", "lsp_find_references", "lsp_hover",
        "lsp_document_symbol", "lsp_workspace_symbol", "lsp_go_to_implementation",
        "lsp_prepare_call_hierarchy", "lsp_incoming_calls", "lsp_outgoing_calls",
    }

    # 需要拦截敏感目标的读类工具（参数里带具体文件或目录路径）
    READ_TARGET_TOOLS = {
        "read_file", "read_pdf", "search_in_file",
        "list_directory", "get_file_info", "ls",
        "search_files", "find_files", "grep", "glob",
    }

    # 工具面已被 YAML 白名单锁死的 agent：权限模式对其不生效，按 auto 处理
    # （敏感读取拦截不受影响，它在 auto 分支内同样生效）
    AGENT_MODE_OVERRIDES = {
        "chat_agent": PermissionMode.AUTO,
    }

    # 默认放行的工具
    DEFAULT_ALLOW = [
        "read_file", "read_pdf", "list_directory", "get_file_info", "ls",
        "grep", "glob", "search_in_file", "search_files", "find_files",
        "git_status", "git_log", "git_diff",
        "todo_read", "todo_write", "get_plan", "create_plan",
        "enter_plan_mode", "exit_plan_mode",
        "list_skills", "load_skill", "tool_search", "list_tools",
        "clarify",
        "get_env_var", "get_system_info", "list_processes",
        "execute_shell:git *", "execute_shell:ls", "execute_shell:dir",
        "execute_shell:python --version", "execute_shell:node --version",
        "execute_shell:npm --version",
    ]

    # 默认需要确认的工具与模式
    DEFAULT_ASK = [
        "execute_shell:rm *", "execute_shell:del *", "execute_shell:rmdir *",
        "execute_shell:sudo *", "execute_shell:chmod *", "execute_shell:chown *",
        "execute_shell:mkfs *", "execute_shell:dd *",
        "execute_shell:shutdown *", "execute_shell:reboot *",
        "execute_shell:taskkill *",
        "write_file:.env", "write_file:*.pem", "write_file:*.key",
        "write_file:.git/*", "write_file:*.secret", "write_file:credentials*",
        "delete_file", "kill_process", "set_env_var", "notebook_edit",
    ]

    def __init__(self, config_path: str = "permission_rules.json"):
        self.config_path = config_path
        self.context = PermissionContext()
        self._permission_service: Optional[PermissionService] = None
        self._load_rules()

    @property
    def permission_service(self) -> PermissionService:
        """懒加载权限服务"""
        if self._permission_service is None:
            self._permission_service = get_permission_service()
        return self._permission_service

    def _load_rules(self):
        """从配置文件加载规则"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as fh:
                    config = json.load(fh)

                raw_mode = config.get("mode", "manual")
                self.context.mode = _LEGACY_MODES.get(raw_mode)
                if self.context.mode is None:
                    try:
                        self.context.mode = PermissionMode(raw_mode)
                    except ValueError:
                        self.context.mode = PermissionMode.MANUAL

                for value in config.get("allow", []):
                    rule = self._parse_rule(value, PermissionBehavior.ALLOW)
                    if rule:
                        self.context.allow_rules.append(rule)
                for value in config.get("deny", []):
                    rule = self._parse_rule(value, PermissionBehavior.DENY)
                    if rule:
                        self.context.deny_rules.append(rule)
                for value in config.get("ask", []):
                    rule = self._parse_rule(value, PermissionBehavior.ASK)
                    if rule:
                        self.context.ask_rules.append(rule)

                logger.info(
                    "[PermissionManager] Loaded %d allow / %d deny / %d ask rules",
                    len(self.context.allow_rules),
                    len(self.context.deny_rules),
                    len(self.context.ask_rules),
                )
            except Exception as exc:
                logger.error("[PermissionManager] Failed to load permission rules: %s", exc)
                self._load_default_rules()
        else:
            self._load_default_rules()
            self._save_rules()

    def _load_default_rules(self):
        """加载默认规则"""
        for value in self.DEFAULT_ALLOW:
            rule = self._parse_rule(value, PermissionBehavior.ALLOW)
            if rule:
                self.context.allow_rules.append(rule)
        for value in self.DEFAULT_ASK:
            rule = self._parse_rule(value, PermissionBehavior.ASK)
            if rule:
                self.context.ask_rules.append(rule)

    def _parse_rule(self, value: str, effect: PermissionBehavior) -> Optional[PermissionRule]:
        """解析规则字符串：tool 或 tool:pattern"""
        if not value or not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        if ":" in raw:
            tool, pattern = raw.split(":", 1)
            tool = tool.strip()
            pattern = pattern.strip() or None
        else:
            tool, pattern = raw, None
        if not tool:
            return None
        return PermissionRule(tool=tool, pattern=pattern, effect=effect)

    def _match_rule(self, rule: PermissionRule, tool_name: str, content: Optional[str]) -> bool:
        """检查规则是否匹配"""
        if rule.tool != tool_name:
            return False
        if rule.pattern is None:
            return True
        if content is None:
            return False

        pattern = rule.pattern
        normalized = content.replace("\\", "/")

        # 命令前缀：'git *' 匹配 'git' 与 'git status'，不匹配 'gitk'
        if pattern.endswith(" *"):
            prefix = pattern[:-2]
            if content == prefix or content.startswith(prefix + " "):
                return True
            return False

        if fnmatch.fnmatch(normalized, pattern):
            return True
        base = normalized.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(base, pattern):
            return True
        return fnmatch.fnmatch(normalized, f"*/{pattern}")

    def _is_inline_code_exec(self, tool_name: str, content: Optional[str]) -> bool:
        """
        检测命令是否为内联代码执行。

        python -c / node -e / powershell -Command / bash -c 这类形态，
        命令前缀 allow 规则不得放行，需升级为用户询问。
        """
        if not content:
            return False
        if tool_name not in ("execute_shell", "powershell", "execute_code"):
            return False
        cmd = content.strip()
        if re.search(r"\b(python[23]?|node|perl|ruby|php|deno|bun|ts-node)\s+(-\w*c\b|--eval\b|-\w*e\b|-r\b)", cmd, re.IGNORECASE):
            return True
        if re.search(r"\b(powershell|pwsh)(\.exe)?\b[^|;&]*\s-(c|command|encodedcommand|enc|e)\b", cmd, re.IGNORECASE):
            return True
        if re.search(r"\b(bash|sh|zsh|dash|cmd)(\.exe)?\b[^|;&]*\s(/c|-c)\b", cmd, re.IGNORECASE):
            return True
        return False

    def _effective_mode(self, agent_name: Optional[str]) -> PermissionMode:
        """agent 级模式覆盖：工具面已由 YAML 白名单锁死的 agent 不受用户权限模式影响"""
        override = self.AGENT_MODE_OVERRIDES.get(agent_name or "")
        return override or self.context.mode

    def _sensitive_read_reason(self, tool_name: str, content: Optional[str]) -> Optional[str]:
        """
        敏感目标拦截（三种模式共用）。

        仅对参数里带路径的读类工具生效，命中高置信度敏感文件或目录时返回原因。
        敏感模式模块不可用时按 fail-closed 处理。
        """
        if tool_name not in self.READ_TARGET_TOOLS or not content:
            return None
        try:
            from agent_core.tool_permission import PathBoundaryChecker
        except ImportError as exc:
            logger.warning("[PermissionManager] sensitive-read check unavailable: %s", exc)
            return "sensitive-read check unavailable (fail-closed)"
        is_sensitive, why = PathBoundaryChecker.check_sensitive_read(content)
        return why if is_sensitive else None

    async def check_permission(
        self,
        tool_name: str,
        content: Optional[str] = None,
        target: Optional[str] = None,
        reason: str = "",
        agent_name: Optional[str] = None,
    ) -> tuple[bool, str]:
        """检查权限，必要时向用户发起请求"""
        mode = self._effective_mode(agent_name)
        if mode == PermissionMode.AUTO:
            sensitive_reason = self._sensitive_read_reason(tool_name, content)
            if sensitive_reason:
                logger.info("[PermissionManager] auto mode blocks sensitive read: %s", sensitive_reason)
                return False, f"sensitive read blocked: {sensitive_reason}"
            logger.info("[PermissionManager] auto mode, allowing %s", tool_name)
            return True, "auto mode"

        # deny 规则先于只读白名单：显式拒绝不被只读模式豁免
        for rule in self.context.deny_rules:
            if self._match_rule(rule, tool_name, content):
                logger.info("[PermissionManager] denied by rule: %s:%s", rule.tool, rule.pattern)
                return False, f"denied by rule: {rule.tool}"

        if mode == PermissionMode.READONLY:
            if tool_name not in self.READONLY_TOOLS:
                return False, f"readonly mode rejects {tool_name}"
            sensitive_reason = self._sensitive_read_reason(tool_name, content)
            if sensitive_reason:
                return False, f"readonly mode blocks sensitive read: {sensitive_reason}"
            return True, "readonly mode"

        # manual：敏感文件读取必须由用户确认，allow 规则不能直接放行
        sensitive_reason = self._sensitive_read_reason(tool_name, content)
        if sensitive_reason:
            detail = f"sensitive file: {sensitive_reason}"
            return await self._request_user_permission(
                tool_name=tool_name,
                content=content,
                target=target or content or "",
                reason=f"{reason} ({detail})" if reason else detail,
            )

        for rule in self.context.ask_rules:
            if self._match_rule(rule, tool_name, content):
                return await self._request_user_permission(
                    tool_name=tool_name,
                    content=content,
                    target=target or content or "",
                    reason=reason,
                )

        for rule in self.context.allow_rules:
            if self._match_rule(rule, tool_name, content):
                if self._is_inline_code_exec(tool_name, content):
                    logger.info("[PermissionManager] allow rule skipped for inline code: %s", rule.tool)
                    continue
                logger.info("[PermissionManager] allowed by rule: %s:%s", rule.tool, rule.pattern)
                return True, f"allowed by rule: {rule.tool}"

        perm_type = self._get_permission_type(tool_name)
        level = self.permission_service.get_permission_level(perm_type)
        if level == PermissionLevel.LOW:
            return True, "low risk"

        return await self._request_user_permission(
            tool_name=tool_name,
            content=content,
            target=target or content or "",
            reason=reason,
        )

    def check_permission_sync(
        self,
        tool_name: str,
        content: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        同步评估权限（不触发用户交互，供 RPC 预检使用）。

        命中 ask 规则或需要人工确认时返回 (False, "requires user confirmation")，
        由调用方决定是否走完整流程。
        """
        mode = self._effective_mode(agent_name)
        if mode == PermissionMode.AUTO:
            sensitive_reason = self._sensitive_read_reason(tool_name, content)
            if sensitive_reason:
                return False, f"sensitive read blocked: {sensitive_reason}"
            return True, "auto mode"

        # deny 规则先于只读白名单：显式拒绝不被只读模式豁免
        for rule in self.context.deny_rules:
            if self._match_rule(rule, tool_name, content):
                return False, f"denied by rule: {rule.tool}"

        if mode == PermissionMode.READONLY:
            if tool_name not in self.READONLY_TOOLS:
                return False, f"readonly mode rejects {tool_name}"
            sensitive_reason = self._sensitive_read_reason(tool_name, content)
            if sensitive_reason:
                return False, f"readonly mode blocks sensitive read: {sensitive_reason}"
            return True, "readonly mode"

        # manual：敏感文件读取需人工确认
        sensitive_reason = self._sensitive_read_reason(tool_name, content)
        if sensitive_reason:
            return False, f"requires user confirmation (sensitive file: {sensitive_reason})"

        for rule in self.context.ask_rules:
            if self._match_rule(rule, tool_name, content):
                return False, "requires user confirmation"
        for rule in self.context.allow_rules:
            if self._match_rule(rule, tool_name, content):
                if self._is_inline_code_exec(tool_name, content):
                    continue
                return True, f"allowed by rule: {rule.tool}"

        perm_type = self._get_permission_type(tool_name)
        level = self.permission_service.get_permission_level(perm_type)
        if level == PermissionLevel.LOW:
            return True, "low risk"
        return False, "requires user confirmation"

    def _get_permission_type(self, tool_name: str) -> PermissionType:
        """根据工具名称获取权限类型"""
        try:
            from agent_core.tool_permission import get_tool_permission
            result = get_tool_permission(tool_name, {})
            if result is not None:
                perm_type, _ = result
                if isinstance(perm_type, PermissionType):
                    return perm_type
        except Exception as exc:
            logger.debug("[PermissionManager] tool_permission lookup failed for %s: %s", tool_name, exc)

        mapping = {
            "read_file": PermissionType.FILE_READ,
            "read_pdf": PermissionType.FILE_READ,
            "list_directory": PermissionType.FILE_READ,
            "get_file_info": PermissionType.FILE_READ,
            "ls": PermissionType.FILE_READ,
            "grep": PermissionType.FILE_READ,
            "glob": PermissionType.FILE_READ,
            "search_in_file": PermissionType.FILE_READ,
            "search_files": PermissionType.FILE_READ,
            "find_files": PermissionType.FILE_READ,
            "write_file": PermissionType.FILE_WRITE,
            "edit_file": PermissionType.FILE_WRITE,
            "notebook_edit": PermissionType.FILE_WRITE,
            "delete_file": PermissionType.FILE_DELETE,
            "copy_file": PermissionType.FILE_WRITE,
            "move_file": PermissionType.FILE_WRITE,
            "execute_code": PermissionType.EXEC_COMMAND,
            "execute_shell": PermissionType.EXEC_COMMAND,
            "powershell": PermissionType.EXEC_COMMAND,
            "kill_process": PermissionType.EXEC_COMMAND,
            "set_env_var": PermissionType.EXEC_COMMAND,
            "browser_navigate": PermissionType.NETWORK_FETCH,
            "browser_download": PermissionType.NETWORK_FETCH,
            "fetch_url": PermissionType.NETWORK_FETCH,
            "web_search": PermissionType.NETWORK_FETCH,
        }
        return mapping.get(tool_name, PermissionType.UNKNOWN_TOOL)

    async def _request_user_permission(
        self,
        tool_name: str,
        content: Optional[str],
        target: str,
        reason: str,
    ) -> tuple[bool, str]:
        """请求用户权限"""
        perm_type = self._get_permission_type(tool_name)

        if self.context.session_id:
            if await self.permission_service.check_permission(perm_type, self.context.session_id):
                logger.info("[PermissionManager] session %s trusted, auto-approving %s", self.context.session_id, tool_name)
                return True, "trusted session"

        request = await self.permission_service.request_permission(
            permission_type=perm_type,
            target=target,
            reason=reason or f"调用工具 {tool_name}",
            session_id=self.context.session_id,
        )
        if not request:
            return False, "failed to create permission request"

        decision = await self.permission_service.wait_for_decision(request.request_id, timeout=120)
        if decision is None:
            return False, "permission request timed out"

        return decision.approved, "user approved" if decision.approved else "user denied"

    def add_rule(self, rule: PermissionRule):
        """添加规则"""
        if rule.effect == PermissionBehavior.ALLOW:
            self.context.allow_rules.append(rule)
        elif rule.effect == PermissionBehavior.DENY:
            self.context.deny_rules.append(rule)
        else:
            self.context.ask_rules.append(rule)
        self._save_rules()

    def remove_rule(self, rule: PermissionRule) -> bool:
        """移除规则"""
        if rule.effect == PermissionBehavior.ALLOW:
            rules = self.context.allow_rules
        elif rule.effect == PermissionBehavior.DENY:
            rules = self.context.deny_rules
        else:
            rules = self.context.ask_rules
        if rule in rules:
            rules.remove(rule)
            self._save_rules()
            return True
        return False

    def _save_rules(self):
        """保存规则到配置文件"""
        config = {
            "mode": self.context.mode.value,
            "allow": [self._rule_to_string(rule) for rule in self.context.allow_rules],
            "deny": [self._rule_to_string(rule) for rule in self.context.deny_rules],
            "ask": [self._rule_to_string(rule) for rule in self.context.ask_rules],
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(config, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error("[PermissionManager] Failed to save permission rules: %s", exc)

    def _rule_to_string(self, rule: PermissionRule) -> str:
        """规则转字符串"""
        if rule.pattern:
            return f"{rule.tool}:{rule.pattern}"
        return rule.tool

    def set_mode(self, mode: PermissionMode):
        """设置授权模式"""
        self.context.mode = mode
        self._save_rules()
        logger.info("[PermissionManager] Mode set to: %s", mode.value)

    def get_mode(self) -> PermissionMode:
        """获取当前授权模式"""
        return self.context.mode

    def set_session_id(self, session_id: str):
        """设置会话 ID"""
        self.context.session_id = session_id

    def get_all_rules(self) -> Dict[str, List[str]]:
        """获取所有规则"""
        return {
            "mode": self.context.mode.value,
            "allow": [self._rule_to_string(rule) for rule in self.context.allow_rules],
            "deny": [self._rule_to_string(rule) for rule in self.context.deny_rules],
            "ask": [self._rule_to_string(rule) for rule in self.context.ask_rules],
        }

    def clear_rules(self):
        """清空所有规则"""
        self.context.allow_rules.clear()
        self.context.deny_rules.clear()
        self.context.ask_rules.clear()
        self._save_rules()


# 全局单例
_permission_manager: Optional[PermissionManager] = None


def get_permission_manager() -> PermissionManager:
    """获取权限管理器单例"""
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager()
    return _permission_manager
