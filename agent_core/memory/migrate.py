"""旧版 Markdown 记忆的一次性导入。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from .schema import LedgerDatabase, project_id_for

logger = logging.getLogger(__name__)

MIGRATION_KEY = "legacy_memory_migrated"
TYPE_TO_KIND = {
    "user": "preference",
    "feedback": "correction",
    "project": "pitfall",
    "reference": "reference",
}
_PATH_SANITIZE = re.compile(r"[\\/:]+")


def _config_home() -> str:
    home = os.environ.get("PEBBLE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".pebble")
    return os.path.abspath(os.path.expanduser(home))


def _sanitize(path: str) -> str:
    return _PATH_SANITIZE.sub("_", path).strip("_")


def _read_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    try:
        import yaml
        data = yaml.safe_load("\n".join(lines[1:end])) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {str(k): v for k, v in data.items()}, "\n".join(lines[end + 1:]).strip()


def _load_projects(home: str) -> List[str]:
    path = os.path.join(home, "projects.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        projects = data.get("projects") if isinstance(data, dict) else None
        if isinstance(projects, list):
            return [p for p in projects if isinstance(p, str)]
    except (OSError, ValueError):
        pass
    return []


def _iter_memory_files(home: str) -> List[Tuple[str, str, str]]:
    """返回 (项目路径, 文件路径, 项目目录名)。"""
    projects = _load_projects(home)
    mapping = {_sanitize(p): p for p in projects}
    results: List[Tuple[str, str, str]] = []
    root = os.path.join(home, "projects")
    if not os.path.isdir(root):
        return results
    for name in sorted(os.listdir(root)):
        memory_dir = os.path.join(root, name, "memory")
        if not os.path.isdir(memory_dir):
            continue
        project_path = mapping.get(name, "")
        for filename in sorted(os.listdir(memory_dir)):
            if not filename.endswith(".md") or filename == "MEMORY.md":
                continue
            results.append((project_path, os.path.join(memory_dir, filename), name))
    return results


def migrate_legacy(db: LedgerDatabase) -> int:
    """把旧版 memory/*.md 与 agent-memory/*.md 导入账本。"""
    conn = db.connection()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (MIGRATION_KEY,)).fetchone()
    if row:
        return 0

    home = _config_home()
    imported = 0

    for project_path, file_path, dir_name in _iter_memory_files(home):
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        meta, body = _read_frontmatter(raw)
        kind = TYPE_TO_KIND.get(str(meta.get("type") or meta.get("memory_type") or "").lower(), "reference")
        description = str(meta.get("description") or meta.get("name") or os.path.basename(file_path))
        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
        statement = description.strip()[:300]
        if not statement:
            continue
        rationale = body[:1000]
        mtime = os.path.getmtime(file_path)
        scope = "project" if project_path else "global"
        pid = project_id_for(project_path) if project_path else None
        _insert_legacy(
            conn, kind, statement, rationale, scope, pid,
            [str(t) for t in tags], file_path, mtime,
        )
        imported += 1

    agent_root = os.path.join(home, "agent-memory")
    if os.path.isdir(agent_root):
        for agent_type in sorted(os.listdir(agent_root)):
            note_path = os.path.join(agent_root, agent_type, "MEMORY.md")
            if not os.path.isfile(note_path):
                continue
            try:
                with open(note_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read().strip()
            except OSError:
                continue
            if not content:
                continue
            conn.execute(
                """
                INSERT INTO agent_notes (agent_type, scope, project_id, content, updated_at)
                VALUES (?, 'user', '', ?, ?)
                ON CONFLICT(agent_type, scope, project_id)
                DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
                """,
                (agent_type, content, time.time()),
            )
            imported += 1

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (MIGRATION_KEY, str(int(time.time()))),
    )
    conn.commit()
    if imported:
        logger.info("[Ledger] Migrated %d legacy memory records", imported)
    return imported


def _insert_legacy(
    conn,
    kind: str,
    statement: str,
    rationale: str,
    scope: str,
    project_id: Optional[str],
    tags: List[str],
    file_path: str,
    mtime: float,
) -> None:
    import uuid

    from .schema import tokenize_for_index

    exp_id = uuid.uuid4().hex
    keywords = " ".join(tags)
    applies_to = json.dumps({"keywords": tags}, ensure_ascii=False)
    evidence = json.dumps(
        [{"conversation_uuid": "", "quote": "legacy:" + os.path.basename(file_path), "timestamp": mtime}],
        ensure_ascii=False,
    )
    cursor = conn.execute(
        """
        INSERT INTO experiences (
            id, scope, project_id, agent_type, kind, statement, rationale,
            applies_to, evidence, keywords, confidence, status,
            hits, created_at, updated_at
        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 0.7, 'active', 0, ?, ?)
        """,
        (
            exp_id, scope, project_id, kind, statement, rationale,
            applies_to, evidence, keywords, mtime, mtime,
        ),
    )
    conn.execute(
        "INSERT INTO experiences_fts(rowid, statement_tok, rationale_tok, keywords_tok) VALUES (?, ?, ?, ?)",
        (
            cursor.lastrowid,
            tokenize_for_index(statement),
            tokenize_for_index(rationale),
            tokenize_for_index(keywords),
        ),
    )
