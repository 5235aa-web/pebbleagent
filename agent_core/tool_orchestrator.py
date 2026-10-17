"""
Tool Orchestrator - 工具编排器

实现智能的工具并发执行：
1. 并发安全工具并行执行
2. 非安全工具串行执行
3. 流式输出执行进度
4. 结果按正确顺序返回

核心设计：
- 使用 asyncio.gather 实现并行执行
- 使用 async generator 实现流式输出
- 保持执行顺序和结果一致性
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import (
    List, Dict, Any, Optional, AsyncGenerator, 
    Callable, Awaitable, Tuple
)
from abc import ABC, abstractmethod
import logging

from .query_state_machine import (
    ToolUseBlock,
    ToolExecutionEvent,
    ToolExecutionContext,
)
from .tool_concurrency import (
    is_concurrency_safe,
    partition_tool_calls,
    ToolCallBatch,
    get_max_concurrency,
)

logger = logging.getLogger(__name__)


# ==================== 工具执行结果 ====================

@dataclass
class ToolExecutionResult:
    """工具执行结果"""
    tool_call_id: str
    tool_name: str
    status: str  # "completed", "failed"
    input: str
    output: str
    duration: int  # 毫秒
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.tool_call_id,
            "name": self.tool_name,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "duration": self.duration,
            "error": self.error
        }


@dataclass
class ToolExecutionUpdate:
    """工具执行更新（用于流式输出）"""
    event: ToolExecutionEvent
    result: Optional[ToolExecutionResult] = None


# ==================== 工具执行器接口 ====================

class ToolExecutor(ABC):
    """工具执行器抽象基类"""
    
    @abstractmethod
    async def execute(
        self, 
        tool_name: str, 
        args: Dict[str, Any],
        context: ToolExecutionContext
    ) -> str:
        """
        执行工具
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文
        
        Returns:
            工具执行结果字符串
        """
        pass


# ==================== 并发工具执行器 ====================

class ToolOrchestrator:
    """
    工具编排器
    
    负责智能调度工具执行：
    - 并发安全工具并行执行
    - 非安全工具串行执行
    - 流式输出执行进度
    """
    
    def __init__(
        self,
        tool_executor: ToolExecutor,
        max_concurrency: int = None
    ):
        """
        初始化工具编排器
        
        Args:
            tool_executor: 实际执行工具的执行器
            max_concurrency: 最大并发数（默认从配置获取）
        """
        self.tool_executor = tool_executor
        self.max_concurrency = max_concurrency or get_max_concurrency()
        
        # 正在执行的工具 ID 集合
        self._in_progress_tool_ids: set = set()
    
    def set_in_progress(self, tool_id: str):
        """标记工具为执行中"""
        self._in_progress_tool_ids.add(tool_id)
    
    def mark_complete(self, tool_id: str):
        """标记工具执行完成"""
        self._in_progress_tool_ids.discard(tool_id)
    
    def get_in_progress_count(self) -> int:
        """获取正在执行的工具数量"""
        return len(self._in_progress_tool_ids)
    
    async def run_tools(
        self,
        tool_calls: List[ToolUseBlock],
        context: ToolExecutionContext
    ) -> AsyncGenerator[ToolExecutionUpdate, None]:
        """
        运行工具调用
        
        根据工具的并发安全性智能调度执行：
        1. 分区工具调用
        2. 并发安全工具并行执行
        3. 非安全工具串行执行
        4. 流式输出执行进度
        
        Args:
            tool_calls: 工具调用列表
            context: 执行上下文
        
        Yields:
            工具执行更新事件
        """
        if not tool_calls:
            return
        
        # 转换为字典格式用于分区
        tool_call_dicts = []
        for tc in tool_calls:
            tool_call_dicts.append({
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments
            })
        
        # 分区
        batches = partition_tool_calls(tool_call_dicts)
        
        logger.info(f"[ToolOrchestrator] Partitioned {len(tool_calls)} tools into {len(batches)} batches")
        for i, batch in enumerate(batches):
            mode = "PARALLEL" if batch.is_concurrency_safe else "SERIAL"
            logger.info(f"[ToolOrchestrator] Batch {i+1}: {mode} - {[tc['name'] for tc in batch.tool_calls]}")
        
        # 按批次执行
        for batch in batches:
            if batch.is_concurrency_safe:
                # 并行执行
                async for update in self._run_tools_concurrently(batch.tool_calls, context):
                    yield update
            else:
                # 串行执行
                async for update in self._run_tools_serially(batch.tool_calls, context):
                    yield update
    
    async def _run_tools_concurrently(
        self,
        tool_calls: List[Dict[str, Any]],
        context: ToolExecutionContext
    ) -> AsyncGenerator[ToolExecutionUpdate, None]:
        """
        并发执行工具
        
        使用 asyncio.gather 实现并行执行，
        但通过队列实现流式输出。
        
        Args:
            tool_calls: 工具调用列表
            context: 执行上下文
        
        Yields:
            工具执行更新事件
        """
        # 创建输出队列
        output_queue: asyncio.Queue[ToolExecutionUpdate] = asyncio.Queue()
        
        # 创建所有工具的执行任务（受 max_concurrency 信号量约束）
        sem = asyncio.Semaphore(max(1, int(self.max_concurrency or 10)))

        async def _guarded_execute(tc):
            async with sem:
                return await self._execute_tool_with_queue(tc, context, output_queue)

        tasks = []
        for tool_call in tool_calls:
            task = asyncio.create_task(_guarded_execute(tool_call))
            tasks.append(task)
        
        # 收集结果的协程
        results_collected = 0
        total_tools = len(tool_calls)
        
        # 同时等待任务完成和队列输出
        all_done = asyncio.Event()
        
        async def wait_for_all_tasks():
            await asyncio.gather(*tasks, return_exceptions=True)
            all_done.set()
        
        # 启动等待任务
        wait_task = asyncio.create_task(wait_for_all_tasks())
        
        # 从队列中读取输出
        while results_collected < total_tools or not all_done.is_set():
            try:
                # 带超时的队列读取，避免死锁
                update = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                yield update
                if update.result:
                    results_collected += 1
            except asyncio.TimeoutError:
                # 检查是否所有任务都完成了
                if all_done.is_set():
                    break
                # 检查取消
                if context.cancel_event.is_set():
                    for task in tasks:
                        task.cancel()
                    break
                continue
        
        # 确保所有任务完成
        await wait_task
    
    async def _run_tools_serially(
        self,
        tool_calls: List[Dict[str, Any]],
        context: ToolExecutionContext
    ) -> AsyncGenerator[ToolExecutionUpdate, None]:
        """
        串行执行工具
        
        按顺序执行每个工具，等待前一个完成后再执行下一个。
        
        Args:
            tool_calls: 工具调用列表
            context: 执行上下文
        
        Yields:
            工具执行更新事件
        """
        for tool_call in tool_calls:
            # 检查取消
            if context.cancel_event.is_set():
                break
            
            # 执行单个工具
            async for update in self._execute_single_tool(tool_call, context):
                yield update
    
    async def _execute_tool_core(
        self,
        tool_call: Dict[str, Any],
        context: ToolExecutionContext
    ) -> AsyncGenerator[ToolExecutionUpdate, None]:
        """
        工具执行核心逻辑（并行/串行共用的单一实现）

        由本 async generator 承担全部执行与事件构造，两个消费端各做薄包装。

        Yields:
            工具执行更新事件
        """
        tool_id = tool_call.get("id", str(uuid.uuid4())[:8])
        tool_name = tool_call.get("name", "unknown")
        tool_args = tool_call.get("arguments", {})
        
        # 标记为执行中
        self.set_in_progress(tool_id)
        
        try:
            # 发送开始事件
            start_event = ToolExecutionEvent(
                tool_execution_id=tool_id,
                tool_name=tool_name,
                status="running",
                input=json.dumps(tool_args, ensure_ascii=False)
            )
            yield ToolExecutionUpdate(event=start_event)
            
            # 执行工具
            start_time = time.time()
            status = "completed"
            output = ""
            error = None
            
            # 设置快照上下文中的 tool_call_id
            if hasattr(context, 'snapshot_tool_call_id'):
                context.snapshot_tool_call_id = tool_call.get("id", "unknown")
            
            try:
                output = await self.tool_executor.execute(tool_name, tool_args, context)
                if output.startswith("Error:"):
                    status = "failed"
                    error = output
            except Exception as e:
                status = "failed"
                output = f"Error: {str(e)}"
                error = str(e)
            
            duration = int((time.time() - start_time) * 1000)
            
            # 发送完成事件
            complete_event = ToolExecutionEvent(
                tool_execution_id=tool_id,
                tool_name=tool_name,
                status=status,
                input=json.dumps(tool_args, ensure_ascii=False),
                output=output,
                duration=duration
            )
            
            result = ToolExecutionResult(
                tool_call_id=tool_id,
                tool_name=tool_name,
                status=status,
                input=json.dumps(tool_args, ensure_ascii=False),
                output=output,
                duration=duration,
                error=error
            )
            
            yield ToolExecutionUpdate(event=complete_event, result=result)
            
        except asyncio.CancelledError:
            # 被取消
            cancel_event = ToolExecutionEvent(
                tool_execution_id=tool_id,
                tool_name=tool_name,
                status="cancelled",
                input=json.dumps(tool_args, ensure_ascii=False)
            )
            yield ToolExecutionUpdate(event=cancel_event)
            return  # 不要 raise，否则 async for 会把已发送的事件吞掉
        
        finally:
            self.mark_complete(tool_id)
    
    async def _execute_tool_with_queue(
        self,
        tool_call: Dict[str, Any],
        context: ToolExecutionContext,
        output_queue: asyncio.Queue
    ):
        """
        并行批次消费端：遍历核心事件流并放入输出队列
        
        Args:
            tool_call: 工具调用
            context: 执行上下文
            output_queue: 输出队列
        """
        gen = self._execute_tool_core(tool_call, context)
        try:
            async for update in gen:
                await output_queue.put(update)
        finally:
            await gen.aclose()
    
    async def _execute_single_tool(
        self,
        tool_call: Dict[str, Any],
        context: ToolExecutionContext
    ) -> AsyncGenerator[ToolExecutionUpdate, None]:
        """
        串行批次消费端：直接转发核心事件流
        
        Args:
            tool_call: 工具调用
            context: 执行上下文
        
        Yields:
            工具执行更新事件
        """
        async for update in self._execute_tool_core(tool_call, context):
            yield update


# ==================== 辅助函数 ====================

def build_tool_result_message(
    tool_call_id: str,
    result: str,
    is_error: bool = False
) -> Dict[str, Any]:
    """
    构建工具结果消息
    
    Args:
        tool_call_id: 工具调用 ID
        result: 执行结果
        is_error: 是否是错误
    
    Returns:
        工具结果消息字典
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": result,
        "is_error": is_error
    }


def build_assistant_message(
    content: str,
    tool_calls: List[ToolUseBlock] = None
) -> Dict[str, Any]:
    """
    构建助手消息
    
    Args:
        content: 消息内容
        tool_calls: 工具调用列表
    
    Returns:
        助手消息字典
    """
    message = {
        "role": "assistant",
        "content": content or None
    }
    
    if tool_calls:
        message["tool_calls"] = [tc.to_dict() for tc in tool_calls]
    
    return message
