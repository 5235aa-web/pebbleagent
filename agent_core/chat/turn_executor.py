"""Turn preparation for the chat loop."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from ..agent_loader import get_cached_agent, get_agent_system_prompt
from .. import agent_chat_history as history
from ..llm_client import build_default_headers
from .prompt import compose_system_prompt

logger = logging.getLogger(__name__)

IMAGE_EXT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}

_FILE_PATTERN = re.compile(
    r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|vue|json|md|yaml|yml|toml|css|scss|html|java|go|rs|cpp|c|h|sql)\b"
)


def _recent_context(messages: List[dict], limit: int = 8) -> Tuple[List[str], List[str]]:
    """收集最近消息里的工具名与文件路径，用于经验检索加权。"""
    tools: List[str] = []
    paths: List[str] = []
    for msg in messages[-limit:]:
        calls = msg.get("tool_calls") or []
        if isinstance(calls, str):
            try:
                calls = json.loads(calls)
            except (TypeError, ValueError):
                calls = []
        for call in calls:
            name = (call.get("function") or {}).get("name") if isinstance(call, dict) else None
            if name and name not in tools:
                tools.append(name)
        content = msg.get("content")
        if isinstance(content, str):
            for match in _FILE_PATTERN.finditer(content):
                candidate = match.group(0)
                if candidate not in paths:
                    paths.append(candidate)
    return tools[:10], paths[:10]


@dataclass
class TurnState:
    """State assembled before the tool loop starts."""

    agent_name: str = ""
    agent_def: Any = None
    conversation_id: int = 0
    conversation_uuid: str = ""
    messages: List[dict] = field(default_factory=list)
    context_messages: List[dict] = field(default_factory=list)
    llm_config: dict = field(default_factory=dict)
    use_model: str = ""
    final_temperature: Optional[float] = None
    final_top_p: Optional[float] = None
    final_thinking_enabled: bool = True
    final_reasoning_effort: str = "high"
    client: Any = None
    cancel_event: Any = None
    user_message_id: int = 0
    user_msg_uuid: Optional[str] = None
    pending_todo_events: List[Any] = field(default_factory=list)
    full_message: str = ""
    aborted: bool = False


async def prepare_turn(service, params: Dict[str, Any], state: TurnState) -> AsyncIterator[dict]:
    """Assemble agent, conversation, messages, prompt and client for one turn."""
    from ..agent_chat_service_v2 import _extract_attachment_text, count_tokens, _read_json_config

    agent_name = params["agent_name"]
    message = params["message"]
    conversation_uuid = params["conversation_uuid"]
    user_id = params.get("user_id", "default_user")
    model = params.get("model")
    attachments = params.get("attachments")
    project_path = params.get("project_path")
    temperature = params.get("temperature")
    top_p = params.get("top_p")
    thinking_enabled = params.get("thinking_enabled")
    reasoning_effort = params.get("reasoning_effort")
    user_msg_uuid = params.get("user_msg_uuid")

    state.agent_name = agent_name
    state.conversation_uuid = conversation_uuid
    state.user_msg_uuid = user_msg_uuid

    agent_def = get_cached_agent(agent_name)
    if not agent_def:
        yield {"type": "error", "message": f"Agent '{agent_name}' not found"}
        state.aborted = True
        return
    state.agent_def = agent_def

    conv = history.get_agent_conversation_by_uuid(conversation_uuid)
    if not conv:
        history.create_agent_conversation(user_id, agent_name, title="New conversation", uuid=conversation_uuid)
        conv = history.get_agent_conversation_by_uuid(conversation_uuid)
    if conv is None:
        yield {"type": "error", "message": "Failed to create conversation"}
        state.aborted = True
        return
    state.conversation_id = conv["id"]

    service._transcript_manager.set_session(
        session_id=conversation_uuid,
        project_path=project_path or service._current_project_path,
    )

    full_message = message
    llm_images: list = []
    if attachments:
        attachment_texts = []
        for att in attachments:
            name = att.get('name', 'unknown')
            b64 = att.get('data_base64', '')
            if b64:
                ext = '.' + name.rsplit('.', 1)[-1].lower() if '.' in name else ''
                mime = IMAGE_EXT.get(ext, '')
                if mime:
                    img_block = {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                    detail = att.get("detail")
                    if detail in ("low", "high", "original", "auto"):
                        img_block["image_url"]["detail"] = detail
                    llm_images.append(img_block)
                    continue
            text_content = att.get("text_content")
            if not text_content and b64:
                text_content = _extract_attachment_text(name, b64)
            if text_content:
                attachment_texts.append(f"[附件: {name}]\n{text_content}")
            else:
                attachment_texts.append(
                    f"[附件: {name}]\n(该文件类型的文本内容无法解析，请提示用户改用 read_file/read_pdf 工具处理或转换为文本格式)"
                )
        if attachment_texts:
            full_message = message + "\n\n" + "\n\n".join(attachment_texts)
    state.full_message = full_message

    state.user_message_id = history.add_agent_chat_message(
        conversation_id=state.conversation_id,
        role="user",
        content=full_message,
        token_count=count_tokens(full_message),
        agent_name=agent_name,
        msg_uuid=user_msg_uuid,
    )

    state.context_messages = history.get_agent_chat_messages_for_context(state.conversation_id, limit=999999)

    agent_model_override = service._load_agent_model_config(agent_name) or {}
    service._load_path_whitelist()
    llm_config = service._get_llm_config(agent_name, model)
    state.llm_config = llm_config
    state.use_model = llm_config['model']

    configured_temperature = agent_model_override.get("temperature")
    state.final_temperature = temperature if temperature is not None else (
        configured_temperature if configured_temperature is not None else (
            agent_def.temperature if agent_def.temperature is not None else llm_config.get("temperature")
        )
    )
    state.final_top_p = top_p if top_p is not None else getattr(agent_def, 'top_p', None)
    if state.final_top_p is None:
        state.final_top_p = llm_config.get("top_p")

    state.final_thinking_enabled = thinking_enabled if thinking_enabled is not None else True
    state.final_reasoning_effort = reasoning_effort if reasoning_effort is not None else "high"

    system_prompt = get_agent_system_prompt(agent_def)
    experience_prompt = ""
    try:
        ledger = service._ledger
        ledger.set_project(project_path or service._current_project_path)
        recent_tools, recent_paths = _recent_context(state.context_messages)
        experience_prompt = ledger.build_prompt(
            query=full_message or message,
            tools=recent_tools,
            paths=recent_paths,
            agent_type=agent_name,
        )
        if experience_prompt:
            logger.info(f"[Turn] Injected experience ledger ({len(experience_prompt)} chars)")
    except Exception as e:
        logger.warning(f"[Turn] Failed to build experience prompt: {e}")

    usernote_content = ""
    try:
        _un_data = _read_json_config("usernote.json")
        if _un_data:
            usernote_content = (_un_data.get("content") or "").strip()
    except Exception:
        pass

    system_prompt = compose_system_prompt(
        system_prompt, experience_prompt, service._current_project_path, usernote_content
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in state.context_messages[:-1]:
        messages.append(msg)
    if llm_images:
        user_content: list = [{"type": "text", "text": full_message or message}]
        user_content.extend(llm_images)
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": full_message})
    state.messages = service._sanitize_messages(messages)

    try:
        state.client = AsyncOpenAI(
            api_key=llm_config['api_key'],
            base_url=llm_config['base_url'],
            timeout=300.0,
            default_headers=build_default_headers(llm_config, conversation_uuid),
        )
    except Exception as e:
        logger.error(f"[Turn] Failed to create OpenAI client: {e}")
        yield {"type": "error", "message": f"Failed to initialize LLM client: {e}"}
        state.aborted = True
        return

    state.cancel_event = asyncio.Event()
    service._cancel_signals[conversation_uuid] = state.cancel_event

    from ..state.todo import TodoManager
    TodoManager.set_session_id(conversation_uuid)
    try:
        from gateway.permission_manager import get_permission_manager
        get_permission_manager().set_session_id(conversation_uuid)
    except Exception:
        pass

    pending_todo_events: list = []
    TodoManager.set_event_callback(lambda todos: pending_todo_events.append(todos))
    state.pending_todo_events = pending_todo_events

    try:
        from ..little_guard import reset_all_guards
        reset_all_guards(conversation_uuid)
    except ImportError:
        pass
