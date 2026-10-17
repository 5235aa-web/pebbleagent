"""经验账本存储层：SQLite 结构、连接与检索分词。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from typing import Optional

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS experiences (
    id            TEXT PRIMARY KEY,
    scope         TEXT NOT NULL CHECK (scope IN ('global', 'project')),
    project_id    TEXT,
    agent_type    TEXT,
    kind          TEXT NOT NULL CHECK (kind IN
                      ('correction', 'constraint', 'preference', 'pitfall', 'recipe', 'reference')),
    statement     TEXT NOT NULL,
    rationale     TEXT NOT NULL DEFAULT '',
    applies_to    TEXT NOT NULL DEFAULT '{}',
    evidence      TEXT NOT NULL DEFAULT '[]',
    keywords      TEXT NOT NULL DEFAULT '',
    confidence    REAL NOT NULL DEFAULT 0.6,
    status        TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'superseded', 'retired')),
    superseded_by TEXT,
    hits          INTEGER NOT NULL DEFAULT 0,
    last_hit_at   REAL,
    source_message_id TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exp_scope ON experiences(scope, project_id, status);
CREATE INDEX IF NOT EXISTS idx_exp_source ON experiences(source_message_id);
CREATE INDEX IF NOT EXISTS idx_exp_kind ON experiences(kind, status);
CREATE INDEX IF NOT EXISTS idx_exp_updated ON experiences(updated_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
    statement_tok,
    rationale_tok,
    keywords_tok,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS agent_notes (
    agent_type TEXT NOT NULL,
    scope      TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (agent_type, scope, project_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# CJK 统一表意文字、假名、谚文
_CJK_RANGES = (
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
)


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def tokenize_for_index(text: str) -> str:
    """CJK 逐字切分，ASCII 保持整词，供 FTS5 unicode61 使用。"""
    if not text:
        return ""
    out = []
    for ch in text:
        if is_cjk(ch):
            out.append(" ")
            out.append(ch)
            out.append(" ")
        elif ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def build_fts_query(text: str) -> str:
    """把查询串转成 FTS5 表达式：连续汉字按二字窗口展开，英文成独立 term。"""
    tokens = tokenize_for_index(text).split()
    if not tokens:
        return ""
    parts = []
    run = []

    def flush():
        if len(run) == 1:
            parts.append(run[0])
        elif len(run) == 2:
            parts.append('"' + " ".join(run) + '"')
        else:
            for i in range(len(run) - 1):
                parts.append('"' + run[i] + " " + run[i + 1] + '"')
        run.clear()

    for tok in tokens:
        if len(tok) == 1 and is_cjk(tok):
            run.append(tok)
            continue
        flush()
        parts.append(tok.replace('"', '""'))
    flush()
    return " OR ".join(parts)


def project_id_for(project_path: Optional[str]) -> Optional[str]:
    if not project_path:
        return None
    normalized = os.path.normcase(os.path.realpath(os.path.expanduser(project_path)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def default_db_path() -> str:
    override = os.environ.get("PEBBLE_MEMORY_DB")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    home = (os.environ.get("PEBBLE_HOME")
            or os.environ.get("PEBBLE_CONFIG_DIR")
            or os.path.join(os.path.expanduser("~"), ".pebble"))
    return os.path.join(os.path.abspath(os.path.expanduser(home)), "experience.db")


class LedgerDatabase:
    """线程内复用的 SQLite 连接，WAL 模式。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or default_db_path()
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        self._local.conn = conn
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            conn.executescript(DDL)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            self._initialized = True

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def reset(self) -> None:
        """清空全部数据（测试与用户主动重置）。"""
        conn = self.connection()
        conn.executescript(
            "DELETE FROM experiences_fts; DELETE FROM experiences; DELETE FROM agent_notes;"
        )
        conn.commit()
