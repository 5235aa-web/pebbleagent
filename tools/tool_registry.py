"""
工具注册中心

提供统一的工具注册、发现和执行机制
"""
from typing import Dict, Type, List, Optional, Any, Callable
from .base_tool import BaseTool, ToolResult, ToolStatus
import logging
import asyncio

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    工具注册中心（单例模式）
    
    提供：
    - 工具注册
    - 工具发现
    - 工具执行
    - 工具描述生成
    """
    
    _instance = None
    _tools: Dict[str, BaseTool] = {}
    _tool_classes: Dict[str, Type[BaseTool]] = {}
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def register(cls, tool_class: Type[BaseTool]) -> Type[BaseTool]:
        """
        注册工具（装饰器用法）
        
        Usage:
            @ToolRegistry.register
            class MyTool(BaseTool):
                ...
        """
        tool = tool_class()
        cls._tools[tool.name] = tool
        cls._tool_classes[tool.name] = tool_class
        logger.info(f"[ToolRegistry] Registered tool: {tool.name}")
        return tool_class
    
    @classmethod
    def register_instance(cls, tool: BaseTool):
        """注册工具实例"""
        cls._tools[tool.name] = tool
        logger.info(f"[ToolRegistry] Registered tool instance: {tool.name}")
    
    @classmethod
    def register_function(cls, name: str, func: Callable, description: str = "", 
                          danger_level: str = "medium", requires_permission: bool = True):
        """
        注册函数为工具（简化用法）
        
        用于快速将现有函数包装为工具
        """
        from .base_tool import SimpleTool
        
        class FunctionTool(SimpleTool):
            pass
        
        tool = FunctionTool(func)
        tool.name = name
        tool.description = description or f"Function: {name}"
        tool.danger_level = danger_level
        tool.requires_permission = requires_permission
        
        cls._tools[name] = tool
        logger.info(f"[ToolRegistry] Registered function as tool: {name}")
    
    @classmethod
    def unregister(cls, name: str) -> bool:
        """注销工具"""
        if name in cls._tools:
            del cls._tools[name]
            if name in cls._tool_classes:
                del cls._tool_classes[name]
            logger.info(f"[ToolRegistry] Unregistered tool: {name}")
            return True
        return False
    
    @classmethod
    def get_tool(cls, name: str) -> Optional[BaseTool]:
        """获取工具"""
        return cls._tools.get(name)
    
    @classmethod
    def has_tool(cls, name: str) -> bool:
        """检查工具是否存在"""
        return name in cls._tools
    
    @classmethod
    def list_tools(cls) -> List[str]:
        """列出所有工具名称"""
        return list(cls._tools.keys())
    
    @classmethod
    def get_all_tools(cls) -> Dict[str, BaseTool]:
        """获取所有工具"""
        return cls._tools.copy()
    
    @classmethod
    def get_tool_descriptions(cls) -> str:
        """获取所有工具描述（用于LLM）"""
        descriptions = []
        for name, tool in cls._tools.items():
            descriptions.append(tool.get_tool_description())
        return "\n\n---\n\n".join(descriptions)
    
    @classmethod
    def get_tools_openai_format(cls, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        获取OpenAI API原生tool_use格式的工具列表
        
        Args:
            allowed_tools: 可选，只返回指定名称的工具。
                          如果为None，返回所有已注册工具。
        
        Returns:
            符合OpenAI tools参数格式的列表
        """
        tools = []
        for name, tool in cls._tools.items():
            if allowed_tools and name not in allowed_tools:
                continue
            tools.append(tool.get_openai_tool_format())
        return tools
    
    @classmethod
    async def execute_tool(cls, name: str, **kwargs) -> ToolResult:
        """
        执行工具
        
        Args:
            name: 工具名称
            **kwargs: 输入参数
            
        Returns:
            ToolResult
        """
        tool = cls.get_tool(name)
        if not tool:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                error=f"Unknown tool: {name}"
            )
        
        return await tool.execute(**kwargs)
    
    @classmethod
    def execute_tool_sync(cls, name: str, **kwargs) -> ToolResult:
        """
        同步执行工具
        """
        tool = cls.get_tool(name)
        if not tool:
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                error=f"Unknown tool: {name}"
            )
        
        return tool.execute_sync(**kwargs)
    
    @classmethod
    def clear(cls):
        """清空所有注册的工具"""
        cls._tools.clear()
        cls._tool_classes.clear()
        logger.info("[ToolRegistry] All tools cleared")
    
    @classmethod
    def auto_register_from_module(cls, module_name: str):
        """
        从模块自动注册工具
        
        扫描模块中所有BaseTool子类并注册
        """
        import importlib
        import inspect
        
        try:
            module = importlib.import_module(module_name)
            
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    issubclass(obj, BaseTool) and 
                    obj != BaseTool and
                    obj != SimpleTool):
                    try:
                        cls.register(obj)
                    except Exception as e:
                        logger.error(f"Failed to register tool {name}: {e}")
                        
        except Exception as e:
            logger.error(f"Failed to import module {module_name}: {e}")


def auto_register_tools():
    """
    自动注册tools目录下的所有工具
    
    支持 PyInstaller (.py) 和 Nuitka (.pyd) 两种打包模式。
    在程序启动时调用。
    """
    import os
    import importlib
    import inspect
    from .base_tool import BaseTool, SimpleTool

    tools_dir = os.path.dirname(__file__)

    # 1) 静态导入：Nuitka 编译后 os.listdir 扫不到 .py，用静态列表兜底
    _STATIC_TOOL_MODULES = [
        "tools.ask_question_tool",
        "tools.brief_tool",
        "tools.execute_code_tool",
        "tools.file_tools",
        "tools.glob_tool",
        "tools.grep_tool",
        "tools.notebook_edit_tool",
        "tools.plan_mode_tool",
        "tools.powershell_tool",
        "tools.skill_tools",
        "tools.todo_tool",
        "tools.tool_search_tool",
        "tools.web_fetch_tool",
    ]
    for module_path in _STATIC_TOOL_MODULES:
        try:
            module = importlib.import_module(module_path)
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and
                    issubclass(obj, BaseTool) and
                    obj != BaseTool and
                    obj != SimpleTool):
                    ToolRegistry.register(obj)
        except Exception as e:
            logger.debug(f"Tool module {module_path} not available: {e}")

    # 2) 动态扫描：兼容开发模式的 .py 和 Nuitka 编译后的 .pyd
    for filename in os.listdir(tools_dir):
        is_py = filename.endswith('_tool.py') and filename != 'base_tool.py'
        is_pyd = filename.endswith('_tool.pyd') and filename != 'base_tool.pyd'
        if not (is_py or is_pyd):
            continue
        module_name = filename.rsplit('.', 1)[0]
        try:
            module = importlib.import_module(f"tools.{module_name}")
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and
                    issubclass(obj, BaseTool) and
                    obj != BaseTool and
                    obj != SimpleTool):
                    ToolRegistry.register(obj)
        except Exception as e:
            logger.error(f"Failed to load tool module {module_name}: {e}")


# 便捷函数
def get_tool(name: str) -> Optional[BaseTool]:
    """获取工具"""
    return ToolRegistry.get_tool(name)


def execute_tool(name: str, **kwargs) -> ToolResult:
    """执行工具（同步）"""
    return ToolRegistry.execute_tool_sync(name, **kwargs)


async def execute_tool_async(name: str, **kwargs) -> ToolResult:
    """执行工具（异步）"""
    return await ToolRegistry.execute_tool(name, **kwargs)