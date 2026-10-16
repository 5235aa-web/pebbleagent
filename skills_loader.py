"""
Skills Loader - Progressive Disclosure 技能加载器

先只传技能名称和描述给 LLM（metadata），
当 LLM 判断需要时，再加载完整 SKILL.md 内容。
"""

from pathlib import Path
from typing import Optional, Dict, List
import re
import logging

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "skills"


def list_skills() -> List[Dict[str, str]]:
    """
    列出所有技能（只返回名称和描述，不加载完整内容）
    
    Returns:
        技能列表，每项包含 name 和 description
    """
    if not SKILLS_DIR.exists():
        logger.info("[Skills] Skills directory not found")
        return []
    
    skills = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir():
            skill_md = d / "SKILL.md"
            if skill_md.exists():
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    # 提取 frontmatter 中的 description
                    desc = ""
                    match = re.search(r'description:\s*(.+)', content)
                    if match:
                        desc = match.group(1).strip()
                    skills.append({
                        "name": d.name,
                        "description": desc or "无描述"
                    })
                except Exception as e:
                    logger.warning(f"[Skills] Failed to read {d.name}: {e}")
    
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
    skill_path = SKILLS_DIR / name / "SKILL.md"
    if skill_path.exists():
        try:
            content = skill_path.read_text(encoding="utf-8")
            
            # 拼接示例输出（如果存在 example.html）
            example_path = SKILLS_DIR / name / "example.html"
            if example_path.exists():
                html = example_path.read_text(encoding="utf-8")
                content += f"\n\n## 示例输出\n```html\n{html}\n```"
            
            logger.info(f"[Skills] Loaded skill: {name}")
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
    if not SKILLS_DIR.exists():
        return []
    return sorted([d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists()])
