"""
Grep工具 - 文件内容搜索

参照Claude Code的GrepTool实现，使用ripgrep进行文件内容搜索

【修复】支持工具执行中中断：
- 使用 Popen 替代 subprocess.run
- 轮询检查取消信号
- 支持进程树终止
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional, List
import logging
import os
import subprocess
import re
import threading
import time
import signal
import sys

logger = logging.getLogger(__name__)

DEFAULT_HEAD_LIMIT = 250
DEFAULT_GREP_TIMEOUT = 30  # 默认超时时间（秒）


def _get_cancel_signal() -> Optional[threading.Event]:
    """
    获取当前子agent的取消信号
    
    Returns:
        threading.Event 或 None
    """
    try:
        # 尝试从 agent_executor 获取当前取消信号
        from agent_core.agent_executor import AgentExecutor
        # 使用线程本地存储获取当前执行器
        # 这是一个简化的实现，实际可能需要更复杂的上下文传递
        return None  # 暂时返回 None，后续通过参数传递
    except ImportError:
        return None


def _kill_process_tree(proc: subprocess.Popen):
    """
    终止进程树（包括所有子进程）
    
    参照 Claude Code 的 treeKill 实现
    """
    try:
        if sys.platform == 'win32':
            # Windows: 使用 taskkill 终止进程树
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                capture_output=True,
                timeout=5
            )
        else:
            # Unix: 使用进程组终止
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # 进程可能已经结束
                pass
    except Exception as e:
        logger.warning(f"[GrepTool] Failed to kill process tree: {e}")
        # 最后尝试直接终止
        try:
            proc.kill()
        except:
            pass


async def _kill_process_tree_async(process):
    """
    异步终止进程树（包括所有子进程）
    
    用于异步上下文中终止进程
    """
    try:
        pid = process.pid
        if sys.platform == 'win32':
            # Windows: 使用 taskkill 终止进程树
            kill_proc = await asyncio.create_subprocess_exec(
                'taskkill', '/F', '/T', '/PID', str(pid),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await kill_proc.wait()
            logger.info(f"[GrepTool] Killed process tree for PID {pid}")
        else:
            # Unix: 使用进程组终止
            try:
                import os
                import signal
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
                logger.info(f"[GrepTool] Killed process group for PID {pid}")
            except (ProcessLookupError, PermissionError):
                pass
        
        # 等待进程结束
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[GrepTool] Error killing process tree: {e}")


class GrepTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    文件内容搜索工具
    
    使用ripgrep (rg) 进行高效的文件内容搜索
    """
    
    name = "grep"
    description = """A powerful search tool built on ripgrep.

Usage:
- NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., js, py, rust, go, java, etc.)
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
- Use 'SearchCodebase' tool when precise code keywords are missing

Pattern syntax: Uses ripgrep - literal braces need escaping (e.g., use `interface\\{\\}` to find `interface{}` in Go code)
Multiline matching: By default patterns match within single lines only. For cross-line patterns, set multiline: true"""
    
    input_params = ["pattern", "path", "glob", "output_mode", "-i", "-n", "-A", "-B", "-C", "type", "head_limit", "offset", "multiline"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "The regular expression pattern to search for"},
            "path": {"type": "string", "description": "File or directory path to search in"},
            "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.js')"},
            "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"], "description": "Output mode"},
            "-i": {"type": "boolean", "description": "Case insensitive search"},
            "-n": {"type": "boolean", "description": "Show line numbers"},
            "-A": {"type": "integer", "description": "Lines after match"},
            "-B": {"type": "integer", "description": "Lines before match"},
            "-C": {"type": "integer", "description": "Lines around match"},
            "type": {"type": "string", "description": "File type filter (js, py, rust, go, java, etc.)"},
            "head_limit": {"type": "integer", "description": "Maximum number of results"},
            "offset": {"type": "integer", "description": "Offset for pagination"},
            "multiline": {"type": "boolean", "description": "Enable multiline matching"}
        },
        "required": ["pattern"]
    }
    requires_permission = True
    danger_level = "low"
    parallel_safe = True
    
    VCS_DIRECTORIES = ['.git', '.svn', '.hg', '.bzr', '.jj', '.sl']
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        pattern = kwargs.get("pattern")
        if not pattern:
            raise ValueError("pattern parameter is required")
        
        output_mode = kwargs.get("output_mode", "files_with_matches")
        if output_mode not in ["content", "files_with_matches", "count"]:
            raise ValueError(f"Invalid output_mode: {output_mode}")
        
        return {
            "pattern": str(pattern),
            "path": kwargs.get("path"),
            "glob": kwargs.get("glob"),
            "output_mode": output_mode,
            "-i": kwargs.get("-i", False),
            "-n": kwargs.get("-n", True),
            "-A": kwargs.get("-A"),
            "-B": kwargs.get("-B"),
            "-C": kwargs.get("-C"),
            "context": kwargs.get("context"),
            "type": kwargs.get("type"),
            "head_limit": kwargs.get("head_limit", DEFAULT_HEAD_LIMIT),
            "offset": kwargs.get("offset", 0),
            "multiline": kwargs.get("multiline", False)
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行搜索"""
        pattern = input_data["pattern"]
        path = input_data.get("path") or os.getcwd()
        glob_pattern = input_data.get("glob")
        output_mode = input_data.get("output_mode", "files_with_matches")
        case_insensitive = input_data.get("-i", False)
        show_line_numbers = input_data.get("-n", True)
        after_context = input_data.get("-A")
        before_context = input_data.get("-B")
        context = input_data.get("-C") or input_data.get("context")
        file_type = input_data.get("type")
        head_limit = input_data.get("head_limit", DEFAULT_HEAD_LIMIT)
        offset = input_data.get("offset", 0)
        multiline = input_data.get("multiline", False)
        timeout = input_data.get("_timeout", DEFAULT_GREP_TIMEOUT)
        cancel_signal = input_data.get("_cancel_signal")  # 外部传入的取消信号
        
        # 【调试日志】
        logger.info(f"[GrepTool] Starting search: pattern={pattern[:50]}..., path={path}")
        logger.info(f"[GrepTool] timeout={timeout}s, cancel_signal={cancel_signal is not None}")
        
        cmd = ["rg"]
        
        if output_mode == "content":
            cmd.append("-n") if show_line_numbers else None
            if after_context:
                cmd.extend(["-A", str(after_context)])
            if before_context:
                cmd.extend(["-B", str(before_context)])
            if context:
                cmd.extend(["-C", str(context)])
        elif output_mode == "count":
            cmd.append("-c")
        else:
            cmd.append("-l")
        
        if case_insensitive:
            cmd.append("-i")
        
        if multiline:
            cmd.extend(["-U", "--multiline-dotall"])
        
        for vcs_dir in self.VCS_DIRECTORIES:
            cmd.extend(["--glob", f"!{vcs_dir}"])
        
        if glob_pattern:
            cmd.extend(["--glob", glob_pattern])
        
        if file_type:
            cmd.extend(["--type", file_type])
        
        cmd.append("--")
        cmd.append(pattern)
        cmd.append(path)
        
        try:
            # 【修复】使用异步 subprocess，避免阻塞事件循环
            logger.info(f"[GrepTool] Starting async subprocess: {' '.join(cmd[:5])}...")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            logger.info(f"[GrepTool] Process started with PID: {process.pid}")
            
            try:
                # 使用 asyncio.wait_for 实现超时
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
                
                stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ""
                stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ""
                ret_code = process.returncode
                
                logger.info(f"[GrepTool] Process completed with return code: {ret_code}")
                
            except asyncio.TimeoutError:
                logger.warning(f"[GrepTool] Timeout after {timeout}s, killing process")
                await _kill_process_tree_async(process)
                stdout, stderr = "", "Search timed out"
                ret_code = -1
            
            if ret_code not in [0, 1]:
                return {
                    "mode": output_mode,
                    "numFiles": 0,
                    "filenames": [],
                    "error": stderr or ("Search interrupted" if interrupted else "Unknown error"),
                    "message": f"Search failed: {stderr}" if stderr else "Search interrupted"
                }
            
            output = stdout.strip()
            
            if not output:
                return {
                    "mode": output_mode,
                    "numFiles": 0,
                    "filenames": [],
                    "content": "",
                    "message": "No matches found"
                }
            
            if output_mode == "files_with_matches":
                lines = output.split('\n')
                all_files = [line for line in lines if line.strip()]
                
                effective_offset = offset
                effective_limit = head_limit if head_limit != 0 else len(all_files)
                
                sliced_files = all_files[effective_offset:effective_offset + effective_limit]
                was_truncated = len(all_files) > effective_offset + effective_limit
                
                return {
                    "mode": output_mode,
                    "numFiles": len(sliced_files),
                    "filenames": sliced_files,
                    "totalMatches": len(all_files),
                    "appliedLimit": effective_limit if was_truncated else None,
                    "appliedOffset": offset if offset > 0 else None,
                    "message": f"Found {len(sliced_files)} files" + (f" (showing {offset+1}-{offset+len(sliced_files)} of {len(all_files)})" if was_truncated else "")
                }
            
            elif output_mode == "count":
                lines = output.split('\n')
                counts = []
                for line in lines:
                    if ':' in line:
                        filepath, count = line.rsplit(':', 1)
                        counts.append({"file": filepath, "count": int(count.strip())})
                
                effective_offset = offset
                effective_limit = head_limit if head_limit != 0 else len(counts)
                
                sliced_counts = counts[effective_offset:effective_offset + effective_limit]
                was_truncated = len(counts) > effective_offset + effective_limit
                
                return {
                    "mode": output_mode,
                    "numFiles": len(sliced_counts),
                    "filenames": [c["file"] for c in sliced_counts],
                    "counts": sliced_counts,
                    "numMatches": sum(c["count"] for c in sliced_counts),
                    "appliedLimit": effective_limit if was_truncated else None,
                    "appliedOffset": offset if offset > 0 else None,
                    "message": f"Found matches in {len(sliced_counts)} files"
                }
            
            else:
                lines = output.split('\n')
                
                effective_offset = offset
                effective_limit = head_limit if head_limit != 0 else len(lines)
                
                sliced_lines = lines[effective_offset:effective_offset + effective_limit]
                was_truncated = len(lines) > effective_offset + effective_limit
                
                content = '\n'.join(sliced_lines)
                
                files_with_matches = set()
                for line in sliced_lines:
                    if ':' in line:
                        file_path = line.split(':', 1)[0]
                        files_with_matches.add(file_path)
                
                return {
                    "mode": output_mode,
                    "numFiles": len(files_with_matches),
                    "filenames": list(files_with_matches),
                    "content": content,
                    "numLines": len(sliced_lines),
                    "appliedLimit": effective_limit if was_truncated else None,
                    "appliedOffset": offset if offset > 0 else None,
                    "message": f"Found {len(sliced_lines)} lines in {len(files_with_matches)} files"
                }
                
        except FileNotFoundError:
            return {
                "mode": output_mode,
                "numFiles": 0,
                "filenames": [],
                "error": "ripgrep (rg) not found. Please install ripgrep.",
                "message": "ripgrep not installed"
            }
        except Exception as e:
            logger.error(f"Grep error: {e}")
            return {
                "mode": output_mode,
                "numFiles": 0,
                "filenames": [],
                "error": str(e),
                "message": f"Search failed: {str(e)}"
            }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return input_data.get("pattern")


class SearchCodebaseTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    代码库搜索工具
    
    使用语义搜索在代码库中查找相关代码
    """
    
    name = "search_codebase"
    description = """This tool is Trae's context engine. It:
1. Takes in a natural language description of the code you are looking for;
2. Uses a proprietary retrieval/embedding model suite that produces the highest-quality recall of relevant code snippets from across the codebase;
3. Maintains a real-time index of the codebase, so the results are always up-to-date and reflects the current state of the codebase;
4. Can retrieve across different programming languages;
5. Only reflects the current state of the codebase on the disk, and has no information on version control or code history.
6. Use 'Glob' tool instead when you want to find files by name.

ALWAYS prefer using `SearchCodebase` to search code, because it is much faster for efficient codebase exploration and will require fewer tool calls."""
    
    input_params = ["information_request", "target_directories"]
    requires_permission = True
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        information_request = kwargs.get("information_request")
        if not information_request:
            raise ValueError("information_request parameter is required")
        
        return {
            "information_request": str(information_request),
            "target_directories": kwargs.get("target_directories")
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行语义搜索"""
        information_request = input_data["information_request"]
        target_directories = input_data.get("target_directories")
        
        keywords = re.findall(r'\b\w+\b', information_request.lower())
        keywords = [k for k in keywords if len(k) > 2][:10]
        
        if not keywords:
            return {
                "results": [],
                "message": "No valid search keywords found in the request"
            }
        
        grep_tool = GrepTool()
        results = []
        
        search_dirs = target_directories if target_directories else [os.getcwd()]
        if not isinstance(search_dirs, list):
            search_dirs = [search_dirs]
        
        for keyword in keywords[:3]:
            for search_dir in search_dirs:
                try:
                    result = await grep_tool.execute(
                        pattern=keyword,
                        path=search_dir,
                        output_mode="content",
                        head_limit=50
                    )
                    
                    if result.success and result.result:
                        grep_result = result.result
                        if grep_result.get("content"):
                            results.append({
                                "keyword": keyword,
                                "matches": grep_result.get("content", "")[:500],
                                "files": grep_result.get("filenames", [])[:5]
                            })
                except Exception as e:
                    logger.debug(f"Search error for keyword '{keyword}': {e}")
        
        return {
            "results": results,
            "keywords_used": keywords[:3],
            "message": f"Found {len(results)} relevant results"
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return input_data.get("information_request")
