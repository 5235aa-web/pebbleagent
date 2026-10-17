"""
文件操作工具

提供文件读写、编辑、搜索等功能
"""
from .base_tool import BaseTool
from typing import Dict, Any, Optional
import logging
import os

logger = logging.getLogger(__name__)


class ReadFileTool(BaseTool[Dict[str, Any], str]):
    """
    文件读取工具
    """
    
    name = "read_file"
    description = "读取文件内容"
    input_params = ["path", "start_line", "end_line"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "start_line": {"type": "integer", "description": "Start line number (1-based)"},
            "end_line": {"type": "integer", "description": "End line number"}
        },
        "required": ["path"]
    }
    requires_permission = True
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        path = kwargs.get('path')
        if not path:
            raise ValueError("path parameter is required")
        
        return {
            'path': str(path),
            'start_line': kwargs.get('start_line'),
            'end_line': kwargs.get('end_line')
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """读取文件"""
        path = input_data['path']
        start_line = input_data.get('start_line')
        end_line = input_data.get('end_line')
        
        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            start = (start_line or 1) - 1
            end = end_line or len(lines)
            
            content = ''.join(lines[start:end])
            return content or "File is empty"
            
        except Exception as e:
            return f"Error reading file: {str(e)}"
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取文件路径用于权限检查"""
        return input_data.get('path')


class WriteFileTool(BaseTool[Dict[str, Any], str]):
    """
    文件写入工具
    """
    
    name = "write_file"
    description = "写入文件（创建新文件或覆盖现有文件）"
    input_params = ["path", "content"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Content to write to the file"}
        },
        "required": ["path", "content"]
    }
    requires_permission = True
    danger_level = "medium"
    parallel_safe = False
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        path = kwargs.get('path')
        content = kwargs.get('content')
        
        if not path:
            raise ValueError("path parameter is required")
        if content is None:
            raise ValueError("content parameter is required")
        
        return {
            'path': str(path),
            'content': str(content)
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """写入文件"""
        path = input_data['path']
        content = input_data['content']
        
        try:
            # 确保目录存在
            dir_path = os.path.dirname(os.path.abspath(path))
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return f"File written successfully: {path}"
            
        except Exception as e:
            return f"Error writing file: {str(e)}"
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取文件路径用于权限检查"""
        return input_data.get('path')


class EditFileTool(BaseTool[Dict[str, Any], str]):
    """
    文件编辑工具
    
    使用字符串替换方式编辑文件
    """
    
    name = "edit_file"
    description = "编辑文件（文本替换）"
    input_params = ["path", "old_text", "new_text"]
    requires_permission = True
    danger_level = "medium"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        path = kwargs.get('path')
        old_text = kwargs.get('old_text') or kwargs.get('original_text')
        new_text = kwargs.get('new_text') or kwargs.get('replacement_text')
        
        if not path:
            raise ValueError("path parameter is required")
        if old_text is None:
            raise ValueError("old_text parameter is required")
        if new_text is None:
            raise ValueError("new_text parameter is required")
        
        return {
            'path': str(path),
            'old_text': str(old_text),
            'new_text': str(new_text)
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """编辑文件"""
        path = input_data['path']
        old_text = input_data['old_text']
        new_text = input_data['new_text']
        
        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if old_text not in content:
                return f"Error: Text not found in file: {old_text[:50]}..."
            
            new_content = content.replace(old_text, new_text)
            
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            return f"File edited successfully: {path}"
            
        except Exception as e:
            return f"Error editing file: {str(e)}"
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取文件路径用于权限检查"""
        return input_data.get('path')


class SearchFilesTool(BaseTool[Dict[str, Any], str]):
    """
    文件搜索工具
    """
    
    name = "search_files"
    description = "搜索文件"
    input_params = ["query", "path"]
    requires_permission = True
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        query = kwargs.get('query')
        if not query:
            raise ValueError("query parameter is required")
        
        return {
            'query': str(query),
            'path': kwargs.get('path', '.')
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """搜索文件"""
        query = input_data['query']
        search_path = input_data.get('path', '.')
        
        # 使用纯 Python 实现文件名搜索
        results = []
        try:
            for root, dirs, files in os.walk(search_path):
                # 跳过隐藏目录和常见的排除目录
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', '.git', 'venv', 'env']]
                
                for file in files:
                    if query.lower() in file.lower():
                        results.append(os.path.join(root, file))
                        
                # 限制搜索结果数量
                if len(results) >= 100:
                    break
        except Exception as e:
            return f"搜索错误: {str(e)}"
        
        if results:
            return "\n".join(results[:50])  # 限制结果数量
        return f"No files found matching: {query}"