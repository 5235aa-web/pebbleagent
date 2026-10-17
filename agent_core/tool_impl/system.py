"""Environment, process and repository state tools."""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..truncation_manager import truncate_output
from ..code_network_guardV2 import (
    scan_code_for_scraping_behavior,
    get_rate_limiter,
    get_path_depth_guard,
    get_download_guard,
    reset_all_guards,
)
from ..code_destructive_guard import scan_code_for_destructive_behavior

logger = logging.getLogger(__name__)



class SystemToolsMixin:

    async def _tool_git_status(self, repo_path: str = ".") -> str:
        """获取Git状态"""
        try:
            result = subprocess.run(
                ['git', 'status'],
                cwd=self._resolve_path(repo_path),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Git status timed out"
        except FileNotFoundError:
            return "Error: Git not found"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_git_log(self, repo_path: str = ".", count: int = 10) -> str:
        """获取Git日志"""
        try:
            result = subprocess.run(
                ['git', 'log', '--oneline', f'-{count}'],
                cwd=self._resolve_path(repo_path),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Git log timed out"
        except FileNotFoundError:
            return "Error: Git not found"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_git_diff(self, repo_path: str = ".", target: str = None) -> str:
        """获取Git差异"""
        try:
            cmd = ['git', 'diff']
            if target:
                cmd.append(target)
            
            result = subprocess.run(
                cmd,
                cwd=self._resolve_path(repo_path),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout or "No differences"
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Git diff timed out"
        except FileNotFoundError:
            return "Error: Git not found"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_get_env_var(self, name: str) -> str:
        """获取环境变量"""
        if not name:
            return "Error: Variable name required"
        
        value = os.environ.get(name)
        if value is not None:
            return f"{name}={value}"
        else:
            return f"Error: Environment variable '{name}' not found"


    async def _tool_get_system_info(self) -> str:
        """获取系统信息"""
        import platform
        
        try:
            info = {
                "system": platform.system(),
                "node": platform.node(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "cwd": os.getcwd(),
                "project_path": self._current_project_path,
            }
            
            try:
                import psutil
                info["cpu_count"] = psutil.cpu_count()
                mem = psutil.virtual_memory()
                info["memory_total"] = f"{mem.total // (1024**3)} GB"
                info["memory_available"] = f"{mem.available // (1024**3)} GB"
            except ImportError:
                pass
            
            return json.dumps(info, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_list_processes(self, filter_name: str = None) -> str:
        """列出进程"""
        try:
            if os.name == 'nt':
                result = subprocess.run(
                    ['tasklist'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            else:
                result = subprocess.run(
                    ['ps', 'aux'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if filter_name:
                    lines = [l for l in lines if filter_name.lower() in l.lower()]
                return '\n'.join(lines[:50])
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Process list timed out"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_kill_process(self, pid: int, force: bool = False) -> str:
        """终止进程"""
        if pid is None:
            return "Error: PID required"
        
        import signal
        
        try:
            if os.name == 'nt':
                # Windows
                cmd = ['taskkill', '/F' if force else '', '/PID', str(pid)]
                cmd = [c for c in cmd if c]  # 移除空字符串
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            else:
                # Linux/Mac
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.kill(pid, sig)
                result = type('obj', (object,), {'returncode': 0, 'stdout': 'Process killed'})()
            
            if result.returncode == 0:
                return f"Success: Process {pid} terminated"
            else:
                return f"Error: {result.stderr}"
        except ProcessLookupError:
            return f"Error: Process {pid} not found"
        except subprocess.TimeoutExpired:
            return "Error: Kill command timed out"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_set_env_var(self, name: str, value: str) -> str:
        """设置环境变量"""
        if not name:
            return "Error: Variable name required"
        
        try:
            os.environ[name] = value
            return f"Success: Set {name}={value}"
        except Exception as e:
            return f"Error: {str(e)}"
