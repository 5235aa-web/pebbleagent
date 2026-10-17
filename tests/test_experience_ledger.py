#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Experience ledger: write, recall, supersede, signals, notes, migration.

Run: python tests/test_experience_ledger.py   (exit 0 = pass)
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_ledger_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP
os.environ["PEBBLE_MEMORY_DB"] = os.path.join(TMP, "experience.db")

from agent_core.memory import ExperienceLedger, migrate_legacy, get_ledger  # noqa: E402
from agent_core.memory import signals  # noqa: E402

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


def main() -> int:
    ledger = ExperienceLedger(os.path.join(TMP, "experience.db"))
    ledger.set_project(os.path.join(TMP, "project"))

    entry = ledger.create(
        kind="constraint",
        statement="所有前端提示必须使用 Tooltip 组件，禁止原生 title 属性",
        rationale="项目 UI 规范",
        applies_to={"tools": ["write_file"], "paths": ["*.vue"]},
        confidence=0.85,
    )
    check("create returns entry", bool(entry and entry["id"]))
    check("create persists kind", entry and entry["kind"] == "constraint")
    check("create parses applies_to", entry and entry["applies_to"]["paths"] == ["*.vue"])

    fetched = ledger.get(entry["id"])
    check("get by id", fetched is not None and fetched["statement"] == entry["statement"])

    listed = ledger.list()
    check("list active", len(listed) == 1)

    hits = ledger.recall("前端提示组件用哪个", tools=["write_file"], paths=["src/App.vue"])
    check("recall finds entry", len(hits) == 1, f"got {len(hits)}")
    check("recall scores tool hit", hits and hits[0]["score"] > 1.0)

    unrelated = ledger.recall("数据库连接池配置")
    check("recall filters unrelated", len(unrelated) == 0, f"got {len(unrelated)}")

    duplicate = ledger.create(
        kind="constraint",
        statement="所有前端提示必须使用 Tooltip 组件，禁止原生 title 属性",
        confidence=0.5,
    )
    check("duplicate merges into existing", duplicate and duplicate["id"] == entry["id"])
    check("duplicate keeps single row", len(ledger.list()) == 1)

    stronger = ledger.create(
        kind="constraint",
        statement="所有前端提示必须使用 Tooltip 组件，禁止原生 title 属性",
        confidence=0.95,
    )
    check("stronger entry supersedes", stronger and stronger["id"] != entry["id"])
    old = ledger.get(entry["id"])
    check("old entry marked superseded", old and old["status"] == "superseded")
    check("supersede points to new", old and old["superseded_by"] == stronger["id"])

    created = ledger.observe_turn(
        conversation_uuid="conv-1",
        user_message="不对，不要在 Vue 组件里用原生 title 属性，必须统一用 Tooltip 组件",
        assistant_message="我用了 title 属性",
        message_id="msg-1",
    )
    check("observe_turn creates correction", len(created) >= 1, f"got {len(created)}")
    again = ledger.observe_turn(
        conversation_uuid="conv-1",
        user_message="不对，不要在 Vue 组件里用原生 title 属性，必须统一用 Tooltip 组件",
        message_id="msg-1",
    )
    check("observe_turn is idempotent", len(again) == 0, f"got {len(again)}")

    tool_created = ledger.observe_turn(
        conversation_uuid="conv-2",
        user_message="继续",
        tool_events=[
            {"name": "execute_shell", "status": "failed", "args": {"command": "cd /d E:\\x"}, "output": "error"},
            {"name": "execute_shell", "status": "completed", "args": {"command": "cd E:\\x"}, "output": "ok"},
        ],
        message_id="msg-2",
    )
    check("tool retry creates pitfall", any(e["kind"] == "pitfall" for e in tool_created), f"got {tool_created}")

    note_ok = ledger.set_agent_note("code_agent", "project", "始终先读图谱")
    check("agent note set", note_ok)
    check("agent note get", ledger.get_agent_note("code_agent", "project") == "始终先读图谱")
    ledger.set_agent_note("code_agent", "user", "全局偏好")
    check("agent note user scope", ledger.get_agent_note("code_agent", "user") == "全局偏好")
    check("agent note project isolated", ledger.get_agent_note("code_agent", "project") == "始终先读图谱")
    merged_prompt = ledger.build_prompt("前端提示组件", agent_type="code_agent")
    check("agent note user injected", "全局偏好" in merged_prompt)
    check("agent note project injected", "始终先读图谱" in merged_prompt)
    check("agent note clear", ledger.clear_agent_note("code_agent", "project"))
    check("agent note cleared", ledger.get_agent_note("code_agent", "project") == "")
    after_clear_prompt = ledger.build_prompt("前端提示组件", agent_type="code_agent")
    check("cleared project note not injected", "始终先读图谱" not in after_clear_prompt)
    check("user note still injected", "全局偏好" in after_clear_prompt)

    prompt = ledger.build_prompt("前端提示组件", tools=["write_file"], agent_type="code_agent")
    check("build_prompt renders ledger", "<experience-ledger>" in prompt)
    check("build_prompt includes statement", "Tooltip" in prompt)

    scoped = ExperienceLedger(os.path.join(TMP, "scope.db"))
    scoped.set_project(os.path.join(TMP, "project_a"))
    shared = "作用域合并测试语句唯一内容"
    global_entry = scoped.create(kind="constraint", statement=shared, scope="global", confidence=0.9)
    project_entry = scoped.create(kind="constraint", statement=shared, scope="project", confidence=0.8)
    check("global and project stay separate", global_entry["id"] != project_entry["id"])
    check("global has no project_id", scoped.get(global_entry["id"])["project_id"] is None)
    check("project carries project_id", scoped.get(project_entry["id"])["project_id"] is not None)

    scoped.set_project(os.path.join(TMP, "project_b"))
    other_project = scoped.create(kind="constraint", statement=shared, scope="project", confidence=0.8)
    check("different projects stay separate",
          other_project["id"] not in (global_entry["id"], project_entry["id"]))

    short_a = scoped.create(kind="preference", statement="abc", confidence=0.5)
    short_b = scoped.create(kind="preference", statement="abc", confidence=0.5)
    check("short statements do not merge", short_a["id"] != short_b["id"])

    check("delete removes entry", ledger.delete(stronger["id"]))
    check("deleted entry gone", ledger.get(stronger["id"]) is None)

    stats = ledger.stats()
    check("stats totals", stats["total"] >= 2 and stats["active"] >= 1)

    check("confirmation detector", signals.is_confirmation("对，就是这样"))
    check("correction detector", len(signals.detect_user_signals("不要用硬编码")) >= 1)
    check("constraint detector", any(
        s.kind == "constraint" for s in signals.detect_user_signals("必须使用 UTC 时间"))
    )
    check("plain text no signal", len(signals.detect_user_signals("今天天气不错")) == 0)

    legacy_root = os.path.join(TMP, "legacy")
    project_dir = os.path.join(legacy_root, "projects", "E_demo", "memory")
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(legacy_root, "projects.json"), "w", encoding="utf-8") as fh:
        json.dump({"projects": ["E:\\demo"]}, fh)
    with open(os.path.join(project_dir, "feedback_style.md"), "w", encoding="utf-8") as fh:
        fh.write("---\ntype: feedback\ndescription: 不要用 emoji\ntags:\n  - style\n---\n用户要求输出不使用 emoji。")
    old_home = os.environ["PEBBLE_CONFIG_DIR"]
    old_pebble_home = os.environ.get("PEBBLE_HOME")
    os.environ["PEBBLE_CONFIG_DIR"] = legacy_root
    os.environ["PEBBLE_HOME"] = legacy_root
    legacy_db_path = os.path.join(TMP, "legacy.db")
    legacy_ledger = ExperienceLedger(legacy_db_path)
    imported = migrate_legacy(legacy_ledger.db)
    os.environ["PEBBLE_CONFIG_DIR"] = old_home
    if old_pebble_home is None:
        os.environ.pop("PEBBLE_HOME", None)
    else:
        os.environ["PEBBLE_HOME"] = old_pebble_home
    check("migration imports records", imported == 1, f"got {imported}")
    legacy_ledger.set_project("E:\\demo")
    migrated = legacy_ledger.list()
    check("migration maps kind", migrated and migrated[0]["kind"] == "correction", f"got {migrated}")
    check("migration keeps statement", migrated and "emoji" in migrated[0]["statement"])
    check("migration is one-shot", migrate_legacy(legacy_ledger.db) == 0)

    from types import SimpleNamespace
    import asyncio
    from agent_core.tool_executor_impl import AgentToolExecutorImpl
    tool_project = os.path.join(TMP, "project")
    executor = AgentToolExecutorImpl(project_path=tool_project)
    ctx = SimpleNamespace(project_path=tool_project, agent_name="code_agent")
    tool_result = asyncio.run(executor._tool_experience_write(
        "Ledger writes must be searchable by keyword", "preference", "", "project", ["ledger"], ctx))
    check("tool write records entry", "Recorded experience" in tool_result, tool_result)
    tool_hits = get_ledger().recall("searchable")
    check("tool write is recallable", any("searchable" in h.get("statement", "") for h in tool_hits), len(tool_hits))
    tool_id = tool_hits[0]["id"] if tool_hits else ""

    updated = asyncio.run(executor._tool_experience_update(
        tool_id, "Updated ledger statement text", "constraint", "", 0.9, ctx))
    check("tool update returns comparison", "statement:" in updated and "->" in updated, updated)
    after = get_ledger().get(tool_id)
    check("tool update changes statement", after and after["statement"] == "Updated ledger statement text")
    check("tool update changes kind", after and after["kind"] == "constraint")
    check("tool update keeps scope", after and after["scope"] == "project")
    check("tool update keeps project_id", after and after["project_id"] is not None)

    other_ctx = SimpleNamespace(project_path=os.path.join(TMP, "project_other"), agent_name="code_agent")
    cross = asyncio.run(executor._tool_experience_update(
        tool_id, "should not apply", "", "", None, other_ctx))
    check("tool update rejects other project", cross.startswith("Error: entry belongs to another project"), cross)

    long_result = asyncio.run(executor._tool_experience_update(
        tool_id, "x" * 121, "", "", None, ctx))
    check("tool update rejects long statement", "120" in long_result, long_result)

    shutil.rmtree(TMP, ignore_errors=True)
    print()
    if failed:
        print(f"FAILED: {failed} checks")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
