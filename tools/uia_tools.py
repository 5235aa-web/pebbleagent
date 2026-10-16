"""
UIA3 工具封装

Windows UI Automation API 工具
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class UIAGetElementsTool(BaseTool[Dict[str, Any], str]):
    """获取 UI 元素树工具"""
    
    name = "uia_get_elements"
    description = "获取窗口的 UI 元素树（名称、类型、坐标）"
    input_params = ["window_name", "depth"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "window_name": {"type": "string", "description": "窗口名称（可选，不指定则获取当前焦点窗口）"},
            "depth": {"type": "integer", "description": "遍历深度（默认 5）"}
        },
        "required": []
    }
    requires_permission = False
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        return {
            'window_name': kwargs.get('window_name'),
            'depth': kwargs.get('depth', 5)
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        from uia3_tool import get_ui_elements
        return get_ui_elements(input_data['window_name'], input_data['depth'])


class UIAFindElementTool(BaseTool[Dict[str, Any], str]):
    """查找 UI 元素工具"""
    
    name = "uia_find_element"
    description = "按名称或类型查找 UI 元素"
    input_params = ["name", "type", "window_name"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "元素名称（支持模糊匹配）"},
            "type": {"type": "string", "description": "元素类型（如 Button, Edit, ComboBox）"},
            "window_name": {"type": "string", "description": "窗口名称（可选）"}
        },
        "required": []
    }
    requires_permission = False
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        return {
            'name': kwargs.get('name'),
            'type': kwargs.get('type'),
            'window_name': kwargs.get('window_name')
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        from uia3_tool import find_element
        return find_element(input_data['name'], input_data['type'], input_data['window_name'])


class UIAGetWindowListTool(BaseTool[Dict[str, Any], str]):
    """获取窗口列表工具"""
    
    name = "uia_get_window_list"
    description = "获取所有打开的窗口列表"
    input_params = []
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": []
    }
    requires_permission = False
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        return {}
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        from uia3_tool import get_window_list
        return get_window_list()
