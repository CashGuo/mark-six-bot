# -*- coding: utf-8 -*-
"""12/13/14（平特 / 连肖 / N中M）结构化回归。

和 check_buylist.py 只判「能不能解析」不同，这里逐条核对：
  · 玩法归类（kind = pingte / lianxiao / zhong）
  · 生肖集合（连肖补位后的结果）
  · 整笔金额（带「各」= 每肖各，不带 = 原文整笔）
  · 分盘（MO / HK）
  · 号码盘个数（12/13/14 一律为 0；与普通生肖注连写时不许串味）

用法：python build\\check_zx_plays.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bet_rules
from main import get_current_lunar_year

# (文本, 市场, 号码盘号数, [(kind, 生肖串, 整笔金额), ...])
CASES = [
    # ---- 平特：不带「各」＝原文那个数就是这几个肖一共的钱 ----
    ("平特鼠100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平特鼠牛100", "MO", 0, [("pingte", "鼠牛", 100)]),
    ("平特鼠牛各100", "MO", 0, [("pingte", "鼠牛", 200)]),
    ("平特鼠牛各50", "MO", 0, [("pingte", "鼠牛", 100)]),
    ("平特鼠牛虎各30", "MO", 0, [("pingte", "鼠牛虎", 90)]),
    ("平特鼠各100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平码鼠100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平肖鼠100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平鼠100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平特鼠牛五十", "MO", 0, [("pingte", "鼠牛", 50)]),
    ("香港 平特马50", "HK", 0, [("pingte", "马", 50)]),
    # ---- 平特：空格 / 「各」位置 / 重复肖 / 中文金额 ----
    ("平特 鼠 100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平特鼠牛各 100", "MO", 0, [("pingte", "鼠牛", 200)]),
    ("平肖 鼠牛虎 各30", "MO", 0, [("pingte", "鼠牛虎", 90)]),
    ("平特鼠牛虎兔各25", "MO", 0, [("pingte", "鼠牛虎兔", 100)]),
    ("香港 平特鼠牛各100", "HK", 0, [("pingte", "鼠牛", 200)]),
    ("澳 平特马各50", "MO", 0, [("pingte", "马", 50)]),
    ("平特鼠牛一百", "MO", 0, [("pingte", "鼠牛", 100)]),
    ("平特鼠牛各一百", "MO", 0, [("pingte", "鼠牛", 200)]),
    ("平特鼠鼠100", "MO", 0, [("pingte", "鼠", 100)]),
    ("平特各鼠牛100", "MO", 0, [("pingte", "鼠牛", 200)]),
    ("平特鼠牛虎兔龙蛇马各10", "MO", 0, [("pingte", "鼠牛虎兔龙蛇马", 70)]),
    # ---- 平特：一条两条 ----
    ("平特鼠 50 平特牛 60", "MO", 0, [("pingte", "鼠", 50), ("pingte", "牛", 60)]),
    ("平特鼠牛各10 平特鼠100", "MO", 0, [("pingte", "鼠牛", 20), ("pingte", "鼠", 100)]),
    # ---- 连肖：不写位数默认二连，写不够自动补，只有「各」才每肖各 ----
    ("鼠二连肖100", "MO", 0, [("lianxiao", "鼠牛", 100)]),
    ("二连肖鼠100", "MO", 0, [("lianxiao", "鼠牛", 100)]),
    ("鼠牛二连肖100", "MO", 0, [("lianxiao", "鼠牛", 100)]),
    ("2连肖鼠牛50", "MO", 0, [("lianxiao", "鼠牛", 50)]),
    ("鼠3连肖300", "MO", 0, [("lianxiao", "鼠牛虎", 300)]),
    ("鼠五连肖500", "MO", 0, [("lianxiao", "鼠牛虎兔龙", 500)]),
    ("鼠牛三连肖各100", "MO", 0, [("lianxiao", "鼠牛虎", 300)]),
    ("鼠牛二连肖各50", "MO", 0, [("lianxiao", "鼠牛", 100)]),
    ("连肖鼠50", "MO", 0, [("lianxiao", "鼠牛", 50)]),
    ("鼠牛虎三连肖100", "MO", 0, [("lianxiao", "鼠牛虎", 100)]),
    # ---- 连肖：回绕补位 / 一位数 / 空格 / 两条 ----
    ("鸡四连肖200", "MO", 0, [("lianxiao", "鸡狗猪鼠", 200)]),
    ("猪二连肖各100", "MO", 0, [("lianxiao", "猪鼠", 200)]),
    ("猴三连肖200", "MO", 0, [("lianxiao", "猴鸡狗", 200)]),
    ("鼠一连肖100", "MO", 0, [("lianxiao", "鼠", 100)]),
    ("3连肖 鼠 100", "MO", 0, [("lianxiao", "鼠牛虎", 100)]),
    ("香港 鼠二连肖100", "HK", 0, [("lianxiao", "鼠牛", 100)]),
    ("鼠牛四连肖各50", "MO", 0, [("lianxiao", "鼠牛虎兔", 200)]),
    ("鼠二连肖100 鼠牛二连肖100", "MO", 0,
     [("lianxiao", "鼠牛", 100), ("lianxiao", "鼠牛", 100)]),
    # ---- N中M：不枚举具体词，四中四 / 五中三 / 六中六 都认 ----
    ("鼠牛二中二100", "MO", 0, [("zhong", "鼠牛", 100)]),
    ("二中二鼠牛100", "MO", 0, [("zhong", "鼠牛", 100)]),
    # 客户判决：肖数比 N 少（只写 2 肖却写三中二）不解析
    ("鼠牛三中二100", None, None, "fail"),
    # 客户判决：组合注一般不带「各」，出现就判死（不猜金额）
    ("鼠牛二中二各100", None, None, "fail"),
    ("鼠牛虎三中三200", "MO", 0, [("zhong", "鼠牛虎", 200)]),
    ("三中三鼠牛虎200", "MO", 0, [("zhong", "鼠牛虎", 200)]),
    ("澳门 三中三 鼠牛虎 200", "MO", 0, [("zhong", "鼠牛虎", 200)]),
    ("港 鼠牛虎三中三各50", None, None, "fail"),
    ("鼠牛虎蛇四中二100", "MO", 0, [("zhong", "鼠牛虎蛇", 100)]),
    ("鼠牛虎蛇猴五中三200", "MO", 0, [("zhong", "鼠牛虎蛇猴", 200)]),
    ("鼠牛虎兔四中四各50", None, None, "fail"),
    ("四中四鼠牛虎兔50", "MO", 0, [("zhong", "鼠牛虎兔", 50)]),
    ("港 鼠牛虎兔四中四各50", None, None, "fail"),
    ("鼠牛虎兔龙五中五200", "MO", 0, [("zhong", "鼠牛虎兔龙", 200)]),
    ("鼠牛虎兔龙蛇六中六100", "MO", 0, [("zhong", "鼠牛虎兔龙蛇", 100)]),
    ("鼠牛虎兔龙蛇六中六各10", None, None, "fail"),
    # ---- 与号码盘并存 ----
    ("平特鼠牛各100 鼠牛二中二100", "MO", 0,
     [("pingte", "鼠牛", 200), ("zhong", "鼠牛", 100)]),
    ("平特鼠100 鼠牛二中二100 鼠牛虎三中三200", "MO", 0,
     [("pingte", "鼠", 100), ("zhong", "鼠牛", 100), ("zhong", "鼠牛虎", 200)]),
    # 组合注带「各」判死 → 整条判死
    ("平特鼠牛各100，鼠牛二中二各100", None, None, "fail"),
    ("蛇50 平特蛇100", "MO", 4, [("pingte", "蛇", 100)]),
    ("平特鼠100 鼠50", "MO", 4, [("pingte", "鼠", 100)]),
    # ---- 连肖：十连 / 十一连 / 十二连（客户判决：要支持） ----
    ("鼠十连肖100", "MO", 0, [("lianxiao", "鼠牛虎兔龙蛇马羊猴鸡", 100)]),
    ("10连肖鼠100", "MO", 0, [("lianxiao", "鼠牛虎兔龙蛇马羊猴鸡", 100)]),
    ("鼠十一连肖100", "MO", 0, [("lianxiao", "鼠牛虎兔龙蛇马羊猴鸡狗", 100)]),
    ("鼠十二连肖100", "MO", 0, [("lianxiao", "鼠牛虎兔龙蛇马羊猴鸡狗猪", 100)]),
    ("马十二连肖各10", "MO", 0, [("lianxiao", "马羊猴鸡狗猪鼠牛虎兔龙蛇", 120)]),
    # ---- 平特里写号码：客户判决「不解析，这是错的」 ----
    ("平特鼠 07 100", None, None, "fail"),
    # ---- 港澳分盘：一条消息里港、澳都写，按词各落一笔账 ----
    ("港 平特鼠100 澳 平特牛100", "HK", None, "multi",
     [("HK", 0, [("pingte", "鼠", 100)]), ("MO", 0, [("pingte", "牛", 100)])]),
    ("港 牛100 澳 鼠50", "HK", None, "multi",
     [("HK", 4, []), ("MO", 4, [])]),
    # ---- 应判死 ----
    ("鼠牛中二100", None, None, "fail"),
    ("平特鼠5尾100", None, None, "fail"),
]


def describe(results):
    return "；".join("%s/%s/%s" % r for r in results) or "（空）"


def main():
    _, cur, zodiac = get_current_lunar_year()
    zodiac = {k: list(v) for k, v in zodiac.items()}
    print("当年生肖：%s　共 %d 条\n" % (cur, len(CASES)))

    bad = []
    for case in CASES:
        text, market, n_numbers, expect = case[:4]
        parcel_exp = case[4] if len(case) > 4 else None
        r = bet_rules.parse_local(text, zodiac)
        if expect == "fail":
            ok = not r.get("ok")
            got = "判死" if ok else "解析成功（不该）"
            print("%-34s %s  %s" % (text, "ok  " if ok else "FAIL", got))
            if not ok:
                bad.append(text)
            continue

        # 分盘用例：顶层 extras 是各段汇总的，这里先把期望摊平
        if parcel_exp:
            expect = [it for (_pm, _pn, plist) in parcel_exp for it in plist]
        got_list = [(e["kind"], e["zodiacs"], e["amount"]) for e in (r.get("extras") or [])]
        problems = []
        if not r.get("ok"):
            problems.append("解析失败：%s" % r.get("reason"))
        else:
            if r.get("market") != market:
                problems.append("盘 %s≠%s" % (r.get("market"), market))
            if n_numbers is not None and len(r.get("bets") or {}) != n_numbers:
                problems.append("号码盘 %d≠%d" % (len(r.get("bets") or {}), n_numbers))
            if parcel_exp:
                ps = r.get("parcels") or []
                if len(ps) != len(parcel_exp):
                    problems.append("段数 %d≠%d" % (len(ps), len(parcel_exp)))
                else:
                    for i, (pm, pn, plist) in enumerate(parcel_exp):
                        p = ps[i]
                        if p.get("market") != pm:
                            problems.append("第%d段盘 %s≠%s" % (i + 1, p.get("market"), pm))
                        if pn is not None and len(p.get("bets") or {}) != pn:
                            problems.append("第%d段号码盘 %d≠%d"
                                            % (i + 1, len(p.get("bets") or {}), pn))
                        g = [(e["kind"], "".join(e["zodiacs"]), e["amount"])
                             for e in (p.get("extras") or [])]
                        if len(g) != len(plist):
                            problems.append("第%d段笔数 %d≠%d" % (i + 1, len(g), len(plist)))
                        else:
                            for got, want in zip(g, plist):
                                if got[0] != want[0] or got[1] != want[1] \
                                        or abs(got[2] - want[2]) > 0.01:
                                    problems.append("第%d段 %s≠%s" % (i + 1, got, want))
            if len(got_list) != len(expect):
                problems.append("笔数 %d≠%d" % (len(got_list), len(expect)))
            else:
                for got, want in zip(got_list, expect):
                    if got[0] != want[0]:
                        problems.append("kind %s≠%s" % (got[0], want[0]))
                    if "".join(got[1]) != want[1]:
                        problems.append("生肖 %s≠%s" % ("".join(got[1]), want[1]))
                    if abs(got[2] - want[2]) > 0.01:
                        problems.append("金额 %s≠%s" % (got[2], want[2]))
            # 整笔合计必须等于各笔之和
            total = sum(x[2] for x in got_list)
            if abs(r.get("flat_total") or 0 - 0) >= 0:
                if abs((r.get("flat_total") or 0) - total) > 0.01:
                    problems.append("flat_total %s≠%s" % (r.get("flat_total"), total))

        if problems:
            bad.append(text)
            print("%-34s FAIL  %s" % (text, "、".join(problems)))
            print("%-34s   got=%s" % ("", describe(got_list)))
        else:
            print("%-34s ok    %s｜盘=%s 号盘=%s"
                  % (text, describe(got_list), market,
                     "—" if n_numbers is None else n_numbers))

    print("\n共 %d 条，失败 %d 条%s"
          % (len(CASES), len(bad), ("：" + "、".join(bad)) if bad else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
