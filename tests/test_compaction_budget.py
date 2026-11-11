#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compaction budgeting: window, calibration, splitting, no rule fallback.

The 11-09 failures came from a single summary request that carried 1.63M tokens
because the estimate said 665k and the budget never fired, after which a
rule-built shell replaced the history. Every invariant that prevents a repeat is
pinned here.

Run: python tests/test_compaction_budget.py   (exit 0 = pass)
"""
import os
import sys

os.environ.setdefault("PEBBLE_SKIP_PROMPT_HISTORY", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SUMMARY = """## Task
- finish the layout of the settings window

## Status
- Done: read the config module
- In progress: wiring the accent picker
- Blocked: nothing

## Decisions
- keep the accent value in theme.json rather than localStorage

## Todo
- resume the picker wiring

## Clues
- files: frontend/src/data/wire.ts; commands: python tests; error text: none"""


class RejectingClient:
    """Rejects calls for size (with a real token count) before answering."""

    def __init__(self, reject_forever=False):
        self.calls = []
        self.reject_forever = reject_forever
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        from agent_core import context_compressor as cc
        prompt = kwargs["messages"][0]["content"]
        self.calls.append(len(prompt))
        if self.reject_forever or len(self.calls) == 1:
            actual = max(1, int(cc.estimate_tokens(prompt) * 3))
            raise RuntimeError(
                "Error code: 400 - {'error': {'code': 'context_length_exceeded', "
                "'message': 'Input too long: %d input tokens, limit is 400000'}}" % actual
            )
        return type("R", (), {
            "choices": [type("C", (), {"message": type("M", (), {"content": SUMMARY})()})()],
            "usage": type("U", (), {"prompt_tokens": 100})(),
        })()


def main() -> int:
    from agent_core import context_compressor as cc

    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  [PASS] %s" % name)
        else:
            failed += 1
            print("  [FAIL] %s | %s" % (name, detail))

    # --- 1. the two rejections from 11-09 must be classified as size errors ---
    print("=== prompt-too-long detection ===")
    gateway_400 = (
        "Error code: 400 - {'error': {'param': None, 'type': 'invalid_request_error', "
        "'code': 'context_length_exceeded', 'message': 'Upstream request failed: "
        "[context_length_exceeded] Input too long: 1636417 input tokens, "
        "limit is 1048576 for this model'}}"
    )
    openai_400 = (
        "Error code: 400 - {'error': {'message': \"This model's maximum context length is "
        "1048576 tokens. However, you requested 1192676 tokens (942676 in the messages, "
        "250000 in the completion).\"}}"
    )
    detected, _gap, limit = cc.is_prompt_too_long_error(gateway_400)
    check("gateway form detected", detected, str(detected))
    check("gateway limit parsed", limit == 1048576, str(limit))
    check("gateway actual parsed", cc.extract_actual_prompt_tokens(gateway_400) == 1636417,
          str(cc.extract_actual_prompt_tokens(gateway_400)))
    detected2, _gap2, limit2 = cc.is_prompt_too_long_error(openai_400)
    check("openai form detected", detected2, str(detected2))
    check("openai limit parsed", limit2 == 1048576, str(limit2))
    check("openai actual parsed", cc.extract_actual_prompt_tokens(openai_400) == 1192676,
          str(cc.extract_actual_prompt_tokens(openai_400)))
    check("an unrelated error is not a size error",
          not cc.is_prompt_too_long_error("GraphRecursionError: too many steps")[0])

    # --- 2. calibration: measured/estimated ratio corrects the scale ---
    print("=== token calibration ===")
    cc._calibration_samples.clear()
    check("no factor before a sample", cc.get_token_calibration_factor() is None)
    plain = cc.estimate_tokens("x" * 40000)
    check("uncalibrated estimate is pessimistic",
          cc.estimate_tokens_calibrated("x" * 40000) >= int(plain * cc.UNCALIBRATED_PESSIMISM),
          "%d vs %d" % (cc.estimate_tokens_calibrated("x" * 40000), plain))
    cc.record_token_calibration(665287, 1636417)
    factor = cc.get_token_calibration_factor()
    check("factor follows the sample", factor and abs(factor - 2.4594) < 0.01, str(factor))
    check("calibrated estimate is scaled up",
          cc.estimate_tokens_calibrated("x" * 40000) >= int(plain * 2.4))
    cc.record_token_calibration(1000, 1000)
    cc.record_token_calibration(1000, 1000)
    check("median resists one outlier", cc.get_token_calibration_factor() == 1.0,
          str(cc.get_token_calibration_factor()))
    cc.record_token_calibration(0, 10)
    cc.record_token_calibration(10, 0)
    check("empty samples are ignored", cc.get_token_calibration_factor() == 1.0)
    cc._calibration_samples.clear()

    # --- 3. the part budget comes from the window, never a constant ---
    print("=== budget from the model window ===")
    small = cc._summary_input_budget(128000)
    large = cc._summary_input_budget(1048576)
    tiny = cc._summary_input_budget(32000)
    check("small window shrinks the budget", small < cc.SUMMARY_PART_INPUT_TOKENS, str(small))
    check("large window is capped", large == cc.SUMMARY_PART_INPUT_TOKENS, str(large))
    check("tiny window still allows a request", tiny >= 8000, str(tiny))
    check("budget stays under the window minus output",
          small + cc._summary_output_budget(128000) + cc.SUMMARY_PROMPT_OVERHEAD_TOKENS
          <= int(128000 * 0.95),
          str(small))
    check("output ceiling is 90k", cc.SUMMARY_MAX_OUTPUT_TOKENS == 90000,
          str(cc.SUMMARY_MAX_OUTPUT_TOKENS))
    check("output shrinks on a small window",
          cc._summary_output_budget(32000) < cc._summary_output_budget(1000000),
          str(cc._summary_output_budget(32000)))
    check("part output is generous",
          cc._part_output_budget(1000000) >= 20000,
          str(cc._part_output_budget(1000000)))
    check("part retry doubles the ceiling",
          cc._part_output_budget(1000000, retry=True) > cc._part_output_budget(1000000),
          str(cc._part_output_budget(1000000, retry=True)))

    # --- 4. a huge transcript is split, and no row may take the whole budget ---
    print("=== splitting ===")
    rows = ["Tool: [grep] " + ("y" * 4000) for _ in range(120)]
    total = sum(cc.estimate_tokens_calibrated(r) for r in rows)
    parts = cc._split_rendered_parts(rows, 120000)
    check("a 120-row transcript becomes several parts", len(parts) > 1, str(len(parts)))
    check("every row survives exactly once",
          sum(p.count("[grep]") for p in parts) == len(rows),
          str(sum(p.count("[grep]") for p in parts)))
    budget = cc._summary_input_budget(1048576)
    parts2 = cc._split_rendered_parts(rows, budget)
    biggest = max(cc.estimate_tokens_calibrated(p) for p in parts2)
    check("no part exceeds the budget by much", biggest <= budget * 1.15,
          "%d vs %d" % (biggest, budget))
    check("splitting actually reduces the request size", biggest < total,
          "%d vs %d" % (biggest, total))

    # --- 5. per-message caps: one 2 MB output can never reach the request ---
    print("=== per-message caps ===")
    from agent_core.context_compressor import Message
    huge = Message(uuid="u1", role="tool", content="E:\\a\\b.md: hit\n" * 150000,
                   tool_call_id="c1")
    rendered_unknown = cc._summarize_message_content(huge, cc.RENDER_TOOL_CHARS, None)
    rendered_read = cc._summarize_message_content(huge, cc.RENDER_TOOL_CHARS, "grep")
    rendered_cmd = cc._summarize_message_content(huge, cc.RENDER_TOOL_CHARS, "execute_shell")
    rendered_failed = cc._summarize_message_content(huge, cc.RENDER_TOOL_CHARS, "execute_shell")
    check("unknown tool name is capped", len(rendered_unknown) <= cc.RENDER_TOOL_CHARS + 80,
          str(len(rendered_unknown)))
    check("read tool name is capped tighter", len(rendered_read) <= cc.RENDER_TOOL_READ_CHARS + 80,
          str(len(rendered_read)))
    check("command tool name keeps more", len(rendered_cmd) <= cc.RENDER_TOOL_COMMAND_CHARS + 80,
          str(len(rendered_cmd)))
    check("failure output is capped too",
          "FAILED" not in rendered_failed or len(rendered_failed) <= cc.RENDER_TOOL_COMMAND_CHARS + 80,
          str(len(rendered_failed)))

    # --- 6. tool names come back from the transcript's own block shape ---
    print("=== tool name index ===")
    block_style = Message(uuid="a1", role="assistant", content="", tool_calls=[
        {"type": "tool", "order": 1, "tool_info": {"id": "call-1", "name": "grep"}},
    ])
    openai_style = Message(uuid="a2", role="assistant", content="", tool_calls=[
        {"id": "call-2", "type": "function", "function": {"name": "read_file"}},
    ])
    index = cc._tool_name_index([block_style, openai_style])
    check("block shape indexed", index.get("call-1") == "grep", str(index))
    check("openai shape still indexed", index.get("call-2") == "read_file", str(index))

    # --- 7. the rule-based summary is gone for good ---
    print("=== no rule fallback ===")
    check("simple_structured_summary removed", not hasattr(cc, "simple_structured_summary"))
    check("rule copy table removed", not hasattr(cc, "_RULE_COPY"))
    check("no hard-coded 700k budget",
          not hasattr(cc, "DEFAULT_SUMMARY_INPUT_BUDGET"))

    # --- 8. a size rejection is answered by shrinking, not by giving up ---
    print("=== adaptive retry ===")
    from agent_core.context_compressor import Message

    big = [
        Message(uuid="m%d" % i, role="user" if i % 2 == 0 else "assistant",
                content="row %d " % i + "z" * 3000)
        for i in range(40)
    ]
    cc._calibration_samples.clear()
    retry_client = RejectingClient()
    out = cc.generate_structured_summary(
        messages=big, llm_client=retry_client, llm_model="m", context_limit=1000000
    )
    check("summary survives one rejection", bool(out), str(out)[:60])
    check("the request was retried once", len(retry_client.calls) == 2,
          str(len(retry_client.calls)))
    check("the retry carried less", retry_client.calls[1] < retry_client.calls[0],
          str(retry_client.calls))
    check("the measured count calibrated the scale",
          cc.get_token_calibration_factor() is not None,
          str(cc.get_token_calibration_factor()))

    cc._calibration_samples.clear()
    dead = RejectingClient(reject_forever=True)
    out2 = cc.generate_structured_summary(
        messages=big, llm_client=dead, llm_model="m", context_limit=1000000
    )
    check("a permanent rejection fails visibly", out2 is None, str(out2)[:60])

    # --- 9. a rejected part is split and digested part by part ---
    print("=== part split on rejection ===")
    rows_big = ["Tool: [grep] " + ("q" * 3000) for _ in range(80)]
    cc._calibration_samples.clear()
    split_client = RejectingClient()
    digest = cc._summarize_part_digest(
        "\n\n".join(rows_big), 1, 1, split_client, "m", "English", 50000, 1000000
    )
    check("part digest still returns text", bool(digest), str(digest)[:40])
    check("the rejected part was split into several calls", len(split_client.calls) > 2,
          str(len(split_client.calls)))

    # --- 9. an empty answer is a diagnostic, not a verdict: retry it ---
    print("=== empty answer retry ===")

    class EmptyThenOk:
        def __init__(self):
            self.ceilings = []
            self.chat = type("Chat", (), {"completions": self})()

        def create(self, **kw):
            self.ceilings.append(kw["max_tokens"])
            body = "" if len(self.ceilings) == 1 else SUMMARY
            msg = type("M", (), {"content": body, "reasoning_content": "x" * 500})()
            return type("R", (), {
                "choices": [type("C", (), {"message": msg})()],
                "usage": type("U", (), {"prompt_tokens": 100})(),
            })()

    cc._calibration_samples.clear()
    empty_client = EmptyThenOk()
    got = cc._summarize_part_digest(
        "User: hello\n\nAssistant: hi", 1, 1, empty_client, "m", "English",
        120000, 1000000,
    )
    check("empty answer is retried", len(empty_client.ceilings) == 2, str(empty_client.ceilings))
    check("the retry raises the ceiling",
          empty_client.ceilings[1] > empty_client.ceilings[0], str(empty_client.ceilings))
    check("the retry produces the digest", bool(got), str(got)[:40])

    # --- 10. every summary request carries the preamble ---
    print("=== preamble coverage ===")
    MARK = "reformatting job, not engineering work"

    class RecordingClient:
        def __init__(self):
            self.prompts = []
            self.chat = type("Chat", (), {"completions": self})()

        def create(self, **kw):
            self.prompts.append(kw["messages"][0]["content"])
            body = type("M", (), {"content": SUMMARY})()
            return type("R", (), {
                "choices": [type("C", (), {"message": body})()],
                "usage": type("U", (), {"prompt_tokens": 100})(),
            })()

    rec = RecordingClient()
    cc._call_summary_model(rec, "m", "User: hi", 1000, 0.3)
    check("the choke point adds it", bool(rec.prompts) and MARK in rec.prompts[0])

    rec = RecordingClient()
    cc._summarize_part_digest("User: hi\n\nAssistant: ok", 1, 1, rec, "m", "English",
                              120000, 1000000)
    check("part digest carries it", bool(rec.prompts) and all(MARK in p for p in rec.prompts))

    rec = RecordingClient()
    # A budget small enough that the digests cannot be returned as-is, so the
    # merge actually calls the model instead of joining the text.
    cc._merge_part_digests(["a" * 100, "b" * 100], rec, "m", "English", 10)
    check("merge carries it", bool(rec.prompts) and all(MARK in p for p in rec.prompts))

    rec = RecordingClient()
    cc._retry_summary_with_strict_prompt(rec, "m", "User: hi", language="English",
                                         context_limit=1000000)
    check("strict retry carries it", bool(rec.prompts) and all(MARK in p for p in rec.prompts))

    from agent_core.context_compressor import Message as _Msg
    rec = RecordingClient()
    out = cc.generate_structured_summary(
        messages=[_Msg(uuid="a", role="user", content="hi"),
                  _Msg(uuid="b", role="assistant", content="hello")],
        llm_client=rec, llm_model="m", context_limit=1000000,
    )
    check("final summary carries it", bool(out) and bool(rec.prompts)
          and all(MARK in p for p in rec.prompts), str(len(rec.prompts)))


    print("\n=== RESULT: PASS=%d FAIL=%d ===" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
