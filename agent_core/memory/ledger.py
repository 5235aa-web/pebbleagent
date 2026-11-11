"""经验账本：跨会话经验的写入、检索与生命周期管理。"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import lifecycle, recall as recall_mod, signals
from .prompt import confidence_label, render_agent_note, render_entries
from .schema import LedgerDatabase, project_id_for, tokenize_for_index

logger = logging.getLogger(__name__)

VALID_KINDS = ("correction", "constraint", "preference", "pitfall", "recipe", "reference")
VALID_SCOPES = ("global", "project")
AGENT_NOTE_SCOPES = ("user", "project")

# 注入顺序即优先级：约束排在最前，偏好和参考排在最后。
KIND_ORDER = ("constraint", "correction", "pitfall", "preference", "recipe", "reference")

# 注入窗口的默认容量。渲染层预算 2000 字符，去掉证据行后每条约 60 到 100
# 字符，14 条留有余量。
DEFAULT_LIMIT = 14

# 经验块缓存的条目上限。键是 (project_id, agent_type)，正常远小于此值；
# 上限只用于兜住项目频繁切换的极端情况。
PROMPT_CACHE_LIMIT = 64


class ExperienceLedger:
    """单一经验账本，按 scope 区分全局与项目。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db = LedgerDatabase(db_path)
        self._project_path: Optional[str] = None
        self._project_id: Optional[str] = None
        # 渲染结果按 (project_id, agent_type) 缓存：经验块位于 system prompt 里，
        # 一旦内容随 turn 变化，模型侧的前缀缓存就整体失效。缓存命中时直接复用
        # 上一次的文本，使同一项目内的 system prompt 跨 turn 保持稳定。
        self._prompt_cache: Dict[
            Tuple[Optional[str], str], Tuple[Tuple, str, List[str]]
        ] = {}
        self._prompt_lock = threading.Lock()

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
        statement = (statement or "").strip()[:160]
        rationale = (rationale or "").strip()[:300]
        if not statement or kind not in VALID_KINDS:
            return None
        if source_message_id and self._exists_for_message(source_message_id, statement):
            return None

        scope, pid = self._resolve(scope)
        applies_to = applies_to or {}
        evidence = evidence or []
        for item in evidence:
            if isinstance(item, dict) and isinstance(item.get("quote"), str) and len(item["quote"]) > 300:
                item["quote"] = item["quote"][:300]
        keywords = self._keyword_blob(statement, applies_to)

        similar = lifecycle.find_similar(self.db, statement, pid, scope=scope)
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
        query: Optional[str] = None,
        tools: Optional[Sequence[str]] = None,
        paths: Optional[Sequence[str]] = None,
        agent_type: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        use_cache: bool = True,
    ) -> str:
        """兼容包装：query / tools / paths 已不参与选取，保留只为旧调用方。"""
        return self.build_block(agent_type=agent_type, limit=limit, use_cache=use_cache)

    def build_prompt_with_status(
        self,
        query: Optional[str] = None,
        tools: Optional[Sequence[str]] = None,
        paths: Optional[Sequence[str]] = None,
        agent_type: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        use_cache: bool = True,
    ) -> Tuple[str, bool]:
        """兼容包装，见 build_block_with_status。"""
        return self.build_block_with_status(
            agent_type=agent_type, limit=limit, use_cache=use_cache
        )

    def build_block(
        self, agent_type: Optional[str] = None, limit: int = DEFAULT_LIMIT, use_cache: bool = True
    ) -> str:
        text, _ = self.build_block_with_status(
            agent_type=agent_type, limit=limit, use_cache=use_cache
        )
        return text

    def build_block_with_status(
        self,
        agent_type: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        use_cache: bool = True,
    ) -> Tuple[str, bool]:
        """渲染经验块，返回 (文本, 是否命中缓存)。

        选取与 query 无关。经验条目是跨会话的长期约束，不按当前这句话做相关性
        过滤：按 query 召回会让块随每轮对话漂移，而块位于 system prompt 内，
        模型侧的前缀缓存会因此整段失效（进程重启后的第一次渲染尤其明显，召回
        组合变了，整段历史跟着重新计费）。现在同一项目、同一 agent 在同一批
        条目下永远得到相同的文本，重启和换话题都不再影响它。

        缓存键是 (project_id, agent_type, limit)。签名直接由本次选取结果构造，
        只覆盖参与渲染的字段，所以 confidence 的微调不会打掉缓存，而正文、
        kind、置信度档、rationale 或 agent note 的任何真实变化都会。

        use_cache=False 供预览类只读调用使用：既不读缓存也不写缓存，避免预览
        把结果带进正式请求。

        project_id 在入口处取一次快照：set_project 是共享状态，并发会话可能
        在渲染途中切换它，键、指纹与选取必须基于同一个项目才自洽。
        """
        project_id = self._project_id
        key = (project_id, agent_type or "", limit)
        entries = self._select_entries(project_id, limit)
        notes = self._collect_notes(agent_type, project_id)
        ids = [entry["id"] for entry in entries]
        signature = self._signature(entries, notes)

        if use_cache:
            with self._prompt_lock:
                cached = self._prompt_cache.get(key)
            if cached is not None and cached[0] == signature:
                # 复用文本，但命中计数照常累加：它决定经验条目的淘汰时机，
                # 而且写 hits / last_hit_at 不在签名里，不会反过来打掉缓存。
                self._safe_mark_hits(ids)
                return cached[1], True

        self._safe_mark_hits(ids)
        text = render_entries(entries) + render_agent_note(
            agent_type or "", "\n\n".join(notes)
        )
        if use_cache:
            with self._prompt_lock:
                if len(self._prompt_cache) >= PROMPT_CACHE_LIMIT:
                    self._prompt_cache.clear()
                self._prompt_cache[key] = (signature, text, ids)
        return text, False

    def _select_entries(
        self, project_id: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        """确定性地选取要注入的条目，不经过相关性打分。

        按 kind 轮转填充：每类各取一条，循环到凑满 limit 或所有队列取空。
        纯时间排序下第 11 条以后永远进不了窗口，而 constraint 只要不是最新
        就会被后来写入的偏好挤出去，hits 停在 0，最终被 expire_stale 退役 ——
        用户看到的是「明明教过，过一阵又忘了」。轮转保证每一类都有机会，
        数量少的类自动把名额让给其他类，不需要硬配额。

        每类内部按 created_at DESC, id ASC 排序：新条目优先进入预算，同一批
        条目每次得到完全相同的顺序。刻意避开 updated_at 与 confidence —— 它们
        会被 boost_recent / _reinforce 改写，一旦参与排序，同一批条目的顺序
        就会在某个时刻翻转，经验块随之变化。

        返回顺序按 KIND_ORDER 分组：约束全部在前，组内保持时间降序。
        """
        conn = self.db.connection()
        window = max(1, int(limit or DEFAULT_LIMIT))
        queues: Dict[str, List[Dict[str, Any]]] = {}
        for kind in KIND_ORDER:
            rows = conn.execute(
                """
                SELECT * FROM experiences
                WHERE status = 'active' AND (scope = 'global' OR project_id = ?)
                  AND kind = ?
                ORDER BY created_at DESC, id ASC
                LIMIT ?
                """,
                (project_id or "", kind, window),
            ).fetchall()
            queues[kind] = [self._row_to_dict(row) for row in rows]

        picked: Dict[str, List[Dict[str, Any]]] = {kind: [] for kind in KIND_ORDER}
        count = 0
        while count < window:
            progressed = False
            for kind in KIND_ORDER:
                queue = queues[kind]
                if not queue:
                    continue
                picked[kind].append(queue.pop(0))
                count += 1
                progressed = True
                if count >= window:
                    break
            if not progressed:
                break
        return [entry for kind in KIND_ORDER for entry in picked[kind]]

    def _collect_notes(
        self, agent_type: Optional[str], project_id: Optional[str]
    ) -> List[str]:
        """按固定顺序收集要追加在经验块之后的 agent 便签。"""
        notes: List[str] = []
        for note_scope in ("user", "project"):
            # 显式传空串表示"本项目没有项目级备注"，避免 None 让 _note_key
            # 回退去读可能已被并发切换的 self._project_id。
            content = self.get_agent_note(
                agent_type or "", note_scope, project_id=project_id if project_id else ""
            )
            if content:
                notes.append(content)
        return notes

    def _safe_mark_hits(self, ids: Optional[List[str]]) -> None:
        """累加命中计数，失败不影响经验块本身。

        mark_hits 只服务于淘汰统计。写锁争用让它抛异常时，丢掉整块经验、
        顺带改变 system prompt 是更坏的后果，所以这里只降级记日志。
        """
        if not ids:
            return
        try:
            recall_mod.mark_hits(self.db, ids)
        except Exception as exc:
            logger.debug("[Ledger] mark_hits skipped: %s", exc)

    @staticmethod
    def _signature(entries: List[Dict[str, Any]], notes: List[str]) -> Tuple:
        """经验块可见内容的签名，用于判断缓存是否仍然有效。

        只覆盖会出现在渲染结果里的字段：id 决定组内顺序，kind 与置信度档决定
        首行标签，statement 与 rationale 是正文，created_at 决定组内排序，
        applies_to 决定「适用」行。hits / last_hit_at / updated_at / 精确置信度
        一律排除 —— 它们会被 mark_hits、boost_recent、_reinforce 每轮改写，一旦
        进入签名缓存就永远不会命中，而渲染结果其实一模一样。

        rationale 只对 correction 计入：其他 kind 的 rationale 不参与渲染，把它
        算进来会让一次无意义的说明修改白白打掉缓存。

        置信度必须用档位而不是四舍五入后的数值：0.749 与 0.75 分属 low 和
        medium 两档，按数值比较会漏判。
        """
        rows = []
        for entry in entries:
            kind = entry.get("kind")
            rows.append((
                entry.get("id"),
                kind,
                confidence_label(float(entry.get("confidence") or 0.0)),
                entry.get("created_at"),
                entry.get("statement") or "",
                (entry.get("rationale") or "") if kind == "correction" else "",
                entry.get("applies_to") or "",
            ))
        return tuple(rows) + (tuple(notes),)

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
            detected.extend(signals.detect_tool_signals(tool_events or [], user_message))

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
                    scope="project" if self._project_id else "global",
                    source_message_id=message_id,
                )
                if entry:
                    created.append(entry)
        except Exception as exc:
            logger.warning("[Ledger] observe_turn failed: %s", exc)
        return created

    # ---------- Agent 笔记 ----------

    def _note_key(self, scope: str, project_id: Optional[str] = None) -> tuple:
        scope = scope if scope in AGENT_NOTE_SCOPES else "project"
        if scope == "user":
            return scope, ""
        pid = self._project_id if project_id is None else project_id
        return scope, pid or ""

    def get_agent_note(
        self, agent_type: str, scope: str = "project", project_id: Optional[str] = None
    ) -> str:
        if not agent_type:
            return ""
        scope, pid = self._note_key(scope, project_id)
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
