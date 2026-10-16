"""
Auto Memory Extractor - 记忆自动提取器

自动记忆提取机制：
1. 在对话结束时自动运行
2. 使用独立的提取会话（不影响主对话）
3. 分析对话内容，提取值得记忆的信息
4. 与主 agent 互斥（主 agent 已写记忆则跳过）

触发时机：
- 每个查询循环结束时（通过 post-sampling hook）
- 用户手动触发（/remember 命令）

提取逻辑：
- 分析最近的对话内容
- 识别用户画像、反馈、项目上下文、外部引用
- 检查是否与现有记忆重复
- 保存新记忆并更新索引
"""

import os
import logging
import json
import re
import asyncio

from ..safe_json import safe_json_loads
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from openai import AsyncOpenAI

from .auto_memory_types import MemoryType, MemoryEntry
from .auto_memory import AutoMemoryManager, get_auto_memory_manager
from .auto_memory_paths import get_auto_memory_dir, is_auto_memory_path
from .auto_memory_scan import scan_memory_files, format_memory_manifest

logger = logging.getLogger(__name__)


# ============================================================================
# 配置
# ============================================================================

@dataclass
class ExtractMemoryConfig:
    """记忆提取配置"""
    # 触发阈值
    min_messages_to_extract: int = 4          # 最少消息数才触发
    min_tokens_between_extract: int = 5000    # 两次提取间的最小 token 数
    max_turns_between_extract: int = 3        # 两次提取间的最小轮数
    
    # 提取限制
    max_memories_per_extract: int = 3         # 每次最多提取的记忆数
    max_conversation_context: int = 20         # 用于分析的最近消息数
    
    # 功能开关
    enable_user_extraction: bool = True        # 提取用户画像
    enable_feedback_extraction: bool = True    # 提取反馈
    enable_project_extraction: bool = True     # 提取项目上下文
    enable_reference_extraction: bool = True   # 提取外部引用


DEFAULT_EXTRACT_CONFIG = ExtractMemoryConfig()


# ============================================================================
# 提取状态
# ============================================================================

@dataclass
class ExtractMemoryState:
    """记忆提取状态"""
    last_extract_turn: int = 0
    current_turn: int = 0
    last_extract_token_count: int = 0
    extract_count: int = 0
    in_progress: bool = False
    
    def increment_turn(self) -> None:
        """增加轮数"""
        self.current_turn += 1
    
    def record_extract(self, token_count: int) -> None:
        """记录提取"""
        self.last_extract_turn = self.current_turn
        self.last_extract_token_count = token_count
        self.extract_count += 1
    
    def turns_since_last_extract(self) -> int:
        """距离上次提取的轮数"""
        return self.current_turn - self.last_extract_turn


# ============================================================================
# 提取提示词
# ============================================================================

EXTRACT_MEMORY_SYSTEM_PROMPT = """你是一个记忆提取专家。你的任务是分析对话内容，提取值得持久化记忆的信息。

## 记忆类型

1. **user** - 用户画像
   - 角色、职责、知识背景
   - 偏好、工作风格
   - 触发：了解到用户的角色、偏好或专业知识

2. **feedback** - 行为指导
   - 用户纠正的行为
   - 用户确认的正确做法
   - 触发：用户说"不要"、"应该"、"好的就这样"

3. **project** - 项目上下文
   - 重要决策及其原因
   - 时间线、里程碑
   - 进行中的工作
   - 触发：了解到项目决策、时间线或工作计划

4. **reference** - 外部引用
   - 外部系统链接（Linear、Jira、Slack等）
   - 文档、资源位置
   - 触发：用户提供外部系统链接或引用

## 不应保存的内容

- 代码模式、架构（可从代码推导）
- Git 历史（可通过 git 命令获取）
- 临时任务细节
- 项目已有文档中记录的内容
- 具体的代码片段或文件路径（除非是关键配置）

## 输出格式

以 JSON 数组返回提取的记忆，每个记忆包含：
- type: "user" | "feedback" | "project" | "reference"
- name: 简短名称（用于文件名，如 "user_role"）
- description: 一行描述
- content: 详细内容
- reason: 为什么值得记忆

如果没有值得记忆的内容，返回空数组 `[]`。

只输出 JSON 数组，不要有其他内容。"""


EXTRACT_MEMORY_USER_PROMPT = """分析以下对话，提取值得记忆的信息。

## 现有记忆

{existing_memories}

## 对话内容

{conversation}

---

请提取记忆，只输出 JSON 数组。"""


