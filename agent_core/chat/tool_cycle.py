"""Tool execution for one assistant turn."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Dict, List

from ..query_state_machine import ToolExecutionContext, ToolUseBlock
from ..tool_orchestrator import ToolOrchestrator
from ..tool_concurrency import get_max_concurrency
from ..safe_json import safe_json_loads

logger = logging.getLogger(__name__)


class ToolCycle:
    """Prepare and execute the tool calls of one assistant message."""

    def __init__(
        self,
        executor,
        cancel_event,
        execution_order: List[dict],
        order_index: int,
        messages: List[dict],
        conversation_uuid: str,
        agent_name: str,
        project_path: str,
        user_message_id: Any,
        tool_executions: List[dict],
        pending_todo_events: List[Any],
    ):
        self.executor = executor
        self.cancel_event = cancel_event
        self.execution_order = execution_order
        self.order_index = order_index
        self.messages = messages
        self.conversation_uuid = conversation_uuid
        self.agent_name = agent_name
        self.project_path = project_path
        self.user_message_id = user_message_id
        self.tool_executions = tool_executions
        self.pending_todo_events = pending_todo_events
        self.results: Dict[str, Any] = {}
        self.interrupted = False

    async def prepare(self, tool_calls: List[dict]) -> AsyncIterator[dict]:
        """Inject question ids and announce questions before execution."""
        for tc in tool_calls:
            if tc.get("function", {}).get("name", "") != "clarify":
                continue
            raw = tc.get("function", {}).get("arguments", "{}")
            try:
                tool_args = safe_json_loads(raw, default={}) if isinstance(raw, str) else raw
            except Exception:
                tool_args = {}
            questions = tool_args.get("questions", [])
            if not questions:
                continue
            question_id = str(uuid.uuid4())
            tool_args["question_id"] = question_id
            tc["function"]["arguments"] = json.dumps(tool_args, ensure_ascii=False)

            # 先注册再发事件：避免用户答案在注册前到达而被清掉
            from tools.ask_question_tool import QuestionManager
            QuestionManager.set_pending_questions({
                "id": question_id,
                "questions": questions,
                "timestamp": time.time(),
            })
            logger.info(f"[ToolCycle] ask_question event {question_id}")
            yield {"type": "ask_question", "question_id": question_id, "questions": questions}

    def _record(self, tool_info: dict) -> None:
        self.execution_order.append({
            "type": "tool",
            "order": self.order_index,
            "tool_info": tool_info,
        })
        self.order_index += 1

    async def run(self, tool_calls: List[dict]) -> AsyncIterator[dict]:
        """Execute tool calls and append their results to the message list."""
        blocks = []
        for tc in tool_calls:
            raw = tc.get("function", {}).get("arguments", "{}")
            try:
                args = safe_json_loads(raw, default={}) if isinstance(raw, str) else raw
            except Exception:
                args = {}
            blocks.append(ToolUseBlock(
                id=tc.get("id", str(uuid.uuid4())[:8]),
                name=tc.get("function", {}).get("name", ""),
                arguments=args,
            ))

        context = ToolExecutionContext(
            conversation_uuid=self.conversation_uuid,
            agent_name=self.agent_name,
            project_path=self.project_path,
            cancel_event=self.cancel_event,
            snapshot_session_uuid=self.conversation_uuid,
            snapshot_message_id=self.user_message_id,
        )
        self.executor.set_project_path(self.project_path)

        if self.cancel_event.is_set():
            logger.info("[ToolCycle] Cancelled before tool execution")
            self.interrupted = True
            for tc in tool_calls:
                self._record({
                    "id": tc.get("id", ""),
                    "name": tc.get("function", {}).get("name", "unknown"),
                    "status": "cancelled",
                    "input": tc.get("function", {}).get("arguments", ""),
                    "output": "Cancelled by user",
                    "duration": 0,
                })
            yield {"type": "cancelled", "message": "Generation cancelled before tool execution"}
            return

        orchestrator = ToolOrchestrator(
            tool_executor=self.executor,
            max_concurrency=get_max_concurrency(),
        )

        try:
            async for update in orchestrator.run_tools(blocks, context):
                event = update.event
                result = update.result
                yield {
                    "type": "tool_execution",
                    "tool_execution_id": event.tool_execution_id,
                    "tool_call_id": result.tool_call_id if result else event.tool_execution_id,
                    "tool_name": event.tool_name,
                    "status": event.status,
                    "input": event.input or "",
                    "output": event.output or "",
                    "duration": event.duration or 0,
                    "order": self.order_index,
                }

                if result:
                    self.results[result.tool_call_id] = result
                    self.tool_executions.append({
                        "id": result.tool_call_id,
                        "name": result.tool_name,
                        "status": result.status,
                        "input": result.input,
                        "output": result.output,
                        "duration": result.duration,
                    })
                    self._record({
                        "id": result.tool_call_id,
                        "name": result.tool_name,
                        "status": result.status,
                        "input": result.input,
                        "output": result.output,
                        "duration": result.duration,
                    })
                elif event.status == "cancelled":
                    self._record({
                        "id": event.tool_execution_id,
                        "name": event.tool_name,
                        "status": "cancelled",
                        "input": event.input or "",
                        "output": "Error: Tool execution cancelled by user",
                        "duration": event.duration or 0,
                    })

                while self.pending_todo_events:
                    yield {"type": "todo_update", "todos": self.pending_todo_events.pop(0)}

                if self.cancel_event.is_set():
                    logger.info("[ToolCycle] Cancellation detected during tool execution")
                    self.interrupted = True
                    for tc in tool_calls:
                        tool_id = tc.get("id", "")
                        tool_result = self.results.get(tool_id)
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_result.output if tool_result else "Error: Tool execution cancelled by user",
                        })
                    yield {"type": "cancelled", "message": "Generation cancelled"}
                    break
        except GeneratorExit:
            self.interrupted = True
        except asyncio.CancelledError:
            self.interrupted = True
        except Exception:
            raise

        if self.interrupted:
            return

        for tc in tool_calls:
            tool_id = tc.get("id", "")
            tool_result = self.results.get(tool_id)
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "content": tool_result.output if tool_result else "Error: Tool execution result not found",
            })
        logger.info("[ToolCycle] Tool execution completed")
