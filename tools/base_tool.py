"""
统一工具基类

提供：
- Pydantic Schema验证
- 权限系统集成
- 统一结果格式
- 标准化描述
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Type, Generic, TypeVar
from enum import Enum
import logging
import time
import asyncio

logger = logging.getLogger(__name__)

# 类型变量
InputT = TypeVar('InputT')
OutputT = TypeVar('OutputT')


class ToolStatus(str, Enum):
    """工具执行状态"""
    SUCCESS = "success"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    VALIDATION_ERROR = "validation_error"


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    status: ToolStatus
    result: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "execution_time": self.execution_time,
            "metadata": self.metadata
        }
    
    def __str__(self) -> str:
        if self.success:
            return str(self.result) if self.result else "Success"
        return f"Error: {self.error}"


@dataclass
class PermissionResult:
    """权限检查结果"""
    allowed: bool
    reason: str = ""
    ask_user: bool = False
    target: str = ""


class BaseTool(ABC, Generic[InputT, OutputT]):
    """
    工具基类
    
    所有工具应继承此类并实现：
    - name: 工具名称
    - description: 工具描述
    - call(): 执行逻辑
    
    可选覆盖：
    - parameters_schema: JSON Schema格式的参数定义（用于原生tool_use）
    - input_schema: Pydantic Model用于参数验证
    - requires_permission: 是否需要权限检查
    - danger_level: 危险等级
    - check_permissions(): 权限检查逻辑
    - parallel_safe: 是否可并行执行（默认False）
    """
    
    # 工具名称（子类必须覆盖）
    name: str = "base_tool"
    
    # 工具描述（子类必须覆盖）
    description: str = "Base tool class"
    
    # 输入参数名列表（用于LLM调用，向后兼容）
    input_params: List[str] = []
    
    # JSON Schema格式的参数定义（用于OpenAI原生tool_use）
    # 子类应覆盖此属性，格式遵循 JSON Schema 规范
    parameters_schema: Dict[str, Any] = {}
    
    # 是否需要权限检查
    requires_permission: bool = True
    
    # 危险等级: low, medium, high, critical
    danger_level: str = "medium"
    
    # 是否可安全并行执行（只读、无副作用的工具设为True）
    parallel_safe: bool = False
    
    # 是否需要用户交互（如 ask_user_question 需要等待用户回复）
    requires_user_interaction: bool = False
    
    def __init__(self):
        self._start_time = 0.0
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """
        验证输入参数
        
        子类可以覆盖此方法实现自定义验证逻辑
        
        Returns:
            验证后的参数字典
        """
        return kwargs
    
    async def check_permissions(self, input_data: Dict[str, Any]) -> PermissionResult:
        """
        检查权限
        
        Args:
            input_data: 输入数据
            
        Returns:
            PermissionResult
        """
        if not self.requires_permission:
            return PermissionResult(allowed=True, reason="No permission required")
        
        # 所有线程的工具执行都经过统一权限层，不按线程跳过检查。
        
        # 默认实现：调用权限管理器
        try:
            from gateway.permission_manager import get_permission_manager
            perm_manager = get_permission_manager()
            
            # 提取内容（子类可覆盖）
            content = self._extract_content(input_data)
            
            # 提取原因（如果有）
            reason = input_data.get("reason", "") if isinstance(input_data, dict) else ""
            
            allowed, message = await perm_manager.check_permission(
                tool_name=self.name,
                content=content,
                target=content or self.name,
                reason=reason or f"Executing {self.name}"
            )
            
            return PermissionResult(
                allowed=allowed,
                reason=message,
                ask_user=not allowed and "rule" not in message.lower()
            )
        except ImportError:
            # 权限管理器不可用：fail-closed 拒绝
            logger.error(f"[{self.name}] Permission manager not available, denying (fail-closed)")
            return PermissionResult(allowed=False, reason="Permission manager not available (fail-closed)")
        except Exception as e:
            logger.warning(f"[{self.name}] Permission check failed: {e}")
            # 权限检查失败时，为了安全起见，默认拒绝
            return PermissionResult(allowed=False, reason=f"Permission check error: {e}")
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """
        从输入数据中提取内容（用于权限检查）
        子类可覆盖此方法
        """
        if isinstance(input_data, dict):
            if 'code' in input_data:
                return str(input_data['code'])[:100]
            elif 'path' in input_data:
                return str(input_data['path'])
            elif 'command' in input_data:
                return str(input_data['command'])
        return None
    
    @abstractmethod
    async def call(self, input_data: Dict[str, Any]) -> OutputT:
        """
        执行工具（子类必须实现）
        
        Args:
            input_data: 验证后的输入数据
            
        Returns:
            执行结果
        """
        pass
    
    async def execute(self, **kwargs) -> ToolResult:
        """
        执行工具（统一入口）
        
        包含完整的流程：
        1. 参数验证
        2. 权限检查
        3. 执行工具
        4. 结果序列化
        
        Args:
            **kwargs: 输入参数
            
        Returns:
            ToolResult
        """
        self._start_time = time.time()
        
        try:
            # 1. 参数验证
            validated_input = self.validate_input(**kwargs)
            
            # 2. 权限检查
            if self.requires_permission:
                perm_result = await self.check_permissions(validated_input)
                if not perm_result.allowed:
                    return ToolResult(
                        success=False,
                        status=ToolStatus.PERMISSION_DENIED,
                        error=perm_result.reason,
                        execution_time=time.time() - self._start_time
                    )
            
            # 3. 执行工具
            result = await self.call(validated_input)
            
            # 4. 返回成功结果
            return ToolResult(
                success=True,
                status=ToolStatus.SUCCESS,
                result=result,
                execution_time=time.time() - self._start_time,
                metadata=self._get_metadata(validated_input)
            )
            
        except ValueError as e:
            return ToolResult(
                success=False,
                status=ToolStatus.VALIDATION_ERROR,
                error=f"Validation error: {str(e)}",
                execution_time=time.time() - self._start_time
            )
        except Exception as e:
            logger.error(f"[{self.name}] Execution failed: {e}")
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                error=str(e),
                execution_time=time.time() - self._start_time
            )
    
    # 默认工具执行超时时间（秒）
    # 【修复】从 60 秒改为 10 秒，避免长时间等待
    DEFAULT_TOOL_TIMEOUT = 10
    
    def execute_sync(self, **kwargs) -> ToolResult:
        """
        同步执行工具
        
        用于在不支持async的环境中调用
        
        Args:
            **kwargs: 输入参数
            _timeout: 可选的超时时间（秒），默认10秒
            
        Returns:
            ToolResult
        """
        # 提取超时参数（内部使用，不从 kwargs 传递给工具）
        timeout = kwargs.pop('_timeout', self.DEFAULT_TOOL_TIMEOUT)
        
        # 【调试日志】
        logger.info(f"[{self.name}] execute_sync called with timeout={timeout}s")
        
        try:
            # 尝试获取当前事件循环
            loop = None
            try:
                loop = asyncio.get_running_loop()
                # 有运行中的事件循环，但我们在同步上下文中
                # 需要在新线程中运行异步代码
            except RuntimeError:
                # 没有运行中的事件循环
                loop = None
            
            if loop is not None:
                # 有运行中的事件循环，使用新线程执行
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    # 在新线程中创建新的事件循环并执行
                    def run_in_new_loop():
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            async def execute_with_timeout():
                                return await asyncio.wait_for(
                                    self.execute(**kwargs),
                                    timeout=timeout
                                )
                            return new_loop.run_until_complete(execute_with_timeout())
                        finally:
                            new_loop.close()
                    
                    future = executor.submit(run_in_new_loop)
                    try:
                        result = future.result(timeout=timeout + 1)  # 额外1秒用于线程开销
                        logger.info(f"[{self.name}] Tool execution completed successfully")
                        return result
                    except concurrent.futures.TimeoutError:
                        logger.error(f"[{self.name}] ========== TOOL TIMEOUT ==========")
                        logger.error(f"[{self.name}] Tool execution timed out after {timeout}s")
                        future.cancel()
                        return ToolResult(
                            success=False,
                            status=ToolStatus.TIMEOUT,
                            error=f"Tool execution timed out after {timeout} seconds",
                            execution_time=timeout
                        )
            else:
                # 没有运行中的事件循环，直接使用 asyncio.run
                async def execute_with_timeout():
                    return await asyncio.wait_for(
                        self.execute(**kwargs),
                        timeout=timeout
                    )
                result = asyncio.run(execute_with_timeout())
                logger.info(f"[{self.name}] Tool execution completed successfully")
                return result
                
        except asyncio.TimeoutError:
            logger.error(f"[{self.name}] ========== TOOL TIMEOUT ==========")
            logger.error(f"[{self.name}] Tool execution timed out after {timeout}s")
            return ToolResult(
                success=False,
                status=ToolStatus.TIMEOUT,
                error=f"Tool execution timed out after {timeout} seconds",
                execution_time=timeout
            )
        except Exception as e:
            logger.error(f"[{self.name}] Tool execution error: {e}")
            import traceback
            logger.error(f"[{self.name}] Traceback: {traceback.format_exc()}")
            return ToolResult(
                success=False,
                status=ToolStatus.ERROR,
                error=str(e),
                execution_time=0
            )
    
    def _get_metadata(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取执行元数据（子类可覆盖）
        """
        return {
            "tool_name": self.name,
            "danger_level": self.danger_level
        }
    
    def get_tool_description(self) -> str:
        """
        获取工具描述（用于LLM）
        """
        params_str = ""
        if self.input_params:
            params_str = f"\n\nParameters: {', '.join(self.input_params)}"
        
        return f"""Tool: {self.name}
Description: {self.description}
Danger Level: {self.danger_level}
Requires Permission: {self.requires_permission}{params_str}""".strip()
    
    def get_openai_tool_format(self) -> Dict[str, Any]:
        """
        获取OpenAI API原生tool_use格式的工具定义
        
        Returns:
            符合OpenAI tools参数格式的字典
        """
        # 如果子类定义了parameters_schema，使用它
        if self.parameters_schema:
            parameters = self.parameters_schema
        else:
            # 向后兼容：从input_params自动生成简易schema
            parameters = self._auto_generate_schema()
        
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters
            }
        }
    
    def _auto_generate_schema(self) -> Dict[str, Any]:
        """
        从input_params自动生成JSON Schema（向后兼容）
        
        仅在子类未定义parameters_schema时使用。
        生成的schema所有参数为string类型且可选。
        """
        if not self.input_params:
            return {"type": "object", "properties": {}}
        
        properties = {}
        for param in self.input_params:
            properties[param] = {"type": "string"}
        
        return {
            "type": "object",
            "properties": properties
        }


class SimpleTool(BaseTool[Dict[str, Any], str]):
    """
    简单工具基类
    
    用于快速创建不需要复杂验证的工具
    """
    
    def __init__(self, func: callable = None):
        super().__init__()
        self._func = func
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        if self._func:
            # 提取位置参数
            args = [input_data.get(p) for p in self.input_params if p in input_data]
            result = self._func(*args)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result) if result is not None else "Success"
        return "Not implemented"