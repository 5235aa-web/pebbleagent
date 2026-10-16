"""
Agent Chat Service V2 - Agent聊天服务核心（状态机版本）

使用状态机模式重构的 Agent 聊天服务：
- while(true) + Continue/Terminal 控制流
- 工具智能并发执行
- 完整的流式输出
- 状态跨迭代传递

核心改进：
1. 状态机模式替代简单 for 循环
2. 只读工具并行执行，写工具串行执行
3. 更清晰的错误恢复机制

完整功能（从原版迁移）：
- 上下文压缩（自动/响应式）
- 记忆系统（Auto Memory / Agent Memory）
- 标题自动生成
- Transcript 管理
- Todo 回调
- Ask Question 阻塞机制
- 权限检查
- 文件/技能追踪
"""

import asyncio
import logging
import json
import uuid
import time
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, AsyncGenerator
from openai import AsyncOpenAI

from .query_state_machine import (
    QueryState,
    QueryConfig,
    ChatMessage,
    ToolUseBlock,
    StreamEvent,
    ContentEvent,
    ToolExecutionEvent,
    CompactEvent,
    ChatStartedEvent,
    ChatCompletedEvent,
    ErrorEvent,
    CancelledEvent,
    ToolExecutionContext,
    parse_tool_calls_from_response,
)
from .tool_orchestrator import (
    ToolOrchestrator,
    ToolExecutor,
    ToolExecutionResult,
    ToolExecutionUpdate,
)
from .tool_concurrency import (
    is_concurrency_safe,
    partition_tool_calls,
    ToolCallBatch,
    get_max_concurrency,
)

from .agent_loader import get_cached_agent, get_agent_system_prompt, filter_tools_for_agent, list_available_agents_from_yaml
from .context_compressor import (
    # 核心类
    Message,
    CompactBoundaryMessage,
    CompactionResult,
    CompactTrigger,
    CompactDirection,
    FileState,
    SkillState,
    AutoCompactConfig,
    AutoCompactState,
    ReactiveCompactState,
    # Token 计算
    estimate_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    # 核心函数
    compact_conversation,
    should_auto_compact,
    reactive_compact_on_error,
    is_prompt_too_long_error,
    strip_images_from_messages,
    adjust_index_to_preserve_api_invariants,
    create_compact_boundary_message,
    is_compact_boundary_message,
    get_messages_after_compact_boundary,
    PlanManager,
    # 兼容旧 API
    smart_compress,
)
# 导入 Transcript 管理器
from .transcript_manager import (
    get_transcript_manager,
    get_transcript_path,
    TranscriptManager,
)
from . import agent_chat_history as history

# 导入记忆系统
from .memory import (
    get_auto_memory_manager,
    get_agent_memory_manager,
    AutoMemoryExtractor,
)

from .tool_definitions import get_tools_definition, get_tools_definition_with_mcp
from .tool_executor_impl import AgentToolExecutorImpl

# MCP 管理器（延迟导入，避免循环依赖）
_mcp_manager = None

from .safe_json import safe_json_loads, safe_json_dumps

logger = logging.getLogger(__name__)

# ==================== 模型上下文限制 ====================

MODEL_CONTEXT_LIMITS = {
    'deepseek': 1_000_000,
    'mimo': 1_000_000,
    'gpt-4': 128_000,
    'gpt-3.5': 16_000,
    'claude': 200_000,
    'gemini': 1_000_000,
}

def get_model_context_limit(model_name: str) -> int:
    """根据模型名称获取上下文限制 token 数"""
    name = model_name.lower().replace(' ', '')
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if key in name:
            return limit
    return 1_000_000  # default fallback

# ==================== Token 计数辅助 ====================

_encoding_cache = {}

def _get_encoding():
    if 'enc' not in _encoding_cache:
        try:
            import tiktoken
            _encoding_cache['enc'] = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoding_cache['enc'] = None
    return _encoding_cache['enc']

def count_tokens(text: str) -> int:
    """使用 tiktoken 精确计算 token 数，失败时降级为 len//3"""
    if not text:
        return 0
    enc = _get_encoding()
    if enc:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 3

def count_tokens_for_full_context(
    system_prompt: str,
    messages: list,
    tools_definitions: list
) -> int:
    """计算完整上下文（system prompt + 历史消息 + tools 定义）的总 token 数"""
    total = count_tokens(system_prompt or '')
    for msg in messages:
        content = msg.get('content') or ''
        total += count_tokens(content)
    # tools 定义为 JSON 序列化后计数
    if tools_definitions:
        import json
        tools_json = json.dumps(tools_definitions, ensure_ascii=False)
        total += count_tokens(tools_json)
    return total


def _is_suspicious_stream_end(finish_reason: Optional[str], response_text: str) -> bool:
    """可疑流结束检测（2026-10-15 "过程突然终止"事件后新增）。

    在"无工具调用、即将结束回合"时判定，两类可疑特征：
    1. finish_reason == 'length'：输出到达 max_tokens 被截断，任务未必完成；
    2. 无文字输出：模型在"思考→行动"衔接点静默停止（事件特征：reasoning 正常
       生成、本应发出工具调用或文字收尾，却直接断流）、完全空响应（API 异常）、
       或工具调用无效被全部丢弃等场景。
    正常结束（finish_reason=stop 且有文字收尾）不在此列。
    """
    if finish_reason == "length":
        return True
    if not (response_text or "").strip():
        return True
    return False

# ==================== Agent Chat Service V2 ====================

_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(os.path.expanduser("~"), ".pebble")

# ==================== 高峰/空闲时段判定（北京时间） ====================
# DeepSeek 分时定价规则（前端 StatusBar 展示与后端计费必须使用同一规则）：
#   高峰时段 = 北京时间周一至周五（不含中国法定节假日）09:00-12:00、14:00-18:00
#   （左闭右开：[09:00,12:00) ∪ [14:00,18:00)，12:00/18:00 整点转入空闲）
#   空闲时段 = 其余全部时间（含周末、中国法定节假日全天、12:00-14:00、18:00 后、09:00 前）
# 中国法定节假日 = 国务院公告的放假安排（含调休连休），数据在 cn_holidays.json：
#   放假日期命中即全天空闲；调休补班日按官方文字口径不参与判定（周末补班日仍按周末全天空闲处理）。
# 空闲价格 = 高峰价格的一半（由 model_pricing.json 的 pricing_offpeak 显式配置）。
# 北京时间 = UTC+8 固定偏移（无夏令时），不依赖服务器本地时区。

def is_peak_period(now: Optional[datetime] = None) -> bool:
    """判断给定时刻是否处于高峰计费时段（默认当前时刻，北京时间）"""
    if now is None:
        now = datetime.now()
    bj = now.astimezone(timezone(timedelta(hours=8)))
    if bj.weekday() >= 5:  # 周六(5)/周日(6)
        return False
    if is_cn_holiday(bj):  # 中国法定节假日（国务院公告放假安排，含调休连休）全天空闲
        return False
    h = bj.hour
    return (9 <= h < 12) or (14 <= h < 18)


def current_pricing_period(now: Optional[datetime] = None) -> str:
    """返回当前计费时段标识：'peak'（高峰）或 'offpeak'（空闲）"""
    return "peak" if is_peak_period(now) else "offpeak"


def _read_json_config(filename: str) -> Optional[dict]:
    """读取 JSON 配置，优先用户数据目录，回退到打包根"""
    import os as _os
    data_path = _os.path.join(_DATA_HOME, filename)
    bundled_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), filename)
    for p in [data_path, bundled_path]:
        if _os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def build_thinking_params(model: str, thinking_enabled: bool, reasoning_effort: str) -> Dict[str, Any]:
    """构建思考模式相关的请求参数（所有厂商统一处理）。

    - 开启：thinking={"type": "enabled"} + reasoning_effort（全厂商发送，不再限 DeepSeek）
      - 非 DeepSeek 模型的 "max" 档降级为 "high"：小米 MiMo 仅接受 low/medium/high
        （max 会 400），OpenAI/Gemini 值域亦不含 max
    - 关闭：thinking={"type": "disabled"}（显式关闭，不再发送 reasoning_effort）
    """
    if not thinking_enabled:
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    effort = reasoning_effort
    if effort == "max" and "deepseek" not in (model or "").lower():
        effort = "high"
    return {
        "extra_body": {"thinking": {"type": "enabled"}},
        "reasoning_effort": effort,
    }

# ==================== 中国法定节假日数据（cn_holidays.json） ====================
# 用途：is_peak_period 判定"周一至周五（不含中国法定节假日）"中的节假日部分。
# 数据：国务院公告放假安排（含调休连休）——holidays=放假日列表（命中即全天空闲），
#   workdays=调休补班日列表（按官方文字口径不参与判定，仅记录）。
# 加载：路径优先级 ~/.pebble/cn_holidays.json > 打包根/cn_holidays.json；候选文件快照比对热重载（免重启，同 model_pricing 理念）。
# 降级：数据缺失/损坏时按空集合处理（判定退化为周末规则），保证系统可用性。

_cn_holidays: set = set()          # 放假日日期集合（"YYYY-MM-DD"）
_cn_workdays: set = set()          # 补班日日期集合（记录用，不参与判定）
_cn_holidays_meta: dict = {}       # 数据文件元信息（years_covered / updated_at 等）
_cn_holidays_watch: dict = {}      # 候选文件状态快照（path -> mtime），供热重载比对
_cn_holidays_warned: bool = False  # 数据缺失/损坏警告是否已提示（防刷屏）

def _cn_holidays_candidates():
    """节假日数据文件候选路径（与 _read_json_config 相同的优先级：数据目录 > 打包根）"""
    return [
        os.path.join(_DATA_HOME, "cn_holidays.json"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "cn_holidays.json"),
    ]

def _snapshot_cn_holiday_files() -> dict:
    """候选数据文件状态快照（path -> mtime），供热重载比对"""
    snap = {}
    for p in _cn_holidays_candidates():
        try:
            if os.path.exists(p):
                snap[p] = os.path.getmtime(p)
        except Exception:
            pass
    return snap

def _load_cn_holidays() -> None:
    """读取 cn_holidays.json 并重建模块级缓存；失败时降级为空集合。
    解析失败自动尝试下一个候选（与 _read_json_config 同模式）。"""
    global _cn_holidays, _cn_workdays, _cn_holidays_meta, _cn_holidays_warned, _cn_holidays_watch
    data = None
    for p in _cn_holidays_candidates():
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except Exception as e:
                logger.warning("cn_holidays.json 解析失败（%s）：%s", p, e)
                continue
    if isinstance(data, dict):
        holidays = data.get("holidays")
        workdays = data.get("workdays")
        _cn_holidays = {d for d in holidays if isinstance(d, str)} if isinstance(holidays, list) else set()
        _cn_workdays = {d for d in workdays if isinstance(d, str)} if isinstance(workdays, list) else set()
        _cn_holidays_meta = data
        _cn_holidays_warned = False
        if not _cn_holidays:
            logger.warning("cn_holidays.json 已加载但 holidays 为空，节假日判定不生效")
        # 年份覆盖检查：当前年份（北京时间）无数据时提醒更新（次年安排通常在 11 月发布）
        try:
            cur_year = datetime.now(timezone(timedelta(hours=8))).year
            covered = data.get("years_covered")
            if isinstance(covered, list) and cur_year not in covered:
                logger.warning("cn_holidays.json 未覆盖 %s 年，节假日判定本年不生效，请更新数据文件", cur_year)
        except Exception:
            pass
    else:
        # 降级为空集合（周末规则）：数据缺失/损坏时判定退化为现状行为，保证系统可用
        _cn_holidays, _cn_workdays, _cn_holidays_meta = set(), set(), {}
        if not _cn_holidays_warned:
            _cn_holidays_warned = True
            logger.warning("cn_holidays.json 不可用（缺失或损坏），节假日判定降级为周末规则（中国法定节假日将按高峰计费）")
    _cn_holidays_watch = _snapshot_cn_holiday_files()

