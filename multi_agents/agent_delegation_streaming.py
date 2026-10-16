"""
Agent Delegation Streaming Module - 支持事件流的Agent委托

提供完整的Agent委托功能，支持：
- 实时事件流（步骤开始、工具调用、执行完成等）
- 前端工作面板集成
- 异步执行不阻塞主Agent
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable, AsyncGenerator
from multi_agents.agent_router import get_agent, set_parent_config
from multi_agents.agent_loader import get_cached_agent
from multi_agents.agent_events import AgentEventManager, get_agent_event_manager, AgentEventType

logger = logging.getLogger(__name__)


class AgentDelegationStreaming:
    """
    支持事件流的Agent委托管理器
    
    用于在brain.py中调用子Agent，并实时转发事件到前端
    """
    
    def __init__(self):
        self.event_manager = get_agent_event_manager()
        self._event_callback: Optional[Callable[[Dict], None]] = None
        # 追踪当前正在运行的子agent实例（用于外部中断）
        self._current_agent: Optional[Any] = None
    
    def set_event_callback(self, callback: Callable[[Dict], None]):
        """
        设置事件回调函数，用于将事件发送到前端
        
        Args:
            callback: 回调函数，接收事件字典
        """
        self._event_callback = callback
    
    async def delegate_with_streaming(
        self,
        agent_name: str,
        task_description: str,
        parent_messages: List[Dict[str, str]],
        parent_config: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        委托任务给子Agent，并实时yield事件
        
        Yields:
            {"type": "agent_started", "agent_name": str, "task_id": str}
            {"type": "agent_step", "step": {...}}
            {"type": "agent_progress", "message": str}
            {"type": "agent_completed", "result": str}
            {"type": "agent_failed", "error": str}
        
        Args:
            agent_name: Agent名称
            task_description: 任务描述
            parent_messages: 父Agent的消息历史
            parent_config: 父Agent配置
        
        Returns:
            最终执行结果字符串
        """
        # 【修复】确保 parent_config 包含工作区目录
        if parent_config is None:
            parent_config = {}
        
        # 从 config.py 获取当前工作区目录并注入到 parent_config
        try:
            from config import get_workspace_directory
            workspace_dir = get_workspace_directory()
            if workspace_dir and "workspace_directory" not in parent_config:
                parent_config = {**parent_config, "workspace_directory": workspace_dir}
                logger.info(f"[AgentDelegation] Injected workspace_directory into parent_config: {workspace_dir}")
        except Exception as e:
            logger.warning(f"[AgentDelegation] Failed to get workspace_directory: {e}")
        
        # 检查Agent是否存在
        agent_def = get_cached_agent(agent_name)
        if not agent_def:
            error_msg = f"错误：未找到名为 '{agent_name}' 的Agent"
            yield {"type": "agent_failed", "error": error_msg}
            return
        
        # 获取Agent实例
        agent = get_agent(agent_name, parent_config=parent_config)
        if not agent:
            error_msg = f"错误：无法加载Agent '{agent_name}'"
            yield {"type": "agent_failed", "error": error_msg}
            return
        
        # 追踪当前运行的子agent实例（用于外部中断）
        self._current_agent = agent
        logger.info(f"[AgentDelegation] Set _current_agent to: {agent.name if hasattr(agent, 'name') else 'unknown'}")
        logger.info(f"[AgentDelegation] _current_agent id: {id(agent)}")
        
        # 生成任务ID
        import uuid
        task_id = f"agent_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"[AgentDelegation] Starting task {task_id} for agent {agent_name}")
        
        # 发送开始事件
        start_event = {
            "type": "agent_started",
            "agent_name": agent_name,
            "task_id": task_id,
            "message": task_description
        }
        yield start_event
        if self._event_callback:
            self._event_callback(start_event)
        
        try:
            # 使用流式执行
            step_count = 0
            async for event in agent.execute_streaming(task_description, parent_messages):
                event_type = event.get("type")
                
                # 转换事件格式以兼容前端
                if event_type == "step_started":
                    step_count += 1
                    frontend_event = {
                        "type": "agent_step",
                        "agent_name": agent_name,
                        "task_id": task_id,
                        "step": {
                            "step_id": event.get("step_id"),
                            "order": event.get("order", step_count),
                            "type": event.get("step_type", "executing"),
                            "title": event.get("title", "执行中..."),
                            "detail": event.get("detail", ""),
                            "status": "running"
                        }
                    }
                    yield frontend_event
                    if self._event_callback:
                        self._event_callback(frontend_event)
                
                elif event_type == "step_updated":
                    # 【修复】处理步骤更新事件
                    frontend_event = {
                        "type": "agent_step_updated",
                        "agent_name": agent_name,
                        "task_id": task_id,
                        "step_id": event.get("step_id"),
                        "detail": event.get("detail", "")
                    }
                    yield frontend_event
                    if self._event_callback:
                        self._event_callback(frontend_event)
                
                elif event_type == "step_completed":
                    # 【修复】包含result、duration、execution_time字段
                    frontend_event = {
                        "type": "agent_step_completed",
                        "agent_name": agent_name,
                        "task_id": task_id,
                        "step_id": event.get("step_id"),
                        "status": event.get("status", "completed"),
                        "result": event.get("result", ""),
                        "duration": event.get("duration"),
                        "execution_time": event.get("execution_time")
                    }
                    yield frontend_event
                    if self._event_callback:
                        self._event_callback(frontend_event)
                
                elif event_type == "tool_executed":
                    # 【修复】包含step_id、execution_time、error字段
                    frontend_event = {
                        "type": "tool_executed",
                        "agent_name": agent_name,
                        "task_id": task_id,
                        "tool_name": event.get("tool_name"),
                        "success": event.get("success"),
                        "result": event.get("result", ""),
                        "error": event.get("error", ""),
                        "step_id": event.get("step_id"),
                        "execution_time": event.get("execution_time")
                    }
                    yield frontend_event
                    if self._event_callback:
                        self._event_callback(frontend_event)
                
                elif event_type == "execution_completed":
                    result = event.get("result", "")
                    work_summary = event.get("work_summary", "")  # 【修复】获取工作汇报
                    cancelled = event.get("cancelled", False)  # 【修复】检查是否被中断
                    completed_work = event.get("completed_work", [])  # 【修复】获取已完成的工作
                    
                    if cancelled:
                        # 【修复】发送 agent_cancelled 事件，而不是 agent_completed
                        cancel_event = {
                            "type": "agent_cancelled",
                            "agent_name": agent_name,
                            "task_id": task_id,
                            "result": result,
                            "work_summary": work_summary,
                            "completed_work": completed_work,
                            "message": "任务已被用户中断"
                        }
                        logger.info(f"[AgentDelegation] Agent {agent_name} was cancelled, sending agent_cancelled event with {len(completed_work)} completed_work items")
                        yield cancel_event
                        if self._event_callback:
                            self._event_callback(cancel_event)
                        return
                    else:
                        complete_event = {
                            "type": "agent_completed",
                            "agent_name": agent_name,
                            "task_id": task_id,
                            "result": result,
                            "work_summary": work_summary  # 【修复】传递工作汇报
                        }
                        yield complete_event
                        if self._event_callback:
                            self._event_callback(complete_event)
                        return
                
                elif event_type == "error":
                    error = event.get("message", "未知错误")
                    error_event = {
                        "type": "agent_failed",
                        "agent_name": agent_name,
                        "task_id": task_id,
                        "error": error
                    }
                    yield error_event
                    if self._event_callback:
                        self._event_callback(error_event)
                    return
                
                else:
                    # 【修复】未知/自定义事件类型（如 todo_list, plan_created, plan_mode_entered 等）
                    # 直接透传给前端，不丢弃
                    passthrough_event = {
                        **event,
                        "agent_name": agent_name,
                        "task_id": task_id,
                    }
                    yield passthrough_event
                    if self._event_callback:
                        self._event_callback(passthrough_event)
            
            # 如果流式执行结束但没有收到 completion 事件
            complete_event = {
                "type": "agent_completed",
                "agent_name": agent_name,
                "task_id": task_id,
                "result": "执行完成"
            }
            yield complete_event
            if self._event_callback:
                self._event_callback(complete_event)
        
        except Exception as e:
            logger.error(f"[AgentDelegation] Error during execution: {e}")
            error_event = {
                "type": "agent_failed",
                "agent_name": agent_name,
                "task_id": task_id,
                "error": str(e)
            }
            yield error_event
            if self._event_callback:
                self._event_callback(error_event)
        finally:
            # 清除追踪的子agent实例
            logger.info(f"[AgentDelegation] Clearing _current_agent (was: {self._current_agent.name if self._current_agent and hasattr(self._current_agent, 'name') else 'None'})")
            self._current_agent = None


