"""对话语言判定：经验条目的措辞跟随对话，而不是固定中文。"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def is_chinese(text: str) -> bool:
    """CJK 字符占字母总数两成以上即视为中文。

    阈值与 context_compressor 的摘要语言判定一致：中文句子里夹几个英文术语
    仍然算中文，英文句子里夹一个中文词仍然是英文。
    """
    if not text:
        return False
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    total = cjk + latin
    if total == 0:
        return False
    return cjk / total >= 0.2
