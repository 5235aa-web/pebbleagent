"""
PowerShell 执行工具

Windows 原生 PowerShell 命令执行，替代 bash/execute_shell
"""
from .base_tool import BaseTool
from typing import Dict, Any, Optional
import logging
import asyncio
import subprocess
import os

logger = logging.getLogger(__name__)


class PowerShellTool(BaseTool[Dict[str, Any], str]):
    """
    PowerShell 命令执行工具
    
    Windows 原生 PowerShell 执行，支持：
    - PowerShell cmdlets
    - Git, npm, docker 等命令行工具
    - 脚本执行
    """
    
    name = "powershell"
    description = "执行 PowerShell 命令（Windows 原生）"
    input_params = ["command", "timeout", "run_in_background"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "PowerShell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in milliseconds (default 60000)"},
            "run_in_background": {"type": "boolean", "description": "Run command in background (non-blocking)"}
        },
        "required": ["command"]
    }
    requires_permission = True
    danger_level = "high"
    parallel_safe = False
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        command = kwargs.get('command')
        if not command:
            raise ValueError("command parameter is required")
        
        return {
            'command': str(command),
            'timeout': int(kwargs.get('timeout', 60000)),
            'run_in_background': bool(kwargs.get('run_in_background', False))
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """执行 PowerShell 命令"""
        command = input_data['command']
        timeout_ms = input_data['timeout']
        run_in_background = input_data['run_in_background']
        
        timeout_sec = timeout_ms / 1000
        
        logger.info(f"[PowerShell] Executing: {command[:100]}...")
        
        # 检测 PowerShell 版本
        ps_command = self._build_ps_command(command)
        
        try:
            if run_in_background:
                # 后台执行
                return await self._execute_background(ps_command, timeout_sec)
            else:
                # 同步执行
                return await self._execute_sync(ps_command, timeout_sec)
                
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout_sec} seconds"
        except Exception as e:
            logger.error(f"[PowerShell] Execution error: {e}")
            return f"Error: {str(e)}"
    
    def _build_ps_command(self, command: str) -> list:
        """构建 PowerShell 命令参数"""
        # 检测是否有 PowerShell 7+ (pwsh)
        ps_exe = "pwsh" if self._has_pwsh() else "powershell"
        
        return [
            ps_exe,
            "-NoLogo",
            "-NonInteractive",
            "-NoProfile",
            "-Command",
            command
        ]
    
    def _has_pwsh(self) -> bool:
        """检查是否安装了 PowerShell 7+"""
        try:
            result = subprocess.run(
                ["pwsh", "-Command", "$PSVersionTable.PSVersion"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False
    
    async def _execute_sync(self, ps_command: list, timeout: float) -> str:
        """同步执行命令"""
        process = await asyncio.create_subprocess_exec(
            *ps_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd()
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            
            output = ""
            if stdout:
                output += stdout.decode('utf-8', errors='replace')
            if stderr:
                output += f"\n[Error]: {stderr.decode('utf-8', errors='replace')}"
            
            if process.returncode != 0:
                output += f"\n[Exit Code]: {process.returncode}"
            
            return output.strip() or "Command completed with no output"
            
        except asyncio.TimeoutError:
            process.kill()
            return f"Error: Command timed out after {timeout} seconds"
    
    async def _execute_background(self, ps_command: list, timeout: float) -> str:
        """后台执行命令"""
        try:
            process = await asyncio.create_subprocess_exec(
                *ps_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd()
            )
            
            return f"Background task started (PID: {process.pid}). Command will continue running."
            
        except Exception as e:
            return f"Error starting background task: {str(e)}"
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取命令内容用于权限检查"""
        return input_data.get('command', '')[:200]


class PowerShellEdition:
    """PowerShell 版本检测"""
    
    @staticmethod
    def get_edition() -> str:
        """
        获取 PowerShell 版本
        
        Returns:
            'core' - PowerShell 7+ (pwsh)
            'desktop' - Windows PowerShell 5.1 (powershell)
            'unknown' - 无法检测
        """
        try:
            result = subprocess.run(
                ["pwsh", "-Command", "$PSVersionTable.PSVersion.Major"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return 'core'
        except:
            pass
        
        try:
            result = subprocess.run(
                ["powershell", "-Command", "$PSVersionTable.PSVersion.Major"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return 'desktop'
        except:
            pass
        
        return 'unknown'
    
    @staticmethod
    def get_syntax_guidance() -> str:
        """获取版本相关的语法指导"""
        edition = PowerShellEdition.get_edition()
        
        if edition == 'desktop':
            return """
PowerShell edition: Windows PowerShell 5.1 (powershell.exe)
- Pipeline chain operators `&&` and `||` are NOT available
- Use `A; if ($?) { B }` to chain conditionally
- Ternary (`?:`), null-coalescing (`??`) operators NOT available
- Default encoding is UTF-16 LE (with BOM)
"""
        elif edition == 'core':
            return """
PowerShell edition: PowerShell 7+ (pwsh)
- Pipeline chain operators `&&` and `||` ARE available
- Ternary (`$cond ? $a : $b`), null-coalescing (`??`) operators available
- Default encoding is UTF-8 without BOM
"""
        else:
            return """
PowerShell edition: unknown — assume Windows PowerShell 5.1 for compatibility
- Do NOT use `&&`, `||`, ternary `?:`, null-coalescing `??`
- Use `A; if ($?) { B }` to chain conditionally
"""
