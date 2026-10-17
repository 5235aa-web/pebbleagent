"""
Glob工具 - 文件名模式匹配

提供文件名模式匹配功能
"""
from .base_tool import BaseTool
from typing import Dict, Any, Optional, List
import logging
import os
import fnmatch
import time

logger = logging.getLogger(__name__)

MAX_RESULTS = 100


class GlobTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    文件名模式匹配工具
    
    使用glob模式匹配查找文件
    """
    
    name = "glob"
    description = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the `SearchCodebase` tool instead"""
    
    input_params = ["pattern", "path"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match files (e.g. '*.js', 'src/**/*.ts')"},
            "path": {"type": "string", "description": "Directory to search in"}
        },
        "required": ["pattern"]
    }
    requires_permission = True
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        pattern = kwargs.get("pattern")
        if not pattern:
            raise ValueError("pattern parameter is required")
        
        return {
            "pattern": str(pattern),
            "path": kwargs.get("path")
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行文件模式匹配"""
        pattern = input_data["pattern"]
        path = input_data.get("path") or os.getcwd()
        
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        
        if not os.path.exists(path):
            return {
                "durationMs": 0,
                "numFiles": 0,
                "filenames": [],
                "truncated": False,
                "error": f"Directory does not exist: {path}",
                "message": "Directory not found"
            }
        
        if not os.path.isdir(path):
            return {
                "durationMs": 0,
                "numFiles": 0,
                "filenames": [],
                "truncated": False,
                "error": f"Path is not a directory: {path}",
                "message": "Path is not a directory"
            }
        
        start_time = time.time()
        
        try:
            matches = self._find_files(path, pattern)
            
            def get_mtime(filepath):
                try:
                    return os.path.getmtime(filepath)
                except:
                    return 0
            
            matches.sort(key=get_mtime, reverse=True)
            
            truncated = len(matches) > MAX_RESULTS
            if truncated:
                matches = matches[:MAX_RESULTS]
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            return {
                "durationMs": duration_ms,
                "numFiles": len(matches),
                "filenames": matches,
                "truncated": truncated,
                "message": f"Found {len(matches)} files in {duration_ms}ms" + (" (truncated)" if truncated else "")
            }
            
        except Exception as e:
            logger.error(f"Glob error: {e}")
            return {
                "durationMs": 0,
                "numFiles": 0,
                "filenames": [],
                "truncated": False,
                "error": str(e),
                "message": f"Search failed: {str(e)}"
            }
    
    def _find_files(self, root_path: str, pattern: str) -> List[str]:
        """递归查找匹配的文件"""
        matches = []
        
        if '**' in pattern:
            base_pattern = pattern.replace('**/', '').replace('/**', '')
            for root, dirs, files in os.walk(root_path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', '.git']]
                
                for filename in files:
                    if fnmatch.fnmatch(filename, base_pattern):
                        matches.append(os.path.join(root, filename))
        else:
            for root, dirs, files in os.walk(root_path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', '.git']]
                
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern):
                        matches.append(os.path.join(root, filename))
        
        return matches
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return input_data.get("pattern")


class LS(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    目录列表工具
    
    列出目录内容
    """
    
    name = "ls"
    description = """Lists files and directories in a given path.
The path parameter must be an absolute path, not a relative path.
You can optionally provide an array of glob patterns to ignore with the ignore parameter."""
    
    input_params = ["path", "ignore"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to directory to list"},
            "ignore": {"type": "array", "items": {"type": "string"}, "description": "Glob patterns to ignore"}
        },
        "required": ["path"]
    }
    requires_permission = True
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        path = kwargs.get("path")
        if not path:
            raise ValueError("path parameter is required")
        
        return {
            "path": str(path),
            "ignore": kwargs.get("ignore", [])
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """列出目录内容"""
        path = input_data["path"]
        ignore_patterns = input_data.get("ignore", [])
        
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        
        if not os.path.exists(path):
            return {
                "path": path,
                "entries": [],
                "error": f"Path does not exist: {path}",
                "message": "Path not found"
            }
        
        if not os.path.isdir(path):
            return {
                "path": path,
                "entries": [],
                "error": f"Path is not a directory: {path}",
                "message": "Not a directory"
            }
        
        try:
            entries = []
            
            for entry in os.listdir(path):
                should_ignore = False
                for pattern in ignore_patterns:
                    if fnmatch.fnmatch(entry, pattern):
                        should_ignore = True
                        break
                
                if not should_ignore:
                    full_path = os.path.join(path, entry)
                    is_dir = os.path.isdir(full_path)
                    entries.append({
                        "name": entry,
                        "type": "directory" if is_dir else "file",
                        "path": full_path
                    })
            
            entries.sort(key=lambda x: (x["type"] == "file", x["name"].lower()))
            
            return {
                "path": path,
                "entries": entries,
                "message": f"Found {len(entries)} entries"
            }
            
        except PermissionError:
            return {
                "path": path,
                "entries": [],
                "error": "Permission denied",
                "message": "Permission denied"
            }
        except Exception as e:
            logger.error(f"LS error: {e}")
            return {
                "path": path,
                "entries": [],
                "error": str(e),
                "message": f"Failed to list directory: {str(e)}"
            }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return input_data.get("path")
