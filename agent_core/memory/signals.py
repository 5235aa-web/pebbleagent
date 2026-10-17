"""对话信号检测：从用户措辞与工具执行序列中识别值得记账的经验。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# 纠正：用户否定既有做法
CORRECTION_PATTERNS = [
    r"不对", r"不是这样", r"错了", r"别(?:再)?", r"不要", r"禁止", r"我说过",
    r"应该是", r"改成", r"而不是", r"换成", r"谁让你", r"为什么又",
    r"\bno,", r"\bactually\b", r"\bwrong\b", r"\binstead\b", r"\bstop\b",
]

# 约束：长期生效的硬性要求
CONSTRAINT_PATTERNS = [
    r"必须", r"务必", r"只能", r"一律", r"绝对", r"永远", r"不允许",
    r"严禁", r"统一(?:用|改|走|成)", r"都要", r"全都",
    r"\bmust\b", r"\balways\b", r"\bnever\b", r"\bonly\b",
]

# 偏好：习惯与取向
PREFERENCE_PATTERNS = [
    r"我(?:更)?(?:喜欢|习惯|倾向|偏好)", r"以后(?:都)?(?:用|走|按)",
    r"我一般", r"默认(?:用|走)", r"\bi prefer\b", r"\bi usually\b",
]

# 显式要求记账
EXPLICIT_PATTERNS = [
    r"记住", r"记下来", r"记一下", r"别忘了", r"\bremember (?:that|this)\b",
]

# 确认：用于给既有条目加权
CONFIRM_PATTERNS = [
    r"^(?:对|是的|没错|就是这样|可以|好的|行|没问题|正确|ok|okay)"
    r"(?:[，,]\s*(?:就是这样|这样|的|了|好|可以|没问题))?[。！!. ]*$",
]

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?\n])")
_SECRET_KEY = re.compile(r"(token|password|passwd|secret|api[_-]?key|authorization|cookie)", re.I)
_PATH_LIKE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}\b")
_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_BACKTICK = re.compile(r"`([^`]{1,80})`")


@dataclass
class Signal:
    kind: str
    statement: str
    rationale: str = ""
    confidence: float = 0.6
    keywords: List[str] = field(default_factory=list)
    evidence_quote: str = ""


def _match_any(text: str, patterns: Iterable[str]) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(0)
    return None


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def _clean_statement(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-—，,。；;：:")
    text = re.sub(r"^(?:不对|不是|错了|别|不要|喂|哎)[，,。！!、\s]*", "", text)
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def extract_keywords(text: str, extra: Optional[Iterable[str]] = None) -> List[str]:
    found: List[str] = []
    for pattern in (_PATH_LIKE, _BACKTICK, _IDENTIFIER):
        for m in pattern.finditer(text or ""):
            token = (m.group(1) if m.groups() else m.group(0)).strip()
            if 2 <= len(token) <= 80 and token.lower() not in {t.lower() for t in found}:
                found.append(token)
    for item in extra or []:
        if item and item not in found:
            found.append(str(item))
    return found[:12]


def detect_user_signals(
    user_message: str,
    previous_assistant: str = "",
) -> List[Signal]:
    """按句子扫描用户消息，返回至多两条信号。"""
    text = (user_message or "").strip()
    if not text or len(text) > 4000:
        return []

    signals: List[Signal] = []
    sentences = _sentences(text)

    explicit = _match_any(text, EXPLICIT_PATTERNS)
    if explicit:
        statement = _clean_statement(sentences[0] if sentences else text)
        if statement:
            signals.append(Signal(
                kind="preference",
                statement=statement,
                rationale="用户明确要求记住",
                confidence=0.9,
                keywords=extract_keywords(statement),
                evidence_quote=text[:400],
            ))
        return signals

    for sentence in sentences:
        cleaned = _clean_statement(sentence)
        if len(cleaned) < 4:
            continue
        if _match_any(sentence, CORRECTION_PATTERNS):
            rationale = "用户否定了先前的做法"
            if previous_assistant:
                rationale = "用户否定了先前的做法：" + previous_assistant.strip()[:120]
            signals.append(Signal(
                kind="correction",
                statement=cleaned,
                rationale=rationale,
                confidence=0.8,
                keywords=extract_keywords(cleaned),
                evidence_quote=sentence.strip()[:400],
            ))
            break
        if _match_any(sentence, CONSTRAINT_PATTERNS):
            signals.append(Signal(
                kind="constraint",
                statement=cleaned,
                rationale="用户下达了长期约束",
                confidence=0.75,
                keywords=extract_keywords(cleaned),
                evidence_quote=sentence.strip()[:400],
            ))
            break
        if _match_any(sentence, PREFERENCE_PATTERNS):
            signals.append(Signal(
                kind="preference",
                statement=cleaned,
                rationale="用户表达了稳定偏好",
                confidence=0.65,
                keywords=extract_keywords(cleaned),
                evidence_quote=sentence.strip()[:400],
            ))
            break

    return signals[:2]


def is_confirmation(user_message: str) -> bool:
    return bool(_match_any((user_message or "").strip(), CONFIRM_PATTERNS))


def _summarize_args(args: Any) -> str:
    if not isinstance(args, dict):
        return str(args)[:80]
    parts = []
    for key, value in args.items():
        if _SECRET_KEY.search(str(key)):
            continue
        text = str(value).replace("\n", " ")[:40]
        parts.append(f"{key}={text}")
        if len(parts) >= 2:
            break
    return ", ".join(parts)


def detect_tool_signals(tool_events: List[Dict[str, Any]]) -> List[Signal]:
    """同一工具失败后换参数成功，记为一条 pitfall。"""
    if not tool_events:
        return []
    signals: List[Signal] = []
    last_failure: Dict[str, Dict[str, Any]] = {}
    for event in tool_events:
        name = event.get("name") or ""
        status = event.get("status") or ""
        if not name:
            continue
        if status == "failed":
            last_failure[name] = event
            continue
        if status == "completed" and name in last_failure:
            failed = last_failure.pop(name)
            failed_args = _summarize_args(failed.get("args"))
            ok_args = _summarize_args(event.get("args"))
            if failed_args and ok_args and failed_args != ok_args:
                signals.append(Signal(
                    kind="pitfall",
                    statement=f"调用 {name} 时 {failed_args} 会失败，改用 {ok_args} 可成功。",
                    rationale=(failed.get("output") or "")[:200],
                    confidence=0.7,
                    keywords=extract_keywords(ok_args, [name]),
                    evidence_quote=f"{name}: {failed_args} -> {ok_args}"[:400],
                ))
    return signals[:2]