# ============================================================================
# 记忆提取器
# ============================================================================

class AutoMemoryExtractor:
    """
    记忆提取器
    
    在对话结束时运行，自动提取值得记忆的信息
    """
    
    def __init__(
        self,
        llm_client: AsyncOpenAI,
        llm_model: str,
        memory_manager: Optional[AutoMemoryManager] = None,
        config: ExtractMemoryConfig = None
    ):
        self.llm_client = llm_client
        self.llm_model = llm_model
        self.memory_manager = memory_manager or get_auto_memory_manager()
        self.config = config or DEFAULT_EXTRACT_CONFIG
        
        # 提取状态（按会话 ID 存储）
        self._states: Dict[str, ExtractMemoryState] = {}
        
        # 最后提取的消息 UUID（用于追踪）
        self._last_extract_uuid: Dict[str, str] = {}
    
    def get_state(self, session_id: str) -> ExtractMemoryState:
        """获取会话的提取状态"""
        if session_id not in self._states:
            self._states[session_id] = ExtractMemoryState()
        return self._states[session_id]
    
    def should_extract(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        current_token_count: int
    ) -> Tuple[bool, str]:
        """
        判断是否应该提取记忆
        
        Args:
            messages: 消息列表
            session_id: 会话 ID
            current_token_count: 当前 token 数
        
        Returns:
            (是否提取, 原因)
        """
        state = self.get_state(session_id)
        state.increment_turn()
        
        # 检查消息数量
        if len(messages) < self.config.min_messages_to_extract:
            return False, f"消息数量不足 ({len(messages)} < {self.config.min_messages_to_extract})"
        
        # 检查轮数间隔
        if state.turns_since_last_extract() < self.config.max_turns_between_extract:
            return False, f"轮数间隔不足 ({state.turns_since_last_extract()} < {self.config.max_turns_between_extract})"
        
        # 检查 token 间隔
        token_gap = current_token_count - state.last_extract_token_count
        if token_gap < self.config.min_tokens_between_extract:
            return False, f"Token 间隔不足 ({token_gap} < {self.config.min_tokens_between_extract})"
        
        return True, f"满足提取条件 (消息: {len(messages)}, 轮数间隔: {state.turns_since_last_extract()}, token 间隔: {token_gap})"
    
    async def extract(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        current_token_count: int = 0
    ) -> List[Dict[str, Any]]:
        """
        从对话中提取记忆
        
        Args:
            messages: 消息列表
            session_id: 会话 ID
            current_token_count: 当前 token 数
        
        Returns:
            提取的记忆列表
        """
        state = self.get_state(session_id)
        
        # 检查是否已在进行
        if state.in_progress:
            logger.warning(f"[MemoryExtractor] Extraction already in progress for {session_id}")
            return []
        
        state.in_progress = True
        
        try:
            # 检查主 agent 是否已写记忆
            if self._has_memory_writes(messages):
                logger.info("[MemoryExtractor] Main agent already wrote memories, skipping")
                # 更新追踪
                last_msg = messages[-1] if messages else None
                if last_msg and last_msg.get('uuid'):
                    self._last_extract_uuid[session_id] = last_msg['uuid']
                return []
            
            # 获取现有记忆
            existing_memories = self._get_existing_memories()
            
            # 格式化对话
            conversation_text = self._format_conversation(messages)
            
            # 构建提示词
            user_prompt = EXTRACT_MEMORY_USER_PROMPT.format(
                existing_memories=existing_memories,
                conversation=conversation_text
            )
            
            # 调用 LLM
            response = await self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": EXTRACT_MEMORY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            content = response.choices[0].message.content
            
            # 解析结果
            memories = self._parse_memories(content)
            
            # 限制数量
            memories = memories[:self.config.max_memories_per_extract]
            
            # 保存记忆
            saved = []
            for mem in memories:
                try:
                    entry = self.memory_manager.create_memory(
                        name=mem["name"],
                        memory_type=MemoryType(mem["type"]),
                        description=mem["description"],
                        content=mem["content"]
                    )
                    saved.append({
                        **entry.to_dict(),
                        "reason": mem.get("reason", "")
                    })
                except Exception as e:
                    logger.warning(f"[MemoryExtractor] Failed to save memory: {e}")
            
            # 更新状态
            state.record_extract(current_token_count)
            
            # 更新追踪
            last_msg = messages[-1] if messages else None
            if last_msg and last_msg.get('uuid'):
                self._last_extract_uuid[session_id] = last_msg['uuid']
            
            logger.info(f"[MemoryExtractor] Extracted {len(saved)} memories")
            
            return saved
            
        except Exception as e:
            logger.error(f"[MemoryExtractor] Extraction failed: {e}")
            return []
        
        finally:
            state.in_progress = False
    
    def _has_memory_writes(self, messages: List[Dict[str, Any]]) -> bool:
        """检查主 agent 是否已写记忆"""
        project_path = self.memory_manager.get_project_path()
        if not project_path:
            return False
        
        # 检查最近的 assistant 消息是否有写入记忆目录的操作
        for msg in reversed(messages[-10:]):
            if msg.get('role') != 'assistant':
                continue
            
            # 只检查 tool_calls 中是否有实际写入记忆文件的操作
            # 不检查 text content，因为 agent 提到 "MEMORY.md" 不等于写了记忆
            tool_calls = msg.get('tool_calls', [])
            if not isinstance(tool_calls, list):
                continue
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                if tc.get('name') in ['write_file', 'edit_file']:
                    args = tc.get('args') or {}
                    if not isinstance(args, dict):
                        continue
                    file_path = args.get('file_path', '')
                    if is_auto_memory_path(file_path, project_path):
                        return True
        
        return False
    
    def _get_existing_memories(self) -> str:
        """获取现有记忆清单"""
        project_path = self.memory_manager.get_project_path()
        if not project_path:
            return "（无现有记忆）"
        
        memory_dir = get_auto_memory_dir(project_path)
        scan_result = scan_memory_files(memory_dir)
        
        return format_memory_manifest(scan_result)
    
    def _format_conversation(self, messages: List[Dict[str, Any]]) -> str:
        """格式化对话为文本"""
        lines = []
        
        # 只取最近的 N 条消息
        recent = messages[-self.config.max_conversation_context:]
        
        for msg in recent:
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')
            
            # 处理不同类型的 content
            if isinstance(content, str):
                text = content[:1000]  # 限制长度
            elif isinstance(content, list):
                # 处理多块内容
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            text_parts.append(block.get('text', '')[:500])
                        elif block.get('type') == 'tool_use':
                            text_parts.append(f"[工具调用: {block.get('name', 'unknown')}]")
                        elif block.get('type') == 'tool_result':
                            text_parts.append(f"[工具结果: {str(block.get('content', ''))[:200]}]")
                text = "\n".join(text_parts)
            else:
                text = str(content)[:500]
            
            if role == 'user':
                lines.append(f"用户: {text}")
            elif role == 'assistant':
                lines.append(f"AI: {text}")
            elif role == 'system':
                lines.append(f"系统: {text}")
        
        return "\n\n".join(lines)
    
    def _parse_memories(self, content: str) -> List[Dict[str, Any]]:
        """解析 LLM 返回的记忆 JSON"""
        # 提取 JSON 数组
        match = re.search(r'\[[\s\S]*\]', content)
        if not match:
            return []
        
        memories = safe_json_loads(match.group(), default=[])
        if not isinstance(memories, list):
            return []
        
        # 验证每个记忆
        valid = []
        for mem in memories:
            if not isinstance(mem, dict):
                continue
            
            if not all(k in mem for k in ['type', 'name', 'description', 'content']):
                continue
            
            # 验证类型
            if mem['type'] not in ['user', 'feedback', 'project', 'reference']:
                continue
            
            # 检查是否启用该类型提取
            type_enabled = {
                'user': self.config.enable_user_extraction,
                'feedback': self.config.enable_feedback_extraction,
                'project': self.config.enable_project_extraction,
                'reference': self.config.enable_reference_extraction,
            }
            
            if not type_enabled.get(mem['type'], False):
                continue
            
            valid.append(mem)
        
        return valid


# ============================================================================
# 便捷函数
# ============================================================================

def create_extractor(
    llm_config: Dict[str, Any],
    config: ExtractMemoryConfig = None
) -> AutoMemoryExtractor:
    """
    创建记忆提取器
    
    Args:
        llm_config: LLM 配置 {'api_key', 'base_url', 'model'}
        config: 提取配置
    
    Returns:
        AutoMemoryExtractor 实例
    """
    client = AsyncOpenAI(
        api_key=llm_config.get('api_key', os.environ.get('OPENAI_API_KEY', '')),
        base_url=llm_config.get('base_url', os.environ.get('OPENAI_BASE_URL', '')),
        timeout=60.0
    )
    
    return AutoMemoryExtractor(
        llm_client=client,
        llm_model=llm_config.get('model', 'gpt-4'),
        config=config
    )
