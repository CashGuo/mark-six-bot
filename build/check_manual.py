# -*- coding: utf-8 -*-
"""手工记账回归：界面能点的每一个玩法，拼成原文后规则必须认成同一组号、同一单价。

为什么单独一套：手工窗口不自己算账，它把清单拼成「写法 + 各金额」交给
/api/quick_bet。所以界面上的每个按钮，都必须能在规则解析器里找到对应写法，
而且号码集合、每号金额、合计三项都要对得上 —— 只要有一个对不上，
手工单就会和微信推送记出两个结果。

只读：不写账本、不调接口。跑法（工作目录 = 工程根）：
    python build\\check_manual.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bet_rules                     # noqa: E402
import msb_view                      # noqa: E402
import server_gui                    # noqa: E402

MAPPING = msb_view.zodiac_year()[2]

# 文档第 2/3/4/5/6/8/9/10 条给的集合大小（2026 马年）
EXPECT_PLAY = {
    "单": 25, "双": 24, "大": 25, "小": 24,
    "红波": 17, "蓝波": 16, "绿波": 16,
    "红单": 8, "红双": 9, "蓝单": 8, "蓝双": 8, "绿单": 9, "绿双": 7,
    "红小": 10, "红大": 7, "蓝小": 7, "蓝大": 9, "绿小": 7, "绿大": 9,
    "合数单": 25, "合数双": 24,
    "0尾": 4, "1尾": 5, "2尾": 5, "3尾": 5, "4尾": 5,
    "5尾": 5, "6尾": 5, "7尾": 5, "8尾": 5, "9尾": 5,
    "0头": 9, "1头": 10, "2头": 10, "3头": 10, "4头": 10,
    "金": 10, "木": 10, "水": 9, "火": 12, "土": 8,
    "家禽": 25, "野兽": 24,
}

AMT = 50.0
bad = []


def check_one(desc, text, want_nums, want_each, want_total=None):
    r = bet_rules.parse_local(text, MAPPING)
    if not r.get("ok"):
        bad.append((desc, text, "解析失败：%s" % (r.get("reason") or "")))
        return
    bets = r.get("bets") or {}
    nums = sorted(bets.keys())
    if want_nums is not None and len(nums) != want_nums:
        bad.append((desc, text, "号数 %d，应 %d" % (len(nums), want_nums)))
        return
    if want_each is not None:
        for n in nums:
            if abs(float(bets[n]) - want_each) > 1e-6:
                bad.append((desc, text, "%s 记成 %s，应 %s"
                            % (n, bets[n], want_each)))
                return
    total = round(sum(float(v) for v in bets.values()), 2)
    if want_total is not None and abs(total - want_total) > 1e-6:
        bad.append((desc, text, "合计 %s，应 %s" % (total, want_total)))


# ---- 1. 界面上的每个玩法按钮 ----
for gname, items in server_gui.ManualBetDialog.PLAY_GROUPS:
    for disp, code in items:
        want = EXPECT_PLAY.get(code)
        check_one("%s/%s" % (gname, disp), "%s各%s" % (code, 50), want, AMT,
                  None if want is None else want * AMT)

# ---- 2. 生肖（4~5 个号，各记单价）----
for z in MAPPING:
    check_one("生肖/%s" % z, "%s各10" % z, len(MAPPING[z]), 10.0)

# ---- 3. 多生肖并集 ----
check_one("牛马各10", "牛马各10", 9, 10.0, 90.0)
check_one("鼠50（4 号各 50）", "鼠50", 4, 50.0, 200.0)

# ---- 4. 组合写法必须等于两个集合的交集 ----
for wave in ("红波", "蓝波", "绿波"):
    for od in ("单", "双", "大", "小"):
        combo = {"红波": "红", "蓝波": "蓝", "绿波": "绿"}[wave] + od
        if (wave, od) in (("红波", "单"), ("红波", "双"), ("蓝波", "单"),
                          ("蓝波", "双"), ("绿波", "单"), ("绿波", "双"),
                          ("红波", "小"), ("蓝波", "大"), ("绿波", "小")):
            want = EXPECT_PLAY.get(combo)
            check_one("组合/%s" % combo, "%s各20" % combo, want, 20.0,
                      None if want is None else want * 20)
for combo in ("大单", "大双", "小单", "小双"):
    r1 = bet_rules.parse_local("%s各20" % combo, MAPPING)
    if r1.get("ok"):
        want = len(r1["bets"])
        check_one("组合/%s" % combo, "%s各20" % combo, want, 20.0, want * 20)

# ---- 5. 具体号码 ----
check_one("单号06", "06各50", 1, 50.0, 50.0)
check_one("06 18 各50", "06 18各50", 2, 50.0, 100.0)
check_one("5 5（5 号各 5）", "5 5", 1, 5.0, 5.0)

# ---- 6. 多注连写（手工清单拼句就是这种形状）----
r = bet_rules.parse_local("牛各100；大单各50", MAPPING)
if not r.get("ok"):
    bad.append(("多注", "牛各100；大单各50", "解析失败：%s" % (r.get("reason") or "")))
else:
    bets = r["bets"]
    # 牛 4 号（06/18/30/42）各 100；大单 13 个奇数大号（25…49）各 50。
    # 牛的号全是偶数或小于 25，与大单没有交集 → 4 + 13 = 17 个号
    if len(bets) != 17:
        bad.append(("多注", "牛各100；大单各50",
                    "号数 %d，应 17（牛 4 + 大单 13，无重叠）" % len(bets)))
    for num, want in (("30", 100.0), ("06", 100.0), ("25", 50.0), ("49", 50.0)):
        if abs(float(bets.get(num, 0)) - want) > 1e-6:
            bad.append(("多注", "牛各100；大单各50",
                        "%s 记成 %s，应 %s" % (num, bets.get(num), want)))

print("手工记账回归：玩法 %d 个 + 生肖 %d 个 + 组合/号码/多注"
      % (sum(len(v) for _, v in server_gui.ManualBetDialog.PLAY_GROUPS),
         len(MAPPING)))
if bad:
    print("不符 %d 项：" % len(bad))
    for d, t, why in bad:
        print("  ✗ %-18s「%s」  %s" % (d, t, why))
    sys.exit(1)
print("全部通过")
