"""
Auto Memory Scan - 记忆文件扫描

1. 扫描记忆目录中的所有记忆文件
2. 解析 frontmatter 提取元数据
3. 格式化记忆清单（用于注入提示词）
4. 相关记忆搜索

用于：
- 记忆提取时预注入现有记忆列表
- 记忆管理界面展示
- 相关记忆检索
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .auto_memory_types import MemoryEntry, MemoryType, parse_memory_type
from .auto_memory_paths import get_auto_memory_dir, ENTRYPOINT_NAME

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class ScannedMemory:
    """扫描到的记忆文件信息"""
    name: str
    file_path: str
    memory_type: MemoryType
    description: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    content_preview: str = ""  # 内容预览（前 200 字符）
    error: Optional[str] = None  # 解析错误
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'name': self.name,
            'file_path': self.file_path,
            'memory_type': self.memory_type.value,
            'description': self.description,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'tags': self.tags,
            'content_preview': self.content_preview,
            'error': self.error,
        }


@dataclass
class MemoryScanResult:
    """记忆扫描结果"""
    memories: List[ScannedMemory]
    total_count: int
    by_type: Dict[str, int] = field(default_factory=dict)
    errors: List[Tuple[str, str]] = field(default_factory=list)  # (file_path, error)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'memories': [m.to_dict() for m in self.memories],
            'total_count': self.total_count,
            'by_type': self.by_type,
            'errors': self.errors,
        }


# ============================================================================
# 扫描实现
# ============================================================================

def scan_memory_file(file_path: str) -> Optional[ScannedMemory]:
    """
    扫描单个记忆文件
    
    Args:
        file_path: 文件路径
    
    Returns:
        ScannedMemory 或 None
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析 frontmatter
        entry = MemoryEntry.from_frontmatter(content, file_path)
        
        if entry is None:
            return ScannedMemory(
                name=os.path.basename(file_path).replace('.md', ''),
                file_path=file_path,
                memory_type=MemoryType.PROJECT,
                description='（解析失败）',
                error='Failed to parse frontmatter',
            )
        
        # 提取内容预览
        content_preview = entry.content[:200] if len(entry.content) > 200 else entry.content
        
        return ScannedMemory(
            name=entry.name,
            file_path=file_path,
            memory_type=entry.memory_type,
            description=entry.description,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            tags=entry.tags,
            content_preview=content_preview,
        )
        
    except Exception as e:
        logger.error(f"[MemoryScan] Failed to scan {file_path}: {e}")
        return ScannedMemory(
            name=os.path.basename(file_path).replace('.md', ''),
            file_path=file_path,
            memory_type=MemoryType.PROJECT,
            description='（读取失败）',
            error=str(e),
        )


def scan_memory_files(
    memory_dir: str,
    signal: Optional[Any] = None
) -> MemoryScanResult:
    """
    扫描记忆目录中的所有记忆文件
    
    Args:
        memory_dir: 记忆目录路径
        signal: 可选的取消信号（asyncio.Event 或 threading.Event）
    
    Returns:
        MemoryScanResult
    """
    memories = []
    errors = []
    by_type = {t.value: 0 for t in MemoryType}
    
    if not os.path.exists(memory_dir):
        return MemoryScanResult(
            memories=[],
            total_count=0,
            by_type=by_type,
            errors=[],
        )
    
    try:
        entries = os.listdir(memory_dir)
    except Exception as e:
        logger.error(f"[MemoryScan] Failed to list directory {memory_dir}: {e}")
        return MemoryScanResult(
            memories=[],
            total_count=0,
            by_type=by_type,
            errors=[(memory_dir, str(e))],
        )
    
    for filename in entries:
        # 检查取消信号
        if signal is not None:
            if hasattr(signal, 'is_set') and signal.is_set():
                break
        
        # 跳过非 .md 文件和索引文件
        if not filename.endswith('.md'):
            continue
        if filename == ENTRYPOINT_NAME:
            continue
        
        file_path = os.path.join(memory_dir, filename)
        
        # 跳过目录
        if os.path.isdir(file_path):
            continue
        
        # 扫描文件
        scanned = scan_memory_file(file_path)
        
        if scanned:
            memories.append(scanned)
            by_type[scanned.memory_type.value] = by_type.get(scanned.memory_type.value, 0) + 1
            
            if scanned.error:
                errors.append((file_path, scanned.error))
    
    # 按更新时间排序（最新的在前）
    memories.sort(
        key=lambda m: m.updated_at or m.created_at or '',
        reverse=True
    )
    
    return MemoryScanResult(
        memories=memories,
        total_count=len(memories),
        by_type=by_type,
        errors=errors,
    )


async def scan_memory_files_async(
    memory_dir: str,
    signal: Optional[Any] = None
) -> MemoryScanResult:
    """
    异步扫描记忆目录
    
    Args:
        memory_dir: 记忆目录路径
        signal: 可选的取消信号
    
    Returns:
        MemoryScanResult
    """
    loop = asyncio.get_event_loop()
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        result = await loop.run_in_executor(
            executor,
            scan_memory_files,
            memory_dir,
            signal
        )
    
    return result


# ============================================================================
# 格式化输出
# ============================================================================

