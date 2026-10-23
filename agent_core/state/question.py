"""
澄清提问工具 - 多选题询问用户

提供多选题询问用户功能
支持用户自定义输入回答
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import logging
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class QuestionOption:
    """选项"""
    label: str
    detail: str = ""
    preview: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "label": self.label,
            "detail": self.detail
        }
        if self.preview:
            result["preview"] = self.preview
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuestionOption":
        return cls(
            label=data["label"],
            detail=data.get("detail", ""),
            preview=data.get("preview")
        )


@dataclass
class Question:
    """问题"""
    prompt: str
    title: str = ""
    options: List[QuestionOption] = field(default_factory=list)
    multiple: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "title": self.title,
            "options": [o.to_dict() for o in self.options],
            "multiple": self.multiple
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Question":
        options = [QuestionOption.from_dict(o) for o in data.get("options", [])]
        return cls(
            prompt=data["prompt"],
            title=data.get("title", ""),
            options=options,
            multiple=data.get("multiple", False)
        )


@dataclass
class QuestionAnswer:
    """问题答案"""
    question_text: str
    selected_options: List[str]
    custom_input: Optional[str] = None
    notes: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "question": self.question_text,
            "selected": self.selected_options
        }
        if self.custom_input:
            result["customInput"] = self.custom_input
        if self.notes:
            result["notes"] = self.notes
        return result


class QuestionManager:
    """问题管理器（单例模式）"""
    
    _instance: Optional["QuestionManager"] = None
    _pending_questions: Optional[Dict[str, Any]] = None
    # The session that raised the pending question. The manager itself is a
    # process-wide singleton, so without this the pending question cannot be
    # attributed to a conversation when a client reconnects and asks for it.
    _pending_session_id: str = ""
    _answer_event: Optional[asyncio.Event] = None
    _answers: Optional[Dict[str, Any]] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._answer_event = asyncio.Event()
        return cls._instance
    
    @classmethod
    def set_pending_questions(cls, questions_data: Dict[str, Any], session_id: str = "") -> None:
        """设置待回答的问题"""
        cls._pending_questions = questions_data
        if session_id:
            cls._pending_session_id = session_id
        cls._answers = None
        if cls._answer_event:
            cls._answer_event.clear()
        else:
            cls._answer_event = asyncio.Event()
    
    @classmethod
    def get_pending_session_id(cls) -> str:
        """Which session owns the pending question (empty when none)."""
        return cls._pending_session_id

    @classmethod
    def get_pending_questions(cls) -> Optional[Dict[str, Any]]:
        """获取待回答的问题"""
        return cls._pending_questions
    
    @classmethod
    def submit_answers(cls, answers: Dict[str, Any]) -> None:
        """
        提交答案
        """
        cls._answers = answers
        cls._pending_questions = None
        if cls._answer_event:
            cls._answer_event.set()
        else:
            cls._answer_event = asyncio.Event()
            cls._answer_event.set()
    
    @classmethod
    async def wait_for_answers(cls, timeout: float = 300.0) -> Optional[Dict[str, Any]]:
        """等待用户回答"""
        if cls._answer_event is None:
            cls._answer_event = asyncio.Event()
        
        if cls._answers is not None:
            return cls._answers
        
        try:
            await asyncio.wait_for(cls._answer_event.wait(), timeout=timeout)
            return cls._answers
        except asyncio.TimeoutError:
            logger.warning("Question timeout - no answer received")
            return None
    
    @classmethod
    def clear(cls) -> None:
        """清除状态"""
        cls._pending_questions = None
        cls._pending_session_id = ""
        cls._answers = None
        if cls._answer_event:
            cls._answer_event.clear()