# 全局实例
_delegation_instance: Optional[AgentDelegationStreaming] = None


def get_delegation_streaming() -> AgentDelegationStreaming:
    """获取全局Agent委托流式管理器实例"""
    global _delegation_instance
    if _delegation_instance is None:
        _delegation_instance = AgentDelegationStreaming()
    return _delegation_instance


def reset_delegation_streaming():
    """重置全局实例"""
    global _delegation_instance
    _delegation_instance = None


def cancel_current_agent() -> Dict[str, Any]:
    """
    中断当前正在运行的子agent
    
    【修复】参照 Claude Code，确保中断事件传播：
    1. 设置取消信号
    2. 发送 agent_cancelled 事件到前端
    3. 收集已完成的工作并返回
    
    通过设置子agent executor的取消信号，让ReAct循环在下一个检查点退出。
    这是独立于主agent cancel_event的信号通道，不影响主agent流程。
    
    Returns:
        Dict containing:
        - success: 是否成功设置取消信号
        - completed_work: 已完成的工作列表
    """
    global _delegation_instance
    
    # 【调试日志】入口
    logger.info(f"[AgentDelegation] cancel_current_agent() called")
    logger.info(f"[AgentDelegation] _delegation_instance exists: {_delegation_instance is not None}")
    if _delegation_instance:
        logger.info(f"[AgentDelegation] _current_agent exists: {_delegation_instance._current_agent is not None}")
        logger.info(f"[AgentDelegation] _event_callback exists: {_delegation_instance._event_callback is not None}")
    
    result = {
        "success": False,
        "completed_work": []
    }
    
    if _delegation_instance and _delegation_instance._current_agent:
        agent = _delegation_instance._current_agent
        logger.info(f"[AgentDelegation] Cancelling current agent: {agent.name}")
        try:
            # 1. 调用 agent.cancel() 设置取消信号
            agent.cancel()
            result["success"] = True
            
            # 2. 收集已完成的工作
            completed_work = []
            if hasattr(agent, 'executor') and hasattr(agent.executor, 'steps'):
                for step in agent.executor.steps:
                    if step.status in ("completed", "cancelled") and hasattr(step, 'result') and step.result:
                        completed_work.append({
                            "step_id": step.step_id,
                            "title": step.title,
                            "result": step.result[:500] if len(step.result) > 500 else step.result
                        })
            result["completed_work"] = completed_work
            
            # 3. 【修复】发送 agent_cancelled 事件到前端
            if _delegation_instance._event_callback:
                cancel_event = {
                    "type": "agent_cancelled",
                    "agent_name": agent.name,
                    "task_id": getattr(agent, '_current_task_id', 'unknown'),
                    "message": "任务已被用户中断",
                    "completed_work": completed_work
                }
                _delegation_instance._event_callback(cancel_event)
                logger.info(f"[AgentDelegation] Sent agent_cancelled event for {agent.name}")
            
            logger.info(f"[AgentDelegation] Agent {agent.name} cancelled with {len(completed_work)} completed steps")
            return result
            
        except Exception as e:
            logger.error(f"[AgentDelegation] Failed to cancel agent: {e}")
            import traceback
            logger.error(f"[AgentDelegation] Traceback: {traceback.format_exc()}")
            return result
    else:
        logger.warning("[AgentDelegation] No running agent to cancel")
        logger.warning(f"[AgentDelegation] _delegation_instance: {_delegation_instance}")
        logger.warning(f"[AgentDelegation] _current_agent: {_delegation_instance._current_agent if _delegation_instance else 'N/A'}")
        return result
