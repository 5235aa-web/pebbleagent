# -*- coding: utf-8 -*-
"""峰谷机制作用范围检查：只有配置了 pricing_peak/pricing_offpeak 的模型受时段影响，
其余模型在任何时段都恒用各自单档单价（period 恒为 None）。

用法: python peb_check_pricing_scope.py
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import agent_core.agent_chat_service_v2 as svc_mod
from agent_core.agent_chat_service_v2 import AgentChatServiceV2

svc = AgentChatServiceV2.__new__(AgentChatServiceV2)
svc._model_pricing_mtime = 0.0
svc._model_pricing = svc._load_model_pricing()

class U:
    def __init__(self, p, c, ch=0):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.prompt_cache_hit_tokens = ch

_orig = svc_mod.current_pricing_period

def calc_with(period, model, usage):
    tracker = svc._ensure_usage()
    tracker.period_fn = lambda: period
    return tracker.calculate_cost(model, usage)

fails = []
def check(cond, msg):
    if cond:
        print(f"  [OK] {msg}")
    else:
        fails.append(msg)
        print(f"  [FAIL] {msg}")

# ===== 1. 全模型时段敏感性扫描 =====
print("=== 1. 全模型时段敏感性扫描（1000 prompt / 500 completion，无缓存）===")
u = U(1000, 500)
for name in svc._model_pricing.get("models", {}):
    cfg = svc._model_pricing["models"][name]
    has_split = bool(cfg.get("pricing_peak") and cfg.get("pricing_offpeak"))
    rp = calc_with("peak", name, u)
    ro = calc_with("offpeak", name, u)
    if rp is None or ro is None:
        print(f"  -- {name}: 无定价数据（跳过）")
        continue
    same = abs(rp["total_cost"] - ro["total_cost"]) < 1e-12
    if has_split:
        period_ok = (rp["period"] == "peak" and ro["period"] == "offpeak")
        check((not same) and period_ok,
              f"{name}（分时）: peak={rp['total_cost']} offpeak={ro['total_cost']} period=({rp['period']},{ro['period']})")
    else:
        period_ok = (rp["period"] is None and ro["period"] is None)
        check(same and period_ok,
              f"{name}（单档）: peak={rp['total_cost']} == offpeak={ro['total_cost']}，period 恒 None")

# ===== 2. 精确数值断言 =====
print()
print("=== 2. 精确数值断言 ===")
r = calc_with("peak", "mimo-v2.6-flash", U(1000, 500))
r2 = calc_with("offpeak", "mimo-v2.6-flash", U(1000, 500))
# mimo-flash: input 1.0/M, output 2.0/M → 0.001 + 0.001 = 0.002（两时段相同）
check(abs(r["total_cost"] - 0.002) < 1e-9 and abs(r2["total_cost"] - 0.002) < 1e-9 and r2["period"] is None,
      f"mimo-v2.6-flash 单档恒定: peak={r['total_cost']} offpeak={r2['total_cost']}（期望 0.002）")

r = calc_with("peak", "mimo-v2.6-flash", U(1000, 100, 400))
# miss=600→0.0006, cache=400→0.000008, out=100→0.0002 → 0.000808
check(abs(r["total_cost"] - 0.000808) < 1e-9 and r["period"] is None,
      f"mimo-v2.6-flash 缓存场景: {r['total_cost']}（期望 0.000808）")

rp = calc_with("peak", "deepseek-flash", U(1000, 500))
ro = calc_with("offpeak", "deepseek-flash", U(1000, 500))
check(abs(rp["total_cost"] - 0.006) < 1e-9 and abs(ro["total_cost"] - 0.003) < 1e-9
      and rp["period"] == "peak" and ro["period"] == "offpeak",
      f"deepseek-flash 分时生效: peak={rp['total_cost']} offpeak={ro['total_cost']}（期望 0.006 / 0.003）")

rp = calc_with("peak", "deepseek-v4-pro", U(1000, 500))
ro = calc_with("offpeak", "deepseek-v4-pro", U(1000, 500))
check(abs(rp["total_cost"] - 0.0225) < 1e-9 and abs(ro["total_cost"] - 0.01125) < 1e-9,
      f"deepseek-v4-pro 分时生效: peak={rp['total_cost']} offpeak={ro['total_cost']}（期望 0.0225 / 0.01125）")

# ===== 3. 未登记价格表的模型（llm_providers 中存在） =====
print()
print("=== 3. llm_providers 中未登记价格的模型（信息输出）===")
for name in ["deepseek-v4-flash-vision-exp", "mimo-v2-pro", "mimo-v2-omni", "mimo-v2-flash",
             "mimo-v2.5", "mimo-v2.5-pro", "qwen3.5-2b"]:
    r = calc_with("peak", name, u)
    tag = "None（不计费/不显示）" if r is None else f"total={r['total_cost']} period={r['period']}"
    print(f"  -- {name}: _calculate_cost -> {tag}")

svc_mod.current_pricing_period = _orig

print()
if fails:
    print(f"!!! {len(fails)} 项失败")
    raise SystemExit(1)
print("全部通过：仅分时模型（deepseek-flash / deepseek-v4-pro）受时段影响；其余模型恒用单档单价，period 恒 None")
