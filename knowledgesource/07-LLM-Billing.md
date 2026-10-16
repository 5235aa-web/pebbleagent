# 7. LLM Integration & Billing

### API Call Parameter Construction (agent_chat_service_v2.py near L1700)

```python
stream_params = {
    "model": use_model,
    "messages": messages,
    "stream": True,
    "temperature": final_temperature,
    "top_p": final_top_p,
}
# unified thinking-parameter construction (build_thinking_params, all vendors; non-DeepSeek "max" downgraded to "high")
stream_params.update(build_thinking_params(use_model, final_thinking_enabled, final_reasoning_effort))
if tools:
    stream_params["tools"] = tools
    if not (is_deepseek and final_thinking_enabled):
        stream_params["tool_choice"] = "auto"  # DeepSeek thinking mode does not support this parameter
```

**Unified thinking-parameter construction (build_thinking_params, added 2026-10-15, module-level function)**:

| Case | What is sent |
|------|---------|
| Enabled (default) | `thinking: {"type": "enabled"}` + `reasoning_effort` (all vendors, no longer DeepSeek-only) |
| Disabled | `thinking: {"type": "disabled"}`, reasoning_effort not sent |
| Non-DeepSeek + "max" | Automatically downgraded to `"high"` (Xiaomi accepts only low/medium/high; "max" causes a 400; OpenAI/Gemini value ranges exclude max too) |

- Frontend toggle: the thinking row in the LLM config panel (default on), stored as `thinking_enabled` in llm_providers.json; when off, the reasoning tier is greyed out
- server.py read: `prov.get("thinking_enabled") is not False` (absent = on, old configs unchanged); saved via `_bool(v, True)` lenient parsing (recognizes bool/"true"/"1"/"yes"/"on")
- Title generation generate_conversation_title also goes through build_thinking_params (toggle & effort passed in by the caller)
- Vendor specs (measured 2026-10-15): DeepSeek supports none/low/high/max (medium→high server-side mapping); Xiaomi MiMo only low/medium/high (off = thinking:disabled; thinking on by default); the Claude compat layer ignores reasoning_effort (thinking uses budget_tokens); the Gemini compat layer supports reasoning_effort none/minimal/low/medium/high; lenient implementations ignore unknown fields

### Parameter Source Priority

| Parameter | Priority |
|------|--------|
| temperature | explicit > agent_model_config.json > YAML agent_def > llm_providers.json |
| top_p | explicit > YAML agent_def > llm_providers.json |
| reasoning_effort | explicit > llm_providers.json.reasoning_effort > default "high" (sent to all vendors; non-DeepSeek "max" downgraded to high) |
| thinking_enabled | explicit > llm_providers.json.thinking_enabled (absent = True) > default True |
| model | explicit > agent_model_config.json.model_source > llm_providers.json default |

### Cost Calculation Chain

```
chunk.usage → _calculate_cost(model, usage)
  → read model_pricing.json for per-1M-token prices
  → split cache_hit / cache_miss / output
  → _accumulate_cost() → in-memory accumulation + agent_chat.db persistence
  → yield "usage" event → server.py → write_event("message.usage")
  → gateway.ts → StatusBar.vue (shows total cost / tokens / cache hit)
```

### ⏱ DeepSeek Time-of-Day Pricing (added 2026-09-06, holidays added 2026-09-25)

- **Rule**: peak = Beijing time (fixed UTC+8 offset), Monday–Friday (excluding Chinese statutory holidays), [09:00,12:00) ∪ [14:00,18:00); everything else (weekends, statutory holidays all day, lunch break, evenings, early morning) is off-peak; off-peak price = half of peak
- **Holidays (added 2026-09-25)**: single source of truth = cn_holidays.json (State Council holiday schedule, incl. make-up workdays; 2025–2026 covered); a holiday date hit means off-peak all day; make-up workdays do not participate per the official text (weekend make-up days still count as weekend = off-peak all day; the workdays field is kept for a future switch); missing/corrupt data degrades to the weekend rule (with a log warning); candidate-file snapshot comparison hot reload (no restart to change data); uncovered years trigger a startup log reminder; run peb_check_holiday.py after updating data
- **Scope (verified 2026-09-25)**: peak/off-peak applies only to models configured with BOTH pricing_peak+pricing_offpeak (currently deepseek-flash/deepseek-v4-pro); other models (mimo etc.) always use their single-tier price, period=None, and the frontend shows no peak/off-peak UI (4 elements rendered conditionally on isTimeBilling); regression tool peb_check_pricing_scope.py (all-model time sensitivity + exact numeric assertions)
- **Config**: in model_pricing.json, deepseek-v4-flash / deepseek-v4-pro / deepseek-v4-flash-vision-exp carry `pricing_peak` + `pricing_offpeak` (sibling fields of pricing_tier_high), `pricing` falls back = peak price; deepseek-v4-flash-vision-exp is same-priced as flash in all three tiers (vision image tokens count into usage.prompt_tokens, billed automatically, no special-casing); models without time-of-day fields (mimo/qwen) keep single pricing
- **Determination**: `is_peak_period()` / `current_pricing_period()` / `is_cn_holiday()` are defined module-level in agent_chat_service_v2.py (server.py re-imports), single authority; the frontend StatusBar pre-judges locally with the same rule via Intl Asia/Shanghai (holiday list delivered with config.get 'pricing' cn_holidays), and aligns to the backend authoritative period on flips — **actual billing follows the backend**
- **Per-call trace**: `_calculate_cost` return value carries `period` (peak/offpeak, null for non-time-of-day models) and `pricing_used` (the actual unit price of this call); cost_info flows via this_call into the message.usage event and callHistory; the panel shows "last call @ peak/off-peak rate" from it
- **Hot reload**: `_ensure_model_pricing_fresh()` re-reads model_pricing.json automatically on mtime change; price edits need no backend restart
- **Historical costs**: costs already persisted in agent_chat.db are frozen and never recalculated; price edits affect only future calls

