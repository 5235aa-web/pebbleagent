"""
PowerShell 命令验证器（2026-10-17 安全修复）

背景：powershell 工具此前只有网络行为扫描（code_network_guardV2），
缺少与 execute_shell（BashValidator）同级的破坏性/路径校验，
存在"换个入口绕过校验"的问题（安全评审问题 2）。

检查项（与 BashValidator 对齐）：
1. 系统级破坏命令（磁盘/启动配置/备份删除/关机等）→ blocked
2. 路径越界（项目外路径，含 $env: 变量展开；对齐 BashValidator.path_check）→ blocked
3. 危险操作（删除/注册表/权限修改/动态执行等）→ warning（放行 + 审计）

使用方式:
    validator = PowerShellValidator(project_path="/path/to/project")
    report = validator.validate("Remove-Item -Recurse -Force C:\\")
    if report.blocked:
        raise PermissionError(report.block_reason)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class PowerShellValidationReport:
    """PowerShell 命令验证统一报告"""
    command: str
    blocked: bool = False                # 是否应完全阻止执行
    block_reason: str = ""               # 阻止原因
    warnings: List[str] = field(default_factory=list)  # 警告列表
    target_paths: List[str] = field(default_factory=list)  # 涉及的文件路径
    path_violations: List[str] = field(default_factory=list)  # 越界路径

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "warnings": self.warnings,
            "target_paths": self.target_paths,
            "path_violations": self.path_violations,
        }


class PowerShellValidator:
    """PowerShell / cmd 命令验证器（与 BashValidator 同级）"""

    # 立即阻止（🔴）：系统级毁灭操作，无论目标
    _DESTRUCTIVE_BLOCK: List[Tuple[str, str]] = [
        (r'\bFormat-Volume\b', "磁盘格式化 (Format-Volume)"),
        (r'\bClear-Disk\b', "磁盘清除 (Clear-Disk)"),
        (r'\bInitialize-Disk\b', "磁盘初始化 (Initialize-Disk)"),
        (r'\bdiskpart\b', "磁盘分区工具 (diskpart)"),
        (r'\bbcdedit\b', "启动配置修改 (bcdedit)"),
        (r'\bcipher\s+/w\b', "磁盘空闲空间擦除 (cipher /w)"),
        (r'\bmkfs(\.\w+)?\b', "文件系统格式化 (mkfs)"),
        (r'\b(vssadmin|wbadmin)\b[^\r\n]*\bdelete\b', "备份/快照删除 (vssadmin/wbadmin)"),
        (r'\b(Stop-Computer|Restart-Computer)\b', "关机/重启计算机"),
        (r'\bformat\s+[A-Za-z]:', "格式化驱动器 (format X:)"),
        (r'\bdd\s+if=[^\r\n]*\bof=/dev/', "直接写入磁盘设备 (dd of=/dev/)"),
        (r'\bshutdown\b[^\r\n]*\b(/s|/r|/p)\b', "系统关机/重启 (shutdown)"),
    ]

    # 警告但允许（🟡）
    _DESTRUCTIVE_WARN: List[Tuple[str, str]] = [
        (r'\bRemove-Item\b', "删除操作 (Remove-Item)"),
        (r'\bdel\b|\berase\b', "文件删除 (del/erase)"),
        (r'\brd\b|\brmdir\b', "目录删除 (rd/rmdir)"),
        (r'\b(Set-ExecutionPolicy|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty)\b',
         "执行策略/注册表修改"),
        (r'\b(Stop-Process|Stop-Service)\b', "停止进程/服务"),
        (r'\b(icacls|takeown|Set-Acl)\b', "权限修改 (icacls/takeown/Set-Acl)"),
        (r'\b(Invoke-Expression|iex)\b', "动态代码执行 (Invoke-Expression/IEX)"),
        (r'\b(Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\b', "网络请求 (Invoke-WebRequest)"),
        (r'\b(reg\s+(add|delete)|REG\s+(ADD|DELETE))\b', "注册表修改 (reg)"),
        (r'\bnet\s+user\b', "用户管理 (net user)"),
        (r'\brm\s+-[a-zA-Z]*[rf]', "递归/强制删除 (rm -rf)"),
    ]

    # 路径提取（对齐 BashValidator._path_check 的提取精神）
    _PATH_PATTERNS: List[str] = [
        r'[A-Za-z]:[\\/][^\s;|&<>`\'"]*',                            # C:\... / C:/...
        r'\\\\[A-Za-z0-9_.\-]+\\[^\s;|&<>`\'"]*',                    # \\server\share
        r'\$\{env:[A-Za-z_][A-Za-z0-9_]*\}(?:[\\/][^\s;|&<>`\'"]*)?',    # ${env:X}\...
        r'\$env:[A-Za-z_][A-Za-z0-9_]*(?:[\\/][^\s;|&<>`\'"]*)?',        # $env:X\...
        r'(?:^|\s)(/[^\s;|&<>`\'"]*)',                               # /xxx（Unix 风格/PSDrive 根）
        r'(?:^|\s)(~[\\/]?[^\s;|&<>`\'"]*)',                         # ~ / ~/...
        r'(?:^|\s)(\.{1,2}[\\/][^\s;|&<>`\'"]*)',                    # .\ ..\ ./
    ]

    def __init__(self, project_path: str = ""):
        self._project_path = os.path.abspath(project_path or os.getcwd())
        self._path_whitelist: List[str] = []

    def set_path_whitelist(self, paths: List[str]) -> None:
        """设置额外白名单路径"""
        if not paths:
            return
        self._path_whitelist = [os.path.abspath(os.path.expandvars(p)) for p in paths if p]

    # ==================== 主入口 ====================

    def validate(self, command: str) -> PowerShellValidationReport:
        """运行全部检查，返回统一报告"""
        report = PowerShellValidationReport(command=command)
        self._destructive_check(command, report)
        self._path_check(command, report)
        return report

    # ==================== 1. 破坏性检查 ====================

    def _destructive_check(self, command: str, report: PowerShellValidationReport) -> None:
        """两级：🔴 blocked 立即阻止；🟡 warning 记录不阻止"""
        for pattern, reason in self._DESTRUCTIVE_BLOCK:
            if re.search(pattern, command, re.IGNORECASE):
                report.blocked = True
                report.block_reason = reason
                report.warnings.append(f"🔴 {reason}")
                return

        for pattern, reason in self._DESTRUCTIVE_WARN:
            if re.search(pattern, command, re.IGNORECASE):
                report.warnings.append(f"🟡 {reason}")

    # ==================== 2. 路径检查 ====================

    def _path_check(self, command: str, report: PowerShellValidationReport) -> None:
        """提取命令中的路径并做边界检查（项目外 → blocked，对齐 BashValidator）"""
        paths: List[str] = []
        for pat in self._PATH_PATTERNS:
            for m in re.finditer(pat, command):
                p = m.group(1).strip() if m.lastindex else m.group(0).strip()
                if p:
                    paths.append(p)

        # 去重保序
        report.target_paths = list(dict.fromkeys(paths))

        for p in report.target_paths:
            if self._is_path_safe(p):
                continue
            report.path_violations.append(p)
            report.warnings.append(f"🔴 路径越界: {p}")
            if not report.blocked:
                report.blocked = True
                report.block_reason = f"命令访问了项目目录外的路径: {p}"

    def _is_path_safe(self, path: str) -> bool:
        """路径是否安全：项目内 / 用户白名单 / 系统临时目录"""
        # 根目录永远越界（'/'、'\'、'C:\' 等盘根由通用逻辑覆盖）
        stripped = path.strip().rstrip("\\/")
        if stripped in ("", "/") or (len(stripped) == 2 and stripped[1] == ":"):
            return False
        try:
            expanded = self._expand_ps_vars(path)
            # PowerShell 的 ~ 是用户主目录（不是项目内相对路径）
            if expanded.startswith("~"):
                expanded = os.path.expanduser(expanded)
            # 相对路径按项目解析
            if not os.path.isabs(expanded):
                expanded = os.path.join(self._project_path, expanded)
            abs_path = os.path.realpath(os.path.abspath(expanded))
            abs_project = os.path.realpath(os.path.abspath(self._project_path))

            # 项目目录内
            if self._is_subpath(abs_path, abs_project):
                return True

            # 用户白名单
            for wp in self._path_whitelist:
                wpe = os.path.realpath(os.path.abspath(os.path.expanduser(os.path.expandvars(wp))))
                if self._is_subpath(abs_path, wpe):
                    return True

            # 系统临时目录（对齐 BashValidator 的 /tmp/ 白名单）
            for sys_tmp in ("%TEMP%", "%TMP%"):
                t = os.path.expandvars(sys_tmp)
                if t and t != sys_tmp and self._is_subpath(abs_path, os.path.realpath(t)):
                    return True

            return False
        except Exception:
            return False

    @staticmethod
    def _expand_ps_vars(p: str) -> str:
        """$env:XXX / ${env:XXX} → %XXX%（交由 os.path.expandvars 展开）"""
        p = re.sub(r'\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}', r'%\1%', p)
        p = re.sub(r'\$env:([A-Za-z_][A-Za-z0-9_]*)', r'%\1%', p)
        return os.path.expandvars(p)

    @staticmethod
    def _is_subpath(child: str, parent: str) -> bool:
        """组件级包含判断（commonpath），避免前缀穿越"""
        try:
            common = os.path.commonpath([os.path.normcase(child), os.path.normcase(parent)])
            return common == os.path.normcase(parent)
        except ValueError:
            return False


# ==================== 便捷函数 ====================

def get_powershell_validator(project_path: str = "") -> PowerShellValidator:
    """获取 PowerShellValidator 实例"""
    return PowerShellValidator(project_path)
