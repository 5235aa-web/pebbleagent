"""
Truncation Manager - 工具输出截断管理

当工具输出超过限制时，自动保存完整内容到磁盘，
只向 LLM 返回预览 + 文件路径提示。

行为：
1. 超出 MAX_LINES(5000) 或 MAX_BYTES(50KB) 时触发
2. 完整内容写入 .pebble/truncation/ 目录
3. 返回截断预览 + 路径提示
4. LLM 可通过 read_file 按需读取完整内容
5. 7 天后自动清理过期文件
"""

import hashlib
import logging
import os
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 每日清理标记（避免每次写入都扫描）
_last_cleanup = 0

# 截断目录（默认值，可通过 set_truncation_dir 修改）
TRUNCATION_DIR = os.path.join(os.getcwd(), ".pebble", "truncation")


def set_truncation_dir(base_dir: str) -> None:
    """设置截断目录（绑定到 agent 工作区目录）"""
    global TRUNCATION_DIR
    TRUNCATION_DIR = os.path.join(os.path.abspath(base_dir), ".pebble", "truncation")

# 限制
MAX_LINES = 5000
MAX_BYTES = 50 * 1024  # 50KB
RETENTION_SECONDS = 7 * 24 * 3600  # 7 天

# 提示模板
_TRUNCATION_HINT = "\n\n---(Content truncated at {reason}. Full content saved to: {path})---"


def _ensure_dir() -> str:
    """确保截断目录存在，并执行每日清理"""
    global _last_cleanup
    os.makedirs(TRUNCATION_DIR, exist_ok=True)
    now = time.time()
    if now - _last_cleanup > 86400:
        cleanup_old_files()
        _last_cleanup = now
    return TRUNCATION_DIR


def _file_path(content: str) -> str:
    """生成截断文件的路径"""
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    ts = int(time.time() * 1000)
    return os.path.join(_ensure_dir(), f"tool_{ts}_{h}.txt")


def truncate_output(text: str, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES) -> Tuple[str, bool]:
    """
    截断工具输出。

    Args:
        text: 原始输出
        max_lines: 最大行数
        max_bytes: 最大字节数

    Returns:
        (最终内容, 是否被截断)
        如果未超限，返回原文 + False
        如果超限，返回预览 + 路径提示 + True
    """
    if not text:
        return text, False

    reason = None
    
    # 检查行数
    lines = text.split("\n")
    if len(lines) > max_lines:
        reason = f"超过 {max_lines} 行"
    # 检查字节数
    elif len(text.encode("utf-8")) > max_bytes:
        reason = f"超过 {max_bytes // 1024}KB"
    
    if not reason:
        return text, False
    
    # 保存完整内容
    path = _file_path(text)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"[Truncation] Saved {len(text)} chars / {len(lines)} lines to {path}")
    except Exception as e:
        logger.warning(f"[Truncation] Failed to save truncation file: {e}")
        # 即使保存失败，也要截断返回
        preview = "\n".join(lines[:max_lines]) if len(lines) > max_lines else text[:max_bytes]
        return preview + _TRUNCATION_HINT.format(reason=reason, path="(save failed)"), True
    
    # 生成预览
    preview = "\n".join(lines[:max_lines]) if len(lines) > max_lines else text[:max_bytes]
    preview += _TRUNCATION_HINT.format(reason=reason, path=path)
    
    return preview, True


def read_truncated_file(file_path: str) -> Optional[str]:
    """读取截断保存的完整内容"""
    try:
        if os.path.exists(file_path) and file_path.startswith(TRUNCATION_DIR):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        logger.warning(f"[Truncation] Failed to read truncation file {file_path}: {e}")
    return None


def cleanup_old_files() -> int:
    """清理过期的截断文件，返回删除数量"""
    if not os.path.exists(TRUNCATION_DIR):
        return 0
    
    now = time.time()
    count = 0
    for fname in os.listdir(TRUNCATION_DIR):
        fpath = os.path.join(TRUNCATION_DIR, fname)
        if not fname.startswith("tool_") or not os.path.isfile(fpath):
            continue
        try:
            if now - os.path.getmtime(fpath) > RETENTION_SECONDS:
                os.remove(fpath)
                count += 1
        except Exception:
            pass
    
    if count > 0:
        logger.info(f"[Truncation] Cleaned up {count} expired truncation files")
    return count