### Model Price Editor Panel (pricing.save, added 2026-10-15)

- **Frontend**: TitleBar "$" button (next to Memory Archive) → PricingPanel.vue; on open it fetches the latest price table via config.get 'pricing'; inline editing of base/peak/off price groups (in/hit/out, per 1M tokens); [+] add model, [×] delete (ConfirmDialog); "Save to JSON" submits all models
- **Backend**: server.py `pricing.save` — validation (`_validate_pricing_block`: all three fields present, finite non-negative numbers; NaN/inf/negative/missing all rejected) → sanitization (string numbers → float, null blocks dropped, unknown fields preserved e.g. pricing_tier_high) → `_atomic_write_json` atomic write of `~/.pebble/model_pricing.json` (temp file + os.replace, prevents corruption; safe in packaged builds)
- **Effect chain**: after writing to the data dir, mtime hot reload picks it up automatically (no restart); on success the frontend dispatches 'pricing-updated' → StatusBar refetches immediately (otherwise it waits up to the 60s poll)
- **Semantics**: panel edits = price numbers + add/remove models; display_name/provider keep original values (new models default display_name = model name); peak/off fields shown if present, no add-entry otherwise (non-deepseek models stay single-tier); first save creates the data-dir file (previously read from the project root)
- **Verification**: isolated test `.pebble/_tmp_verify/verify_pricing_save.py` 27/27 (incl. the "save → hot reload → read" full chain + 8 validation-rejection cases + file-corruption protection)

### Context Compaction Strategy

| Trigger | Function | Description |
|---------|------|------|
| Manual | server.py → manual_compress() | User clicks the StatusBar compress button |
| Auto | should_auto_compact() | Triggered when tokens exceed the threshold |
| Reactive | reactive_compact_on_error() | Triggered when the API returns prompt_too_long |

Compaction results are written to both sessions.db + agent_chat.db (dual-DB sync); the frontend shows the summary via `CompactSummaryCard.vue`.

**Trigger & retention (2026-10-15 revamp)**: manual compaction triggers at ≥2 dialog messages (was >10); effective retention degrades dynamically `effective_keep = min(recent_messages, len(dialog_messages)-1)` — with ≤10 messages at least 1 is compacted and the rest kept; with ≥11 messages the most recent 10 are kept (original behavior). The frontend StatusBar checks `<2` accordingly.

2026-07-31 fix points:
1. **agent_chat.db insert_compaction_result does NOT accept kept_count** — agent_chat is a slim DB (user/assistant only); computing the cutoff from the sessions retention count marks 0 messages. Correct semantics: **mark ALL old messages before the boundary is_compacted=1**, append boundary/summary at the end.
2. **Bounded context expansion**: `_expand_assistant_context_message` caps tool output at 2000 chars, tool input at 1000, reasoning at 2000, text at 4000 (execution_order stores full outputs; only expansion into context is capped).
3. **Summary detection by field**: both sessions.db / agent_chat.db messages tables have an `is_compact_summary` column; the frontend convertBackendMessages prefers the field, and the `This session is being continued` prefix is only backward-compat.
4. **Summary generation**: input has a token budget (DEFAULT_SUMMARY_INPUT_BUDGET=40000, newest-first trimming), output max_tokens=8000, `format_compact_summary` tolerates truncated output without closing tags.
5. **Tool-pairing protection boundary (new defense 2026-10-15)**: `adjust_index_to_preserve_api_invariants` may expand the kept range to all messages (the to-compact set becomes empty, e.g. with two messages `[assistant(tool_use), user(tool_result)]`) → a defensive check returns "no messages to compact (tool pairing integrity forbids splitting)".

### Notes

