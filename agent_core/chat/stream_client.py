"""Streaming completion execution."""

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from ..context_compressor import is_prompt_too_long_error

logger = logging.getLogger(__name__)


class PromptTooLong(RuntimeError):
    """Model context window exceeded."""

    def __init__(self, gap: Optional[int], limit: Optional[int]):
        detail = f"（gap={gap}, limit={limit}）" if gap is not None else ""
        super().__init__(
            f"上下文长度超出模型窗口{detail}，本次生成已停止。"
            f"请手动压缩会话后重试。"
        )
        self.gap = gap
        self.limit = limit


async def open_stream(
    client,
    params: Dict[str, Any],
    cancel_event,
    in_suspicious_retry: bool = False,
    max_retries: int = 3,
):
    """Open a streamed completion.

    Returns (stream, status, graceful_abort) where status is "cancelled" or None.
    Raises PromptTooLong for context-window errors and the original error after
    the retry budget is exhausted.
    """
    last_error = None
    for attempt in range(max_retries):
        if cancel_event.is_set():
            logger.info("[Stream] Cancelled during API retry")
            return None, "cancelled", False
        try:
            stream = await client.chat.completions.create(**params)
            return stream, None, False
        except Exception as api_error:
            last_error = api_error
            is_ptl, token_gap, context_limit = is_prompt_too_long_error(api_error)
            if is_ptl:
                logger.warning(
                    f"[Stream] prompt_too_long (gap={token_gap}, limit={context_limit}); manual compaction required"
                )
                raise PromptTooLong(token_gap, context_limit) from api_error
            if in_suspicious_retry and "invalid assistant message" in str(api_error).lower():
                logger.warning(
                    f"[Stream] Suspicious-retry request rejected by API ({api_error}); accepting turn as complete"
                )
                return None, None, True
            if attempt == max_retries - 1:
                raise
    raise last_error if last_error else RuntimeError("Failed to get API response")


class StreamConsumer:
    """Consume one streamed completion, emitting frontend events."""

    def __init__(self, execution_order: List[dict], order_index: int, cancel_event):
        self.execution_order = execution_order
        self.order_index = order_index
        self.cancel_event = cancel_event
        self.content = ""
        self.reasoning = ""
        self.finish_reason: Optional[str] = None
        self.tool_calls_chunks: Dict[int, dict] = {}
        self.interrupted = False
        self.chunk_count = 0
        self.tool_call_chunk_count = 0
        self._chunk_type: Optional[str] = None
        self._chunk_content = ""

    def _flush_chunk(self) -> None:
        if not (self._chunk_type and self._chunk_content):
            return
        self.execution_order.append({
            "type": "reasoning" if self._chunk_type == "reasoning" else "text",
            "content": self._chunk_content,
            "order": self.order_index,
        })
        self.order_index += 1
        self._chunk_content = ""
        self._chunk_type = None

    async def consume(self, stream) -> AsyncIterator[dict]:
        try:
            async for chunk in stream:
                self.chunk_count += 1
                if self.cancel_event.is_set():
                    logger.info(f"[Stream] Cancel detected at chunk #{self.chunk_count}")
                    self.interrupted = True
                    yield {"type": "cancelled", "message": "Generation cancelled"}
                    break
                if not (chunk.choices and len(chunk.choices) > 0):
                    continue
                if chunk.choices[0].finish_reason:
                    self.finish_reason = chunk.choices[0].finish_reason
                delta = chunk.choices[0].delta

                if getattr(delta, "reasoning_content", None):
                    if self._chunk_type == "content" and self._chunk_content:
                        self._flush_chunk()
                    self._chunk_type = "reasoning"
                    self._chunk_content += delta.reasoning_content
                    self.reasoning += delta.reasoning_content
                    yield {"type": "reasoning", "content": delta.reasoning_content, "order": self.order_index}
                    await asyncio.sleep(0)

                if delta.content:
                    if self._chunk_type == "reasoning" and self._chunk_content:
                        self._flush_chunk()
                    self._chunk_type = "content"
                    self._chunk_content += delta.content
                    self.content += delta.content
                    yield {"type": "content", "content": delta.content, "order": self.order_index}
                    await asyncio.sleep(0)

                if getattr(delta, "tool_calls", None):
                    self.tool_call_chunk_count += 1
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in self.tool_calls_chunks:
                            self.tool_calls_chunks[idx] = {
                                "id": "", "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.id:
                            self.tool_calls_chunks[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                self.tool_calls_chunks[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                self.tool_calls_chunks[idx]["function"]["arguments"] += tc.function.arguments
                        current_tool_id = self.tool_calls_chunks[idx]["id"] or f"temp_{idx}"
                        current_tool_name = self.tool_calls_chunks[idx]["function"]["name"] or ""
                        if current_tool_name:
                            yield {
                                "type": "tool_call_streaming",
                                "tool_call_id": current_tool_id,
                                "tool_name": current_tool_name,
                                "arguments_chunk": tc.function.arguments if tc.function and tc.function.arguments else "",
                                "arguments_full": self.tool_calls_chunks[idx]["function"]["arguments"],
                                "index": idx,
                            }
                            await asyncio.sleep(0)
        except Exception as stream_err:
            logger.info(f"[Stream] Stream ended: {stream_err}")
            if self.cancel_event.is_set():
                self.interrupted = True
                yield {"type": "cancelled", "message": "Generation cancelled"}
                return
            raise
        self._flush_chunk()
