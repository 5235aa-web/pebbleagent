"""Environment, process and repository state tools."""

import logging
import os
import shutil
import subprocess



logger = logging.getLogger(__name__)

_GIT_MISSING = ("Error: git executable not found. Install Git for Windows "
                "(https://git-scm.com/download/win) and reopen the app so the "
                "new PATH is inherited.")


def _resolve_git():
    """Locate git: PATH first, then the usual Git for Windows install roots.

    The installer only adds git to the PATH of shells started afterwards, so an
    app launched from an older environment can miss it while git is plainly
    installed.
    """
    found = shutil.which("git")
    if found:
        return found
    for candidate in (
        os.path.expandvars(r"%ProgramFiles%\Git\cmd\git.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Git\cmd\git.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None



class SystemToolsMixin:

    async def _tool_git_status(self, repo_path: str = ".") -> str:
        """获取Git状态"""
        git = _resolve_git()
        if not git:
            return _GIT_MISSING
        try:
            result = subprocess.run(
                [git, 'status'],
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
        git = _resolve_git()
        if not git:
            return _GIT_MISSING
        try:
            result = subprocess.run(
                [git, 'log', '--oneline', f'-{count}'],
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
        git = _resolve_git()
        if not git:
            return _GIT_MISSING
        try:
            cmd = [git, 'diff']
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
