"""经验生命周期：去重取代、确认加权、过期淘汰。"""

from __future__ import annotations

import difflib
import time
from typing import Any, Dict, Optional

from .schema import LedgerDatabase, build_fts_query

SIMILARITY_THRESHOLD = 0.72
STALE_DAYS = 180


def _normalize(text: str) -> str:
    return "".join((text or "").lower().split())


def find_similar(
    db: LedgerDatabase,
    statement: str,
    project_id: Optional[str],
    threshold: float = SIMILARITY_THRESHOLD,
    scope: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """在同一作用域内查找表述高度接近的既有条目。

    global 只与 global 比较，project 只与同一 project_id 比较。
    """
    target = _normalize(statement)
    if len(target) < 6:
        return None
    fts_query = build_fts_query(statement)
    if not fts_query:
        return None
    if scope not in ("global", "project"):
        scope = "project" if project_id else "global"
    if scope == "project" and not project_id:
        return None
    conn = db.connection()
    try:
        if scope == "project":
            rows = conn.execute(
                """
                SELECT e.*
                FROM experiences_fts
                JOIN experiences e ON e.rowid = experiences_fts.rowid
                WHERE experiences_fts MATCH ?
                  AND e.status = 'active'
                  AND e.scope = 'project'
                  AND e.project_id = ?
                LIMIT 10
                """,
                (fts_query, project_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT e.*
                FROM experiences_fts
                JOIN experiences e ON e.rowid = experiences_fts.rowid
                WHERE experiences_fts MATCH ?
                  AND e.status = 'active'
                  AND e.scope = 'global'
                  AND e.project_id IS NULL
                LIMIT 10
                """,
                (fts_query,),
            ).fetchall()
    except Exception:
        return None

    best = None
    best_ratio = 0.0
    for row in rows:
        ratio = difflib.SequenceMatcher(None, target, _normalize(row["statement"])).ratio()
        if ratio > best_ratio:
            best, best_ratio = dict(row), ratio
    return best if best_ratio >= threshold else None


def supersede(db: LedgerDatabase, old_id: str, new_id: str) -> None:
    conn = db.connection()
    now = time.time()
    conn.execute(
        "UPDATE experiences SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
        (new_id, now, old_id),
    )
    conn.commit()


def retire(db: LedgerDatabase, exp_id: str) -> None:
    conn = db.connection()
    conn.execute(
        "UPDATE experiences SET status = 'retired', updated_at = ? WHERE id = ?",
        (time.time(), exp_id),
    )
    conn.commit()


def boost_recent(db: LedgerDatabase, hours: int = 24, delta: float = 0.05) -> int:
    """用户确认后提升近期条目的置信度。"""
    cutoff = time.time() - hours * 3600
    conn = db.connection()
    cursor = conn.execute(
        """
        UPDATE experiences
        SET confidence = MIN(1.0, confidence + ?), updated_at = ?
        WHERE status = 'active' AND updated_at >= ?
        """,
        (delta, time.time(), cutoff),
    )
    conn.commit()
    return cursor.rowcount or 0


def expire_stale(db: LedgerDatabase, days: int = STALE_DAYS) -> int:
    """长期未命中且置信度偏低的条目转为 retired。"""
    cutoff = time.time() - days * 86400
    conn = db.connection()
    cursor = conn.execute(
        """
        UPDATE experiences
        SET status = 'retired', updated_at = ?
        WHERE status = 'active' AND hits = 0 AND confidence < 0.7 AND created_at < ?
        """,
        (time.time(), cutoff),
    )
    conn.commit()
    return cursor.rowcount or 0
