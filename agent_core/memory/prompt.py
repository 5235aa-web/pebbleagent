"""经验注入：把检索结果渲染为系统提示词片段。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

MAX_PROMPT_CHARS = 2000

_HEADER = (
    "<experience-ledger>\n"
    "以下条目来自此前会话中你被纠正或确认过的做法，按相关性排序。"
    "它们是历史结论，若与当前代码冲突，以代码为准。\n"
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


def _evidence_line(evidence: List[Dict[str, Any]]) -> str:
    if not evidence:
        return ""
    item = evidence[0]
    conversation = str(item.get("conversation_uuid") or "")[:8]
    timestamp = item.get("timestamp")
    when = ""
    if isinstance(timestamp, (int, float)):
        when = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    label = "会话 " + conversation if conversation else "历史会话"
    return f"{label}（{when}）" if when else label


def render_entries(entries: List[Dict[str, Any]], max_chars: int = MAX_PROMPT_CHARS) -> str:
    if not entries:
        return ""
    lines: List[str] = []
    used = len(_HEADER)
    for entry in entries:
        applies_to = _load(entry.get("applies_to"), {})
        evidence = _load(entry.get("evidence"), [])
        kind = entry.get("kind", "")
        confidence = float(entry.get("confidence") or 0.0)
        statement = (entry.get("statement") or "").strip()
        if not statement:
            continue
        block = [f"- [{kind} · {confidence:.2f}] {statement}"]
        scope = _scope_line(applies_to)
        if scope:
            block.append(f"  适用: {scope}")
        source = _evidence_line(evidence)
        if source:
            block.append(f"  证据: {source}")
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
