"""
Safe JSON - 安全的 JSON 解析工具

处理 LLM 输出中常见的非法 JSON 转义字符问题。
例如：\w, \s, \d, \b 等正则表达式转义在 JSON 中是非法的。
"""

import json
import re
from typing import Any

# 非法转义字符正则：匹配 \ 后面跟非 JSON 合法转义字符
# JSON 合法转义：\" \\ \/ \b \f \n \r \t \uXXXX
_ILLEGAL_ESCAPE_PATTERN = re.compile(r'\\([^"\\/bfnrtu])')


def safe_json_loads(s: str, default: Any = None) -> Any:
    """
    安全解析 JSON 字符串，自动修复非法转义字符
    
    Args:
        s: JSON 字符串
        default: 解析失败时的默认返回值，默认为 None
    
    Returns:
        解析后的 Python 对象，或默认值
    
    Examples:
        >>> safe_json_loads('{"pattern": "\\w+"}')  # \w 是非法的 JSON 转义
        {'pattern': '\\w+'}
        
        >>> safe_json_loads('{"valid": "value"}')
        {'valid': 'value'}
    """
    if not isinstance(s, str):
        return s
    
    # 第一次尝试：直接解析
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    
    # 第二次尝试：修复非法转义字符
    try:
        fixed = _ILLEGAL_ESCAPE_PATTERN.sub(r'\\\\\1', s)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 第三次尝试：更激进的修复（处理双重转义等情况）
    try:
        # 尝试替换所有反斜杠序列
        fixed = _fix_all_escapes(s)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    
    # 返回默认值
    if default is not None:
        return default
    return {}


def _fix_all_escapes(s: str) -> str:
    """
    更激进地修复所有可能的非法转义
    
    处理情况：
    - 单反斜杠 + 非法字符 -> 双反斜杠 + 字符
    - 已经是双反斜杠 -> 保持不变
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            next_char = s[i + 1]
            # JSON 合法转义字符
            if next_char in '"\\/bfnrtu':
                # 检查是否是 \uXXXX 格式
                if next_char == 'u' and i + 5 < len(s):
                    # 保持 \uXXXX 不变
                    result.append(s[i:i+6])
                    i += 6
                    continue
                else:
                    result.append(s[i:i+2])
                    i += 2
                    continue
            else:
                # 非法转义，添加额外反斜杠
                result.append('\\\\')
                result.append(next_char)
                i += 2
                continue
        else:
            result.append(s[i])
            i += 1
    
    return ''.join(result)


def safe_json_dumps(obj: Any, ensure_ascii: bool = False, **kwargs) -> str:
    """
    安全序列化 Python 对象为 JSON 字符串
    
    这是 json.dumps 的包装，确保输出是有效的 JSON。
    
    Args:
        obj: Python 对象
        ensure_ascii: 是否转义非 ASCII 字符
        **kwargs: 传递给 json.dumps 的其他参数
    
    Returns:
        JSON 字符串
    """
    return json.dumps(obj, ensure_ascii=ensure_ascii, **kwargs)


def is_valid_json(s: str) -> bool:
    """
    检查字符串是否是有效的 JSON
    
    Args:
        s: 要检查的字符串
    
    Returns:
        是否是有效的 JSON
    """
    if not isinstance(s, str):
        return False
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        # 尝试修复后解析
        try:
            fixed = _ILLEGAL_ESCAPE_PATTERN.sub(r'\\\\\1', s)
            json.loads(fixed)
            return True
        except json.JSONDecodeError:
            return False
