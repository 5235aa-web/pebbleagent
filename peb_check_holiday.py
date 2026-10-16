# -*- coding: utf-8 -*-
"""节假日计费判定检查（主）：真实模块 + 项目根 cn_holidays.json

用法: python peb_check_holiday.py
说明: 每年更新 cn_holidays.json 后重跑本脚本回归验证；
      "当前时刻"部分为信息输出，不做固定断言（运行日期不定）。
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from datetime import datetime, timezone, timedelta, date

print("=== 环境检查 ===")
print("python:", sys.version.split()[0])
print("agent_core/__init__.py 存在:", os.path.exists(os.path.join(ROOT, "agent_core", "__init__.py")))
user_file = os.path.join(os.path.expanduser("~"), ".pebble", "cn_holidays.json")
print("~/.pebble/cn_holidays.json 存在:", os.path.exists(user_file))

import agent_core.agent_chat_service_v2 as svc

BJ = timezone(timedelta(hours=8))

print()
print("=== 数据加载检查 ===")
print(f"holidays 条数: {len(svc._cn_holidays)} (期望 61)")
print(f"workdays 条数: {len(svc._cn_workdays)} (期望 11)")
print(f"watch 快照: {list(svc._cn_holidays_watch.keys())}")
assert len(svc._cn_holidays) == 61, f"holidays 条数不符: {len(svc._cn_holidays)}"
assert len(svc._cn_workdays) == 11, f"workdays 条数不符: {len(svc._cn_workdays)}"

payload = svc.get_cn_holidays_payload()
print(f"payload: years_covered={payload['years_covered']}, updated_at={payload['updated_at']}")
assert payload["years_covered"] == [2025, 2026]
assert "2026-09-25" in payload["holidays"]

def parse(s):
    y, m, d = map(int, s.split("-")); return date(y, m, d)

groups = [
    ("2025-01-01","2025-01-01"), ("2025-01-28","2025-02-04"), ("2025-04-04","2025-04-06"),
    ("2025-05-01","2025-05-05"), ("2025-05-31","2025-06-02"), ("2025-10-01","2025-10-08"),
    ("2026-01-01","2026-01-03"), ("2026-02-15","2026-02-23"), ("2026-04-04","2026-04-06"),
    ("2026-05-01","2026-05-05"), ("2026-06-19","2026-06-21"), ("2026-09-25","2026-09-27"),
    ("2026-10-01","2026-10-07"),
]
gcount = 0
for a, b in groups:
    cur = parse(a)
    while cur <= parse(b):
        s = cur.isoformat()
        assert s in svc._cn_holidays, f"缺失: {s}"
        gcount += 1
        cur += timedelta(days=1)
assert gcount == 61, f"组内日期数 {gcount} != 61"
print(f"假期组完整性校验通过：13 组共 {gcount} 天，与总数吻合")

wchecks = ["2025-01-26","2025-02-08","2025-04-27","2025-09-28","2025-10-11",
           "2026-01-04","2026-02-14","2026-02-28","2026-05-09","2026-09-20","2026-10-10"]
for s in wchecks:
    assert s in svc._cn_workdays, f"缺补班日: {s}"
print("补班日校验通过（11 条）")

print()
print("=== 关键日期星期自检 ===")
for d, exp in [((2026,9,25),"五"), ((2026,9,24),"四"), ((2026,9,19),"六"), ((2026,9,20),"日"),
               ((2026,9,28),"一"), ((2026,10,1),"四"), ((2026,10,10),"六"), ((2026,2,16),"一"),
               ((2026,6,19),"五"), ((2025,6,10),"二")]:
    wd = "一二三四五六日"[datetime(*d).weekday()]
    print(f"{d} => 周{wd} (预期周{exp}) {'OK' if wd == exp else '!! MISMATCH !!'}")

def dt(y, mo, da, h, mi, tz=BJ):
    return datetime(y, mo, da, h, mi, tzinfo=tz)

print()
print("=== 判定用例 ===")
cases = [
    ("中秋 9/25(五) 10:30 -> offpeak (修复点)", dt(2026,9,25,10,30), False),
    ("中秋 9/25 15:00 -> offpeak", dt(2026,9,25,15,0), False),
    ("中秋 9/25 9:00 整点 -> offpeak", dt(2026,9,25,9,0), False),
    ("中秋 9/26(六) 10:30 -> offpeak", dt(2026,9,26,10,30), False),
    ("节前工作日 9/24(四) 10:30 -> peak", dt(2026,9,24,10,30), True),
    ("节后工作日 9/28(一) 10:30 -> peak", dt(2026,9,28,10,30), True),
    ("补班日 9/20(日) 10:30 -> offpeak (官方文字口径)", dt(2026,9,20,10,30), False),
    ("普通周六 9/19 10:30 -> offpeak", dt(2026,9,19,10,30), False),
    ("国庆 10/1(四) 10:30 -> offpeak", dt(2026,10,1,10,30), False),
    ("国庆 10/7(三) 14:30 -> offpeak", dt(2026,10,7,14,30), False),
    ("国庆补班 10/10(六) 10:30 -> offpeak", dt(2026,10,10,10,30), False),
    ("节后工作日 10/12(一) 10:30 -> peak", dt(2026,10,12,10,30), True),
    ("端午 6/19(五) 10:30 -> offpeak", dt(2026,6,19,10,30), False),
    ("春节 2/16(一) 10:30 -> offpeak", dt(2026,2,16,10,30), False),
    ("春节 2/23(一) 10:30 -> offpeak (假期末日)", dt(2026,2,23,10,30), False),
    ("春节补班 2/14(六) 10:30 -> offpeak", dt(2026,2,14,10,30), False),
    ("春节后 2/24(二) 10:30 -> peak", dt(2026,2,24,10,30), True),
    ("元旦 1/1(四) 10:30 -> offpeak", dt(2026,1,1,10,30), False),
    ("元旦补班 1/4(日) 10:30 -> offpeak", dt(2026,1,4,10,30), False),
    ("清明 4/4(六) 10:30 -> offpeak", dt(2026,4,4,10,30), False),
    ("劳动 5/1(五) 10:30 -> offpeak", dt(2026,5,1,10,30), False),
    ("2025 国庆 10/1(三) 10:30 -> offpeak", dt(2025,10,1,10,30), False),
    ("2025 普通工作日 6/10(二) 10:30 -> peak", dt(2025,6,10,10,30), True),
    ("工作日 12:00 整点 -> offpeak", dt(2026,9,24,12,0), False),
    ("工作日 14:00 整点 -> peak", dt(2026,9,24,14,0), True),
    ("工作日 18:00 整点 -> offpeak", dt(2026,9,24,18,0), False),
    ("工作日 9:00 整点 -> peak", dt(2026,9,24,9,0), True),
    ("工作日 8:59 -> offpeak", dt(2026,9,24,8,59), False),
    ("UTC 02:30 = 北京 10:30 工作日 -> peak", dt(2026,9,24,2,30,timezone.utc), True),
    ("UTC 02:30 = 北京 10:30 中秋 -> offpeak", dt(2026,9,25,2,30,timezone.utc), False),
]
fails = []
for desc, d, expected in cases:
    got = svc.is_peak_period(d)
    ok = got == expected
    if not ok:
        fails.append((desc, got, expected))
    print(f"[{'PASS' if ok else 'FAIL'}] {desc} => {'peak' if got else 'offpeak'}")

print()
print("=== 当前时刻实测（信息输出，不断言）===")
now = datetime.now(BJ)
print(f"北京时间 now = {now.strftime('%Y-%m-%d %H:%M:%S')} 周{'一二三四五六日'[now.weekday()]}")
print(f"is_peak_period() = {svc.is_peak_period()}")
print(f"current_pricing_period() = {svc.current_pricing_period()}")
print(f"is_cn_holiday() = {svc.is_cn_holiday()}")

print()
if fails:
    print(f"!!! {len(fails)} 个用例失败:")
    for f in fails:
        print("   ", f)
    raise SystemExit(1)
print(f"全部 {len(cases)} 个判定用例通过")
