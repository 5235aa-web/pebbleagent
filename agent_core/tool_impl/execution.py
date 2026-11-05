"""Shell, PowerShell and inline code execution."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from typing import Any, Dict, Optional


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
        command,
        timeout: float,
        context: Optional[ToolExecutionContext] = None,
        cwd: Optional[str] = None,
        shell: bool = False,
    ) -> tuple[int, str, str]:
        """以可取消的方式运行子进程

        读取必须从进程启动时就开始。stdout/stderr 是管道，写满缓冲区
        （Windows 上约 4KB）后子进程会阻塞在 write 上、永远不会退出；
        以前只 poll() 再事后 communicate()，任何输出超过缓冲区的命令都会
        一直挂到超时。communicate() 放在线程里立即启动，由它持续排空管道。
        """
        popen_kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd or self._current_project_path,
        }
        if shell:
            popen_kwargs["shell"] = True
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        start_time = time.time()
        reader = asyncio.ensure_future(asyncio.to_thread(process.communicate))
        try:
            while True:
                if context is not None and context.cancel_event.is_set():
                    logger.info(f"[ToolExecutor] Cancelling subprocess pid={process.pid}")
                    await self._terminate_process(process)
                    await self._settle_reader(reader)
                    raise asyncio.CancelledError()

                done, _ = await asyncio.wait({reader}, timeout=0.1)
                if reader in done:
                    stdout, stderr = reader.result()
                    return (
                        process.returncode or 0,
                        stdout.decode("utf-8", errors="replace") if stdout else "",
                        stderr.decode("utf-8", errors="replace") if stderr else "",
                    )

                if time.time() - start_time > timeout:
                    await self._terminate_process(process)
                    await self._settle_reader(reader)
                    raise asyncio.TimeoutError()
        except asyncio.CancelledError:
            await self._terminate_process(process)
            raise

    async def _settle_reader(self, reader) -> None:
        """进程被杀后给读取线程一点时间返回，避免线程悬挂"""
        try:
            await asyncio.wait({reader}, timeout=1)
        except Exception:
            pass
        if not reader.done():
            reader.cancel()

    async def _tool_execute_code(
        self,
        code: str,
        language: str = "python",
        context: ToolExecutionContext = None
    ) -> str:
        """执行代码"""
        if not code:
            return "Error: No code provided"
        
        # 破坏性模式扫描（与 execute_shell 校验对齐）
        is_blocked, patterns = scan_code_for_destructive_behavior(code, language)
        if is_blocked:
            patterns_str = "; ".join(patterns)
            logger.warning(f"[ToolExecutor] Code blocked by DestructiveGuard: {patterns_str}")
            # 打包类禁令要给 agent 一条明确的行动指令：不要换一种写法重试，让用户
            # 自己跑。系统破坏类没有这个出口，只说被拦。
            if any(p.startswith("build/packaging") for p in patterns):
                hint = (
                    "Packaging is reserved for the user and the permission system blocks it for the agent. "
                    "Do not retry through another form (argv list, `sh -c`, or a script file): say that the "
                    "build must be run by the user, and stop."
                )
            else:
                hint = (
                    "A destructive system operation was detected and blocked. "
                    "Do not attempt a workaround; state what you tried and stop."
                )
            return f"⛔ [Blocked] {hint}\nmatched: {patterns_str}"
        
        # bash 脚本 → 与 execute_shell 同级全量验证（BashValidator，语法一致）
        if language == "bash":
            from ..bash_validator import BashValidator
            validator = BashValidator(self._current_project_path)
            validator.set_path_whitelist(getattr(self, '_path_whitelist', []))
            report = validator.validate(code)
            if report.blocked:
                logger.warning(f"[ToolExecutor] Bash script blocked: {report.block_reason}")
                return f"Error: blocked by the permission system - {report.block_reason}"
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
            return f"Error: blocked by the permission system - {report.block_reason}"
        
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
                # A single command line, not an argv list. list2cmdline escaped the
                # inner quotes of `python -c "..."`, so the interpreter received the
                # code as a string literal (empty output) and a semicolon inside it
                # died as a truncated-source SyntaxError. shell=True hands the string
                # to cmd.exe unchanged, which is what the caller wrote.
                exec_command = command
                use_shell = True
            else:
                exec_command = ['bash', '-c', command]
                use_shell = False

            returncode, stdout, stderr = await self._run_subprocess_exec(
                command=exec_command,
                timeout=timeout,
                context=context,
                cwd=self._current_project_path,
                shell=use_shell
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
        """执行 PowerShell 命令"""
        # The schema drops this tool off Windows. This guard covers the paths that
        # bypass the schema: a replayed history row or a direct RPC call.
        if os.name != "nt":
            return "Error: the powershell tool is Windows-only. Use execute_shell on this platform."
        if not command:
            return "Error: No command provided"

        # 破坏性/路径校验（与 execute_shell 的 BashValidator 同级）
        from ..powershell_validator import PowerShellValidator
        ps_validator = PowerShellValidator(self._current_project_path)
        ps_validator.set_path_whitelist(getattr(self, '_path_whitelist', []))
        ps_report = ps_validator.validate(command)
        if ps_report.blocked:
            logger.warning(f"[ToolExecutor] PowerShell BLOCKED: {command[:200]} - {ps_report.block_reason}")
            return f"Error: blocked by the permission system - {ps_report.block_reason}"
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
