"""Config and pricing RPCs."""

import json
import logging

logger = logging.getLogger(__name__)


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
            pricing_data = None
            for p in [srv._DATA_DIR / "model_pricing.json", srv._PROJECT_ROOT / "model_pricing.json"]:
                if p.exists():
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            pricing_data = json.load(f)
                        break
                    except Exception:
                        continue
            pricing_data = pricing_data or {"models": {}, "default_currency": "CNY"}
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
        models = params.get("models")
        if not isinstance(models, dict) or not models:
            return {"id": rid, "result": {"ok": False, "error": "models must be a non-empty object"}}
        clean_models = {}
        for name, entry in models.items():
            if not isinstance(name, str) or not name.strip():
                return {"id": rid, "result": {"ok": False, "error": "invalid model name"}}
            if not isinstance(entry, dict):
                return {"id": rid, "result": {"ok": False, "error": f"{name}: entry must be an object"}}
            ok, err = srv._validate_pricing_block(entry.get("pricing"))
            if not ok:
                return {"id": rid, "result": {"ok": False, "error": f"{name}: {err}"}}
            clean = dict(entry)
            clean["pricing"] = {k: float(entry["pricing"][k]) for k in srv._PRICING_KEYS}
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
        def_cur = params.get("default_currency")
        if not isinstance(def_cur, str) or not def_cur.strip():
            def_cur = "CNY"
        try:
            srv._atomic_write_json(srv._DATA_DIR / "model_pricing.json", {"models": clean_models, "default_currency": def_cur.strip()})
        except Exception as e:
            logger.error(f"[Server] Failed to save model_pricing.json: {e}")
            return {"id": rid, "result": {"ok": False, "error": f"write failed: {e}"}}
        return {"id": rid, "result": {"ok": True, "count": len(clean_models)}}

    return None
