# -*- coding: utf-8 -*-
"""节假日计费判定检查（隔离环境）：PEBBLE_HOME 临时目录下的优先级 / 热重载 / fallback / 降级

用法: python peb_check_holiday_env.py
说明: 独立进程运行，不影响真实 ~/.pebble 数据；跑完自动清理临时目录。
"""
import sys, os, json, shutil
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(ROOT, "_tmp_holiday_env")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
os.environ["PEBBLE_HOME"] = TMP

sys.path.insert(0, ROOT)
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))
TMP_FILE = os.path.join(TMP, "cn_holidays.json")
PROJ_FILE = os.path.join(ROOT, "cn_holidays.json")

def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)

def bump_mtime(path, delta):
    """显式把 mtime 调整 delta 秒（避免文件系统时间精度导致的相等）"""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + delta))

def d(y, m, day, h=10, mi=30):
    return datetime(y, m, day, h, mi, tzinfo=BJ)

# ---- 步骤 0：准备数据目录自定义文件 ----
write_json(TMP_FILE, {"holidays": ["2026-01-01", "2026-12-24"], "workdays": [], "years_covered": [2026]})

# 注意：用模块属性动态访问（重载会替换模块级变量，from-import 的绑定不会同步）
import multi_agents.agent_chat_service_v2 as svc

# ---- 步骤 1：路径优先级（数据目录 > 项目根）----
print("=== 步骤 1：路径优先级（数据目录 > 项目根）===")
print("加载集合:", sorted(svc._cn_holidays))
print("watch:", list(svc._cn_holidays_watch.keys()))
assert svc._cn_holidays == {"2026-01-01", "2026-12-24"}, "应加载数据目录的自定义文件"
assert set(svc._cn_holidays_watch.keys()) == {TMP_FILE, PROJ_FILE}, "快照应覆盖所有存在的候选文件"
assert svc.is_cn_holiday(d(2026, 1, 1)) is True
assert svc.is_cn_holiday(d(2026, 9, 25)) is False  # 自定义数据里没有中秋（项目根被遮蔽）
print("[PASS] 优先级正确：加载数据目录文件，项目根被遮蔽（9/25 在项目根有、自定义无 -> False）")

# ---- 步骤 2：热重载（修改数据目录文件，mtime 变化 -> 自动重载）----
print()
print("=== 步骤 2：热重载（修改文件后免重启生效）===")
write_json(TMP_FILE, {"holidays": ["2026-01-01", "2026-12-24", "2026-09-25"], "workdays": [], "years_covered": [2026]})
bump_mtime(TMP_FILE, 10)
assert svc.is_cn_holiday(d(2026, 9, 25)) is True, "热重载未生效"
print("修改后 is_cn_holiday(2026-09-25) =", svc.is_cn_holiday(d(2026, 9, 25)))
print("[PASS] 热重载生效：新增 2026-09-25 后判定立即变化")

# ---- 步骤 3：删除数据目录文件 -> fallback 到项目根（61 条）----
print()
print("=== 步骤 3：删除数据目录文件 -> fallback 项目根 ===")
os.remove(TMP_FILE)
assert svc.is_cn_holiday(d(2026, 6, 19)) is True, "fallback 未生效（项目根端午）"
assert svc.is_cn_holiday(d(2026, 12, 24)) is False, "TMP 独有日期应消失（来源已切换）"
assert len(svc._cn_holidays) == 61, f"fallback 后应为项目根 61 条，实际 {len(svc._cn_holidays)}"
print(f"删除后加载集合条数: {len(svc._cn_holidays)}（项目根数据）")
print("[PASS] fallback 生效：数据目录文件删除后自动回退项目根数据")

# ---- 步骤 4：数据目录放损坏文件 -> 解析失败 -> fallback 项目根 ----
print()
print("=== 步骤 4：数据目录文件损坏 -> 解析失败回退下一候选 ===")
with open(TMP_FILE, "w", encoding="utf-8") as f:
    f.write("{ this is not valid json !!!")
assert svc.is_cn_holiday(d(2026, 6, 19)) is True, "损坏文件应回退项目根"
assert len(svc._cn_holidays) == 61
print("[PASS] 损坏文件被跳过，回退项目根数据（61 条）")

# ---- 步骤 5：降级路径（所有候选均不可用 -> 空集合，不抛异常）----
print()
print("=== 步骤 5：降级（模拟所有候选不可用）===")
os.remove(TMP_FILE)
orig = svc._cn_holidays_candidates
svc._cn_holidays_candidates = lambda: [os.path.join(TMP, "nonexistent_a.json"), os.path.join(TMP, "nonexistent_b.json")]
try:
    svc._load_cn_holidays()
    assert svc._cn_holidays == set(), "降级应为空集合"
    assert svc._cn_workdays == set()
    # 降级状态下判定退化为周末规则：工作日 10:30 应为 peak，周末应 offpeak
    assert svc.is_peak_period(d(2026, 9, 24, 10, 30)) is True, "降级后工作日应仍为 peak"
    assert svc.is_peak_period(d(2026, 9, 19, 10, 30)) is False, "降级后周末应 offpeak"
    assert svc.is_cn_holiday(d(2026, 9, 25)) is False, "降级后节假日判定不生效"
    print("[PASS] 降级为空集合，判定退化为周末规则，无异常")
finally:
    svc._cn_holidays_candidates = orig
    svc._load_cn_holidays()  # 恢复真实候选

# ---- 步骤 6：恢复后再次确认 ----
print()
print("=== 步骤 6：恢复候选后重新加载 ===")
assert len(svc._cn_holidays) == 61
print(f"[PASS] 恢复后加载 {len(svc._cn_holidays)} 条（项目根数据）")

shutil.rmtree(TMP, ignore_errors=True)
print()
print("隔离环境测试全部通过")
