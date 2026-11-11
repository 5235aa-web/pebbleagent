#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Experience ledger: kind-aware selection, cache signature, rendering.

Covers the behaviour added on top of tests/test_experience_ledger.py:
  - selection fills the window round-robin across kinds, so constraints are
    never crowded out by a flood of preferences
  - the prompt cache signature tracks rendered content only, so confidence
    boosts stop invalidating it while real edits still do
  - rendering drops the evidence line and carries correction context

Run: python tests/test_experience_selection.py   (exit 0 = pass)
"""
import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_select_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP
os.environ["PEBBLE_MEMORY_DB"] = os.path.join(TMP, "experience.db")

from agent_core.memory import ExperienceLedger, signals  # noqa: E402
from agent_core.memory.ledger import DEFAULT_LIMIT  # noqa: E402

passed = 0
failed = 0

# 灌入窗口容量两倍的偏好条目，用来验证约束不会被挤出去。
FLOOD = 20


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


def main() -> int:
    ledger = ExperienceLedger(os.path.join(TMP, "select.db"))
    ledger.set_project(os.path.join(TMP, "project"))

    keeper = ledger.create(
        kind="constraint",
        statement="永远不要运行 npm run tauri build",
        confidence=0.9,
    )
    check("constraint created", bool(keeper))
    # 每条正文带随机后缀，避免 find_similar 把它们判成同一件事。
    for _ in range(FLOOD):
        created = ledger.create(
            kind="preference",
            statement=f"独立偏好 {uuid.uuid4().hex[:10]}",
            confidence=0.6,
        )
        if not created:
            check("preference created", False, "create returned None")
            break

    active = ledger.list(status="active", limit=100)
    check(
        "preference flood stored",
        len(active) >= DEFAULT_LIMIT + 1,
        f"got {len(active)}",
    )

    # ---------- 选取 ----------

    entries = ledger._select_entries(ledger._project_id, DEFAULT_LIMIT)
    kinds = [e["kind"] for e in entries]
    check("selection respects limit", len(entries) == DEFAULT_LIMIT, f"got {len(entries)}")
    check("constraint survives preference flood", "constraint" in kinds, kinds[:5])
    check("constraint rendered first", kinds[0] == "constraint", kinds[:3])
    check(
        "selection is deterministic",
        [e["id"] for e in ledger._select_entries(ledger._project_id, DEFAULT_LIMIT)]
        == [e["id"] for e in entries],
    )

    text = ledger.build_block("code_agent")
    check("constraint visible in block", "tauri build" in text)

    # ---------- 渲染 ----------

    correction = ledger.create(
        kind="correction",
        statement="不要在 Vue 组件里用原生 title 属性",
        rationale="用户否定了先前的做法：我给按钮加了 title 属性",
        confidence=0.8,
    )
    check("correction created", bool(correction))
    text = ledger.build_block("code_agent")
    check("correction carries context", "当时: 我给按钮加了 title 属性" in text)
    check("evidence line dropped", "证据:" not in text)
    check("ledger block closed", text.rstrip().endswith("</experience-ledger>"))

    # ---------- 缓存签名 ----------

    # 前面已经用 code_agent 渲染过，换一个没用过的 agent_type，
    # 保证这里第一次调用确实是未命中。
    probe = "cache_probe_agent"
    first_text, first_cached = ledger.build_block_with_status(probe)
    check("first call is a miss", first_cached is False)
    again_text, again_cached = ledger.build_block_with_status(probe)
    check("repeat call hits cache", again_cached is True)
    check("repeat call keeps text", again_text == first_text)

    # 0.9 -> 0.95，仍在 high 档，渲染不变，缓存必须保持。
    ledger._reinforce(keeper["id"])
    _, boosted_cached = ledger.build_block_with_status(probe)
    check("confidence boost keeps cache", boosted_cached is True)

    # 0.95 -> 0.70，跨档，渲染变化，缓存必须失效。
    ledger.update(keeper["id"], confidence=0.7)
    tier_text, tier_cached = ledger.build_block_with_status(probe)
    check("tier change invalidates cache", tier_cached is False)
    check("tier change visible in text", "· low]" in tier_text)

    # constraint 的 rationale 不参与渲染，改动它不应打掉缓存。
    ledger.update(keeper["id"], rationale="与渲染无关的说明")
    _, rationale_cached = ledger.build_block_with_status(probe)
    check("constraint rationale change keeps cache", rationale_cached is True)

    # correction 的 rationale 参与渲染，改动它必须失效。
    ledger.update(correction["id"], rationale="用户否定了先前的做法：我给按钮写了 title")
    ctx_text, ctx_cached = ledger.build_block_with_status(probe)
    check("correction rationale change invalidates cache", ctx_cached is False)
    check("updated context rendered", "我给按钮写了 title" in ctx_text)

    # ---------- 入口收紧 ----------

    check(
        "weak pattern without context is ignored",
        signals.detect_user_signals("this actually returns None") == [],
    )
    check(
        "weak pattern with negation is recorded",
        bool(signals.detect_user_signals("only use ruff, not flake8")),
    )
    check(
        "weak pattern inside a correction is recorded",
        bool(signals.detect_user_signals("No, actually it should be X")),
    )
    check(
        "strong pattern alone is recorded",
        bool(signals.detect_user_signals("\u5fc5\u987b\u7528 Tooltip \u7ec4\u4ef6")),
    )

    shutil.rmtree(TMP, ignore_errors=True)
    print()
    if failed:
        print(f"FAILED: {failed} checks")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
