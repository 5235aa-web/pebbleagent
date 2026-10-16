"""
Extension Tools Loader - 自定义工具扩展加载器

扫描 extensions/tools/ 目录下的 .py 文件，动态导入并注册工具。
每个扩展文件需暴露 4 个约定属性：
- tool_name: str
- tool_description: str
- tool_parameters: dict (JSON Schema)
- async def execute(params, context=None) -> str
"""

import os
import importlib.util
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# 扩展工具目录
EXTENSIONS_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "extensions", "tools")

# 内存缓存
_extension_tools_cache: Optional[List[Dict[str, Any]]] = None
_extension_executors_cache: Optional[Dict[str, Any]] = None


def discover_extension_tools() -> List[Dict[str, Any]]:
    """
    发现并加载所有扩展工具定义（用于 LLM tool list）
    
    Returns:
        OpenAI 格式的工具定义列表
    """
    global _extension_tools_cache
    
    if _extension_tools_cache is not None:
        return _extension_tools_cache
    
    tools = []
    
    if not os.path.isdir(EXTENSIONS_TOOLS_DIR):
        logger.debug(f"[Extensions] Tools directory not found: {EXTENSIONS_TOOLS_DIR}")
        _extension_tools_cache = []
        return []
    
    for filename in sorted(os.listdir(EXTENSIONS_TOOLS_DIR)):
        if filename.startswith("_"):
            continue
        if not (filename.endswith(".py") or filename.endswith(".pyd")):
            continue
        
        filepath = os.path.join(EXTENSIONS_TOOLS_DIR, filename)
        module_name = f"extensions.tools.{filename.rsplit('.', 1)[0]}"
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                logger.warning(f"[Extensions] Failed to create spec for {filename}")
                continue
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 验证必需属性
            required_attrs = ["tool_name", "tool_description", "tool_parameters", "execute"]
            missing = [attr for attr in required_attrs if not hasattr(module, attr)]
            if missing:
                logger.warning(f"[Extensions] {filename} missing required attributes: {missing}")
                continue
            
            # 构建 OpenAI 格式工具定义
            tool_def = {
                "type": "function",
                "function": {
                    "name": module.tool_name,
                    "description": module.tool_description,
                    "parameters": module.tool_parameters
                }
            }
            tools.append(tool_def)
            logger.info(f"[Extensions] Loaded tool: {module.tool_name} from {filename}")
            
        except Exception as e:
            logger.error(f"[Extensions] Failed to load {filename}: {e}")
    
    _extension_tools_cache = tools
    return tools


def get_extension_executors() -> Dict[str, Any]:
    """
    获取扩展工具执行器映射（用于运行时调用）
    
    Returns:
        {tool_name: execute_function} 映射
    """
    global _extension_executors_cache
    
    if _extension_executors_cache is not None:
        return _extension_executors_cache
    
    executors = {}
    
    if not os.path.isdir(EXTENSIONS_TOOLS_DIR):
        _extension_executors_cache = {}
        return {}
    
    for filename in sorted(os.listdir(EXTENSIONS_TOOLS_DIR)):
        if filename.startswith("_"):
            continue
        if not (filename.endswith(".py") or filename.endswith(".pyd")):
            continue
        
        filepath = os.path.join(EXTENSIONS_TOOLS_DIR, filename)
        module_name = f"extensions.tools.{filename.rsplit('.', 1)[0]}"
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                continue
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if hasattr(module, "tool_name") and hasattr(module, "execute"):
                executors[module.tool_name] = module.execute
                logger.debug(f"[Extensions] Registered executor: {module.tool_name}")
                
        except Exception as e:
            logger.error(f"[Extensions] Failed to load executor from {filename}: {e}")
    
    _extension_executors_cache = executors
    return executors


def try_execute_extension(tool_name: str, params: Dict[str, Any], context: Any = None) -> Optional[str]:
    """
    尝试执行扩展工具（同步版本，供 tool_executor 调用）
    
    Args:
        tool_name: 工具名称
        params: 工具参数
        context: 执行上下文（可选）
    
    Returns:
        执行结果字符串，如果不是扩展工具返回 None
    """
    executors = get_extension_executors()
    execute_fn = executors.get(tool_name)
    
    if execute_fn is None:
        return None
    
    try:
        import asyncio
        if asyncio.iscoroutinefunction(execute_fn):
            # 尝试获取当前事件循环
            try:
                loop = asyncio.get_running_loop()
                # 如果已经在事件循环中，无法用 run_until_complete
                # 创建新任务并在新线程中运行
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_run_async_in_new_loop, execute_fn, params, context)
                    return future.result(timeout=30)
            except RuntimeError:
                # 没有运行中的循环，创建新的
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(execute_fn(params, context))
                finally:
                    loop.close()
        else:
            return execute_fn(params, context)
    except Exception as e:
        logger.error(f"[Extensions] Error executing {tool_name}: {e}")
        return f"Error: Extension tool '{tool_name}' failed: {str(e)}"


def _run_async_in_new_loop(coro_fn, params, context):
    """在新事件循环中运行异步函数（用于线程池）"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_fn(params, context))
    finally:
        loop.close()


def reload_extensions() -> None:
    """重新加载扩展工具（清除缓存）"""
    global _extension_tools_cache, _extension_executors_cache
    _extension_tools_cache = None
    _extension_executors_cache = None
    logger.info("[Extensions] Cache cleared, will reload on next access")


# 导出
__all__ = [
    "discover_extension_tools",
    "get_extension_executors",
    "try_execute_extension",
    "reload_extensions",
]