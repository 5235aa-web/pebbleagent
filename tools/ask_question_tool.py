"""
AskUserQuestion工具 - 多选题询问用户

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
    """问题选项"""
    label: str
    description: str
    preview: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "label": self.label,
            "description": self.description
        }
        if self.preview:
            result["preview"] = self.preview
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuestionOption":
        return cls(
            label=data["label"],
            description=data["description"],
            preview=data.get("preview")
        )


@dataclass
class Question:
    """问题"""
    question: str
    header: str
    options: List[QuestionOption]
    multi_select: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "header": self.header,
            "options": [o.to_dict() for o in self.options],
            "multiSelect": self.multi_select
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Question":
        options = [QuestionOption.from_dict(o) for o in data.get("options", [])]
        return cls(
            question=data["question"],
            header=data["header"],
            options=options,
            multi_select=data.get("multiSelect", False)
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


class AskUserQuestionTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    多选题询问用户工具
    
    用于向用户提出多选题问题，支持单选和多选，支持用户自定义输入
    """
    
    name = "ask_user_question"
    description = """Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take

When to Use This Tool:
- When the user's request is ambiguous and you need clarification
- When there are multiple valid approaches and you want user input
- When you need to confirm important decisions before proceeding
- When the user might have preferences that would affect your implementation

When NOT to Use This Tool:
- When the task is clear and straightforward
- When you can make reasonable assumptions based on context
- When the decision is trivial and won't significantly impact the outcome
- When you've already asked similar questions in the current session

Question Guidelines:
- Keep questions concise and clear
- Provide helpful descriptions for each option
- Limit to 1-4 questions per call to avoid overwhelming the user
- There should be no 'Other' option - that will be provided automatically
- Each option should be a distinct, mutually exclusive choice (unless multiSelect is enabled)

User Input:
- Users can always select "Other" to provide their own custom answer
- Custom answers are returned alongside the selected options"""
    
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
            
            if "question" not in q:
                raise ValueError(f"question {i} must have 'question' field")
            if "header" not in q:
                raise ValueError(f"question {i} must have 'header' field")
            if "options" not in q:
                raise ValueError(f"question {i} must have 'options' field")
            
            q_text = q["question"]
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
                if "description" not in opt:
                    raise ValueError(f"question {i} option {j} must have 'description' field")
                
                label = opt["label"]
                if label.lower() in ["other", "其他", "其它"]:
                    raise ValueError(f"question {i} option {j} should not use 'Other' label - it will be added automatically")
                if label in option_labels:
                    raise ValueError(f"Duplicate option label in question {i}: {label}")
                option_labels.add(label)
            
            header = q.get("header", "")
            if len(header) > 12:
                logger.warning(f"Header '{header}' exceeds 12 characters, will be truncated")
        
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
            q_text = q.question
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
