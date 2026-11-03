"""
Skills Loader - Progressive Disclosure 技能加载器

先只传技能名称和描述给 LLM（metadata），
当 LLM 判断需要时，再加载完整 SKILL.md 内容。
"""

from pathlib import Path
from typing import Any, Optional, Dict, List
import os
import re
import logging

import yaml

logger = logging.getLogger(__name__)

BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills"

# User-installed skills sit beside the rest of the runtime data. A packaged app
# unpacks its built-in tree into a PyInstaller temp directory that is recreated
# on every launch, so that tree can never be extended; this one can.
USER_SKILLS_DIR = Path(os.environ.get("PEBBLE_HOME") or Path.home() / ".pebble") / "skills"


def skill_dirs() -> List[Path]:
    """Skill roots, highest priority first.

    A user directory shadows a built-in with the same name: copying a built-in
    into the user tree is how you override it without touching the installer.
    """
    return [USER_SKILLS_DIR, BUILTIN_SKILLS_DIR]


def _valid_name(name: str) -> bool:
    """Reject anything that is not a plain directory name.

    `name` reaches this module from the LLM through the `load_skill` tool, so a
    value like `../../secrets` must not resolve to a path outside the roots.
    """
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name


def _parse_frontmatter(content: str) -> Dict[str, Any]:
    """解析 SKILL.md 头部的 YAML frontmatter（--- 包裹）。

    只读取头部块，正文中的同名键不会被误命中；多行值（description: |）
    与含冒号的值交给 YAML 解析器处理。
    """
    content = content.lstrip("\ufeff")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as e:
        logger.warning(f"[Skills] Invalid frontmatter: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _extract_description(content: str) -> str:
    """提取 description；frontmatter 不是合法 YAML 时回退到单行匹配。"""
    desc = _parse_frontmatter(content).get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    match = re.search(r'^description:\s*(.+)$', content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def list_skills() -> List[Dict[str, str]]:
    """
    列出所有技能（只返回名称和描述，不加载完整内容）
    
    Returns:
        技能列表，每项包含 name 和 description
    """
    seen: set = set()
    skills = []
    for root in skill_dirs():
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            skill_md = d / "SKILL.md"
            if not skill_md.exists():
                continue
            seen.add(d.name)
            try:
                content = skill_md.read_text(encoding="utf-8")
                desc = _extract_description(content)
                skills.append({
                    "name": d.name,
                    "description": desc or "无描述"
                })
            except Exception as e:
                logger.warning(f"[Skills] Failed to read {d.name}: {e}")

    skills.sort(key=lambda s: s["name"])
    logger.info(f"[Skills] Found {len(skills)} skills")
    return skills


def load_skill(name: str) -> Optional[str]:
    """
    加载指定技能的完整 SKILL.md 内容
    
    Args:
        name: 技能名称（目录名）
    
    Returns:
        SKILL.md 完整内容（含 example.html 示例输出），如果不存在则返回 None
    """
    if not _valid_name(name):
        logger.warning(f"[Skills] Rejected skill name: {name!r}")
        return None

    for root in skill_dirs():
        skill_path = root / name / "SKILL.md"
        if not skill_path.exists():
            continue
        try:
            content = skill_path.read_text(encoding="utf-8")
            
            # 拼接示例输出（如果存在 example.html）
            example_path = root / name / "example.html"
            if example_path.exists():
                html = example_path.read_text(encoding="utf-8")
                content += f"\n\n## 示例输出\n```html\n{html}\n```"
            
            logger.info(f"[Skills] Loaded skill: {name} from {root}")
            return content
        except Exception as e:
            logger.error(f"[Skills] Failed to load {name}: {e}")
            return None
    logger.warning(f"[Skills] Skill not found: {name}")
    return None


def get_skill_names() -> List[str]:
    """
    仅返回技能名称列表（最轻量）
    
    Returns:
        技能名称列表
    """
    names = set()
    for root in skill_dirs():
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                names.add(d.name)
    return sorted(names)
