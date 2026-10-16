"""
代码执行工具

提供代码执行能力，支持多种编程语言
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional
import logging
import asyncio
import subprocess
import tempfile
import os
import sys
import signal

logger = logging.getLogger(__name__)


def _kill_process_tree_windows(pid: int):
    """
    Windows 上终止进程树
    
    使用 taskkill 命令终止进程及其所有子进程
    """
    try:
        import subprocess as sp
        # 使用 /F 强制终止，/T 终止进程树
        sp.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10
        )
        logger.info(f"[ExecuteCode] Killed process tree for PID {pid}")
    except Exception as e:
        logger.warning(f"[ExecuteCode] Failed to kill process tree: {e}")


def _kill_process_tree_unix(pid: int):
    """
    Unix 上终止进程树
    
    使用 SIGKILL 终止进程及其所有子进程
    """
    try:
        import os
        os.killpg(os.getpgid(pid), signal.SIGKILL)
        logger.info(f"[ExecuteCode] Killed process group for PID {pid}")
    except Exception as e:
        logger.warning(f"[ExecuteCode] Failed to kill process group: {e}")


async def _kill_process_tree(process):
    """
    跨平台终止进程树
    """
    try:
        pid = process.pid
        if sys.platform == "win32":
            # Windows: 使用 taskkill
            _kill_process_tree_windows(pid)
        else:
            # Unix: 使用进程组
            _kill_process_tree_unix(pid)
        
        # 等待进程结束
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            # 如果还没结束，尝试直接 kill
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[ExecuteCode] Error killing process tree: {e}")


class ExecuteCodeTool(BaseTool[Dict[str, Any], str]):
    """
    代码执行工具
    
    支持多种编程语言的代码执行
    使用 subprocess 进行安全的本地执行
    """
    
    name = "execute_code"
    description = "执行代码并返回结果（支持python/bash/javascript等）"
    input_params = ["code", "language", "timeout"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Code to execute"},
            "language": {"type": "string", "enum": ["python", "bash", "shell", "javascript", "js"], "description": "Programming language"},
            "timeout": {"type": "integer", "description": "Execution timeout in seconds"}
        },
        "required": ["code"]
    }
    requires_permission = True
    danger_level = "high"
    parallel_safe = False
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        code = kwargs.get('code')
        if not code:
            raise ValueError("code parameter is required")
        
        return {
            'code': str(code),
            'language': str(kwargs.get('language', 'python')),
            'timeout': int(kwargs.get('timeout', 60))
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """执行代码"""
        code = input_data['code']
        language = input_data['language']
        timeout = input_data['timeout']
        
        logger.info(f"[ExecuteCode] Executing {language} code ({len(code)} chars)")
        
        # === 网络请求安全扫描：硬编码拦截 ===
        from .code_network_guard import check_and_block_network_code
        is_allowed, block_message = check_and_block_network_code(code, language)
        if not is_allowed:
            return block_message
        # === END 网络请求安全扫描 ===
        
        # 使用 subprocess 执行
        return await self._execute_subprocess(code, language, timeout)
    
    async def _execute_subprocess(self, code: str, language: str, timeout: int) -> str:
        """使用 subprocess 执行代码"""
        
        if language == "python":
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(code)
                temp_path = f.name
            
            try:
                # 【修复】使用异步 subprocess，支持真正的超时和取消
                process = await asyncio.create_subprocess_exec(
                    sys.executable, temp_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout
                    )
                    
                    output = stdout.decode('utf-8', errors='replace') if stdout else ""
                    if stderr:
                        output += f"\n[Error]: {stderr.decode('utf-8', errors='replace')}"
                    return output.strip() or "Execution completed with no output"
                    
                except asyncio.TimeoutError:
                    # 【关键】超时后强制终止进程树
                    logger.error(f"[ExecuteCode] Python execution timed out after {timeout}s, killing process tree")
                    await _kill_process_tree(process)
                    return f"Error: Execution timed out after {timeout} seconds"
                    
            except Exception as e:
                return f"Error: {str(e)}"
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
        
        elif language in ["bash", "shell", "sh"]:
            try:
                # Windows 下使用 PowerShell 或 cmd
                if sys.platform == "win32":
                    # 【修复】使用异步 subprocess
                    logger.info(f"[ExecuteCode] Executing shell command on Windows: {code[:100]}...")
                    process = await asyncio.create_subprocess_exec(
                        "powershell", "-NoLogo", "-NonInteractive", "-NoProfile", "-Command", code,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    logger.info(f"[ExecuteCode] Process started with PID: {process.pid}")
                else:
                    process = await asyncio.create_subprocess_shell(
                        code,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout
                    )
                    
                    output = stdout.decode('utf-8', errors='replace') if stdout else ""
                    if stderr:
                        output += f"\n[Error]: {stderr.decode('utf-8', errors='replace')}"
                    return output.strip() or "Execution completed with no output"
                    
                except asyncio.TimeoutError:
                    # 【关键】超时后强制终止进程树
                    logger.error(f"[ExecuteCode] Shell execution timed out after {timeout}s, killing process tree")
                    await _kill_process_tree(process)
                    return f"Error: Execution timed out after {timeout} seconds"
                    
            except Exception as e:
                return f"Error: {str(e)}"
        
        elif language in ["javascript", "js"]:
            # 尝试使用 node 执行
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as f:
                f.write(code)
                temp_path = f.name
            
            try:
                # 【修复】使用异步 subprocess
                process = await asyncio.create_subprocess_exec(
                    "node", temp_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout
                    )
                    
                    output = stdout.decode('utf-8', errors='replace') if stdout else ""
                    if stderr:
                        output += f"\n[Error]: {stderr.decode('utf-8', errors='replace')}"
                    return output.strip() or "Execution completed with no output"
                    
                except asyncio.TimeoutError:
                    # 【关键】超时后强制终止进程树
                    logger.error(f"[ExecuteCode] JavaScript execution timed out after {timeout}s, killing process tree")
                    await _kill_process_tree(process)
                    return f"Error: Execution timed out after {timeout} seconds"
                    
            except FileNotFoundError:
                return "Error: Node.js not found. Please install Node.js to execute JavaScript code."
            except Exception as e:
                return f"Error: {str(e)}"
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
        
        return f"Unsupported language: {language}. Supported: python, bash, shell, javascript"
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取代码内容用于权限检查"""
        return input_data.get('code', '')[:200]


class ExecutePythonTool(ExecuteCodeTool):
    """Python代码执行工具（便捷别名）"""
    
    name = "execute_python"
    description = "执行Python代码"
    input_params = ["code", "timeout"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"},
            "timeout": {"type": "integer", "description": "Execution timeout in seconds"}
        },
        "required": ["code"]
    }
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        return super().validate_input(code=kwargs.get('code'), language='python', 
                                       timeout=kwargs.get('timeout', 60))


class ExecuteShellTool(ExecuteCodeTool):
    """Shell命令执行工具"""
    
    name = "execute_shell"
    description = "执行Shell命令"
    input_params = ["command", "timeout"]
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        command = kwargs.get('command') or kwargs.get('code')
        if not command:
            raise ValueError("command parameter is required")
        return super().validate_input(code=command, language='bash',
                                       timeout=kwargs.get('timeout', 60))
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取命令内容用于权限检查"""
        return input_data.get('code', '')[:200]
