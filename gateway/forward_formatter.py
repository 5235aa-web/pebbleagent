"""Format selected session messages into a single merged forward payload."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

MAX_FORWARD_TOOL_CHARS = 600

# 仅保留产生编辑/写入/代码执行副作用的工具；只读类工具（read_file、grep、glob、
# ls、search_*、lsp_*、git_status、git_diff 等）全部忽略。
EDIT_AND_EXEC_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "delete_file",
    "copy_file",
    "move_file",
    "create_directory",
    "execute_code",
    "execute_shell",
    "powershell",
    "set_env_var",
    "kill_process",
    "git_commit",
    "git_push",
    "git_checkout",
    "todo_write",
    "experience_write",
    "experience_update",
})

_COMPACT_HEADER_RE = re.compile(
    r"^This session is being continued from a previous conversation that ran out of context\."
    r" The summary below covers the earlier portion of the conversation\.\s*",
    re.DOTALL,
)
_COMPACT_TRANSCRIPT_RE = re.compile(
    r"\n\nIf you need specific details from before compaction \(like exact code snippets, "
    r"error messages, or content you generated\), read the full transcript at:[\s\S]*",
    re.DOTALL,
)
_COMPACT_RECENT_RE = re.compile(r"\n\nRecent messages are preserved verbatim\.", re.DOTALL)
_COMPACT_CONTINUE_RE = re.compile(
    r"\nContinue the conversation from where it left off without asking the user any further questions[\s\S]*",
    re.DOTALL,
)


def strip_compact_summary_scaffolding(text: str) -> str:
    """剥离压缩摘要前后包裹的模型引导脚手架，保留完整摘要正文。"""
    s = text or ""
    s = _COMPACT_HEADER_RE.sub("", s)
    s = _COMPACT_TRANSCRIPT_RE.sub("", s)
    s = _COMPACT_RECENT_RE.sub("", s)
    s = _COMPACT_CONTINUE_RE.sub("", s)
    trimmed = s.strip()
    return trimmed or (text or "").strip()


def truncate_tool_content(text: str, limit: int = MAX_FORWARD_TOOL_CHARS) -> str:
    """将单条工具执行内容截断至 limit 字符以内，保留头尾关键信息。"""
    if not text or limit <= 0:
        return "" if limit <= 0 else text
    if len(text) <= limit:
        return text

    omitted_est = len(text) - limit
    marker = f"\n...[truncated ~{omitted_est} chars]...\n"
    if len(marker) >= limit - 20:
        return text[:limit]

    usable = limit - len(marker)
    head_len = int(usable * 0.75)
    tail_len = usable - head_len
    if tail_len <= 0:
        return text[:usable] + marker.rstrip("\n")
    return text[:head_len] + marker + text[-tail_len:]


def _format_time(raw: Any) -> str:
    if raw is None or raw == "":
        return ""
    try:
        ts = float(raw)
        if ts > 1e11:
            ts /= 1000.0
        if ts > 0:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        pass
    return str(raw)


def _parse_execution_order(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except (ValueError, TypeError):
            return []
    return []


_PRIORITY_INPUT_KEYS = (
    "file_path",
    "path",
    "target_file",
    "target_path",
    "source",
    "destination",
    "command",
    "cmd",
    "script",
    "query",
    "url",
)


def _summarize_tool_input(raw_input: Any) -> str:
    """将工具输入精简为关键参数摘要，避免大段文件内容挤占 600 字符预算。"""
    if isinstance(raw_input, dict):
        parts: list[str] = []
        seen: set[str] = set()
        for key in _PRIORITY_INPUT_KEYS:
            if key in raw_input and raw_input[key] not in (None, ""):
                val = str(raw_input[key]).replace("\n", " ").strip()
                if len(val) > 160:
                    val = val[:157] + "..."
                parts.append(f"{key}={val}")
                seen.add(key)
        for key, value in raw_input.items():
            if key in seen or value in (None, ""):
                continue
            if key in ("content", "contents", "new_string", "old_string", "text", "patch", "diff"):
                s = str(value)
                parts.append(f"{key}=<{len(s)} chars>")
                continue
            val = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value).replace("\n", " ").strip()
            if len(val) > 100:
                val = val[:97] + "..."
            parts.append(f"{key}={val}")
        return ", ".join(parts)
    if isinstance(raw_input, str):
        text = raw_input.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return _summarize_tool_input(parsed)
            except Exception:
                pass
        return text
    return str(raw_input or "").strip()


def _format_tool_detail(info: dict[str, Any], tool_outputs: dict[str, str]) -> str:
    tool_id = str(info.get("id") or "")
    input_text = _summarize_tool_input(info.get("input"))

    raw_output = info.get("output")
    if (raw_output is None or raw_output == "") and tool_id and tool_id in tool_outputs:
        raw_output = tool_outputs[tool_id]
    error_text = str(info.get("error") or "").strip()
    output_text = str(raw_output or "").strip()

    parts: list[str] = []
    if input_text:
        parts.append(f"Args: {input_text}")
    if error_text:
        parts.append(f"Error: {error_text}")
    elif output_text:
        parts.append(f"Output: {output_text}")
    combined = "\n".join(parts).strip()
    return truncate_tool_content(combined, MAX_FORWARD_TOOL_CHARS)


def _format_assistant_row(row: dict[str, Any], tool_outputs: dict[str, str]) -> str:
    order = _parse_execution_order(row.get("tool_calls"))
    text_parts: list[str] = []
    tool_blocks: list[str] = []

    for item in order:
        item_type = str(item.get("type") or "")
        if item_type == "text":
            chunk = str(item.get("content") or "").strip()
            if chunk:
                text_parts.append(chunk)
        elif item_type == "tool":
            info = item.get("tool_info") or item.get("toolInfo") or {}
            if not isinstance(info, dict):
                continue
            tool_name = str(info.get("name") or info.get("toolName") or "").strip()
            if tool_name not in EDIT_AND_EXEC_TOOLS:
                continue
            status = str(info.get("status") or "").strip() or "completed"
            detail = _format_tool_detail(info, tool_outputs)
            if detail:
                tool_blocks.append(f"- `{tool_name}` [{status}]\n```text\n{detail}\n```")
            else:
                tool_blocks.append(f"- `{tool_name}` [{status}]")

    body_text = "\n\n".join(text_parts).strip()
    if not body_text:
        body_text = str(row.get("content") or "").strip()

    sections: list[str] = []
    if body_text:
        sections.append(body_text)
    if tool_blocks:
        sections.append(
            f"**Edit/Exec Tool Records (max {MAX_FORWARD_TOOL_CHARS} chars each)**\n"
            + "\n".join(tool_blocks)
        )
    return "\n\n".join(sections).strip()


def format_forwarded_messages(
    source_title: str,
    rows: list[dict[str, Any]],
    tool_outputs: dict[str, str] | None = None,
) -> tuple[str, int]:
    """将选中的源消息合并格式化为单条转发 Markdown 消息。

    返回 ``(合并文本, 实际包含的消息条数)``。
    """
    outputs = tool_outputs or {}
    blocks: list[str] = []

    for row in rows:
        role = str(row.get("role") or "")
        content = str(row.get("content") or "")
        if role == "system" and content.startswith("[COMPACT_BOUNDARY:"):
            continue
        if role == "tool":
            continue

        time_str = _format_time(row.get("created_at"))
        time_suffix = f" ({time_str})" if time_str else ""

        if row.get("is_compact_summary"):
            # 摘要卡片按全量内容转发，不做任何长度截断
            summary_body = strip_compact_summary_scaffolding(content)
            if not summary_body:
                continue
            idx = len(blocks) + 1
            blocks.append(f"### {idx}. Context Summary (Full){time_suffix}\n\n{summary_body}")
            continue

        if role == "user":
            user_body = content.strip()
            if not user_body:
                continue
            idx = len(blocks) + 1
            blocks.append(f"### {idx}. User{time_suffix}\n\n{user_body}")
            continue

        if role == "assistant":
            assistant_body = _format_assistant_row(row, outputs)
            if not assistant_body:
                continue
            idx = len(blocks) + 1
            blocks.append(f"### {idx}. Pebble Assistant{time_suffix}\n\n{assistant_body}")

    if not blocks:
        return ("", 0)

    title = ((source_title or "").strip() or "Untitled session").replace('"', "'")
    header = f'[Forwarded messages · from session "{title}" ({len(blocks)} total)]'
    merged = header + "\n\n---\n\n" + "\n\n---\n\n".join(blocks)
    return (merged, len(blocks))