def _ensure_cn_holidays_fresh() -> None:
    """轻量热重载：任一候选文件出现/消失/mtime 变化时自动重读（模块级实现，server.py 直接 import 场景同样生效）。
    变更检测覆盖：修改文件、后补文件、删除文件、修复损坏文件；比对失败静默跳过（不影响判定）。"""
    try:
        if _snapshot_cn_holiday_files() != _cn_holidays_watch:
            _load_cn_holidays()
    except Exception:
        pass

def is_cn_holiday(now: Optional[datetime] = None) -> bool:
    """判断（北京时间）给定时刻所在日期是否为中国法定节假日（放假日）。
    数据缺失/损坏时返回 False（降级为周末规则）。"""
    if now is None:
        now = datetime.now()
    bj = now.astimezone(timezone(timedelta(hours=8)))
    _ensure_cn_holidays_fresh()
    return bj.strftime("%Y-%m-%d") in _cn_holidays

def get_cn_holidays_payload() -> dict:
    """返回节假日数据负载（server.py 推送给前端做本地预判/展示用）"""
    _ensure_cn_holidays_fresh()
    years = _cn_holidays_meta.get("years_covered")
    return {
        "holidays": sorted(_cn_holidays),
        "workdays": sorted(_cn_workdays),
        "years_covered": years if isinstance(years, list) else [],
        "updated_at": _cn_holidays_meta.get("updated_at"),
    }

# 模块加载时预加载一次（启动日志即可发现数据缺失问题；后续由 _ensure_cn_holidays_fresh 热重载）
_load_cn_holidays()

def _extract_attachment_text(name: str, b64: str):
    """Parse attachment bytes into text for LLM (pdf/docx/xlsx/txt). Returns None when unsupported/unparsable."""
    import base64
    import io
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    try:
        raw = base64.b64decode(b64 or '')
    except Exception:
        return None
    if not raw:
        return None
    try:
        if ext == 'pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            texts = []
            for page in reader.pages[:20]:
                t = (page.extract_text() or '').strip()
                if t:
                    texts.append(t)
            return ("\n\n".join(texts)[:20000]) if texts else "(PDF has no text layer)"
        if ext == 'docx':
            from docx import Document
            doc = Document(io.BytesIO(raw))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return ("\n".join(paras)[:20000]) if paras else "(docx has no text)"
        if ext == 'xlsx':
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets[:5]:
                lines.append(f"--- sheet: {ws.title} ---")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        lines.append("\t".join(cells)[:500])
                if len(lines) > 300:
                    lines.append("...(truncated)")
                    break
            return ("\n".join(lines)[:20000]) if lines else "(xlsx is empty)"
        if ext in ('txt', 'md', 'log', 'csv', 'json', 'ini', 'conf', 'cfg', 'yaml', 'yml', 'toml'):
            try:
                return raw.decode('utf-8')[:20000]
            except UnicodeDecodeError:
                try:
                    return raw.decode('gbk', errors='replace')[:20000]
                except Exception:
                    return None
        return None
    except Exception:
        return None

