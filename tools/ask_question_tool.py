"""
澄清提问工具 - 多选题询问用户

提供多选题询问用户功能
支持用户自定义输入回答
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import logging
import asyncio
import uuid

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
    _answer_event: Optional[asyncio.Event] = None
    _answers: Optional[Dict[str, Any]] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._answer_event = asyncio.Event()
        return cls._instance
    
    @classmethod
    def set_pending_questions(cls, questions_data: Dict[str, Any]) -> None:
        """设置待回答的问题"""
        cls._pending_questions = questions_data
        cls._answers = None
        if cls._answer_event:
            cls._answer_event.clear()
        else:
            cls._answer_event = asyncio.Event()
    
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
        cls._answers = None
        if cls._answer_event:
            cls._answer_event.clear()


class ClarifyTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    澄清提问工具
    
    在动手前向用户确认方向、偏好或取舍，支持单选、多选与自定义回答
    """
    
    name = "clarify"
    description = """在动手前向用户确认方向、偏好或取舍。

适用场景：
- 需求存在多种合理解读，需要用户确认
- 存在多个可行方案，取舍需要用户决定
- 关键操作前需要用户确认
- 用户的偏好会明显影响实现结果

不适用场景：
- 需求已经明确，可以直接执行
- 可以基于现有代码或上下文作出合理默认选择
- 决定无关紧要，不影响结果
- 本次会话已经问过同类问题

提问约定：
- 问题简短明确，一次最多 4 个
- 每个选项给出可读的说明
- 选项互斥；需要多选时把 multiple 设为 true
- 不要提供“其他”选项，前端会自动提供自定义输入

回答方式：
- 用户可以选择选项，也可以填写自定义回答
- 自定义回答与所选选项一起返回"""
    
    input_params = ["questions"]
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        questions = kwargs.get("questions")
        if questions is None:
            raise ValueError("questions parameter is required")
        
        if not isinstance(questions, list):
            raise ValueError("questions must be a list")
        
        if len(questions) < 1 or len(questions) > 4:
            raise ValueError("questions must have 1-4 items")
        
        question_texts = set()
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                raise ValueError(f"question {i} must be a dictionary")
            
            if "prompt" not in q:
                raise ValueError(f"question {i} must have 'prompt' field")
            if "options" not in q:
                raise ValueError(f"question {i} must have 'options' field")
            
            q_text = q["prompt"]
            if q_text in question_texts:
                raise ValueError(f"Duplicate question text: {q_text}")
            question_texts.add(q_text)
            
            options = q.get("options", [])
            if not isinstance(options, list):
                raise ValueError(f"question {i} options must be a list")
            if len(options) < 2 or len(options) > 4:
                raise ValueError(f"question {i} must have 2-4 options")
            
            option_labels = set()
            for j, opt in enumerate(options):
                if not isinstance(opt, dict):
                    raise ValueError(f"question {i} option {j} must be a dictionary")
                if "label" not in opt:
                    raise ValueError(f"question {i} option {j} must have 'label' field")
                
                label = opt["label"]
                if label.lower() in ["other", "其他", "其它"]:
                    raise ValueError(f"question {i} option {j} should not use 'Other' label - it will be added automatically")
                if label in option_labels:
                    raise ValueError(f"Duplicate option label in question {i}: {label}")
                option_labels.add(label)
            
            title = q.get("title", "")
            if len(title) > 12:
                logger.warning(f"Title '{title}' exceeds 12 characters, will be truncated")
        
        return {"questions": questions}
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行问题询问"""
        questions_data = input_data["questions"]
        
        questions = [Question.from_dict(q) for q in questions_data]
        
        question_id = str(uuid.uuid4())
        pending_data = {
            "id": question_id,
            "questions": [q.to_dict() for q in questions],
            "timestamp": asyncio.get_event_loop().time()
        }
        
        QuestionManager.set_pending_questions(pending_data)
        
        answers = await QuestionManager.wait_for_answers(timeout=300.0)
        
        if answers is None:
            return {
                "questions": [q.to_dict() for q in questions],
                "answers": {},
                "custom_inputs": {},
                "cancelled": True,
                "message": "Question timed out or was cancelled"
            }
        
        raw_answers = answers.get("answers", {})
        custom_inputs = answers.get("customInputs", {})
        notes = answers.get("notes", {})
        
        formatted_answers = {}
        for q in questions:
            q_text = q.prompt
            selected = raw_answers.get(q_text, "")
            custom = custom_inputs.get(q_text, "")
            
            if custom:
                formatted_answers[q_text] = f"[自定义] {custom}"
            else:
                formatted_answers[q_text] = selected
        
        return {
            "questions": [q.to_dict() for q in questions],
            "answers": formatted_answers,
            "raw_answers": raw_answers,
            "custom_inputs": custom_inputs,
            "notes": notes,
            "cancelled": False,
            "message": "User answered the questions"
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None
    
    @property
    def requires_user_interaction(self) -> bool:
        """此工具需要用户交互"""
        return True
