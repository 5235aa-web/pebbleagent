"""Config and pricing RPCs."""

import json
import logging
import math

logger = logging.getLogger(__name__)


def _read_pricing_file(srv) -> dict:
    for p in (srv._DATA_DIR / "model_pricing.json", srv._PROJECT_ROOT / "model_pricing.json"):
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    return {"models": {}, "default_currency": "CNY"}


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "config.get":
        key = params.get("key", "")
        if key == "full":
            return {
            "id": rid,
            "result": {
            "config": {
            "display": {
            "tui_auto_resume_recent": True,
            }
            }
            }
            }
        if key == "agent_stats":
            tools_info = srv.load_tools_info()
            skills_info = srv.load_skills_info()
            tool_count = sum(len(v) for v in tools_info.values())
            skill_count = sum(len(v) for v in skills_info.values())
            return {
            "id": rid,
            "result": {
            "tool_count": tool_count,
            "skill_count": skill_count,
            }
            }
        if key == "pricing":
            # 计费面板数据：model_pricing.json 全量价表 + 后端权威时段判定 + 法定节假日数据。
            # 前端本地轮询判断时段仅为展示预判，实际计费以后端 period 为准（单一权威）；
            # cn_holidays 与后端判定同源（cn_holidays.json），供前端本地预判同步排除法定节假日。
            from agent_core.chat.usage import normalize_currency, resolve_cny_per_usd
            pricing_data = _read_pricing_file(srv)
            pricing_data = {
                **pricing_data,
                "models": pricing_data.get("models") if isinstance(pricing_data.get("models"), dict) else {},
                "default_currency": normalize_currency(pricing_data.get("default_currency"), "CNY"),
                "cny_per_usd": resolve_cny_per_usd(pricing_data),
            }
            return {
            "id": rid,
            "result": {
            "pricing": pricing_data,
            "period": srv.current_pricing_period(),  # 后端权威：'peak'（高峰）/ 'offpeak'（空闲）
            "cn_holidays": srv.get_cn_holidays_payload(),  # 法定节假日数据（前端本地预判用，与后端判定同源）
            "timezone": "Asia/Shanghai (UTC+8)",
            }
            }
        return {"id": rid, "result": {}}

    if method == "config.set":
        return {"id": rid, "result": {"ok": True}}

    if method == "pricing.save":
        # 模型价格编辑面板：严格校验后原子写入数据目录 model_pricing.json
        # （mtime 热重载自动生效，无需重启；数据目录优先于打包根，打包版同样安全）
        from agent_core.chat.usage import normalize_currency, resolve_cny_per_usd
        models = params.get("models")
        if not isinstance(models, dict) or not models:
            return {"id": rid, "result": {"ok": False, "error": "models must be a non-empty object"}}
        def_cur = normalize_currency(params.get("default_currency"), "CNY")
        clean_models = {}
        for name, entry in models.items():
            if not isinstance(name, str) or not name.strip():
                return {"id": rid, "result": {"ok": False, "error": "invalid model name"}}
            if not isinstance(entry, dict):
                return {"id": rid, "result": {"ok": False, "error": f"{name}: entry must be an object"}}
            clean = dict(entry)
            if "currency" in clean:
                raw_cur = str(clean.get("currency") or "").strip().upper()
                if not raw_cur:
                    clean.pop("currency", None)
                elif raw_cur not in ("CNY", "USD"):
                    return {"id": rid, "result": {"ok": False, "error": f"{name}: currency must be CNY or USD"}}
                else:
                    clean["currency"] = raw_cur
            # A row may exist only to hold a context window: a subscription
            # provider bills nothing per token, and forcing a price block would
            # write a fake zero rate into the cost table.
            pricing = entry.get("pricing")
            if pricing is None:
                clean.pop("pricing", None)
            else:
                ok, err = srv._validate_pricing_block(pricing)
                if not ok:
                    return {"id": rid, "result": {"ok": False, "error": f"{name}: {err}"}}
                clean["pricing"] = {k: float(pricing[k]) for k in srv._PRICING_KEYS}
            limit = entry.get("context_limit")
            if limit is None or (isinstance(limit, str) and not limit.strip()):
                clean.pop("context_limit", None)
            else:
                try:
                    window = int(limit)
                except (TypeError, ValueError):
                    return {"id": rid, "result": {"ok": False, "error": f"{name}: context_limit must be a whole number"}}
                if window <= 0:
                    return {"id": rid, "result": {"ok": False, "error": f"{name}: context_limit must be positive"}}
                clean["context_limit"] = window
            if "pricing" not in clean and "context_limit" not in clean:
                return {"id": rid, "result": {"ok": False, "error": f"{name}: entry needs pricing or context_limit"}}
            for bk in ("pricing_peak", "pricing_offpeak", "pricing_tier_high"):
                blk = entry.get(bk)
                if blk is None:
                    clean.pop(bk, None)
                    continue
                ok, err = srv._validate_pricing_block(blk)
                if not ok:
                    return {"id": rid, "result": {"ok": False, "error": f"{name}.{bk}: {err}"}}
                clean[bk] = {k: float(blk[k]) for k in srv._PRICING_KEYS}
            clean_models[name.strip()] = clean

        existing_pricing = _read_pricing_file(srv)
        raw_rate = params.get("cny_per_usd")
        if raw_rate is None or raw_rate == "":
            rate = resolve_cny_per_usd(existing_pricing)
        else:
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                return {"id": rid, "result": {"ok": False, "error": "cny_per_usd must be a positive number"}}
            if not math.isfinite(rate) or rate <= 0:
                return {"id": rid, "result": {"ok": False, "error": "cny_per_usd must be a positive number"}}
            rate = round(rate, 4)

        try:
            srv._atomic_write_json(
                srv._DATA_DIR / "model_pricing.json",
                {"models": clean_models, "default_currency": def_cur, "cny_per_usd": rate},
            )
        except Exception as e:
            logger.error(f"[Server] Failed to save model_pricing.json: {e}")
            return {"id": rid, "result": {"ok": False, "error": f"write failed: {e}"}}
        return {"id": rid, "result": {"ok": True, "count": len(clean_models), "default_currency": def_cur, "cny_per_usd": rate}}

    if method == "pricing.set_rate":
        from agent_core.chat.usage import normalize_currency
        try:
            rate = float(params.get("cny_per_usd"))
        except (TypeError, ValueError):
            return {"id": rid, "result": {"ok": False, "error": "cny_per_usd must be a positive number"}}
        if not math.isfinite(rate) or rate <= 0:
            return {"id": rid, "result": {"ok": False, "error": "cny_per_usd must be a positive number"}}
        rate = round(rate, 4)
        existing = _read_pricing_file(srv)
        payload = {
            "models": existing.get("models") if isinstance(existing.get("models"), dict) else {},
            "default_currency": normalize_currency(existing.get("default_currency"), "CNY"),
            "cny_per_usd": rate,
        }
        try:
            srv._atomic_write_json(srv._DATA_DIR / "model_pricing.json", payload)
        except Exception as e:
            logger.error(f"[Server] Failed to update cny_per_usd: {e}")
            return {"id": rid, "result": {"ok": False, "error": f"write failed: {e}"}}
        return {"id": rid, "result": {"ok": True, "cny_per_usd": rate}}

    return None