def format_memory_manifest(scan_result: MemoryScanResult) -> str:
    """
    格式化记忆清单（用于注入提示词）
    
    格式：
    ```
    ## Existing Memories
    
    ### user
    - user_role: 用户是数据科学家
    
    ### feedback
    - feedback_testing: 不要 mock 数据库
    
    ...
    ```
    
    Args:
        scan_result: 扫描结果
    
    Returns:
        格式化的清单字符串
    """
    if not scan_result.memories:
        return ""
    
    lines = ["## Existing Memories", ""]
    
    # 按类型分组
    by_type: Dict[str, List[ScannedMemory]] = {}
    for memory in scan_result.memories:
        type_key = memory.memory_type.value
        if type_key not in by_type:
            by_type[type_key] = []
        by_type[type_key].append(memory)
    
    # 按类型顺序输出
    for type_name in ['user', 'feedback', 'project', 'reference']:
        if type_name not in by_type:
            continue
        
        memories = by_type[type_name]
        lines.append(f"### {type_name}")
        
        for memory in memories:
            if memory.error:
                lines.append(f"- {memory.name}: （解析错误）")
            else:
                lines.append(f"- {memory.name}: {memory.description}")
        
        lines.append("")
    
    return "\n".join(lines)


def format_memory_list(scan_result: MemoryScanResult) -> str:
    """
    格式化记忆列表（用于 UI 展示）
    
    Args:
        scan_result: 扫描结果
    
    Returns:
        格式化的列表字符串
    """
    if not scan_result.memories:
        return "（暂无记忆）"
    
    lines = []
    
    for memory in scan_result.memories:
        type_icon = {
            'user': '👤',
            'feedback': '💡',
            'project': '📁',
            'reference': '🔗',
        }.get(memory.memory_type.value, '📝')
        
        lines.append(f"{type_icon} **{memory.name}** ({memory.memory_type.value})")
        lines.append(f"   {memory.description}")
        
        if memory.tags:
            lines.append(f"   标签: {', '.join(memory.tags)}")
        
        lines.append("")
    
    return "\n".join(lines)


# ============================================================================
# 相关记忆搜索
# ============================================================================

def find_relevant_memories(
    query: str,
    memories: List[ScannedMemory],
    max_results: int = 5
) -> List[ScannedMemory]:
    """
    查找与查询相关的记忆
    
    使用简单的关键词匹配：
    1. 匹配名称
    2. 匹配描述
    3. 匹配标签
    4. 匹配内容预览
    
    Args:
        query: 搜索查询
        memories: 记忆列表
        max_results: 最大结果数
    
    Returns:
        相关记忆列表
    """
    if not query or not memories:
        return []
    
    query_lower = query.lower()
    query_words = set(re.findall(r'\w+', query_lower))
    
    scored_memories = []
    
    for memory in memories:
        score = 0
        
        # 名称匹配（权重最高）
        if query_lower in memory.name.lower():
            score += 10
        
        # 描述匹配
        if query_lower in memory.description.lower():
            score += 5
        
        # 标签匹配
        for tag in memory.tags:
            if query_lower in tag.lower():
                score += 3
        
        # 内容预览匹配
        if query_lower in memory.content_preview.lower():
            score += 2
        
        # 多词匹配加分
        name_words = set(re.findall(r'\w+', memory.name.lower()))
        desc_words = set(re.findall(r'\w+', memory.description.lower()))
        
        name_overlap = len(query_words & name_words)
        desc_overlap = len(query_words & desc_words)
        
        score += name_overlap * 2
        score += desc_overlap
        
        if score > 0:
            scored_memories.append((score, memory))
    
    # 按分数排序
    scored_memories.sort(key=lambda x: x[0], reverse=True)
    
    return [m for _, m in scored_memories[:max_results]]


def search_memory_content(
    query: str,
    memory_dir: str,
    max_results: int = 10
) -> List[Dict[str, Any]]:
    """
    搜索记忆内容
    
    Args:
        query: 搜索查询
        memory_dir: 记忆目录
        max_results: 最大结果数
    
    Returns:
        匹配结果列表
    """
    if not query or not os.path.exists(memory_dir):
        return []
    
    results = []
    query_lower = query.lower()
    
    for filename in os.listdir(memory_dir):
        if not filename.endswith('.md') or filename == ENTRYPOINT_NAME:
            continue
        
        file_path = os.path.join(memory_dir, filename)
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if query_lower in content.lower():
                # 找到匹配，提取上下文
                content_lower = content.lower()
                idx = content_lower.find(query_lower)
                
                # 提取匹配周围的上下文
                start = max(0, idx - 100)
                end = min(len(content), idx + len(query) + 100)
                context = content[start:end]
                
                # 解析元数据
                entry = MemoryEntry.from_frontmatter(content, file_path)
                
                results.append({
                    'name': entry.name if entry else filename.replace('.md', ''),
                    'file_path': file_path,
                    'memory_type': entry.memory_type.value if entry else 'unknown',
                    'description': entry.description if entry else '',
                    'context': context,
                    'match_position': idx,
                })
                
                if len(results) >= max_results:
                    break
                    
        except Exception as e:
            logger.warning(f"[MemorySearch] Failed to search {file_path}: {e}")
    
    return results
