"""经验注入：把检索结果渲染为系统提示词片段。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

MAX_PROMPT_CHARS = 2000

# 写入 correction 时 rationale 的固定前缀，渲染时据此判断后面是否带着上下文。
_CONTEXT_PREFIXES = (
    "用户否定了先前的做法：",
    "The user rejected the previous approach: ",
)

_HEADER = (
    "<experience-ledger>\n"
    "以下条目来自此前会话中你被纠正或确认过的做法，按重要程度分组，同类内最新的在前。"
    "它们是历史结论，若与当前代码冲突，以代码为准。\n"
    "Lessons below come from earlier sessions where you were corrected or the user "
    "confirmed a rule, grouped by importance, newest first within each group. They "
    "are historical conclusions: if they conflict with the current code, trust the code.\n"
)


def _load(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback


def _scope_line(applies_to: Dict[str, Any]) -> str:
    parts = []
    tools = applies_to.get("tools") or []
    paths = applies_to.get("paths") or []
    if tools:
        parts.append("工具 " + ", ".join(tools[:4]))
    if paths:
        parts.append("路径 " + ", ".join(paths[:4]))
    return "；".join(parts)


def _context_line(entry: Dict[str, Any]) -> str:
    """correction 条目附上被否定的做法。

    statement 常常只有后半句：「不对，我不是让你别动那个文件吗」去掉前缀后
    剩下「我不是让你别动那个文件吗」，模型既不知道哪个文件也不知道当时在
    干什么。rationale 里存了助手上一轮的做法，只有这个前缀才说明它是有内容
    的上下文；constraint 的「用户下达了长期约束」之类是废话，跳过。
    """
    if entry.get("kind") != "correction":
        return ""
    rationale = (entry.get("rationale") or "").strip()
    for prefix in _CONTEXT_PREFIXES:
        if rationale.startswith(prefix):
            text = rationale[len(prefix):].strip()
            if len(text) < 4:
                return ""
            if len(text) > 60:
                text = text[:60].rstrip() + "…"
            return f"  当时: {text}"
    return ""


def confidence_label(value: float) -> str:
    """把置信度压成三档。

    渲染里放精确值会让 boost_recent 的每次 +0.05 都改写这一行，进而让整块
    经验在 system prompt 里变化，模型侧的前缀缓存随之失效。三档保留"这条
    有多可靠"的信息，同时让大多数加权落在同一档内。
    """
    if value >= 0.9:
        return "high"
    if value >= 0.75:
        return "medium"
    return "low"


def render_entries(entries: List[Dict[str, Any]], max_chars: int = MAX_PROMPT_CHARS) -> str:
    if not entries:
        return ""
    lines: List[str] = []
    used = len(_HEADER)
    for entry in entries:
        applies_to = _load(entry.get("applies_to"), {})
        kind = entry.get("kind", "")
        confidence = float(entry.get("confidence") or 0.0)
        statement = (entry.get("statement") or "").strip()
        if not statement:
            continue
        if len(statement) > 240:
            statement = statement[:240].rstrip() + "…"
        block = [f"- [{kind} · {confidence_label(confidence)}] {statement}"]
        scope = _scope_line(applies_to)
        if scope:
            block.append(f"  适用: {scope}")
        context = _context_line(entry)
        if context:
            block.append(context)
        text = "\n".join(block)
        if used + len(text) + 1 > max_chars:
            break
        lines.append(text)
        used += len(text) + 1
    if not lines:
        return ""
    return _HEADER + "\n".join(lines) + "\n</experience-ledger>"


def render_agent_note(agent_type: str, content: Optional[str]) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    return (
        f"\n\n<agent-note agent=\"{agent_type}\">\n"
        f"{text[:4000]}\n"
        "</agent-note>"
    )