class AgentChatServiceV2:
    """
    Agent 聊天服务 V2
    
    使用状态机模式重构的核心服务
    
    完整功能：
    - 状态机循环（while true + Continue/Terminal）
    - 工具智能并发
    - 上下文压缩（自动/响应式）
    - 记忆系统
    - 标题自动生成
    - Transcript 管理
    - Todo 回调
    - Ask Question 阻塞
    - 权限检查
    """
    
    def __init__(self):
        # 取消信号（asyncio.Event，用于 MCP 客户端的 await cancel_event.wait()）
        self._cancel_signals: Dict[str, asyncio.Event] = {}
        
        # 当前项目路径
        self._current_project_path: str = os.getcwd()
        
        # 是否需要生成标题
        self._need_generate_title: bool = False
        
        # 当前对话消息计数
        self._conversation_message_count: int = 0
        
        # 自动压缩状态（每个会话独立）
        self._auto_compact_states: Dict[str, AutoCompactState] = {}
        
        # 系统提示词缓存（用于提高 LLM 缓存命中率）
        self._cached_memory_prompt: str = ""
        self._cached_memory_hash: str = ""
        # 自动压缩配置
        self._auto_compact_config = AutoCompactConfig(
            threshold=999999999,
            context_window_size=200000,
            threshold_ratio=0.75,
            min_messages=999,
            cooldown_turns=999
        )
        
        # 响应式压缩状态
        self._reactive_states: Dict[str, ReactiveCompactState] = {}
        
        # 文件状态追踪（用于压缩后恢复）
        self._file_states: Dict[str, Dict[str, FileState]] = {}
        
        # Skill 状态追踪
        self._skill_states: Dict[str, Dict[str, SkillState]] = {}
        
        # Transcript 管理器
        self._transcript_manager = get_transcript_manager()
        
        # 待回答的问题（用于 ask_user_question 工具）
        self._pending_question: Optional[Dict[str, Any]] = None
        
        # Auto Memory 管理器
        self._auto_memory_manager = get_auto_memory_manager()
        
        # Agent Memory 管理器
        self._agent_memory_manager = get_agent_memory_manager()
        
        # 记忆提取器（延迟初始化）
        self._memory_extractor: Optional[AutoMemoryExtractor] = None
        
        # 记忆提取状态
        self._memory_extract_states: Dict[str, Dict[str, Any]] = {}
        
        # 工具执行器
        self._tool_executor = AgentToolExecutorImpl(self._current_project_path)
        
        # MCP 管理器
        self._mcp_manager = None
        self._mcp_initialized = False
        
        # 计费系统
        self._model_pricing_mtime: float = 0.0  # 定价配置文件 mtime（热重载检测）
        self._model_pricing = self._load_model_pricing()
        self._session_usage: Dict[str, dict] = {}  # {conv_uuid: {total_cost, ...}}
    
    def _load_model_pricing(self) -> dict:
        """加载模型价格配置（与 _read_json_config 相同的路径优先级：数据目录 > 项目根）"""
        data = _read_json_config("model_pricing.json")
        if data:
            # 记录实际生效文件的 mtime，供 _ensure_model_pricing_fresh 热重载比对
            try:
                for p in [
                    os.path.join(_DATA_HOME, "model_pricing.json"),
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), "model_pricing.json"),
                ]:
                    if os.path.exists(p):
                        self._model_pricing_mtime = os.path.getmtime(p)
                        break
            except Exception:
                self._model_pricing_mtime = 0.0
        return data if data else {"models": {}}
    
    def _ensure_model_pricing_fresh(self) -> None:
        """轻量热重载：配置文件 mtime 变化时自动重读定价表，无需重启后端"""
        if self._model_pricing_mtime <= 0 and self._model_pricing.get("models"):
            return  # 无文件记录（如打包只读），跳过检测
        try:
            for p in [
                os.path.join(_DATA_HOME, "model_pricing.json"),
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "model_pricing.json"),
            ]:
                if os.path.exists(p):
                    mt = os.path.getmtime(p)
                    if mt != self._model_pricing_mtime:
                        self._model_pricing = self._load_model_pricing()
                    return
        except Exception:
            pass
    
    def _calculate_cost(self, model: str, usage) -> Optional[dict]:
        """
        计算本次调用的费用
        
        Returns:
            {
                "input_tokens": int,
                "cache_hit_tokens": int,
                "output_tokens": int,
                "input_cost": float,
                "cache_hit_cost": float,
                "output_cost": float,
                "total_cost": float,
                "period": "peak" | "offpeak" | None,   # 分时模型本次计费时段
                "pricing_used": {...}                    # 本次实际使用的单价（per 1M tokens）
            }
        """
        # 定价文件 mtime 热重载（改 model_pricing.json 无需重启）
        self._ensure_model_pricing_fresh()
        
        pricing_data = self._model_pricing.get("models", {}).get(model)
        if not pricing_data:
            return None
        
        pricing = pricing_data.get("pricing", {})
        period = None
        
        # mimo-v2.5 阶梯定价（超过阈值 tokens 切换高价档）
        prompt_tokens = getattr(usage, 'prompt_tokens', 0)
        tier_config = pricing_data.get("pricing_tier_high")
        if tier_config and prompt_tokens > tier_config.get("threshold_tokens", 256000):
            pricing = tier_config
        else:
            # DeepSeek 分时定价：配置了 pricing_peak + pricing_offpeak 的模型，
            # 按北京时间实时判定高峰/空闲，选取对应单价（前端展示同规则）。
            peak_cfg = pricing_data.get("pricing_peak")
            offpeak_cfg = pricing_data.get("pricing_offpeak")
            if peak_cfg and offpeak_cfg:
                period = current_pricing_period()
                pricing = peak_cfg if period == "peak" else offpeak_cfg
        
        prompt_all = prompt_tokens
        cache_hit = getattr(usage, 'prompt_cache_hit_tokens', 0)
        cache_miss = prompt_all - cache_hit if cache_hit > 0 else prompt_all
        completion = getattr(usage, 'completion_tokens', 0)
        
        if cache_hit > 0:
            input_cost = (cache_miss / 1_000_000) * pricing.get("input_per_million", 0)
            cache_cost = (cache_hit / 1_000_000) * pricing.get("cache_hit_per_million", 0)
        else:
            input_cost = (prompt_all / 1_000_000) * pricing.get("input_per_million", 0)
            cache_cost = 0.0
            cache_hit = 0
            cache_miss = prompt_all
        
        output_cost = (completion / 1_000_000) * pricing.get("output_per_million", 0)
        total = round(input_cost + cache_cost + output_cost, 6)
        
        return {
            "input_tokens": cache_miss,           # 非缓存输入（用于计费和面板显示）
            "cache_hit_tokens": cache_hit,
            "output_tokens": completion,
            "input_cost": round(input_cost, 6),
            "cache_hit_cost": round(cache_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": total,
            "period": period,
            "pricing_used": dict(pricing) if pricing else None
        }
    
    def _accumulate_cost(self, conversation_uuid: str, cost_info: dict, model_name: str = None) -> dict:
        """累计会话费用（只增不减，不受压缩影响，同时持久化到数据库）"""
        if conversation_uuid not in self._session_usage:
            # 尝试从 agent_chat.db 恢复历史累计值（服务重启后有效）
            existing = self._load_session_cost_from_db(conversation_uuid)
            if existing:
                self._session_usage[conversation_uuid] = existing
            else:
                self._session_usage[conversation_uuid] = {
                    "total_cost": 0.0,
                    "total_input_tokens": 0,
                    "total_cache_hit_tokens": 0,
                    "total_output_tokens": 0,
                    "api_calls": 0,
                    "currency": "CNY"
                }
        
        session = self._session_usage[conversation_uuid]
        session["total_cost"] += cost_info["total_cost"]
        session["total_input_tokens"] += cost_info["input_tokens"]
        session["total_cache_hit_tokens"] += cost_info["cache_hit_tokens"]
        session["total_output_tokens"] += cost_info["output_tokens"]
        session["api_calls"] += 1
        
        # 持久化到 SQLite（异步写入，不阻塞主流程）
        api_call_index = session["api_calls"]
        try:
            from . import agent_chat_history as history
            conv = history.get_agent_conversation_by_uuid(conversation_uuid)
            if conv:
                history.save_api_call_cost(
                    conversation_id=conv["id"],
                    model_name=model_name or "unknown",
                    api_call_index=api_call_index,
                    input_tokens=cost_info["input_tokens"],
                    cache_hit_tokens=cost_info["cache_hit_tokens"],
                    output_tokens=cost_info["output_tokens"],
                    input_cost=cost_info["input_cost"],
                    cache_hit_cost=cost_info["cache_hit_cost"],
                    output_cost=cost_info["output_cost"],
                    total_cost=cost_info["total_cost"],
                    currency="CNY"
                )
        except Exception as e:
            logger.warning(f"[Cost] Failed to persist cost to database: {e}")
        
        return {**session, "this_call": cost_info}
    
    def _load_session_cost_from_db(self, conversation_uuid: str) -> Optional[dict]:
        """从 agent_chat.db 恢复历史费用累计值（服务重启后调用）"""
        try:
            from . import agent_chat_history as history
            conv = history.get_agent_conversation_by_uuid(conversation_uuid)
            if conv:
                summary = history.get_conversation_cost_summary(conv["id"])
                if summary and summary.get("api_calls", 0) > 0:
                    return {
                        "total_cost": summary["total_cost"],
                        "total_input_tokens": summary["total_input_tokens"],
                        "total_cache_hit_tokens": summary["total_cache_hit_tokens"],
                        "total_output_tokens": summary["total_output_tokens"],
                        "api_calls": summary["api_calls"],
                        "currency": summary.get("currency", "CNY")
                    }
        except Exception as e:
            logger.debug(f"[Cost] Failed to load existing session cost from DB: {e}")
        return None
    
    def _resolve_path(self, path: str) -> str:
        """解析路径，支持相对路径"""
        if not path:
            return path
        if os.path.isabs(path):
            return path
        return os.path.join(self._current_project_path, path)
    
    def set_project_path(self, path: str):
        """更新工作区目录，同步到所有相关组件"""
        self._current_project_path = path
        self._tool_executor.set_project_path(path)
        try:
            from .truncation_manager import set_truncation_dir
            set_truncation_dir(path)
        except Exception:
            pass
        self._load_path_whitelist()
        if hasattr(self, '_mcp_manager') and self._mcp_manager:
            from .mcp.mcp_config import get_mcp_config_manager
            try:
                config_manager = get_mcp_config_manager()
                config_manager.set_project_path(path)
            except Exception:
                pass

    def _load_path_whitelist(self) -> list:
        """从 path_whitelist.json 加载用户配置的白名单路径并同步到执行器"""
        user_paths = []
        data = _read_json_config("path_whitelist.json")
        if data:
            user_paths = data.get("paths", [])
        # 合并 agent_model_config.json 中的 allowed_paths
        merged = list(user_paths)
        try:
            agent_config = self._load_agent_model_config(
                getattr(self, '_current_agent', 'pebble')
            ) or {}
            allowed = agent_config.get("allowed_paths", [])
            for p in allowed:
                if p not in merged:
                    merged.append(p)
        except Exception:
            pass
        if merged:
            self._tool_executor.set_path_whitelist(merged)
        return merged

    async def _ensure_mcp_initialized(self, project_path: Optional[str] = None):
        """
        确保 MCP 管理器已初始化
        
        在第一次聊天时初始化 MCP 连接。
        后续调用会跳过。
        """
        if self._mcp_initialized:
            return
        
        try:
            from .mcp.mcp_manager import get_mcp_manager
            from .mcp.mcp_config import get_mcp_config_manager
            
            # 设置项目路径
            if project_path:
                config_manager = get_mcp_config_manager()
                config_manager.set_project_path(project_path)
            
            # 获取或创建 MCP 管理器
            self._mcp_manager = get_mcp_manager()
            
            if not self._mcp_manager._initialized:
                logger.info("[AgentChatServiceV2] Initializing MCP manager...")
                await self._mcp_manager.initialize()
                logger.info(f"[AgentChatServiceV2] MCP initialized: "
                           f"{len(self._mcp_manager._clients)} servers, "
                           f"{len(self._mcp_manager._tools)} tools")
            
            self._mcp_initialized = True
            
        except ImportError as e:
            logger.warning(f"[AgentChatServiceV2] MCP not available: {e}")
            self._mcp_initialized = False
        except Exception as e:
            logger.error(f"[AgentChatServiceV2] Failed to initialize MCP: {e}")
            self._mcp_initialized = False
    
    async def generate_conversation_title(
        self,
        messages: List[Dict[str, str]],
        model: str | None = None,
        thinking_enabled: bool = True,
        reasoning_effort: str = "high"
    ) -> str:
        """根据对话内容生成标题"""
        if not messages or len(messages) < 2:
            return "新对话"
        
        llm_config = self._get_llm_config("code_agent", model)
        use_model = model or llm_config['model']
        
        # 取前几轮对话
        context_messages = messages[:min(6, len(messages))]
        
        conversation_summary = ""
        for msg in context_messages:
            role = "用户" if msg.get("role") == "user" else "AI"
            content = (msg.get("content") or "")[:200]
            conversation_summary += f"{role}: {content}\n"
        
        title_prompt = f"""请根据以下对话内容，生成一个简洁的标题（不超过20个字符）。

对话内容:
{conversation_summary}

要求:
1. 标题要简洁，不超过20个字符
2. 概括对话的主要主题或任务
3. 不要包含标点符号
4. 直接输出标题，不要有其他内容

标题:"""
        
        try:
            client = AsyncOpenAI(
                api_key=llm_config['api_key'],
                base_url=llm_config['base_url'],
                timeout=30.0
            )
            
            title_params = {
                "model": use_model,
                "messages": [{"role": "user", "content": title_prompt}],
                "temperature": 0.3,
                "max_tokens": 100000,
                "stream": True,
            }
            
            # 思考模式参数（与主聊天一致：开关 + 强度，非 DeepSeek 的 max 档自动降级）
            title_params.update(build_thinking_params(use_model, thinking_enabled, reasoning_effort))
            
            raw_content = ""
            async for chunk in await client.chat.completions.create(**title_params):
                if chunk.choices and chunk.choices[0].delta.content:
                    raw_content += chunk.choices[0].delta.content
            if not raw_content:
                return "新对话"
            title = raw_content.strip()
            title = title.replace("标题:", "").strip()
            title = title.replace("：", "").strip()
            title = title.replace(":", "").strip()
            
            if len(title) > 30:
                title = title[:30]
            
            return title or "新对话"
            
        except Exception as e:
            logger.error(f"[AgentChatServiceV2] Failed to generate title: {e}")
            return "新对话"

    def _load_agent_model_config(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """加载独立 Agent 聊天的模型配置"""
        config = _read_json_config("agent_model_config.json")
        if not config:
            return None
        return config.get("agents", {}).get(agent_name)

    def _find_provider_for_model(self, model_name: str) -> Optional[Dict[str, Any]]:
        """根据模型名查找 provider 配置"""
        try:
            config = _read_json_config("llm_providers.json")
            if not config:
                return None

            for provider in config.get("providers", []):
                models = provider.get("models", [])
                if model_name in models:
                    base_url = provider.get("baseUrl", "")
                    if base_url.endswith("/chat/completions"):
                        base_url = base_url.rsplit("/chat/completions", 1)[0]
                    return {
                        "provider": provider.get("provider"),
                        "api_key": provider.get("apiKey"),
                        "base_url": base_url,
                        "model": model_name,
                        "temperature": provider.get("temperature"),
                        "top_p": provider.get("topP"),
                    }
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Failed to find provider for model {model_name}: {e}")
        return None

    def _get_provider_model(self, source: str) -> Optional[str]:
        """按 provider 索引获取默认模型"""
        try:
            if not source.startswith("provider_"):
                return None
            provider_idx = int(source.split("_", 1)[1])
            config = _read_json_config("llm_providers.json")
            if not config:
                return None

            providers = config.get("providers", [])
            if 0 <= provider_idx < len(providers):
                models = providers[provider_idx].get("models", [])
                if models:
                    return models[0]
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Failed to resolve provider model from {source}: {e}")
        return None

    def _resolve_agent_model(self, agent_name: str, requested_model: Optional[str], agent_def) -> Optional[str]:
        """解析最终使用的模型，优先尊重显式请求，其次读取 agent_model_config.json"""
        if requested_model:
            return requested_model

        agent_config = self._load_agent_model_config(agent_name)
        if agent_config:
            source = agent_config.get("model_source", "inherit")
            if source == "custom":
                custom_model = agent_config.get("custom_model")
                if custom_model:
                    return custom_model
            elif source and source != "inherit":
                if source.startswith("provider_"):
                    provider_model = self._get_provider_model(source)
                    if provider_model:
                        return provider_model
                else:
                    return source

        if getattr(agent_def, "model", "inherit") != "inherit":
            return agent_def.model
        return None


    
    def _get_llm_config(self, agent_name: str, model: str = None) -> Dict[str, Any]:
        """获取 LLM 配置
        
        Args:
            agent_name: Agent 名称
            model: 指定的模型名称（可选）
        """
        requested_model = model

        try:
            config = _read_json_config("llm_providers.json")
            if config:
                providers = config.get('providers', [])
                if providers:
                    agent_def = get_cached_agent(agent_name)
                    resolved_model = self._resolve_agent_model(agent_name, requested_model, agent_def) if agent_def else requested_model

                    # 如果指定了模型，找到包含该模型的 provider
                    selected_provider = None
                    if resolved_model:
                        for provider in providers:
                            provider_models = provider.get('models', [])
                            if resolved_model in provider_models:
                                selected_provider = provider
                                break
                    
                    # 如果没找到或没指定模型，使用第一个 provider
                    if not selected_provider:
                        selected_provider = providers[0]
                    
                    base_url = selected_provider.get('baseUrl', '')
                    if base_url.endswith('/chat/completions'):
                        base_url = base_url.rsplit('/chat/completions', 1)[0]

                    fallback_models = selected_provider.get('models', ['gpt-4'])
                    final_model = resolved_model or (fallback_models[0] if fallback_models else 'gpt-4')
                    return {
                        'api_key': selected_provider.get('apiKey', os.getenv("OPENAI_API_KEY", "local-dev-key")),
                        'base_url': base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:8080/v1"),
                        'model': final_model,
                        'provider': selected_provider.get('provider'),
                        'temperature': selected_provider.get('temperature'),
                        'top_p': selected_provider.get('topP'),
                    }
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Failed to load LLM config: {e}")
        
        return {
            'api_key': os.getenv("OPENAI_API_KEY", "local-dev-key"),
            'base_url': os.getenv("OPENAI_BASE_URL", "http://localhost:8080/v1"),
            'model': requested_model or os.getenv("OPENAI_MODEL", "gpt-4"),
            'provider': None,
            'temperature': None,
            'top_p': None,
        }
    
    def get_available_agents(self) -> List[Dict[str, Any]]:
        """获取可用的 Agent 列表"""
        agents = list_available_agents_from_yaml()
        
        result = []
        for agent_info in agents:
            agent_def = get_cached_agent(agent_info['name'])
            if agent_def:
                result.append({
                    "name": agent_def.name,
                    "description": agent_def.description,
                    "model": agent_def.model,
                    "skills": agent_def.skills,
                    "triggers": agent_def.triggers[:5] if agent_def.triggers else []
                })
        
        return result
    
    def get_agent_info(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """获取 Agent 信息"""
        agent_def = get_cached_agent(agent_name)
        if agent_def:
            return {
                "name": agent_def.name,
                "description": agent_def.description,
                "model": agent_def.model,
                "temperature": agent_def.temperature,
                "skills": agent_def.skills,
                "triggers": agent_def.triggers,
                "context_strategy": agent_def.context_strategy.type
            }
        return None
    
    def cancel_chat(self, conversation_uuid: str) -> bool:
        """取消聊天 - 设置取消信号"""
        if conversation_uuid in self._cancel_signals:
            self._cancel_signals[conversation_uuid].set()
            logger.info(f"[AgentChatServiceV2] Cancelled chat: {conversation_uuid}")
            return True
        return False
    
    def track_file_access(
        self,
        conversation_uuid: str,
        file_path: str,
        content: str
    ) -> None:
        """追踪文件访问（用于压缩后恢复）"""
        if conversation_uuid not in self._file_states:
            self._file_states[conversation_uuid] = {}
        
        self._file_states[conversation_uuid][file_path] = FileState(
            path=file_path,
            content=content,
            timestamp=time.time()
        )
    
    def track_skill_invocation(
        self,
        conversation_uuid: str,
        skill_name: str,
        skill_path: str,
        content: str
    ) -> None:
        """追踪 Skill 调用（用于压缩后恢复）"""
        if conversation_uuid not in self._skill_states:
            self._skill_states[conversation_uuid] = {}
        
        self._skill_states[conversation_uuid][skill_name] = SkillState(
            name=skill_name,
            path=skill_path,
            content=content,
            invoked_at=time.time()
        )
    
    def get_memory_manager(self):
        """获取 Auto Memory 管理器实例"""
        return self._auto_memory_manager
    
    def get_agent_memory_manager(self):
        """获取 Agent Memory 管理器实例"""
        return self._agent_memory_manager
    
    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        兜底修复：确保发送给 API 的消息完全合法
        
        1. 移除孤儿 tool 消息（没有对应 assistant tool_calls 的）
        2. assistant 消息中的 tool_calls 必须有 id
        """
        # 第一遍：收集所有 assistant 中 tool_calls 声明的 id
        valid_tool_call_ids = set()
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id")
                    if tc_id:
                        valid_tool_call_ids.add(tc_id)
        
        # 第二遍：过滤
        sanitized = []
        removed_count = 0
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            
            # 孤儿 tool 消息：tool_call_id 不在任何一个 assistant 的 tool_calls 中
            if role == "tool":
                tcid = msg.get("tool_call_id")
                if tcid and tcid not in valid_tool_call_ids:
                    print(f"[SANITIZE] Removing orphan tool message[{i}]: tool_call_id={tcid} has no matching assistant tool_calls")
                    removed_count += 1
                    continue
                if not tcid:
                    print(f"[SANITIZE] Removing tool message[{i}]: missing tool_call_id")
                    removed_count += 1
                    continue
            
            # assistant 消息的 tool_calls 必须每个都有 id
            if role == "assistant" and msg.get("tool_calls"):
                for j, tc in enumerate(msg["tool_calls"]):
                    if not tc.get("id"):
                        tc["id"] = f"fallback_tc_{i}_{j}"
                        print(f"[SANITIZE] messages[{i}] tool_calls[{j}] missing id, generated: {tc['id']}")
            
            sanitized.append(msg)
        
        if removed_count > 0:
            print(f"[SANITIZE] Removed {removed_count} orphan/invalid tool messages")
        
        return sanitized
    
    def _apply_advanced_compression(
        self,
        context_messages: List[Dict[str, str]],
        conversation_uuid: str,
        llm_config: Dict[str, Any],
        force_compact: bool = False
    ) -> List[Dict[str, str]]:
        """应用高级上下文压缩"""
        if not context_messages:
            return context_messages
        
        # 如果不需要压缩，直接返回（保留完整的消息结构）
        current_tokens = estimate_messages_tokens([
            Message(uuid=str(uuid.uuid4()), role=m.get("role", "user"), content=m.get("content", ""))
            for m in context_messages
        ])
        
        if not force_compact:
            return context_messages  # 非强制压缩时永不压缩，由用户手动控制
        
        # 需要压缩时，转换为 Message 对象
        # 保存 reasoning_content 以便压缩后恢复
        reasoning_map = {}
        for i, m in enumerate(context_messages):
            if m.get("role") == "assistant" and m.get("reasoning_content"):
                reasoning_map[i] = m["reasoning_content"]
        
        messages = []
        for i, m in enumerate(context_messages):
            messages.append(Message(
                uuid=str(uuid.uuid4()),
                role=m.get("role", "user"),
                content=m.get("content", ""),
                reasoning_content=reasoning_map.get(i),
                tool_call_id=m.get("tool_call_id"),
                tool_calls=m.get("tool_calls")
            ))
        
        try:
            client = OpenAI(
                api_key=llm_config['api_key'],
                base_url=llm_config['base_url'],
                timeout=60.0
            )
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Failed to create LLM client for compression: {e}")
            return smart_compress(
                messages=context_messages,
                max_tokens=90000,
                strategy="smart"
            )
        
        file_states = self._file_states.get(conversation_uuid, {})
        skill_states = self._skill_states.get(conversation_uuid, {})
        
        result = compact_conversation(
            messages=messages,
            llm_client=client,
            llm_model=llm_config.get('model', 'gpt-4'),
            max_tokens=90000,
            recent_messages=10,
            trigger=CompactTrigger.AUTO if force_compact else CompactTrigger.MANUAL,
            file_states=file_states,
            invoked_skills=skill_states,
            session_id=conversation_uuid,
            project_path=self._current_project_path
        )
        
        if not result.success:
            logger.warning(f"[AgentChatServiceV2] Compression failed: {result.error}")
            return context_messages
        
        logger.info(
            f"[AgentChatServiceV2] Compression success: "
            f"{result.pre_compact_token_count} -> {result.post_compact_token_count} tokens "
            f"({result.saved_percent}% saved)"
        )
        
        compressed_messages = []
        
        if result.boundary_marker:
            compressed_messages.append(result.boundary_marker.to_dict())
        
        for msg in result.summary_messages:
            compressed_messages.append(msg.to_dict())
        
        for msg in result.messages_to_keep:
            msg_dict = msg.to_dict()
            # 恢复 reasoning_content（如果原始消息有）
            # 注意：压缩后消息顺序可能变化，需要通过 role 和 content 匹配
            for orig_idx, reasoning_content in reasoning_map.items():
                orig_msg = context_messages[orig_idx]
                if (msg_dict.get("role") == "assistant" and 
                    msg_dict.get("content") == orig_msg.get("content")):
                    msg_dict["reasoning_content"] = reasoning_content
                    break
            compressed_messages.append(msg_dict)
        
        return compressed_messages
    
    def _handle_reactive_compact(
        self,
        messages: List[Dict[str, str]],
        error: Exception,
        conversation_uuid: str,
        llm_config: Dict[str, Any]
    ) -> Optional[List[Dict[str, str]]]:
        """处理响应式压缩（当 API 返回 prompt_too_long 错误时）"""
        is_ptl, token_gap, context_limit = is_prompt_too_long_error(error)
        
        if not is_ptl:
            return None
        
        logger.info(f"[AgentChatServiceV2] Reactive compact triggered, token gap: {token_gap}, limit: {context_limit}")
        
        if conversation_uuid not in self._reactive_states:
            self._reactive_states[conversation_uuid] = ReactiveCompactState()
        reactive_state = self._reactive_states[conversation_uuid]
        
        if reactive_state.retry_count >= 3:
            logger.warning("[AgentChatServiceV2] Reactive compact max retries exceeded")
            return None
        
        reactive_state.retry_count += 1
        reactive_state.last_error = str(error)
        
        msg_objects = []
        for m in messages:
            msg_objects.append(Message(
                uuid=str(uuid.uuid4()),
                role=m.get("role", "user"),
                content=m.get("content", ""),
                tool_call_id=m.get("tool_call_id"),
                tool_calls=m.get("tool_calls")
            ))
        
        try:
            client = OpenAI(
                api_key=llm_config['api_key'],
                base_url=llm_config['base_url'],
                timeout=60.0
            )
        except Exception:
            return None
        
        success, result = reactive_compact_on_error(
            messages=msg_objects,
            error=error,
            llm_client=client,
            llm_model=llm_config.get('model', 'gpt-4')
        )
        
        if not success or not result:
            return None
        
        compressed = []
        if result.boundary_marker:
            compressed.append(result.boundary_marker.to_dict())
        for msg in result.summary_messages:
            compressed.append(msg.to_dict())
        for msg in result.messages_to_keep:
            compressed.append(msg.to_dict())
        
        reactive_state.retry_count = 0
        
        return compressed
    
    async def _extract_memories_async(
        self,
        conversation_uuid: str,
        messages: List[Dict[str, Any]],
        llm_config: Dict[str, Any]
    ) -> None:
        """异步提取记忆（后台执行，不阻塞响应）"""
        extract_state = self._memory_extract_states.get(conversation_uuid, {
            "turn_count": 0,
            "last_extract_turn": 0
        })
        
        extract_state["turn_count"] += 1
        
        if extract_state["turn_count"] - extract_state["last_extract_turn"] < 3:
            self._memory_extract_states[conversation_uuid] = extract_state
            return
        
        if len(messages) < 4:
            return
        
        logger.info(f"[AgentChatServiceV2] Starting memory extraction for {conversation_uuid}")
        
        try:
            client = OpenAI(
                api_key=llm_config['api_key'],
                base_url=llm_config['base_url'],
                timeout=60.0
            )
            
            if self._memory_extractor is None:
                self._memory_extractor = AutoMemoryExtractor(
                    llm_client=client,
                    llm_model=llm_config.get('model', 'gpt-4')
                )
            else:
                # 每次用当前 llm_config 更新，避免使用过期配置
                self._memory_extractor.llm_client = client
                self._memory_extractor.llm_model = llm_config.get('model', 'gpt-4')
            
            result = await self._memory_extractor.extract(
                messages=messages,
                session_id=conversation_uuid,
                current_token_count=sum(len(m.get("content", "")) // 3 for m in messages)
            )
            
            if result:
                logger.info(f"[AgentChatServiceV2] Extracted {len(result)} memories")
                extract_state["last_extract_turn"] = extract_state["turn_count"]
                self._memory_extract_states[conversation_uuid] = extract_state
            else:
                logger.debug(f"[AgentChatServiceV2] No new memories extracted")
                
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Memory extraction error: {e}")
    
    async def chat_stream(
        self,
        agent_name: str,
        message: str,
        conversation_uuid: str,
        user_id: str = "default_user",
        model: str | None = None,
        attachments: List[Dict[str, Any]] | None = None,
        enable_thinking: bool = False,
        project_path: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        thinking_enabled: bool | None = None,
        reasoning_effort: str | None = None,
        user_msg_uuid: str | None = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式聊天（状态机版本）
        
        完整功能：
        - 状态机循环
        - 工具智能并发
        - 上下文压缩
        - 记忆系统
        - 标题自动生成
        - Todo 回调
        - Ask Question 阻塞
        - 权限检查
        """
        logger.info(f"[AgentChatServiceV2] Starting chat with {agent_name}, temperature={temperature}, top_p={top_p}, thinking_enabled={thinking_enabled}, reasoning_effort={reasoning_effort}")
        
        # 存储项目路径
        self._current_project_path = project_path or os.getcwd()  # project_path 由前端传入，此处仅兜底
        
        # 设置记忆系统的项目路径
        if project_path:
            self._auto_memory_manager.set_project(project_path)
            self._agent_memory_manager.set_project(project_path)
        
        # ========== MCP 初始化 ==========
        await self._ensure_mcp_initialized(project_path)
        
        # 1. 加载 Agent 配置
        agent_def = get_cached_agent(agent_name)
        if not agent_def:
            yield {"type": "error", "message": f"Agent '{agent_name}' not found"}
            return
        
        # 2. 获取或创建对话（使用 conversation_uuid 作为持久键）
        conv = history.get_agent_conversation_by_uuid(conversation_uuid)
        if not conv:
            history.create_agent_conversation(user_id, agent_name, title="New conversation", uuid=conversation_uuid)
            conv = history.get_agent_conversation_by_uuid(conversation_uuid)
        
        if conv is None:
            yield {"type": "error", "message": "Failed to create conversation"}
            return
        
        conversation_id = conv["id"]
        
        # 2.5 设置 Transcript 会话
        self._transcript_manager.set_session(
            session_id=conversation_uuid,
            project_path=project_path or self._current_project_path
        )
        
        # 3. 构建用户消息（文本部分存DB，图片部分仅给LLM、不持久化）
        IMAGE_EXT = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.bmp':'image/bmp'}
        full_message = message
        llm_images: list = []  # image_url 块，仅用于 LLM 请求
        if attachments:
            attachment_texts = []
            for att in attachments:
                name = att.get('name', 'unknown')
                b64 = att.get('data_base64', '')
                is_image = False
                if b64:
                    ext = '.' + name.rsplit('.', 1)[-1].lower() if '.' in name else ''
                    mime = IMAGE_EXT.get(ext, '')
                    if mime:
                        img_block = {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}
                        }
                        # 图片细节级别（DeepSeek Vision：low=缩放512x512省token；high/original=原图；auto=等价 original）
                        detail = att.get("detail")
                        if detail in ("low", "high", "original", "auto"):
                            img_block["image_url"]["detail"] = detail
                        llm_images.append(img_block)
                        is_image = True
                        continue  # 图片不进 DB，只给 LLM
                if is_image:
                    continue
                text_content = att.get("text_content")
                if not text_content and b64:
                    parsed = _extract_attachment_text(name, b64)
                    if parsed:
                        text_content = parsed
                if text_content:
                    attachment_texts.append(f"[附件: {name}]\n{text_content}")
                else:
                    attachment_texts.append(f"[附件: {name}]\n(该文件类型的文本内容无法解析，请提示用户改用 read_file/read_pdf 工具处理或转换为文本格式)")
            if attachment_texts:
                full_message = message + "\n\n" + "\n\n".join(attachment_texts)
        
        # 4. 保存用户消息（仅文本，不含图片）
        user_message_id = history.add_agent_chat_message(
            conversation_id=conversation_id,
            role="user",
            content=full_message,
            token_count=count_tokens(full_message),
            agent_name=agent_name,
            msg_uuid=user_msg_uuid
        )
        
        # 5. 加载历史消息
        context_messages = history.get_agent_chat_messages_for_context(conversation_id, limit=999999)
        
        # 5.5 获取 LLM 配置（显式模型 > agent_model_config.json > YAML > provider 默认）
        agent_model_override = self._load_agent_model_config(agent_name) or {}
        self._load_path_whitelist()
        llm_config = self._get_llm_config(agent_name, model)
        use_model = llm_config['model']
        
        # 判断是否为 DeepSeek 模型
        is_deepseek = 'deepseek' in (use_model or '').lower()
        
        # 确定最终使用的参数
        configured_temperature = agent_model_override.get("temperature")
        final_temperature = temperature if temperature is not None else (
            configured_temperature if configured_temperature is not None else (
                agent_def.temperature if agent_def.temperature is not None else llm_config.get("temperature")
            )
        )
        final_top_p = top_p if top_p is not None else getattr(agent_def, 'top_p', None)
        if final_top_p is None:
            final_top_p = llm_config.get("top_p")
        
        # 思考模式参数（所有厂商统一：开关 + 强度，构建逻辑见 build_thinking_params）
        final_thinking_enabled = thinking_enabled if thinking_enabled is not None else True
        final_reasoning_effort = reasoning_effort if reasoning_effort is not None else "high"
        
        # 调试日志：显示模型和参数
        print(f"[AgentChatServiceV2] LLM配置: model={use_model}, temperature={final_temperature}, top_p={final_top_p}, agent={agent_name}")
        print(f"[AgentChatServiceV2] Provider配置: base_url={llm_config['base_url']}")
        print(f"[AgentChatServiceV2] 思考模式: thinking_enabled={final_thinking_enabled}" + (f", reasoning_effort={final_reasoning_effort}" if final_thinking_enabled else ""))
        
        # 5.6 获取或创建自动压缩状态
        if conversation_uuid not in self._auto_compact_states:
            self._auto_compact_states[conversation_uuid] = AutoCompactState()
        auto_compact_state = self._auto_compact_states[conversation_uuid]
        
        # 5.7 增加轮数
        auto_compact_state.increment_turn()
        
        # 6. 检查是否需要自动压缩
        should_compact, compact_reason = should_auto_compact(
            messages=[
                Message(uuid=str(i), role=m.get("role", "user"), content=m.get("content", ""))
                for i, m in enumerate(context_messages[:-1])
            ],
            state=auto_compact_state,
            config=self._auto_compact_config
        )
        
        # 6.5 应用上下文压缩
        compressed_context = self._apply_advanced_compression(
            context_messages=context_messages[:-1],
            conversation_uuid=conversation_uuid,
            llm_config=llm_config,
            force_compact=should_compact
        )
        
        if should_compact:
            logger.info(f"[AgentChatServiceV2] Auto-compact triggered: {compact_reason}")
            auto_compact_state.record_compact()
            yield {
                "type": "compact_triggered",
                "reason": compact_reason,
                "trigger": "auto"
            }
        
        # 7. 获取系统提示词
        system_prompt = get_agent_system_prompt(agent_def)
        
        # 7.1 注入记忆提示词（带内容哈希缓存，避免 system prompt 不必要的变化）
        memory_prompt_text = ""
        try:
            self._auto_memory_manager.set_project(project_path or self._current_project_path)
            
            from .memory import load_memory_prompt, get_memory_prompt_for_agent
            auto_memory_prompt = load_memory_prompt()
            agent_memory_prompt = get_memory_prompt_for_agent(
                agent_type=agent_name,
                project_path=project_path or self._current_project_path
            )
            
            current_memory = (auto_memory_prompt or "") + (agent_memory_prompt or "")
            memory_hash = str(hash(current_memory))
            
            if memory_hash != self._cached_memory_hash:
                self._cached_memory_hash = memory_hash
                if auto_memory_prompt:
                    self._cached_memory_prompt = "\n\n" + auto_memory_prompt
                else:
                    self._cached_memory_prompt = ""
                if agent_memory_prompt:
                    self._cached_memory_prompt += "\n\n" + agent_memory_prompt
            
            memory_prompt_text = self._cached_memory_prompt
            
            if memory_prompt_text:
                logger.info(f"[AgentChatServiceV2] Injected memory prompt ({len(memory_prompt_text)} chars)")
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Failed to build memory prompt: {e}")
        
        # 7.5 添加项目路径提示
        project_path_instruction = f"""

【重要：项目工作目录】
你的工作目录（项目文件夹）是：{self._current_project_path}

⚠️ 严格遵守以下规则：
1. 所有文件操作必须在这个目录下进行
2. 使用相对路径时，会自动相对于此目录解析
3. 绝对禁止访问此目录之外的文件（安全限制）
4. 执行Shell命令时，会自动在此目录下执行
5. 如果用户要求操作其他目录的文件，必须明确拒绝并告知你的工作目录

当前工作目录: {self._current_project_path}
"""
        # 7.6 加载用户便签 (usernote)
        usernote_content = ""
        try:
            _un_data = _read_json_config("usernote.json")
            if _un_data:
                usernote_content = (_un_data.get("content") or "").strip()
        except Exception:
            pass
        
        usernote_prompt = ""
        if usernote_content:
            usernote_prompt = f"""

<usernote>
{usernote_content}
</usernote>

你有能力通过 usernote_write 工具读取和修改 usernote 的内容。usernote 是用户用来记录重要上下文、决策、待办事项的便签，优先级高于对话历史。当用户要求你修改 usernote 时，调用 usernote_write 工具。"""
        
        system_prompt = system_prompt + memory_prompt_text + project_path_instruction + usernote_prompt
        
        # 8. 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]
        for msg in compressed_context:
            messages.append(msg)
        # 图片用多模态 content 数组；纯文本保持字符串兼容
        if llm_images:
            user_content: list = [{"type": "text", "text": full_message or message}]
            user_content.extend(llm_images)
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": full_message})
        
        # 兜底：过滤/修复不合法的 tool 消息
        messages = self._sanitize_messages(messages)
        
        # 9. 创建 AsyncOpenAI 客户端
        try:
            client = AsyncOpenAI(
                api_key=llm_config['api_key'],
                base_url=llm_config['base_url'],
                timeout=300.0
            )
        except Exception as e:
            logger.error(f"[AgentChatServiceV2] Failed to create OpenAI client: {e}")
            yield {"type": "error", "message": f"Failed to initialize LLM client: {e}"}
            return
        
        # 11. 创建取消信号
        cancel_event = asyncio.Event()
        self._cancel_signals[conversation_uuid] = cancel_event
        
        # 11.5 设置 Todo 事件回调，按会话隔离
        from tools.todo_tool import TodoManager
        
        TodoManager.set_session_id(conversation_uuid)

        # 设置权限管理器的会话 ID（用于权限弹窗关联当前会话）
        try:
            from gateway.permission_manager import get_permission_manager
            get_permission_manager().set_session_id(conversation_uuid)
        except Exception:
            pass

        pending_todo_events = []
        
        def on_todo_update(todos_list):
            pending_todo_events.append(todos_list)
        
        TodoManager.set_event_callback(on_todo_update)
        
        # 11.5 重置防爬守卫（每轮对话开始）
        try:
            from .code_network_guardV2 import reset_all_guards
            reset_all_guards()
        except ImportError:
            pass
        
        # 12. 发送开始事件
        yield {
            "type": "chat_started",
            "agent_name": agent_name,
            "conversation_uuid": conversation_uuid,
            "user_message_id": user_message_id,
        }
        
        # ==================== 状态机主循环 ====================
        
        try:
            final_response = ""
            tool_executions = []
            all_response_text = ""
            all_reasoning_text = ""  # 累积所有迭代的推理内容
            execution_order = []
            order_index = 0  # 全局顺序索引，确保严格按输出顺序
            was_interrupted = False  # 是否被用户中断
            suspicious_retry_done = False  # C: 每回合最多自动重试一次（可疑结束检测）
            in_suspicious_retry = False  # 2026-10-16: 当前 iteration 是否为可疑重试轮（API 兜底保护窗口）
            
            logger.info(f"[AgentChatServiceV2] Starting state machine loop (unlimited iterations)")
            
            # ========== while(true) 主循环 ==========
            iteration = 0
            while True:
                iteration += 1
                current_iteration = iteration
                
                logger.info(f"[AgentChatServiceV2] ========== Iteration {current_iteration} ==========")
                
                # 检查取消
                if cancel_event.is_set():
                    was_interrupted = True
                    yield {"type": "cancelled", "message": "Generation cancelled"}
                    break
                
                # 重置当前迭代状态
                response_text = ""
                reasoning_text = ""  # 思考模式下的推理内容
                finish_reason = None  # B: 本轮流的结束方式（stop/length/tool_calls/None=异常缺失）
                tool_calls_chunks = {}
                iteration_start_order_index = len(execution_order)
                
                # 流式输出时的状态追踪：实时检测内容类型切换
                current_chunk_type = None  # 'reasoning' 或 'content'
                current_chunk_content = ""  # 当前累积的内容
                
                # ========== 调用 LLM ==========
                tools = self._get_tools_definition(agent_def)
                
                # print(f"[AgentChatServiceV2] ========== ITERATION {iteration} ==========")
                # print(f"[AgentChatServiceV2] tools from _get_tools_definition: {len(tools) if tools else 0} tools")
                # if tools:
                #     tool_names = [t.get('function', {}).get('name', 'unknown') for t in tools]
                #     print(f"[AgentChatServiceV2] tool names: {tool_names[:5]}...")  # 只打印前5个
                
                # print(f"[AgentChatServiceV2] messages count: {len(messages)}")
                # for i, msg in enumerate(messages[-3:]):  # 只打印最后3条消息
                #     role = msg.get('role', 'unknown')
                #     # 跳过 system 消息的详细打印（太长）
                #     if role == 'system':
                #         print(f"[AgentChatServiceV2] messages[{len(messages)-3+i}]: role=system, content=<system prompt omitted>")
                #         continue
                #     content_preview = str(msg.get('content', ''))[:100]
                #     reasoning_preview = str(msg.get('reasoning_content', ''))[:50] if msg.get('reasoning_content') else 'None'
                #     tool_calls_info = 'has tool_calls' if msg.get('tool_calls') else 'no tool_calls'
                #     print(f"[AgentChatServiceV2] messages[{len(messages)-3+i}]: role={role}, content={content_preview}..., reasoning={reasoning_preview}..., {tool_calls_info}")

                stream_params = {
                    "model": use_model,
                    "messages": messages,
                    "stream": True,
                    "temperature": final_temperature,
                    "top_p": final_top_p,
                }
                
                # 思考模式参数（所有厂商统一：开关 + 强度；非 DeepSeek 的 max 档自动降级）
                stream_params.update(build_thinking_params(use_model, final_thinking_enabled, final_reasoning_effort))
                
                if tools:
                    stream_params["tools"] = tools
                    # DeepSeek 思考模式不支持 tool_choice 参数
                    if not (is_deepseek and final_thinking_enabled):
                        stream_params["tool_choice"] = "auto"
                        # print(
                        #     f"[AgentChatServiceV2] Added tools to stream_params, "
                        #     f"tool_choice={stream_params['tool_choice']}"
                        # )
                    # else:
                    #     print(f"[AgentChatServiceV2] Added tools to stream_params, tool_choice skipped (DeepSeek thinking mode)")
                # else:
                #     print(f"[AgentChatServiceV2] WARNING: No tools added to stream_params!")
                
                print(f"[AgentChatServiceV2] Final stream_params keys: {list(stream_params.keys())}")
                
                # 尝试 API 调用，支持响应式压缩
                max_api_retries = 3
                api_retry_count = 0
                stream = None
                graceful_abort = False  # 2026-10-16: 重试轮被 API 拒绝时置 True → 跳出主循环走优雅收尾
                
                while api_retry_count < max_api_retries:
                    # 检查取消
                    if cancel_event.is_set():
                        logger.info("[AgentChatServiceV2] Cancelled during API retry")
                        was_interrupted = True
                        yield {"type": "cancelled", "message": "Generation cancelled during API retry"}
                        break
                    
                    try:
                        stream = await client.chat.completions.create(**stream_params)
                        in_suspicious_retry = False  # 请求已成功发出，兜底窗口关闭
                        break
                    except Exception as api_error:
                        api_retry_count += 1
                        api_error_text = str(api_error).lower()

                        is_ptl, token_gap, context_limit = is_prompt_too_long_error(api_error)
                        
                        if is_ptl and api_retry_count < max_api_retries:
                            logger.warning(f"[AgentChatServiceV2] API returned prompt_too_long, attempting reactive compact (attempt {api_retry_count})")
                            
                            yield {
                                "type": "compact_triggered",
                                "reason": f"prompt_too_long (gap: {token_gap}, limit: {context_limit})",
                                "trigger": "reactive"
                            }
                            
                            compressed_messages = self._handle_reactive_compact(
                                messages=messages,
                                error=api_error,
                                conversation_uuid=conversation_uuid,
                                llm_config=llm_config
                            )
                            
                            if compressed_messages:
                                messages = self._sanitize_messages(compressed_messages)
                                stream_params["messages"] = messages
                                logger.info(f"[AgentChatServiceV2] Reactive compact successful, retrying API call")
                                # 中断检查：压缩过程中可能已触发取消
                                if cancel_event.is_set():
                                    logger.info("[AgentChatServiceV2] Cancelled after compact, aborting retry")
                                    was_interrupted = True
                                    yield {"type": "cancelled", "message": "Generation cancelled after compact"}
                                    break
                                continue
                            else:
                                logger.error("[AgentChatServiceV2] Reactive compact failed, cannot recover")
                                raise api_error
                        else:
                            # 2026-10-16 兜底：可疑重试轮的请求被 API 拒绝（如 400 Invalid assistant
                            # message——重试构造的历史消息非法）时，不再硬终止，接受当前状态为完成
                            if in_suspicious_retry and "invalid assistant message" in api_error_text:
                                logger.warning(
                                    f"[AgentChatServiceV2] Suspicious-retry request rejected by API "
                                    f"({api_error}); accepting turn as complete"
                                )
                                graceful_abort = True
                                break
                            raise api_error
                
                if stream is None:
                    if graceful_abort:
                        break  # 跳出 iteration 主循环 → 走正常收尾（接受为完成）
                    raise RuntimeError("Failed to get API response after retries")
                
                # 流式响应处理（async for 在 await 之间让出事件循环 → 取消信号可立即响应）
                chunk_count = 0
                tool_call_chunk_count = 0
                
                try:
                    async for chunk in stream:
                        chunk_count += 1
                        
                        if cancel_event.is_set():
                            logger.info(f"[AgentChatServiceV2] Cancel detected at chunk #{chunk_count}")
                            was_interrupted = True
                            yield {"type": "cancelled", "message": "Generation cancelled"}
                            break
                        
                        if not (chunk.choices and len(chunk.choices) > 0):
                            continue
                        
                        # B: 捕获 finish_reason（最后一个 chunk 携带；None=流异常缺失）
                        if chunk.choices[0].finish_reason:
                            finish_reason = chunk.choices[0].finish_reason
                        
                        delta = chunk.choices[0].delta
                        
                        # ── 推理内容 ──
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            if current_chunk_type == 'content' and current_chunk_content:
                                execution_order.append({"type": "text", "content": current_chunk_content, "order": order_index})
                                order_index += 1
                                current_chunk_content = ""
                            current_chunk_type = 'reasoning'
                            current_chunk_content += delta.reasoning_content
                            reasoning_text += delta.reasoning_content
                            yield {"type": "reasoning", "content": delta.reasoning_content, "order": order_index}
                            await asyncio.sleep(0)  # 让出事件循环，让 WebSocket _send 任务执行
                        
                        # ── 文本内容 ──
                        if delta.content:
                            if current_chunk_type == 'reasoning' and current_chunk_content:
                                execution_order.append({"type": "reasoning", "content": current_chunk_content, "order": order_index})
                                order_index += 1
                                current_chunk_content = ""
                            current_chunk_type = 'content'
                            current_chunk_content += delta.content
                            response_text += delta.content
                            # [DEBUG-STREAM-BACKEND] print(f"[DEBUG-STREAM-BACKEND] yield content chunk: len={len(delta.content)}, total_response={len(response_text)}")
                            yield {"type": "content", "content": delta.content, "order": order_index}
                            await asyncio.sleep(0)  # 让出事件循环，让 WebSocket _send 任务执行
                        
                        # ── 工具调用 ──
                        if hasattr(delta, 'tool_calls') and delta.tool_calls:
                            tool_call_chunk_count += 1
                            for tc in delta.tool_calls:
                                idx = tc.index
                                if idx not in tool_calls_chunks:
                                    tool_calls_chunks[idx] = {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}}
                                if tc.id:
                                    tool_calls_chunks[idx]['id'] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        tool_calls_chunks[idx]['function']['name'] = tc.function.name
                                    if tc.function.arguments:
                                        tool_calls_chunks[idx]['function']['arguments'] += tc.function.arguments
                                current_tool_id = tool_calls_chunks[idx]['id'] or f"temp_{idx}"
                                current_tool_name = tool_calls_chunks[idx]['function']['name'] or ''
                                if current_tool_name:
                                    yield {
                                        "type": "tool_call_streaming",
                                        "tool_call_id": current_tool_id,
                                        "tool_name": current_tool_name,
                                        "arguments_chunk": tc.function.arguments if tc.function and tc.function.arguments else '',
                                        "arguments_full": tool_calls_chunks[idx]['function']['arguments'],
                                        "index": idx
                                    }
                                    await asyncio.sleep(0)  # 让出事件循环，让 WebSocket _send 任务执行
                except Exception as stream_err:
                    err_str = str(stream_err)
                    logger.info(f"[AgentChatServiceV2] Stream ended: {err_str}")
                    if cancel_event.is_set():
                        was_interrupted = True
                        yield {"type": "cancelled", "message": "Generation cancelled"}
                        break
                    raise
                
                if current_chunk_type and current_chunk_content:
                    if current_chunk_type == 'reasoning':
                        execution_order.append({
                            "type": "reasoning",
                            "content": current_chunk_content,
                            "order": order_index
                        })
                        order_index += 1
                    elif current_chunk_type == 'content':
                        execution_order.append({
                            "type": "text",
                            "content": current_chunk_content,
                            "order": order_index
                        })
                        order_index += 1
                current_chunk_content = ""
                current_chunk_type = None

                # 先累加本轮的 response_text 和 reasoning_text（中断前也要保存）
                if reasoning_text:
                    all_reasoning_text += reasoning_text
                if response_text:
                    all_response_text += response_text
                    final_response = response_text

                # B: 记录本轮流的结束方式（stop=正常 / length=截断 / None=异常缺失，可事后追查）
                logger.info(
                    f"[AgentChatServiceV2] Iteration {iteration} stream end: "
                    f"finish_reason={finish_reason}, content_len={len(response_text)}, "
                    f"reasoning_len={len(reasoning_text)}, tool_calls={len(tool_calls_chunks)}"
                )

                # 中断检查：流式结束后立即检查，避免继续执行工具
                if was_interrupted:
                    logger.info("[AgentChatServiceV2] Interrupted after streaming, breaking main loop")
                    break
                
                # ========== 捕获 usage 并计费（对齐 opencode：仅使用 API 原始 token 值） ==========
                if hasattr(chunk, 'usage') and chunk.usage:
                    try:
                        cost_info = self._calculate_cost(use_model, chunk.usage)
                        if cost_info:
                            session_usage = self._accumulate_cost(conversation_uuid, cost_info, model_name=use_model)
                            # 环显示用完整 prompt_tokens（已含缓存），面板用 cost_info.input_tokens（非缓存输入）
                            latest_input = getattr(chunk.usage, 'prompt_tokens', 0)
                            
                            yield {
                                "type": "usage",
                                "model": use_model,
                                "session_cost_cny": round(session_usage["total_cost"], 4),
                                "latest_input_tokens": latest_input,
                                "latest_output_tokens": cost_info.get("output_tokens", 0),
                                "model_context_limit": get_model_context_limit(use_model),
                                "period": cost_info.get("period"),  # 本次计费时段 peak/offpeak（分时模型）
                                "this_call": {**cost_info, "model": use_model}
                            }
                    except Exception as e:
                        logger.warning(f"[Cost] Failed to calculate cost: {e}", exc_info=True)
                # ========================================
                
                # 构建工具调用列表
                tool_calls = None
                if tool_calls_chunks:
                    tool_calls = [tool_calls_chunks[idx] for idx in sorted(tool_calls_chunks.keys())]
                    
                    # ========== 空 id 修复 ==========
                    # 流式返回中 tool_call.id 可能因网络波动/chunk 乱序/取消时机
                    # 而保持空字符串。在此统一修复：为每个空 id 生成稳定 UUID，
                    # 确保下游消息构建、execution_order 记录、数据库保存、取消后
                    # 重建全链路 id 永远非空。
                    for tc in tool_calls:
                        if not tc.get("id"):
                            tc["id"] = f"call_{uuid.uuid4().hex[:16]}"
                            logger.warning(
                                f"[AgentChatServiceV2] Fixed empty tool_call.id -> {tc['id']} "
                                f"(function={tc.get('function', {}).get('name', 'unknown')})"
                            )

                    # 在写入 assistant 消息之前清洗工具调用。
                    # 空名/未知名工具调用不是可恢复的真实工具调用；如果把它们回灌给模型，
                    # 会形成“空工具名 -> 错误反馈 -> 再次尝试空工具名”的循环。
                    valid_tool_names = {}
                    for tool_def in tools:
                        tool_name = tool_def.get("function", {}).get("name", "")
                        if tool_name and tool_name != "_unknown_tool":
                            valid_tool_names[tool_name] = tool_name
                            valid_tool_names[tool_name.lower()] = tool_name

                    sanitized_tool_calls = []
                    dropped_tool_calls = []
                    for tc in tool_calls:
                        function = tc.get("function", {})
                        tool_name = function.get("name", "")

                        if not tool_name:
                            dropped_tool_calls.append((tc.get("id", ""), "empty_name"))
                            continue

                        corrected = valid_tool_names.get(tool_name) or valid_tool_names.get(tool_name.lower())
                        if not corrected:
                            dropped_tool_calls.append((tc.get("id", ""), f"unknown_name:{tool_name}"))
                            continue

                        if corrected != tool_name:
                            logger.info(f"[AgentChatServiceV2] Repaired tool name case: '{tool_name}' -> '{corrected}'")
                            tc["function"]["name"] = corrected

                        sanitized_tool_calls.append(tc)

                    if dropped_tool_calls:
                        logger.warning(
                            "[AgentChatServiceV2] Dropped invalid tool calls before execution: %s",
                            dropped_tool_calls
                        )
                        tool_calls = sanitized_tool_calls
                # ========================================
                
                # ========== 调试日志 ==========
                # print(f"[AgentChatServiceV2] Stream ended: response_text={len(response_text)} chars, reasoning_text={len(reasoning_text)} chars, tool_calls_chunks={len(tool_calls_chunks)}")
                # if tool_calls_chunks:
                #     for idx, tc in tool_calls_chunks.items():
                #         print(f"  tool_calls_chunks[{idx}]: id={tc.get('id')}, name={tc.get('function', {}).get('name')}")
                
                # ========== 判断是否终止 ==========
                if not tool_calls:
                    # C: 可疑结束检测 + 自动重试一次（2026-10-15 "过程突然终止"事件：
                    # 模型在"思考→行动"衔接点静默停止，原逻辑直接误判为"任务完成"）
                    suspicious = _is_suspicious_stream_end(finish_reason, response_text)
                    if suspicious and not suspicious_retry_done:
                        suspicious_retry_done = True
                        in_suspicious_retry = True  # 标记下一 iteration 为兜底保护窗口
                        logger.warning(
                            f"[AgentChatServiceV2] Suspicious stream end (finish_reason={finish_reason}, "
                            f"content_len={len(response_text)}, reasoning_len={len(reasoning_text)}) - auto-retrying once"
                        )
                        # 回传本轮输出保持上下文连贯（DeepSeek 思考模式要求回传 reasoning_content）
                        # 2026-10-16: content 为空时不得回传——空 content + 无 tool_calls 的 assistant
                        # 消息会被 API 拒绝（400 Invalid assistant message），跳过以保持请求合法
                        if response_text:
                            messages.append({
                                "role": "assistant",
                                "content": response_text,
                                "reasoning_content": reasoning_text or None,
                            })
                        messages.append({
                            "role": "user",
                            "content": "[系统提示] 你上一条输出意外中断了（没有工具调用也没有文字收尾）。请从中断处继续完成任务。",
                        })
                        continue
                    if suspicious:
                        logger.warning(
                            f"[AgentChatServiceV2] Suspicious stream end persisted after retry "
                            f"(finish_reason={finish_reason}) - accepting as complete"
                        )
                    logger.info(f"[AgentChatServiceV2] No tool calls, task completed")
                    break
                
                # ========== 执行工具（智能并发） ==========
                logger.info(f"[AgentChatServiceV2] Executing {len(tool_calls)} tool calls with smart concurrency")
                
                # 构建助手消息
                assistant_msg = {
                    "role": "assistant",
                    "content": response_text or None,
                    "tool_calls": tool_calls
                }
                # 如果有推理内容，添加到消息中（DeepSeek 思考模式要求必须回传，其他模型可选）
                assistant_msg["reasoning_content"] = reasoning_text if reasoning_text else None
                messages.append(assistant_msg)
                
                # 检查是否有 ask_user_question 工具，需要提前发送事件并注入 question_id
                for tc in tool_calls:
                    tool_name = tc.get("function", {}).get("name", "")
                    if tool_name == "ask_user_question":
                        # 解析参数获取问题数据
                        tool_args_str = tc.get("function", {}).get("arguments", "{}")
                        try:
                            tool_args = safe_json_loads(tool_args_str, default={}) if isinstance(tool_args_str, str) else tool_args_str
                        except Exception:
                            tool_args = {}
                        
                        questions = tool_args.get("questions", [])
                        if questions:
                            # 生成 question_id 并注入到工具参数中
                            question_id = str(uuid.uuid4())
                            tool_args["question_id"] = question_id
                            # 更新工具参数（字符串格式）
                            tc["function"]["arguments"] = json.dumps(tool_args, ensure_ascii=False)
                            
                            # ⚠️ 必须在 yield 前注册问题，否则用户可能在注册完成前提交答案
                            # 导致 submit_answers() 的答案被 set_pending_questions() 清掉
                            from tools.ask_question_tool import QuestionManager
                            QuestionManager.set_pending_questions({
                                "id": question_id,
                                "questions": questions,
                                "timestamp": time.time()
                            })
                            
                            logger.info(f"[AgentChatServiceV2] Sending ask_question event for {question_id}")
                            yield {
                                "type": "ask_question",
                                "question_id": question_id,
                                "questions": questions
                            }
                
                # 转换工具调用为 ToolUseBlock 格式（在修改 tc 之后）
                tool_use_blocks = []
                tool_results_map = {}
                
                for tc in tool_calls:
                    tool_args_str = tc.get("function", {}).get("arguments", "{}")
                    try:
                        tool_args = safe_json_loads(tool_args_str, default={}) if isinstance(tool_args_str, str) else tool_args_str
                    except Exception:
                        tool_args = {}
                    
                    tool_name = tc.get("function", {}).get("name", "")
                    tc_id = tc.get("id", f"route_{uuid.uuid4().hex[:8]}")
                    
                    tool_use_blocks.append(ToolUseBlock(
                        id=tc.get("id", str(uuid.uuid4())[:8]),
                        name=tool_name,
                        arguments=tool_args
                    ))
                
                # 创建执行上下文
                exec_context = ToolExecutionContext(
                    conversation_uuid=conversation_uuid,
                    agent_name=agent_name,
                    project_path=self._current_project_path,
                    cancel_event=cancel_event,
                    state=QueryState(
                        messages=[],
                        system_prompt="",
                        user_context={}
                    ),
                    # 快照系统上下文字段
                    snapshot_session_uuid=conversation_uuid,
                    snapshot_message_id=user_message_id,
                )
                
                # 【关键修复】确保工具执行器使用正确的项目路径
                self._tool_executor.set_project_path(self._current_project_path)
                
                # 工具执行前检查取消
                if cancel_event.is_set():
                    logger.info("[AgentChatServiceV2] Cancelled before tool execution")
                    was_interrupted = True
                    # 把已收集的工具记录到 execution_order，避免 tool_call_id 丢失
                    for tc in tool_calls:
                        execution_order.append({
                            "type": "tool",
                            "order": order_index,
                            "tool_info": {
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", "unknown"),
                                "status": "cancelled",
                                "input": tc.get("function", {}).get("arguments", ""),
                                "output": "Cancelled by user",
                                "duration": 0
                            }
                        })
                        order_index += 1
                    yield {"type": "cancelled", "message": "Generation cancelled before tool execution"}
                    break
                
                # 使用 ToolOrchestrator 执行工具
                orchestrator = ToolOrchestrator(
                    tool_executor=self._tool_executor,
                    max_concurrency=get_max_concurrency()
                )
                
                # 用 try/except 保护 async for，确保 break 时 aclose() 的异常不会跳过中断处理
                try:
                    async for update in orchestrator.run_tools(tool_use_blocks, exec_context):
                        event = update.event
                        result = update.result
                        
                        # 发送工具执行事件
                        yield {
                            "type": "tool_execution",
                            "tool_execution_id": event.tool_execution_id,
                            "tool_call_id": result.tool_call_id if result else event.tool_execution_id,
                            "tool_name": event.tool_name,
                            "status": event.status,
                            "input": event.input or "",
                            "output": event.output or "",
                            "duration": event.duration or 0,
                            "order": order_index  # 当前顺序索引
                        }
                        
                        # 如果有结果，保存它
                        if result:
                            tool_results_map[result.tool_call_id] = result
                            
                            # 记录到执行列表
                            tool_executions.append({
                                "id": result.tool_call_id,
                                "name": result.tool_name,
                                "status": result.status,
                                "input": result.input,
                                "output": result.output,
                                "duration": result.duration
                            })
                            
                            # 按实际执行顺序记录工具
                            execution_order.append({
                                "type": "tool",
                                "order": order_index,
                                "tool_info": {
                                    "id": result.tool_call_id,
                                    "name": result.tool_name,
                                    "status": result.status,
                                    "input": result.input,
                                    "output": result.output,
                                    "duration": result.duration
                                }
                            })
                            order_index += 1
                        elif event.status == "cancelled":
                            # 取消的工具也要记录到 execution_order，否则重建消息时会缺少 tool_call_id
                            execution_order.append({
                                "type": "tool",
                                "order": order_index,
                                "tool_info": {
                                    "id": event.tool_execution_id,
                                    "name": event.tool_name,
                                    "status": "cancelled",
                                    "input": event.input or "",
                                    "output": "Error: Tool execution cancelled by user",
                                    "duration": event.duration or 0
                                }
                            })
                            order_index += 1
                        
                        # 检查待发送的 todo 事件
                        while pending_todo_events:
                            todos_data = pending_todo_events.pop(0)
                            yield {
                                "type": "todo_update",
                                "todos": todos_data
                            }
                        
                        # 检查待发送的 ask_question 事件
                        if self._pending_question:
                            question_data = self._pending_question
                            self._pending_question = None
                            yield {
                                "type": "ask_question",
                                "question_id": question_data.get("id"),
                                "questions": question_data.get("questions", [])
                            }

                        if cancel_event.is_set():
                            logger.info("[AgentChatServiceV2] Cancellation detected during tool execution")
                            was_interrupted = True
                            
                            # 即使取消了，也要补上已完成的工具结果，否则下次 API 调用缺少 tool_call_id
                            for tc in tool_calls:
                                tool_id = tc.get("id", "")
                                tool_result = tool_results_map.get(tool_id)
                                if tool_result:
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tool_id,
                                        "content": tool_result.output
                                    })
                                else:
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tool_id,
                                        "content": "Error: Tool execution cancelled by user"
                                    })
                            
                            yield {"type": "cancelled", "message": "Generation cancelled"}
                            break
                except GeneratorExit:
                    # async for 被 break 时 aclose() 可能抛出 GeneratorExit，标记中断后放行
                    was_interrupted = True
                except asyncio.CancelledError:
                    was_interrupted = True
                    # 不重新抛出，让 while 循环后的保存代码执行
                except Exception:
                    # 其他异常不特殊处理，让外层 except 捕获
                    raise
                
                # 如果工具执行被中断，直接跳出 while 循环（不再等下一轮迭代）
                if was_interrupted:
                    break
                
                # 添加所有工具结果到消息
                for tc in tool_calls:
                    tool_id = tc.get("id", "")
                    tool_result = tool_results_map.get(tool_id)
                    if tool_result:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_result.output
                        })
                    else:
                        # 如果没有结果，添加空结果
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": "Error: Tool execution result not found"
                        })
                
                logger.info(f"[AgentChatServiceV2] Tool execution completed, continuing to next iteration...")
            
            # ========== 循环结束，保存结果 ==========
            
            # 序列化 execution_order 为 JSON
            execution_order_json = json.dumps(execution_order, ensure_ascii=False) if execution_order else None
            
            # 保存 AI 回复 — 无论是否中断都必须执行
            assistant_message_id = 0
            assistant_msg_uuid = str(uuid.uuid4())
            if execution_order:
                # 只提取 text 类型内容，不包含 tool 类型
                full_response_parts = []
                for item in execution_order:
                    if item["type"] == "text":
                        full_response_parts.append(item["content"])
                    # tool 类型不添加到 content 中，避免 LLM 学习并伪造

                full_response = "\n\n".join(full_response_parts) if full_response_parts else ""
                # 修复：execution_order 无 text 项但有实际响应文本时，用 all_response_text 兜底
                if not full_response and (all_response_text or final_response):
                    full_response = all_response_text or final_response

                assistant_message_id = history.add_agent_chat_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_response,
                    reasoning_content=all_reasoning_text if all_reasoning_text else None,
                    token_count=count_tokens(full_response),
                    agent_name=agent_name,
                    execution_order=execution_order_json,
                    msg_uuid=assistant_msg_uuid
                )
            elif all_response_text or final_response:
                saved_response = all_response_text if all_response_text else final_response
                assistant_message_id = history.add_agent_chat_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=saved_response,
                    reasoning_content=all_reasoning_text if all_reasoning_text else None,
                    token_count=count_tokens(saved_response),
                    agent_name=agent_name,
                    execution_order=execution_order_json,
                    msg_uuid=assistant_msg_uuid
                )
            elif was_interrupted:
                # 中断时且没有任何文本/工具调用的极端情况：存一条空消息
                assistant_message_id = history.add_agent_chat_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content="",
                    reasoning_content=all_reasoning_text if all_reasoning_text else None,
                    token_count=0,
                    agent_name=agent_name,
                    execution_order=execution_order_json,
                    msg_uuid=assistant_msg_uuid
                )
            
            # 非中断时才生成标题和提取记忆
            if not was_interrupted:
                # 检查是否需要生成标题
                current_messages = history.get_agent_chat_messages_for_context(conversation_id, limit=10)
                message_count = len(current_messages)
                
                conv_info = history.get_agent_conversation_by_uuid(conversation_uuid)
                current_title = conv_info.get("title", "") if conv_info else ""
                
                need_generate_title = False
                if message_count >= 3:
                    if current_title == "新对话" or current_title.startswith("与 "):
                        need_generate_title = True
                
                if need_generate_title:
                    try:
                        new_title = await self.generate_conversation_title(current_messages, use_model, final_thinking_enabled, final_reasoning_effort)
                        history.update_agent_conversation_title(conversation_uuid, new_title)
                        yield {
                            "type": "title_updated",
                            "conversation_uuid": conversation_uuid,
                            "title": new_title
                        }
                    except Exception as e:
                        logger.error(f"[AgentChatServiceV2] Failed to generate title: {e}")
            
            # 发送完成事件（无论是否中断都发，让前端能收到完成信号）
            yield {
                "type": "chat_completed",
                "content": all_response_text if all_response_text else final_response,
                "reasoning": all_reasoning_text if all_reasoning_text else None,
                "agent_name": agent_name,
                "execution_order": execution_order,
                "assistant_message_id": assistant_message_id,
                "assistant_msg_uuid": assistant_msg_uuid,
                "interrupted": was_interrupted
            }
            
            # 非中断时才提取记忆
            if not was_interrupted:
                try:
                    extract_messages = []
                    for i, m in enumerate(context_messages):
                        extract_messages.append({
                            "uuid": str(i),
                            "role": m.get("role", "user"),
                            "content": m.get("content", "")
                        })
                    
                    extract_messages.append({
                        "uuid": str(len(extract_messages)),
                        "role": "user",
                        "content": full_message
                    })
                    if all_response_text or final_response:
                        extract_messages.append({
                            "uuid": str(len(extract_messages)),
                            "role": "assistant",
                            "content": all_response_text or final_response
                        })
                    
                    await self._extract_memories_async(
                        conversation_uuid=conversation_uuid,
                        messages=extract_messages,
                        llm_config=llm_config
                    )
                except Exception as e:
                    logger.warning(f"[AgentChatServiceV2] Memory extraction failed: {e}")
        
        except asyncio.CancelledError:
            # future.cancel() 触发的 CancelledError：保存当前已累积的数据到 agent_chat.db
            logger.warning("[AgentChatServiceV2] CancelledError caught, saving partial data")
            was_interrupted = True
            execution_order_json = json.dumps(execution_order, ensure_ascii=False) if execution_order else None
            cancelled_msg_uuid = str(uuid.uuid4())
            # 关键：包含当前迭代已生成但未累加的 response_text / reasoning_text
            partial_content = (all_response_text or "") + (response_text or "")
            partial_reasoning = (all_reasoning_text or "") + (reasoning_text or "")
            print(f"[DEBUG-INTERRUPT] Service CancelledError: all_response={len(all_response_text or '')} response={len(response_text or '')}  partial_content={len(partial_content or '')} partial_reasoning={len(partial_reasoning or '')} all_reasoning={len(all_reasoning_text or '')} reasoning={len(reasoning_text or '')}")
            if execution_order or partial_content or partial_reasoning or final_response:
                try:
                    history.add_agent_chat_message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=partial_content,
                        reasoning_content=partial_reasoning or None,
                        token_count=count_tokens(partial_content),
                        agent_name=agent_name,
                        execution_order=execution_order_json,
                        msg_uuid=cancelled_msg_uuid
                    )
                    logger.info(f"[AgentChatServiceV2] Partial data saved to agent_chat.db after CancelledError, uuid={cancelled_msg_uuid}")
                except Exception as e:
                    logger.error(f"[AgentChatServiceV2] Failed to save partial data after CancelledError: {e}")
            # 发射 chat_completed 事件，携带 interrupted=true，让前端能收到中断标记
            print(f"[DEBUG-INTERRUPT] Service yielding chat_completed: content_len={len(partial_content or '')} reasoning_len={len(partial_reasoning or '')} interrupted=True")
            yield {
                "type": "chat_completed",
                "content": partial_content,
                "reasoning": partial_reasoning or None,
                "agent_name": agent_name,
                "execution_order": execution_order,
                "assistant_message_id": 0,
                "assistant_msg_uuid": cancelled_msg_uuid,
                "interrupted": True
            }
        
        except Exception as e:
            logger.error(f"[AgentChatServiceV2] Error during chat: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
        
        finally:
            # 清理 Todo 回调
            try:
                from tools.todo_tool import TodoManager
                TodoManager.set_event_callback(None)
            except:
                pass
            
            if conversation_uuid in self._cancel_signals:
                del self._cancel_signals[conversation_uuid]
    
    def _get_tools_definition(self, agent_def=None) -> List[Dict[str, Any]]:
        """获取工具定义（包含 MCP 工具），并按 Agent 工具策略过滤

        agent_def.tools.enforce=True 时：被禁止工具的 schema 不会上传给 LLM；
        enforce=False 或 agent_def 为 None 时原样返回（向后兼容）。
        """
        try:
            tools = get_tools_definition_with_mcp(
                include_mcp=self._mcp_initialized,
                mcp_manager=self._mcp_manager
            )
        except Exception as e:
            logger.warning(f"[AgentChatServiceV2] Failed to get MCP tools, using base tools only: {e}")
            tools = get_tools_definition()
        # 按 Agent 权限过滤（enforce 未启用时原样返回）
        return filter_tools_for_agent(tools, agent_def)
    
    async def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """
        执行工具
        
        注意：权限检查已下沉到 tool_executor_impl.py 中统一处理
        此方法仅负责设置项目路径和调用执行器
        """
        logger.info(f"[AgentChatServiceV2] _execute_tool: {tool_name}")
        
        # 更新工具执行器的项目路径
        self._tool_executor.set_project_path(self._current_project_path)
        
        # 创建执行上下文
        context = ToolExecutionContext(
            conversation_uuid="",
            agent_name="",
            project_path=self._current_project_path,
            cancel_event=asyncio.Event(),
            state=QueryState(
                messages=[],
                system_prompt="",
                user_context={}
            )
        )
        
        # 权限检查由 tool_executor.execute() 内部统一处理
        return await self._tool_executor.execute(tool_name, args, context)


# ==================== 单例 ====================

_instance: Optional[AgentChatServiceV2] = None


def get_agent_chat_service_v2() -> AgentChatServiceV2:
    """获取 AgentChatServiceV2 单例"""
    global _instance
    if _instance is None:
        _instance = AgentChatServiceV2()
    return _instance