2. **thinking/reasoning parameter mechanism (2026-10-15)**: ① all vendors send reasoning_effort (built in one place, build_thinking_params); ② disabling thinking is not "omit the field" but `thinking: {"type": "disabled"}` (DeepSeek/Xiaomi default to thinking on; omission cannot turn it off); ③ non-DeepSeek "max" is downgraded to "high" (Xiaomi 400s on max) — if Xiaomi returns 400, check here first; ④ frontend toggle field = llm_providers.json `thinking_enabled` (absent=True); ⑤ title generation follows the same toggle
10. **Time-of-day pricing pitfalls (added 2026-09-06)**: ① never hardcode prices in code/frontend — the single source is model_pricing.json (pushed via config.get 'pricing'); ② period determination is authoritative on the backend `current_pricing_period()`; the frontend Intl Asia/Shanghai pre-judgment is UI-only; ③ `is_peak_period` is a module-level function, not a class method — don't delete it when editing service logic; ④ when `usage.value` is replaced wholesale by resume/create, period is temporarily cleared; StatusBar refetches after reconnect (don't add persistence dependencies here); ⑤ editing model_pricing.json needs no restart (mtime hot reload), but verify billing via period/pricing_used in the message.usage event
11. **Vision model deepseek-v4-flash-vision-exp pitfalls (added 2026-09-06)**: ① the image-input backend pipeline (IMAGE_EXT → image_url blocks, L1256/L1440) has existed for a long time; onboarding a model only needs registration in llm_providers.json + model_pricing.json — don't rebuild it; ② image blocks are sent to the LLM with the current request only, not persisted to DB — image context is lost after switching sessions/compaction, by design; ③ non-vision models (flash/pro/mimo) receiving images are rejected by the DeepSeek API with 400 (the frontend does not intercept; plan B status quo); ④ a vision model whose name contains "deepseek" takes the is_deepseek branch carrying reasoning_effort/thinking params; if the API errors in practice, exclude it by model name; ⑤ the frontend upload cap is 10MB/20 files (useFileDrop), below DeepSeek's 32MiB per-image / 48MiB request limits; base64 direct upload needs no scaling; ⑥ bmp/svg are recognized as images by the frontend but service IMAGE_EXT covers only png/jpg/jpeg/gif/webp/bmp — svg falls through (SVG is TBD; DeepSeek does not support svg either)
17. **Statutory-holiday billing (added 2026-09-25)**: ① the single data source is cn_holidays.json (State Council schedule) — never hardcode dates in code; ② make-up workdays (workdays) do not participate per the official text (weekend make-up days remain off-peak all day); if the official practice changes to work-calendar, only is_peak_period needs editing; ③ the frontend isPeakLocal depends on cn_holidays.holidays delivered by fetchPricing; on fetch failure keep old values (don't clear peakInfo.holidays); ④ hot reload is "candidate-file snapshot comparison" (covers overwrite/append/delete/corruption-repair, not single-file mtime); ⑤ next-year schedules usually publish in November — update cn_holidays.json (append holidays + years_covered + updated_at) and re-run peb_check_holiday.py; ⑥ degradation chain: corrupt data-dir file → try project root → all unavailable = empty set (weekend rule) + log warning; ⑦ packaging (PyInstaller --add-data / spec datas) MUST include cn_holidays.json — if missed, the exe has no data file and degrades to the weekend rule at runtime (packaged-app symptom: holidays still billed at peak); hot-fix for an installed app = copy to ~/.pebble/ (no reinstall); Tauri sidecar logs are nulled (src-tauri/src/lib.rs), so in-app degradation warnings are invisible
18. **Peak/off-peak scope (verified 2026-09-25)**: ① the time-of-day pricing condition = the model has BOTH pricing_peak+pricing_offpeak (config-driven, single point in _calculate_cost); adding these two fields to a non-deepseek model makes it "accidentally time-of-day"; removing them restores single-tier; ② all frontend peak/off-peak UI (cost dot / period badge / last-call preview / price-section divider) is conditioned on isTimeBilling — new peak/off-peak UI elements must reuse that condition; ③ usage.period semantics = most recent call's period (null can overwrite); the "current period" authority is peakInfo.backendPeriod (maintained by fetchPricing); ④ model-name matching chain: callHistory.model / cfg.activeModel ↔ model_pricing.json keys must match (mismatch → curModelPricing=null → isTimeBilling=false safe degradation but no cost display); same on the agent side: a dangling model_source (not in the provider list / price table) falls back to the first provider with no billing — two instances fixed 2026-09-25 (chat_agent: deepseek-v4-flash→deepseek-flash, analysis_agent: mimo-v2-flash→mimo-v2.6-flash, dual-file sync + resolution-chain test); ⑤ runtime config lives in ~/.pebble/ (llm_providers.json has a data-dir version); the same-named file in the project root may be a stale template — check the data dir first when investigating config drift
