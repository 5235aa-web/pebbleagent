"""Shell, PowerShell and inline code execution."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional


from ..code_network_guardV2 import (
    scan_code_for_scraping_behavior,
)
from ..code_destructive_guard import scan_code_for_destructive_behavior
from ..query_state_machine import ToolExecutionContext

logger = logging.getLogger(__name__)



class ExecutionToolsMixin:

    async def _terminate_process(self, process) -> None:
        """尽力终止进程及其子进程"""
        if process.returncode is not None:
            return

        try:
            if os.name == "nt":
                try:
                    await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=5
                    )
                except Exception:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    return
                except Exception:
                    process.terminate()
                await asyncio.sleep(0.2)
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except Exception:
                        process.kill()
        except ProcessLookupError:
            return
        except Exception as e:
            logger.warning(f"[ToolExecutor] Failed to terminate process tree {process.pid}: {e}")
            try:
                process.kill()
            except Exception:
                pass


    async def _run_subprocess_exec(
        self,
        command: List[str],
        timeout: float,
        context: Optional[ToolExecutionContext] = None,
        cwd: Optional[str] = None
    ) -> tuple[int, str, str]:
        """以可取消的方式运行子进程"""
        popen_kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd or self._current_project_path,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        start_time = time.time()
        try:
            while True:
                if context is not None and context.cancel_event.is_set():
                    logger.info(f"[ToolExecutor] Cancelling subprocess pid={process.pid}")
                    await self._terminate_process(process)
                    try:
                        await asyncio.to_thread(process.wait, timeout=1)
                    except Exception:
                        pass
                    raise asyncio.CancelledError()

                if process.poll() is not None:
                    stdout, stderr = await asyncio.to_thread(process.communicate)
                    return (
                        process.returncode or 0,
                        stdout.decode("utf-8", errors="replace") if stdout else "",
                        stderr.decode("utf-8", errors="replace") if stderr else "",
                    )

                if time.time() - start_time > timeout:
                    await self._terminate_process(process)
                    try:
                        await asyncio.to_thread(process.wait, timeout=1)
                    except Exception:
                        pass
                    raise asyncio.TimeoutError()

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            await self._terminate_process(process)
            raise


    async def _tool_execute_code(
        self,
        code: str,
        language: str = "python",
        context: ToolExecutionContext = None
    ) -> str:
        """执行代码"""
        if not code:
            return "Error: No code provided"
        
        # V2 行为模式扫描：检测批量爬虫代码
        is_blocked, patterns = scan_code_for_scraping_behavior(code, language)
        if is_blocked:
            patterns_str = "; ".join(patterns)
            logger.warning(f"[ToolExecutor] Code blocked by NetworkGuardV2: {patterns_str}")
            return (
                f"⛔ [安全拦截] 代码中检测到爬虫行为模式，已被阻止执行。\n"
                f"检测到: {patterns_str}\n"
                f"说明: 禁止批量/遍历/自动化的页面访问行为。"
            )
        
        # 破坏性模式扫描（与 execute_shell 校验对齐）
        is_blocked, patterns = scan_code_for_destructive_behavior(code, language)
        if is_blocked:
            patterns_str = "; ".join(patterns)
            logger.warning(f"[ToolExecutor] Code blocked by DestructiveGuard: {patterns_str}")
            return (
                f"⛔ [安全拦截] 代码中检测到破坏性操作模式，已被阻止执行。\n"
                f"检测到: {patterns_str}\n"
                f"说明: 禁止对系统/根目录的破坏性操作。"
            )
        
        # bash 脚本 → 与 execute_shell 同级全量验证（BashValidator，语法一致）
        if language == "bash":
            from ..bash_validator import BashValidator
            validator = BashValidator(self._current_project_path)
            validator.set_path_whitelist(getattr(self, '_path_whitelist', []))
            report = validator.validate(code)
            if report.blocked:
                logger.warning(f"[ToolExecutor] Bash script blocked: {report.block_reason}")
                return f"Error: 代码被阻止执行 - {report.block_reason}"
            if report.is_destructive:
                logger.warning(f"[ToolExecutor] Bash script warnings: {report.warnings[:2]}")
        
        import tempfile
        
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{language}', delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            if language == "python":
                command = ['python', temp_file]
            else:
                command = [language, temp_file]

            returncode, stdout, stderr = await self._run_subprocess_exec(
                command=command,
                timeout=60,
                context=context,
                cwd=self._current_project_path
            )
            
            if returncode == 0:
                return stdout or "Success (no output)"
            return f"Error: {stderr}"

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return "Error: Execution timed out"
        except Exception as e:
            return f"Error: {str(e)}"
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception:
                    pass


    async def _tool_execute_shell(
        self,
        command: str,
        timeout: int = 60,
        context: ToolExecutionContext = None
    ) -> str:
        """执行Shell命令（BashValidator 全量检查）"""
        if not command:
            return "Error: No command provided"
        
        # 使用 BashValidator 全量验证
        from ..bash_validator import BashValidator
        validator = BashValidator(self._current_project_path)
        validator.set_path_whitelist(getattr(self, '_path_whitelist', []))
        report = validator.validate(command)
        
        # 阻止级：拒绝执行
        if report.blocked:
            logger.warning(f"[ToolExecutor] BLOCKED command: {command} - {report.block_reason}")
            return f"Error: 命令被阻止执行 - {report.block_reason}"
        
        # 警告级：记录但允许
        if report.is_destructive:
            audit_logger = self._enforcer._get_audit_logger()
            if audit_logger:
                from gateway.permission_audit import AuditEvent, AuditEventType
                audit_logger.log_event(
                    AuditEvent(
                        event_type=AuditEventType.PERMISSION_CHECK.value,
                        tool_name='execute_shell',
                        target=command,
                        content=command,
                        allowed=True,
                        decision_source='dangerous_command_warning',
                        reason='; '.join(report.warnings[:3])
                    )
                )
            logger.warning(f"[ToolExecutor] Dangerous command executed: {command} - {report.warnings[:2]}")
        
        try:
            if os.name == 'nt':
                exec_command = ['cmd', '/c', command]
            else:
                exec_command = ['bash', '-c', command]

            returncode, stdout, stderr = await self._run_subprocess_exec(
                command=exec_command,
                timeout=timeout,
                context=context,
                cwd=self._current_project_path
            )

            if returncode == 0:
                return stdout or "Success (no output)"
            return f"Error: {stderr}"

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return "Error: Command timed out"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_powershell(
        self,
        command: str,
        timeout: int = 60000,
        context: ToolExecutionContext = None
    ) -> str:
        """执行 PowerShell 命令（含网络行为守卫，与 execute_code 同级）"""
        if not command:
            return "Error: No command provided"

        # V2 行为模式扫描：禁止 HTTP 库/网络命令/爬虫模式（与 execute_code 一致）
        is_blocked, patterns = scan_code_for_scraping_behavior(command, "powershell")
        if is_blocked:
            patterns_str = "; ".join(patterns)
            logger.warning(f"[ToolExecutor] PowerShell blocked by NetworkGuardV2: {patterns_str}")
            return (
                f"⛔ [安全拦截] 命令中检测到网络请求模式，已被阻止执行。\n"
                f"检测到: {patterns_str}\n"
                f"说明: 网络请求请使用 web_search / fetch_url / browser_navigate 工具。"
            )

        # 破坏性/路径校验（与 execute_shell 的 BashValidator 同级）
        from ..powershell_validator import PowerShellValidator
        ps_validator = PowerShellValidator(self._current_project_path)
        ps_validator.set_path_whitelist(getattr(self, '_path_whitelist', []))
        ps_report = ps_validator.validate(command)
        if ps_report.blocked:
            logger.warning(f"[ToolExecutor] PowerShell BLOCKED: {command[:200]} - {ps_report.block_reason}")
            return f"Error: 命令被阻止执行 - {ps_report.block_reason}"
        if ps_report.warnings:
            logger.warning(f"[ToolExecutor] PowerShell warnings: {ps_report.warnings[:2]}")

        # 检测 PowerShell 版本
        ps_exe = "pwsh" if self._has_pwsh() else "powershell"
        
        ps_command = [
            ps_exe,
            "-NoLogo",
            "-NonInteractive",
            "-NoProfile",
            "-Command",
            command
        ]
        
        timeout_sec = timeout / 1000
        
        try:
            returncode, stdout_text, stderr_text = await self._run_subprocess_exec(
                command=ps_command,
                timeout=timeout_sec,
                context=context,
                cwd=self._current_project_path
            )

            output = ""
            if stdout_text:
                output += stdout_text
            if stderr_text:
                output += f"\n[Error]: {stderr_text}"
            
            if returncode != 0:
                output += f"\n[Exit Code]: {returncode}"
            
            return output.strip() or "Command completed with no output"
        
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return f"Error: Command timed out after {timeout_sec} seconds"
        except FileNotFoundError:
            return "Error: PowerShell not found"
        except Exception as e:
            return f"Error: {str(e)}"


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
