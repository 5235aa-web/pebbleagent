"""
权限管理器 - 权限规则系统

提供：
- 权限模式管理（default/acceptEdits/bypass/dontAsk）
- 权限规则管理（allow/deny/ask）
- 权限检查流程
- 与现有PermissionService集成
"""
import json
import os
import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Any, Callable
from pathlib import Path
from datetime import datetime

from gateway.permission_service import (
    PermissionService, 
    PermissionType, 
    PermissionLevel,
    get_permission_service
)

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    """权限模式"""
    DEFAULT = "default"           # 默认：按规则检查，需要时弹窗询问用户
    AUTO_ALLOW = "autoAllow"      # 自动允许：所有权限请求自动同意，不弹窗
    ACCEPT_EDITS = "acceptEdits"  # 自动接受编辑
    BYPASS = "bypassPermissions"  # 绕过所有检查
    DONT_ASK = "dontAsk"          # 不询问，自动拒绝

class PermissionBehavior(str, Enum):
    """权限行为"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

@dataclass
class PermissionRule:
    """权限规则"""
    tool_name: str              # 工具名称（如 "Bash", "Write"）
    rule_content: Optional[str] = None  # 规则内容（如 "npm:*", ".env"）
    behavior: PermissionBehavior = PermissionBehavior.ASK
    source: str = "userSettings"  # 规则来源

@dataclass
class PermissionContext:
    """权限上下文"""
    mode: PermissionMode = PermissionMode.DEFAULT
    allow_rules: List[PermissionRule] = field(default_factory=list)
    deny_rules: List[PermissionRule] = field(default_factory=list)
    ask_rules: List[PermissionRule] = field(default_factory=list)
    session_id: str = ""

class PermissionManager:
    """
    权限管理器
    
    权限规则系统，提供：
    - 分层规则优先级：deny → ask → allow
    - 多种权限模式
    - 与现有PermissionService集成
    """
    
    # 工具名称映射（兼容别名）
    TOOL_ALIASES = {
        "execute_code": "Bash",
        "execute_bash": "Bash",
        "execute_python": "Bash",
        "execute_shell": "Bash",
        "execute_javascript": "Bash",
        "read_file": "Read",
        "write_file": "Write",
        "edit_file": "Edit",
        "search_files": "Grep",
        "search_content": "Grep",
        "search_codebase": "Grep",
        "ls": "LS",
        "list_directory": "LS",
        "glob": "Grep",
        "delegate_to_agent": "Agent",
        "browser_goto": "Browser",
        "take_screenshot": "Screenshot",
        "clipboard_get": "Clipboard",
        "clipboard_set": "Clipboard",
    }
    
    # 危险操作默认需要询问
    DEFAULT_ASK_PATTERNS = {
        "Bash": ["rm:*", "sudo:*", "chmod:*", "chown:*", "mkfs:*", "dd:*", "shutdown:*", "reboot:*"],
        "Write": [".env", "*.pem", "*.key", ".git/*", ".ssh/*", "*.secret", "credentials*"],
        "Delete": ["*"],  # 删除操作默认询问
    }
    
    # 默认允许的安全操作
    DEFAULT_ALLOW_PATTERNS = {
        "Read": ["*"],  # 读取操作默认允许
        "LS": ["*"],    # 列目录默认允许
        "Grep": ["*"],  # 搜索默认允许
        "Bash": ["ls:*", "cat:*", "git:*", "npm:*", "node:*", "python:*", "pip:*"],
    }
    
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
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                self.context.mode = PermissionMode(config.get("mode", "default"))
                
                for rule_data in config.get("allow", []):
                    rule = self._parse_rule(rule_data, PermissionBehavior.ALLOW)
                    if rule:
                        self.context.allow_rules.append(rule)
                
                for rule_data in config.get("deny", []):
                    rule = self._parse_rule(rule_data, PermissionBehavior.DENY)
                    if rule:
                        self.context.deny_rules.append(rule)
                
                for rule_data in config.get("ask", []):
                    rule = self._parse_rule(rule_data, PermissionBehavior.ASK)
                    if rule:
                        self.context.ask_rules.append(rule)
                
                logger.info(f"[PermissionManager] Loaded {len(self.context.allow_rules)} allow rules, "
                           f"{len(self.context.deny_rules)} deny rules, "
                           f"{len(self.context.ask_rules)} ask rules")
            except Exception as e:
                logger.error(f"[PermissionManager] Failed to load permission rules: {e}")
                self._load_default_rules()
        else:
            # 首次运行，加载默认规则
            self._load_default_rules()
            self._save_rules()
    
    def _load_default_rules(self):
        """加载默认规则"""
        # 默认允许
        for tool, patterns in self.DEFAULT_ALLOW_PATTERNS.items():
            for pattern in patterns:
                self.context.allow_rules.append(PermissionRule(
                    tool_name=tool,
                    rule_content=pattern if pattern != "*" else None,
                    behavior=PermissionBehavior.ALLOW
                ))
        
        # 默认询问
        for tool, patterns in self.DEFAULT_ASK_PATTERNS.items():
            for pattern in patterns:
                self.context.ask_rules.append(PermissionRule(
                    tool_name=tool,
                    rule_content=pattern if pattern != "*" else None,
                    behavior=PermissionBehavior.ASK
                ))
    
    def _parse_rule(self, rule_str: str, behavior: PermissionBehavior) -> Optional[PermissionRule]:
        """解析规则字符串"""
        # 格式: "Tool" 或 "Tool(content)"
        if '(' in rule_str and rule_str.endswith(')'):
            idx = rule_str.index('(')
            tool_name = rule_str[:idx]
            content = rule_str[idx+1:-1]  # 去掉括号
            if content == '*':
                content = None
            return PermissionRule(tool_name=tool_name, rule_content=content, behavior=behavior)
        else:
            return PermissionRule(tool_name=rule_str, rule_content=None, behavior=behavior)
    
    def _match_rule(self, rule: PermissionRule, tool_name: str, content: Optional[str]) -> bool:
        """检查规则是否匹配"""
        # 工具名称匹配（考虑别名）
        normalized_tool = self.TOOL_ALIASES.get(tool_name, tool_name)
        if rule.tool_name != normalized_tool and rule.tool_name != tool_name:
            return False
        
        # 如果规则没有内容，匹配整个工具
        if rule.rule_content is None:
            return True
        
        # 如果规则有内容但没有提供content，不匹配
        if content is None:
            return False
        
        # 通配符匹配
        rule_content = rule.rule_content
        if rule_content.endswith(':*'):
            # 前缀匹配，如 "npm:*"
            prefix = rule_content[:-2]
            return content.startswith(prefix)
        elif rule_content.startswith('*') and rule_content.endswith('*'):
            # 包含匹配
            return rule_content[1:-1] in content
        elif '*' in rule_content:
            # 简单通配符
            import fnmatch
            return fnmatch.fnmatch(content, rule_content)
        else:
            # 精确匹配
            return content == rule_content

    def _is_inline_code_exec(self, tool_name: str, content: Optional[str]) -> bool:
        """
        检测命令是否为"内联代码执行"（2026-10-16 安全加固）。

        python -c / node -e / perl -e / powershell -Command / bash -c 这类形态，
        前缀 allow 规则（如 Bash(python:*)）不得放行，需升级为用户询问。
        """
        if not content:
            return False
        normalized = self.TOOL_ALIASES.get(tool_name, tool_name)
        if normalized != "Bash":
            return False
        cmd = content.strip()
        # 解释器 + 内联标志（紧跟解释器名，如 python -c "..."/ node -e "..."）
        if re.search(r'\b(python[23]?|node|perl|ruby|php|deno|bun|ts-node)\s+(-\w*c\b|--eval\b|-\w*e\b|-r\b)', cmd, re.IGNORECASE):
            return True
        # powershell -Command / -EncodedCommand / -c
        if re.search(r'\b(powershell|pwsh)(\.exe)?\b[^|;&]*\s-(c|command|encodedcommand|enc|e)\b', cmd, re.IGNORECASE):
            return True
        # shell -c（bash/sh/zsh/dash）与 cmd /c
        if re.search(r'\b(bash|sh|zsh|dash|cmd)(\.exe)?\b[^|;&]*\s(/c|-c)\b', cmd, re.IGNORECASE):
            return True
        return False
    
    async def check_permission(
        self,
        tool_name: str,
        content: Optional[str] = None,
        target: Optional[str] = None,
        reason: str = "",
    ) -> tuple[bool, str]:
        """
        检查权限
        
        Args:
            tool_name: 工具名称
            content: 操作内容（如命令、文件路径）
            target: 目标（用于显示）
            reason: AI解释的原因
        
        Returns:
            (allowed, message)
        """
        # 1. AutoAllow模式：直接允许所有请求，不弹窗询问
        if self.context.mode == PermissionMode.AUTO_ALLOW:
            logger.info(f"[PermissionManager] AutoAllow mode, allowing {tool_name}")
    

            return True, "Auto-allowed (autoAllow mode)"
        
        # 2. Bypass模式：直接允许
        if self.context.mode == PermissionMode.BYPASS:
            logger.info(f"[PermissionManager] Bypass mode, allowing {tool_name}")
            return True, "Bypass mode enabled"
        
        # 3. 检查Deny规则（优先级最高）
        for rule in self.context.deny_rules:
            if self._match_rule(rule, tool_name, content):
                logger.info(f"[PermissionManager] Permission denied by rule: {rule.tool_name}({rule.rule_content})")
        
                return False, f"Permission denied by rule: {rule.tool_name}"
        
        # 4. 检查Ask规则
        for rule in self.context.ask_rules:
            if self._match_rule(rule, tool_name, content):
                return await self._request_user_permission(
                    tool_name=tool_name,
                    content=content,
                    target=target or content or "",
                    reason=reason
                )
        
        # 5. 检查Allow规则
        for rule in self.context.allow_rules:
            if self._match_rule(rule, tool_name, content):
                # 内联代码执行不走前缀 allow 规则（防 "Bash(python:*)" 放行任意代码执行）
                if self._is_inline_code_exec(tool_name, content):
                    logger.info(f"[PermissionManager] Allow rule skipped for inline-code command: {rule.tool_name}")
                    continue
                logger.info(f"[PermissionManager] Permission allowed by rule: {rule.tool_name}({rule.rule_content})")
                return True, f"Permission allowed by rule: {rule.tool_name}"
        
        # 6. DontAsk模式：自动拒绝
        if self.context.mode == PermissionMode.DONT_ASK:
    
            return False, "Auto-denied (dontAsk mode)"
        
        # 7. AcceptEdits模式：仅文件编辑操作自动允许（不含命令执行/代码执行）
        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            if tool_name in ["Write", "Edit", "write_file", "edit_file", "notebook_edit"]:
        
                return True, "Auto-accepted (acceptEdits mode)"
        
        # 8. 默认行为：根据危险等级决定
        perm_type = self._get_permission_type(tool_name)
        level = self.permission_service.get_permission_level(perm_type)
        

        
        # 低危险等级自动允许
        if level == PermissionLevel.LOW:
    
            return True, "Auto-allowed (low risk)"
        
        # 中等及以上危险等级需要询问
        return await self._request_user_permission(
            tool_name=tool_name,
            content=content,
            target=target or content or "",
            reason=reason
        )

    def check_permission_sync(
        self,
        tool_name: str,
        content: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        同步评估权限（不触发用户交互，供 RPC 预检使用）。

        与 check_permission 的差异：命中 ask 规则或需要人工确认的场景返回
        (False, "requires user confirmation")，由调用方决定是否走完整流程。
        """
        if self.context.mode == PermissionMode.AUTO_ALLOW:
            return True, "Auto-allowed (autoAllow mode)"
        if self.context.mode == PermissionMode.BYPASS:
            return True, "Bypass mode enabled"
        for rule in self.context.deny_rules:
            if self._match_rule(rule, tool_name, content):
                return False, f"Permission denied by rule: {rule.tool_name}"
        for rule in self.context.ask_rules:
            if self._match_rule(rule, tool_name, content):
                return False, "Requires user confirmation (ask rule)"
        for rule in self.context.allow_rules:
            if self._match_rule(rule, tool_name, content):
                if self._is_inline_code_exec(tool_name, content):
                    continue
                return True, f"Permission allowed by rule: {rule.tool_name}"
        if self.context.mode == PermissionMode.DONT_ASK:
            return False, "Auto-denied (dontAsk mode)"
        if self.context.mode == PermissionMode.ACCEPT_EDITS:
            if tool_name in ["Write", "Edit", "write_file", "edit_file", "notebook_edit"]:
                return True, "Auto-accepted (acceptEdits mode)"
        perm_type = self._get_permission_type(tool_name)
        level = self.permission_service.get_permission_level(perm_type)
        if level == PermissionLevel.LOW:
            return True, "Auto-allowed (low risk)"
        return False, "Requires user confirmation"

    def _get_permission_type(self, tool_name: str) -> PermissionType:
        """根据工具名称获取权限类型"""
        mapping = {
            "Read": PermissionType.FILE_READ,
            "read_file": PermissionType.FILE_READ,
            "ls": PermissionType.FILE_READ,
            "LS": PermissionType.FILE_READ,
            "list_directory": PermissionType.FILE_READ,
            "Write": PermissionType.FILE_WRITE,
            "write_file": PermissionType.FILE_WRITE,
            "Edit": PermissionType.FILE_WRITE,
            "edit_file": PermissionType.FILE_WRITE,
            "Delete": PermissionType.FILE_DELETE,
            "Bash": PermissionType.EXEC_COMMAND,
            "execute_code": PermissionType.EXEC_COMMAND,
            "execute_python": PermissionType.EXEC_COMMAND,
            "execute_shell": PermissionType.EXEC_COMMAND,
            "Browser": PermissionType.NETWORK_FETCH,
            "browser_goto": PermissionType.NETWORK_FETCH,
            "Agent": PermissionType.APP_LAUNCH,
            "delegate_to_agent": PermissionType.APP_LAUNCH,
            "Screenshot": PermissionType.SCREENSHOT,
            "take_screenshot": PermissionType.SCREENSHOT,
            "Clipboard": PermissionType.CLIPBOARD_READ,
            "clipboard_get": PermissionType.CLIPBOARD_READ,
            "clipboard_set": PermissionType.CLIPBOARD_WRITE,
        }
        return mapping.get(tool_name, PermissionType.EXEC_COMMAND)
    
    async def _request_user_permission(
        self,
        tool_name: str,
        content: Optional[str],
        target: str,
        reason: str
    ) -> tuple[bool, str]:
        """请求用户权限"""
        perm_type = self._get_permission_type(tool_name)

        # 检查是否已信任此会话
        if self.context.session_id:
            if await self.permission_service.check_permission(perm_type, self.context.session_id):
                logger.info(f"[PermissionManager] Session {self.context.session_id} already trusted, auto-approving {tool_name}")
                return True, "Auto-approved (trusted session)"

        # 创建权限请求
        request = await self.permission_service.request_permission(
            permission_type=perm_type,
            target=target,
            reason=reason or f"AI wants to use {tool_name}",
            session_id=self.context.session_id
        )
        
        if not request:
            return False, "Failed to create permission request"
        
        # 等待用户决策
        decision = await self.permission_service.wait_for_decision(request.request_id, timeout=120)
        

        
        if decision is None:
            return False, "Permission request timed out"
        
        return decision.approved, "User approved" if decision.approved else "User denied"
    
    def add_rule(self, rule: PermissionRule):
        """添加规则"""
        if rule.behavior == PermissionBehavior.ALLOW:
            self.context.allow_rules.append(rule)
        elif rule.behavior == PermissionBehavior.DENY:
            self.context.deny_rules.append(rule)
        else:
            self.context.ask_rules.append(rule)
        self._save_rules()
    
    def remove_rule(self, rule: PermissionRule) -> bool:
        """移除规则"""
        rule_list = None
        if rule.behavior == PermissionBehavior.ALLOW:
            rule_list = self.context.allow_rules
        elif rule.behavior == PermissionBehavior.DENY:
            rule_list = self.context.deny_rules
        else:
            rule_list = self.context.ask_rules
        
        if rule in rule_list:
            rule_list.remove(rule)
            self._save_rules()
            return True
        return False
    
    def _save_rules(self):
        """保存规则到配置文件"""
        config = {
            "mode": self.context.mode.value,
            "allow": [self._rule_to_string(r) for r in self.context.allow_rules],
            "deny": [self._rule_to_string(r) for r in self.context.deny_rules],
            "ask": [self._rule_to_string(r) for r in self.context.ask_rules],
        }
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[PermissionManager] Failed to save permission rules: {e}")
    
    def _rule_to_string(self, rule: PermissionRule) -> str:
        """规则转字符串"""
        if rule.rule_content:
            return f"{rule.tool_name}({rule.rule_content})"
        return rule.tool_name
    
    def set_mode(self, mode: PermissionMode):
        """设置权限模式"""
        self.context.mode = mode
        self._save_rules()
        logger.info(f"[PermissionManager] Mode set to: {mode.value}")
        # 调试日志：打印当前权限模式
        mode_desc = {
            PermissionMode.DEFAULT: "默认模式（按规则检查，需要时弹窗询问）",
            PermissionMode.AUTO_ALLOW: "自动允许模式（所有请求自动同意，不弹窗）",
            PermissionMode.ACCEPT_EDITS: "自动接受编辑模式",
            PermissionMode.BYPASS: "绕过所有检查模式",
            PermissionMode.DONT_ASK: "不询问自动拒绝模式",
        }
        print(f"\n{'='*60}")
        print(f"[PermissionManager] 🔐 权限模式已切换: {mode_desc.get(mode, mode.value)}")
        print(f"{'='*60}\n")
    
    def get_mode(self) -> PermissionMode:
        """获取当前权限模式"""
        return self.context.mode
    
    def set_session_id(self, session_id: str):
        """设置会话ID"""
        self.context.session_id = session_id
    
    def get_all_rules(self) -> Dict[str, List[str]]:
        """获取所有规则"""
        return {
            "mode": self.context.mode.value,
            "allow": [self._rule_to_string(r) for r in self.context.allow_rules],
            "deny": [self._rule_to_string(r) for r in self.context.deny_rules],
            "ask": [self._rule_to_string(r) for r in self.context.ask_rules],
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