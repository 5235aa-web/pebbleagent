"""经验账本：跨会话经验的写入、检索与生命周期管理。"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from . import lifecycle, recall as recall_mod, signals
from .prompt import render_agent_note, render_entries
from .schema import LedgerDatabase, project_id_for, tokenize_for_index

logger = logging.getLogger(__name__)

VALID_KINDS = ("correction", "constraint", "preference", "pitfall", "recipe", "reference")
VALID_SCOPES = ("global", "project")
AGENT_NOTE_SCOPES = ("user", "project", "local")


class ExperienceLedger:
    """单一经验账本，按 scope 区分全局与项目。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db = LedgerDatabase(db_path)
        self._project_path: Optional[str] = None
        self._project_id: Optional[str] = None

    # ---------- 作用域 ----------

    def set_project(self, project_path: Optional[str]) -> None:
        self._project_path = project_path
        self._project_id = project_id_for(project_path)

    @property
    def project_id(self) -> Optional[str]:
        return self._project_id

    def _resolve(self, scope: Optional[str]) -> tuple:
        scope = scope if scope in VALID_SCOPES else ("project" if self._project_id else "global")
        if scope == "project" and not self._project_id:
            scope = "global"
        return scope, (self._project_id if scope == "project" else None)

    # ---------- 写 ----------

    def create(
        self,
        kind: str,
        statement: str,
        rationale: str = "",
        applies_to: Optional[Dict[str, List[str]]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        confidence: float = 0.6,
        scope: Optional[str] = None,
        agent_type: Optional[str] = None,
        source_message_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        statement = (statement or "").strip()
        if not statement or kind not in VALID_KINDS:
            return None
        if source_message_id and self._exists_for_message(source_message_id, statement):
            return None

        scope, pid = self._resolve(scope)
        applies_to = applies_to or {}
        evidence = evidence or []
        keywords = self._keyword_blob(statement, applies_to)

        similar = lifecycle.find_similar(self.db, statement, pid)
        if similar and float(similar.get("confidence") or 0) > confidence:
            self._reinforce(similar["id"])
            return self.get(similar["id"])

        exp_id = uuid.uuid4().hex
        now = time.time()
        conn = self.db.connection()
        cursor = conn.execute(
            """
            INSERT INTO experiences (
                id, scope, project_id, agent_type, kind, statement, rationale,
                applies_to, evidence, keywords, confidence, status,
                hits, created_at, updated_at, source_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
            """,
            (
                exp_id, scope, pid, agent_type, kind, statement, rationale,
                json.dumps(applies_to, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                keywords, max(0.0, min(1.0, confidence)), now, now,
                source_message_id,
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
        conn.commit()

        if similar:
            lifecycle.supersede(self.db, similar["id"], exp_id)
        return self.get(exp_id)

    def _keyword_blob(self, statement: str, applies_to: Dict[str, Any]) -> str:
        parts: List[str] = []
        for key in ("tools", "paths", "keywords"):
            parts.extend(str(v) for v in (applies_to.get(key) or []))
        parts.extend(signals.extract_keywords(statement))
        seen = set()
        unique = []
        for item in parts:
            low = item.lower()
            if low and low not in seen:
                seen.add(low)
                unique.append(item)
        return " ".join(unique[:24])

    def _exists_for_message(self, message_id: str, statement: str) -> bool:
        conn = self.db.connection()
        row = conn.execute(
            "SELECT 1 FROM experiences WHERE source_message_id = ? AND statement = ? LIMIT 1",
            (message_id, statement),
        ).fetchone()
        return row is not None

    def _reinforce(self, exp_id: str) -> None:
        conn = self.db.connection()
        conn.execute(
            "UPDATE experiences SET confidence = MIN(1.0, confidence + 0.05), updated_at = ? WHERE id = ?",
            (time.time(), exp_id),
        )
        conn.commit()

    def update(self, exp_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        allowed = {"statement", "rationale", "kind", "confidence", "status", "applies_to", "agent_type"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get(exp_id)
        if "kind" in updates and updates["kind"] not in VALID_KINDS:
            updates.pop("kind")
        if "applies_to" in updates:
            updates["applies_to"] = json.dumps(updates["applies_to"], ensure_ascii=False)

        current = self.get(exp_id)
        if not current:
            return None

        statement = updates.get("statement", current["statement"])
        rationale = updates.get("rationale", current["rationale"])
        applies_to = updates.get("applies_to") or json.dumps(current["applies_to"], ensure_ascii=False)
        try:
            applies_obj = json.loads(applies_to) if isinstance(applies_to, str) else applies_to
        except (TypeError, ValueError):
            applies_obj = {}
        updates["keywords"] = self._keyword_blob(statement, applies_obj)
        updates["updated_at"] = time.time()

        assignments = ", ".join(f"{key} = ?" for key in updates)
        conn = self.db.connection()
        conn.execute(
            f"UPDATE experiences SET {assignments} WHERE id = ?",
            (*updates.values(), exp_id),
        )
        row = conn.execute("SELECT rowid FROM experiences WHERE id = ?", (exp_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM experiences_fts WHERE rowid = ?", (row["rowid"],))
            conn.execute(
                "INSERT INTO experiences_fts(rowid, statement_tok, rationale_tok, keywords_tok) VALUES (?, ?, ?, ?)",
                (
                    row["rowid"],
                    tokenize_for_index(statement),
                    tokenize_for_index(rationale),
                    tokenize_for_index(updates["keywords"]),
                ),
            )
        conn.commit()
        return self.get(exp_id)

    def delete(self, exp_id: str) -> bool:
        conn = self.db.connection()
        row = conn.execute("SELECT rowid FROM experiences WHERE id = ?", (exp_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM experiences_fts WHERE rowid = ?", (row["rowid"],))
        conn.execute("DELETE FROM experiences WHERE id = ?", (exp_id,))
        conn.commit()
        return True

    def retire(self, exp_id: str) -> None:
        lifecycle.retire(self.db, exp_id)

    # ---------- 读 ----------

    def get(self, exp_id: str) -> Optional[Dict[str, Any]]:
        conn = self.db.connection()
        row = conn.execute("SELECT * FROM experiences WHERE id = ?", (exp_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list(
        self,
        kind: Optional[str] = None,
        status: Optional[str] = "active",
        agent_type: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        clauses = ["(scope = 'global' OR project_id = ?)"]
        params: List[Any] = [self._project_id or ""]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if agent_type:
            clauses.append("agent_type = ?")
            params.append(agent_type)
        params.append(max(1, min(limit, 2000)))
        conn = self.db.connection()
        rows = conn.execute(
            f"SELECT * FROM experiences WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self) -> Dict[str, Any]:
        entries = self.list(status=None, limit=2000)
        by_kind: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for entry in entries:
            by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
            by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        return {
            "total": len(entries),
            "active": by_status.get("active", 0),
            "superseded": by_status.get("superseded", 0),
            "retired": by_status.get("retired", 0),
            "by_kind": by_kind,
        }

    def recall(
        self,
        query: str,
        tools: Optional[Sequence[str]] = None,
        paths: Optional[Sequence[str]] = None,
        limit: int = 5,
        min_score: float = recall_mod.DEFAULT_MIN_SCORE,
    ) -> List[Dict[str, Any]]:
        return recall_mod.recall(
            self.db, query, self._project_id, tools=tools, paths=paths,
            limit=limit, min_score=min_score,
        )

    def build_prompt(
        self,
        query: str,
        tools: Optional[Sequence[str]] = None,
        paths: Optional[Sequence[str]] = None,
        agent_type: Optional[str] = None,
        limit: int = 5,
    ) -> str:
        entries = self.recall(query, tools=tools, paths=paths, limit=limit)
        if entries:
            recall_mod.mark_hits(self.db, [e["id"] for e in entries])
        text = render_entries(entries)
        note = self.get_agent_note(agent_type) if agent_type else ""
        return text + render_agent_note(agent_type or "", note)

    # ---------- 对话观察 ----------

    def observe_turn(
        self,
        conversation_uuid: str,
        user_message: str,
        assistant_message: str = "",
        tool_events: Optional[List[Dict[str, Any]]] = None,
        message_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """从一轮对话中提取经验并落库。"""
        created: List[Dict[str, Any]] = []
        try:
            if signals.is_confirmation(user_message):
                lifecycle.boost_recent(self.db)

            detected = signals.detect_user_signals(user_message, assistant_message)
            detected.extend(signals.detect_tool_signals(tool_events or []))

            for signal in detected:
                evidence = [{
                    "conversation_uuid": conversation_uuid,
                    "message_id": message_id,
                    "quote": signal.evidence_quote,
                    "timestamp": time.time(),
                }]
                entry = self.create(
                    kind=signal.kind,
                    statement=signal.statement,
                    rationale=signal.rationale,
                    evidence=evidence,
                    confidence=signal.confidence,
                    applies_to={"keywords": signal.keywords},
                    source_message_id=message_id,
                )
                if entry:
                    created.append(entry)
        except Exception as exc:
            logger.warning("[Ledger] observe_turn failed: %s", exc)
        return created

    # ---------- Agent 笔记 ----------

    def _note_key(self, scope: str) -> tuple:
        scope = scope if scope in AGENT_NOTE_SCOPES else "project"
        pid = "" if scope == "user" else (self._project_id or "")
        return scope, pid

    def get_agent_note(self, agent_type: str, scope: str = "project") -> str:
        if not agent_type:
            return ""
        scope, pid = self._note_key(scope)
        conn = self.db.connection()
        row = conn.execute(
            "SELECT content FROM agent_notes WHERE agent_type = ? AND scope = ? AND project_id = ?",
            (agent_type, scope, pid),
        ).fetchone()
        return row["content"] if row else ""

    def set_agent_note(self, agent_type: str, scope: str, content: str) -> bool:
        if not agent_type:
            return False
        scope, pid = self._note_key(scope)
        conn = self.db.connection()
        conn.execute(
            """
            INSERT INTO agent_notes (agent_type, scope, project_id, content, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_type, scope, project_id)
            DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
            """,
            (agent_type, scope, pid, content or "", time.time()),
        )
        conn.commit()
        return True

    def clear_agent_note(self, agent_type: str, scope: str = "project") -> bool:
        if not agent_type:
            return False
        scope, pid = self._note_key(scope)
        conn = self.db.connection()
        conn.execute(
            "DELETE FROM agent_notes WHERE agent_type = ? AND scope = ? AND project_id = ?",
            (agent_type, scope, pid),
        )
        conn.commit()
        return True

    # ---------- 内部 ----------

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        data = dict(row)
        data.pop("rowid", None)
        for key, fallback in (("applies_to", {}), ("evidence", [])):
            try:
                data[key] = json.loads(data.get(key) or "")
            except (TypeError, ValueError):
                data[key] = fallback
        return data


_ledger: Optional[ExperienceLedger] = None


def get_ledger(db_path: Optional[str] = None) -> ExperienceLedger:
    global _ledger
    if _ledger is None or (db_path and _ledger.db.db_path != db_path):
        _ledger = ExperienceLedger(db_path)
        try:
            from .migrate import migrate_legacy
            migrate_legacy(_ledger.db)
        except Exception as exc:
            logger.warning("[Ledger] Legacy migration skipped: %s", exc)
    return _ledger
