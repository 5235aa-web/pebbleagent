"""经验检索：FTS 召回与结构化加权排序。"""

from __future__ import annotations

import fnmatch
import json
import math
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .schema import LedgerDatabase, build_fts_query

KIND_BONUS = {
    "constraint": 0.35,
    "correction": 0.30,
    "pitfall": 0.25,
    "preference": 0.15,
    "recipe": 0.10,
    "reference": 0.0,
}

DEFAULT_MIN_SCORE = 1.0
HALF_LIFE_DAYS = 90.0


def _relevance(rank: float) -> float:
    """把 bm25 的负分（越相关越负）映射到 0..1。"""
    return 1.0 / (1.0 + math.exp(rank / 3.0 + 1.0))


def _load_json(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback


def _tool_hit(applies_to: Dict[str, Any], tools: Sequence[str]) -> bool:
    declared = applies_to.get("tools") or []
    return any(t in declared for t in tools)


def _path_hit(applies_to: Dict[str, Any], paths: Sequence[str]) -> bool:
    patterns = applies_to.get("paths") or []
    for pattern in patterns:
        for path in paths:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.replace("\\", "/"), pattern):
                return True
    return False


def _collect_candidates(
    db: LedgerDatabase,
    fts_query: str,
    project_id: Optional[str],
    tools: Sequence[str],
) -> Dict[int, Dict[str, Any]]:
    conn = db.connection()
    candidates: Dict[int, Dict[str, Any]] = {}

    if fts_query:
        try:
            rows = conn.execute(
                """
                SELECT e.rowid AS rowid, e.*, bm25(experiences_fts) AS rank
                FROM experiences_fts
                JOIN experiences e ON e.rowid = experiences_fts.rowid
                WHERE experiences_fts MATCH ?
                  AND e.status = 'active'
                  AND (e.scope = 'global' OR e.project_id = ?)
                ORDER BY rank
                LIMIT 50
                """,
                (fts_query, project_id or ""),
            ).fetchall()
        except Exception:
            rows = []
        for row in rows:
            record = dict(row)
            record["_rank"] = float(record.pop("rank", 0.0) or 0.0)
            candidates[row["rowid"]] = record

    for tool in tools:
        rows = conn.execute(
            """
            SELECT rowid, * FROM experiences
            WHERE status = 'active'
              AND (scope = 'global' OR project_id = ?)
              AND keywords LIKE ?
            ORDER BY updated_at DESC
            LIMIT 20
            """,
            (project_id or "", f"%{tool}%"),
        ).fetchall()
        for row in rows:
            record = dict(row)
            record.setdefault("_rank", 0.0)
            candidates.setdefault(row["rowid"], record)

    return candidates


def recall(
    db: LedgerDatabase,
    query: str,
    project_id: Optional[str],
    tools: Optional[Sequence[str]] = None,
    paths: Optional[Sequence[str]] = None,
    limit: int = 5,
    min_score: float = DEFAULT_MIN_SCORE,
) -> List[Dict[str, Any]]:
    """返回按相关性排序的经验条目。"""
    tools = list(tools or [])
    paths = list(paths or [])
    fts_query = build_fts_query(query)
    candidates = _collect_candidates(db, fts_query, project_id, tools)
    if not candidates:
        return []

    now = time.time()
    scored: List[Dict[str, Any]] = []
    for record in candidates.values():
        applies_to = _load_json(record.get("applies_to", "{}"), {})
        rank = record.get("_rank", 0.0)
        score = _relevance(rank) * 2.0
        if _tool_hit(applies_to, tools):
            score += 1.2
        if _path_hit(applies_to, paths):
            score += 1.0
        score += KIND_BONUS.get(record.get("kind", ""), 0.0)
        score += float(record.get("confidence") or 0.0) * 0.4

        age_days = max(0.0, (now - float(record.get("updated_at") or now)) / 86400.0)
        score *= 0.5 ** (age_days / HALF_LIFE_DAYS)
        score *= 1.0 + 0.1 * math.log1p(int(record.get("hits") or 0))

        if score < min_score:
            continue
        record["score"] = round(score, 4)
        scored.append(record)

    scored.sort(key=lambda r: (r["score"], r.get("updated_at") or 0), reverse=True)
    return scored[:limit]


def mark_hits(db: LedgerDatabase, experience_ids: Iterable[str]) -> None:
    ids = [str(i) for i in experience_ids if i]
    if not ids:
        return
    conn = db.connection()
    now = time.time()
    conn.executemany(
        "UPDATE experiences SET hits = hits + 1, last_hit_at = ? WHERE id = ?",
        [(now, exp_id) for exp_id in ids],
    )
    conn.commit()
