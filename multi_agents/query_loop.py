"""
Query Loop - 查询循环状态机
- while true + Continue/Terminal 控制流
- 状态跨迭代传递
- 流式输出
- 工具并发执行

核心设计：
1. query() 是主入口，返回 AsyncGenerator
2. queryLoop() 是核心循环，yield 事件，return Terminal
3. Continue 表示继续循环，Terminal 表示终止
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import (
    List, Dict, Any, Optional, AsyncGenerator, 
    Union, Callable, Awaitable
)
from openai import OpenAI

from .query_state_machine import (
    QueryState,
    QueryConfig,
    QueryLoop,
    Continue,
    Terminal,
    ContinueType,
    TerminalType,
    LoopResult,
    ToolExecutionContext,
    ChatMessage,
    ToolUseBlock,
    StreamEvent,
    ContentEvent,
    ToolExecutionEvent,
    CompactEvent,
    ChatStartedEvent,
    ChatCompletedEvent,
    ErrorEvent,
    CancelledEvent,
    parse_tool_calls_from_response,
)
from .tool_orchestrator import (
    ToolOrchestrator,
    ToolExecutor,
    ToolExecutionResult,
    ToolExecutionUpdate,
    build_tool_result_message,
    build_assistant_message,
)

logger = logging.getLogger(__name__)


# ==================== 查询依赖注入 ====================

@dataclass
class QueryDeps:
    """
    查询依赖
    
    用于依赖注入，方便测试和扩展
    """
    # LLM 客户端工厂
    create_client: Callable[[], OpenAI]
    
    # 工具执行器
    tool_executor: ToolExecutor
    
    # UUID 生成器
    uuid_gen: Callable[[], str] = lambda: str(uuid.uuid4())[:8]
    
    # 压缩函数（可选）
    compact_func: Optional[Callable] = None
    
    # Token 估算函数
    estimate_tokens: Callable[[List[Dict]], int] = lambda msgs: sum(len(m.get("content", "")) for m in msgs) // 3


# ==================== 查询循环实现 ====================

class StatefulQueryLoop(QueryLoop):
    """
    状态机查询循环
    
    实现 Claude-Code 风格的 while(true) + Continue/Terminal 模式
    """
    
    def __init__(
        self,
        config: QueryConfig = None,
        deps: QueryDeps = None
    ):
        super().__init__(config)
        self.deps = deps
    
    async def run(
        self,
        initial_state: QueryState,
        context: ToolExecutionContext
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        运行查询循环
        
        这是主入口，yield 流式事件，正常结束时不返回值
        
        Args:
            initial_state: 初始状态
            context: 工具执行上下文
        
        Yields:
            流式事件
        """
        # 发送开始事件
        yield ChatStartedEvent(
            agent_name=context.agent_name,
            conversation_uuid=context.conversation_uuid
        )
        
        # 运行核心循环
        terminal = None
        try:
            async for event in self._query_loop(initial_state, context):
                yield event
        except asyncio.CancelledError:
            yield CancelledEvent(message="Query cancelled")
            return
        except Exception as e:
            logger.error(f"[QueryLoop] Error: {e}", exc_info=True)
            yield ErrorEvent(message=str(e))
            return
    
    async def _query_loop(
        self,
        initial_state: QueryState,
        context: ToolExecutionContext
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        核心查询循环
        
        while(true) 模式：
        - yield 事件
        - continue 继续下一轮
        - return 终止循环
        """
        state = initial_state
        
        # 创建工具编排器
        orchestrator = ToolOrchestrator(
            tool_executor=self.deps.tool_executor,
            max_concurrency=self.config.max_tool_concurrency
        )
        
        # 主循环
        while True:
            # 解构状态
            turn_count = state.turn_count
            
            logger.info(f"[QueryLoop] ========== Turn {turn_count} ==========")
            
            # 检查取消
            if context.cancel_event.is_set():
                yield CancelledEvent(message="User cancelled")
                return
            
            # 重置当前迭代状态
            state.reset_for_next_iteration()
            
            # 调用 LLM
            response_text, tool_calls_chunks = "", {}
            
            try:
                async for event in self._call_llm(state, context):
                    if isinstance(event, ContentEvent):
                        response_text += event.content
                        state.current_response_text = response_text
                        state.accumulated_response_text += event.content
                        yield event
                    elif isinstance(event, dict) and event.get("type") == "tool_call_chunk":
                        # 收集工具调用块
                        idx = event.get("index")
                        tool_calls_chunks[idx] = event.get("data", {})
            except Exception as e:
                logger.error(f"[QueryLoop] LLM call error: {e}")
                yield ErrorEvent(message=f"LLM error: {str(e)}")
                return
            
            # 记录响应文本
            if response_text:
                state.add_execution_item({
                    "type": "text",
                    "content": response_text
                })
            
            # 解析工具调用
            tool_calls = parse_tool_calls_from_response(tool_calls_chunks)
            
            logger.info(f"[QueryLoop] Response text length: {len(response_text)}")
            logger.info(f"[QueryLoop] Tool calls: {len(tool_calls)}")
            
            # 如果没有工具调用，任务完成
            if not tool_calls:
                logger.info(f"[QueryLoop] No tool calls, task completed")
                
                # 保存最终消息
                state.add_message(ChatMessage(
                    uuid=self.deps.uuid_gen(),
                    role="assistant",
                    content=response_text
                ))
                
                yield ChatCompletedEvent(
                    content=state.accumulated_response_text,
                    agent_name=context.agent_name
                )
                return
            
            # 构建助手消息并添加到状态
            assistant_msg = ChatMessage(
                uuid=self.deps.uuid_gen(),
                role="assistant",
                content=response_text or None,
                tool_calls=[tc.to_dict() for tc in tool_calls]
            )
            state.add_message(assistant_msg)
            
            # 执行工具
            logger.info(f"[QueryLoop] Executing {len(tool_calls)} tool calls")
            
            # 将 ToolUseBlock 转换为字典格式
            tool_call_dicts = [tc.to_dict() for tc in tool_calls]
            
            # 使用编排器执行工具
            tool_results = []
            async for update in orchestrator.run_tools(tool_calls, context):
                # yield 执行事件
                yield update.event
                
                # 记录结果
                if update.result:
                    tool_results.append(update.result)
                    
                    # 添加执行项
                    state.add_execution_item({
                        "type": "tool",
                        "tool_info": update.result.to_dict()
                    })
                    
                    # 添加工具结果消息
                    state.add_message(ChatMessage(
                        uuid=self.deps.uuid_gen(),
                        role="tool",
                        content=update.result.output,
                        tool_call_id=update.result.tool_call_id
                    ))
            
            # 更新状态
            state.tool_executions.extend([r.to_dict() for r in tool_results])
            state.turn_count += 1
            
            # 继续下一轮迭代（隐式 continue）
            logger.info(f"[QueryLoop] Tool execution completed, continuing to next iteration...")
    
    async def _call_llm(
        self,
        state: QueryState,
        context: ToolExecutionContext
    ) -> AsyncGenerator[Union[ContentEvent, dict], None]:
        """
        调用 LLM 并流式输出
        
        Args:
            state: 当前状态
            context: 执行上下文
        
        Yields:
            内容事件或工具调用块
        """
        # 构建消息列表
        messages = self._build_messages(state)
        
        # 获取客户端
        client = self.deps.create_client()
        
        # 获取模型
        model = context.user_context.get("model", "gpt-4")
        
        # 构建请求参数
        params = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "stream": True,
        }
        
        # 添加工具定义
        tools = self._get_tools_definition()
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        
        logger.info(f"[QueryLoop] Calling LLM: model={model}, messages={len(messages)}, tools={len(tools) if tools else 0}")
        
        # 流式调用
        stream = client.chat.completions.create(**params)
        
        tool_calls_chunks = {}
        
        for chunk in stream:
            # 检查取消
            if context.cancel_event.is_set():
                break
            
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                
                # 处理内容
                if delta.content:
                    yield ContentEvent(
                        content=delta.content,
                        iteration=state.turn_count
                    )
                
                # 处理工具调用
                if hasattr(delta, 'tool_calls') and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_chunks:
                            tool_calls_chunks[idx] = {
                                'id': '',
                                'type': 'function',
                                'function': {'name': '', 'arguments': ''}
                            }
                        
                        if tc.id:
                            tool_calls_chunks[idx]['id'] = tc.id
                        if tc.type:
                            tool_calls_chunks[idx]['type'] = tc.type
                        if tc.function:
                            if tc.function.name:
                                tool_calls_chunks[idx]['function']['name'] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_chunks[idx]['function']['arguments'] += tc.function.arguments
        
        # 返回工具调用块
        for idx, data in tool_calls_chunks.items():
            yield {
                "type": "tool_call_chunk",
                "index": idx,
                "data": data
            }
    
    def _build_messages(self, state: QueryState) -> List[Dict[str, Any]]:
        """
        构建消息列表
        
        Args:
            state: 当前状态
        
        Returns:
            API 格式的消息列表
        """
        messages = []
        
        # 系统消息
        if state.system_prompt:
            messages.append({
                "role": "system",
                "content": state.system_prompt
            })
        
        # 用户上下文
        for key, value in state.user_context.items():
            if value:
                messages.append({
                    "role": "system",
                    "content": f"[{key}]\n{value}"
                })
        
        # 历史消息
        for msg in state.messages:
            messages.append(msg.to_api_format())
        
        return messages
    
    def _get_tools_definition(self) -> List[Dict[str, Any]]:
        """
        获取工具定义
        
        Returns:
            OpenAI 格式的工具定义列表
        """
        # 这里返回工具定义，实际实现应该从工具注册表获取
        # 为了简化，这里返回一个空列表，实际使用时需要填充
        return []


# ==================== 便捷函数 ====================

async def run_query_loop(
    system_prompt: str,
    messages: List[Dict[str, str]],
    tool_executor: ToolExecutor,
    context: ToolExecutionContext,
    config: QueryConfig = None
) -> AsyncGenerator[StreamEvent, None]:
    """
    运行查询循环的便捷函数
    
    Args:
        system_prompt: 系统提示词
        messages: 消息列表
        tool_executor: 工具执行器
        context: 执行上下文
        config: 查询配置
    
    Yields:
        流式事件
    """
    # 创建初始状态
    initial_state = QueryState(
        messages=[ChatMessage(
            uuid=str(uuid.uuid4())[:8],
            role=m.get("role", "user"),
            content=m.get("content", "")
        ) for m in messages],
        system_prompt=system_prompt,
        user_context=context.user_context if hasattr(context, 'user_context') else {}
    )
    
    # 创建依赖
    def create_client():
        return OpenAI(
            api_key=context.llm_config.get('api_key', 'local-dev-key'),
            base_url=context.llm_config.get('base_url', 'http://localhost:8080/v1'),
            timeout=300.0
        )
    
    deps = QueryDeps(
        create_client=create_client,
        tool_executor=tool_executor
    )
    
    # 创建循环
    loop = StatefulQueryLoop(config=config, deps=deps)
    
    # 运行
    async for event in loop.run(initial_state, context):
        yield event
