"""LLM-generated session titles for the first turn of a conversation.

The gateway owns session titles (sessions.db). This module only produces the
title text: the caller persists it and pushes the update to the UI, so a failed
generation never affects the reply itself.
"""
import asyncio
import logging
import re
from typing import Optional

from agent_core.llm_client import apply_reasoning_mode, build_default_headers, keep_max_effort

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 15  # CJK titles: characters
MAX_TITLE_WORDS = 15  # Latin titles: words
_ATTEMPT_TIMEOUT = 8.0

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LABEL_RE = re.compile(r"^(?:标题|题目|title|subject)\s*[:：]\s*", re.IGNORECASE)
_QUOTE_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("\u201c", "\u201d"),
    ("\u2018", "\u2019"),
    ("\u300c", "\u300d"),
    ("\u300e", "\u300f"),
    ("\u300a", "\u300b"),
)
_TRAILING_PUNCT = "。．.!！?？,，、;；:：…~～"


def detect_title_language(user_text: str) -> str:
    """Return 'zh' when the user wrote CJK text, otherwise 'en'."""
    return "zh" if _CJK_RE.search(user_text or "") else "en"


def _build_prompt(user_text: str, assistant_text: str, language: str) -> str:
    user_part = (user_text or "").strip()[:1000]
    assistant_part = (assistant_text or "").strip()[:600]
    if language == "zh":
        return (
            "为下面这段对话拟一个标题。\n\n"
            f"用户：{user_part}\n"
            f"助手：{assistant_part}\n\n"
            "要求：\n"
            f"1. 不超过 {MAX_TITLE_CHARS} 个汉字\n"
            "2. 概括主题，不要照抄原句\n"
            "3. 不要标点、引号和书名号\n"
            "4. 只输出标题本身\n\n"
            "标题："
        )
    return (
        "Write a title for the conversation below.\n\n"
        f"User: {user_part}\n"
        f"Assistant: {assistant_part}\n\n"
        "Rules:\n"
        f"1. At most {MAX_TITLE_WORDS} words\n"
        "2. Summarize the topic; do not copy the user's wording\n"
        "3. No punctuation or quotation marks\n"
        "4. Output the title only\n\n"
        "Title:"
    )


def _strip_wrapping_quotes(text: str) -> str:
    if len(text) >= 2:
        for left, right in _QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right):
                return text[1:-1].strip()
    return text


def _truncate(text: str) -> str:
    if _CJK_RE.search(text):
        return text[:MAX_TITLE_CHARS].strip()
    words = text.split()
    if len(words) > MAX_TITLE_WORDS:
        return " ".join(words[:MAX_TITLE_WORDS])
    return text


def clean_title(raw: str) -> Optional[str]:
    """Normalize a model reply into a bare title, or None when unusable."""
    if not raw:
        return None
    lines = [line.strip() for line in str(raw).splitlines() if line.strip()]
    if not lines:
        return None
    title = _LABEL_RE.sub("", lines[0]).strip()
    title = _strip_wrapping_quotes(title)
    title = title.strip().strip(_TRAILING_PUNCT).strip()
    title = _truncate(title)
    title = title.strip().strip(_TRAILING_PUNCT).strip()
    title = _strip_wrapping_quotes(title).strip()
    return title or None


async def generate_session_title(
    llm_config: dict,
    user_text: str,
    assistant_text: str,
    session_id: str = "",
) -> Optional[str]:
    """Ask the active model for a short title. Returns None on any failure."""
    config = llm_config or {}
    model = (config.get("model") or "").strip()
    if not model:
        logger.warning("[SessionTitle] No model configured; skipping title generation")
        return None

    language = detect_title_language(user_text)
    prompt = _build_prompt(user_text, assistant_text, language)

    try:
        from openai import AsyncOpenAI
        from agent_core.agent_chat_service_v2 import build_thinking_params
    except Exception as e:
        logger.warning(f"[SessionTitle] LLM client unavailable: {e}")
        return None

    base_params = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 64,
        "stream": False,
    }

    # Thinking off keeps the call fast. Providers that reject the vendor-specific
    # block still get one plain attempt, so a title is not lost on compatibility.
    thinking_params = apply_reasoning_mode(
        build_thinking_params(model, False, "", keep_max_effort(config)), config
    )
    attempts = (thinking_params, {})
    last_error = ""
    for extra in attempts:
        params = dict(base_params)
        params.update(extra)
        try:
            async with AsyncOpenAI(
                api_key=config.get("api_key") or "local-dev-key",
                base_url=config.get("base_url") or None,
                timeout=_ATTEMPT_TIMEOUT,
                max_retries=0,
                default_headers=build_default_headers(config, session_id),
            ) as client:
                resp = await asyncio.wait_for(
                    client.chat.completions.create(**params),
                    timeout=_ATTEMPT_TIMEOUT + 2,
                )
        except Exception as e:
            last_error = str(e) or e.__class__.__name__
            logger.info(f"[SessionTitle] Attempt failed (model={model}): {last_error}")
            continue

        content = ""
        if getattr(resp, "choices", None):
            message = getattr(resp.choices[0], "message", None)
            content = getattr(message, "content", "") or ""
        title = clean_title(content)
        if title:
            logger.info(f"[SessionTitle] Generated title (model={model}): {title!r}")
            return title
        last_error = "empty title in model reply"

    logger.warning(f"[SessionTitle] Giving up (model={model}): {last_error}")
    return None
