"""
Jupyter Notebook 编辑工具

编辑 .ipynb 文件中的单元格
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional, List
import logging
import json
import os

logger = logging.getLogger(__name__)


class NotebookEditTool(BaseTool[Dict[str, Any], str]):
    """
    Jupyter Notebook 编辑工具
    
    支持操作：
    - replace: 替换单元格内容
    - insert: 插入新单元格
    - delete: 删除单元格
    """
    
    name = "notebook_edit"
    description = "编辑 Jupyter Notebook (.ipynb) 文件中的单元格"
    input_params = ["notebook_path", "cell_id", "new_source", "cell_type", "edit_mode"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "notebook_path": {"type": "string", "description": "Jupyter notebook 文件路径 (.ipynb)"},
            "cell_id": {"type": "string", "description": "单元格 ID 或索引 (如 'cell-0')"},
            "new_source": {"type": "string", "description": "新的单元格内容"},
            "cell_type": {"type": "string", "enum": ["code", "markdown"], "description": "单元格类型"},
            "edit_mode": {"type": "string", "enum": ["replace", "insert", "delete"], "description": "编辑模式 (默认 replace)"}
        },
        "required": ["notebook_path", "new_source"]
    }
    requires_permission = True
    danger_level = "medium"
    parallel_safe = False
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        notebook_path = kwargs.get('notebook_path')
        new_source = kwargs.get('new_source')
        
        if not notebook_path:
            raise ValueError("notebook_path parameter is required")
        if not new_source and kwargs.get('edit_mode') != 'delete':
            raise ValueError("new_source parameter is required")
        
        edit_mode = kwargs.get('edit_mode', 'replace')
        if edit_mode not in ['replace', 'insert', 'delete']:
            raise ValueError("edit_mode must be 'replace', 'insert', or 'delete'")
        
        if edit_mode == 'insert' and not kwargs.get('cell_type'):
            raise ValueError("cell_type is required when using edit_mode='insert'")
        
        return {
            'notebook_path': str(notebook_path),
            'cell_id': kwargs.get('cell_id'),
            'new_source': str(new_source) if new_source else '',
            'cell_type': kwargs.get('cell_type', 'code'),
            'edit_mode': edit_mode
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """编辑 notebook"""
        notebook_path = input_data['notebook_path']
        cell_id = input_data.get('cell_id')
        new_source = input_data['new_source']
        cell_type = input_data['cell_type']
        edit_mode = input_data['edit_mode']
        
        # 解析路径
        if not os.path.isabs(notebook_path):
            notebook_path = os.path.abspath(notebook_path)
        
        # 检查文件扩展名
        if not notebook_path.endswith('.ipynb'):
            return "Error: File must be a Jupyter notebook (.ipynb file)"
        
        # 读取 notebook
        try:
            with open(notebook_path, 'r', encoding='utf-8') as f:
                notebook = json.load(f)
        except FileNotFoundError:
            return f"Error: Notebook file not found: {notebook_path}"
        except json.JSONDecodeError:
            return "Error: Notebook is not valid JSON"
        
        # 获取单元格列表
        cells = notebook.get('cells', [])
        
        # 解析 cell_id
        cell_index = self._parse_cell_index(cell_id, cells)
        
        try:
            if edit_mode == 'delete':
                return self._delete_cell(notebook, cells, cell_index, notebook_path)
            elif edit_mode == 'insert':
                return self._insert_cell(notebook, cells, cell_index, new_source, cell_type, notebook_path)
            else:
                return self._replace_cell(notebook, cells, cell_index, new_source, cell_type, notebook_path)
                
        except Exception as e:
            logger.error(f"[NotebookEdit] Error: {e}")
            return f"Error: {str(e)}"
    
    def _parse_cell_index(self, cell_id: Optional[str], cells: List) -> int:
        """解析单元格索引"""
        if cell_id is None:
            return 0
        
        # 尝试直接匹配 ID
        for i, cell in enumerate(cells):
            if cell.get('id') == cell_id:
                return i
        
        # 尝试解析为数字索引 (cell-N 格式)
        if cell_id.startswith('cell-'):
            try:
                index = int(cell_id[5:])
                if 0 <= index < len(cells):
                    return index
            except ValueError:
                pass
        
        # 尝试直接解析为数字
        try:
            index = int(cell_id)
            if 0 <= index < len(cells):
                return index
        except ValueError:
            pass
        
        raise ValueError(f"Cell '{cell_id}' not found in notebook")
    
    def _replace_cell(self, notebook: dict, cells: List, cell_index: int, 
                      new_source: str, cell_type: str, notebook_path: str) -> str:
        """替换单元格"""
        cell = cells[cell_index]
        cell['source'] = new_source
        
        # 更新单元格类型
        if cell_type and cell_type != cell.get('cell_type'):
            cell['cell_type'] = cell_type
            if cell_type == 'code' and 'outputs' not in cell:
                cell['outputs'] = []
                cell['execution_count'] = None
        
        # 重置执行计数
        if cell.get('cell_type') == 'code':
            cell['execution_count'] = None
            cell['outputs'] = []
        
        self._save_notebook(notebook, notebook_path)
        return f"Updated cell {cell_index} in {notebook_path}"
    
    def _insert_cell(self, notebook: dict, cells: List, cell_index: int,
                     new_source: str, cell_type: str, notebook_path: str) -> str:
        """插入新单元格"""
        new_cell = {
            'cell_type': cell_type,
            'source': new_source,
            'metadata': {}
        }
        
        if cell_type == 'code':
            new_cell['execution_count'] = None
            new_cell['outputs'] = []
        
        # 生成 ID (nbformat 4.5+)
        if notebook.get('nbformat', 4) >= 4:
            import random
            import string
            new_cell['id'] = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        
        cells.insert(cell_index + 1, new_cell)
        self._save_notebook(notebook, notebook_path)
        return f"Inserted new {cell_type} cell after cell {cell_index} in {notebook_path}"
    
    def _delete_cell(self, notebook: dict, cells: List, cell_index: int, notebook_path: str) -> str:
        """删除单元格"""
        deleted_cell = cells.pop(cell_index)
        self._save_notebook(notebook, notebook_path)
        return f"Deleted cell {cell_index} from {notebook_path}"
    
    def _save_notebook(self, notebook: dict, notebook_path: str):
        """保存 notebook"""
        with open(notebook_path, 'w', encoding='utf-8') as f:
            json.dump(notebook, f, indent=1, ensure_ascii=False)
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取内容用于权限检查"""
        return f"{input_data.get('notebook_path', '')}: {input_data.get('edit_mode', 'replace')}"
