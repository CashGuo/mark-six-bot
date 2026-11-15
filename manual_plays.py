# -*- coding: utf-8 -*-
"""手工记账的玩法表 + 选号逻辑（控制台窗口和网页共用这一份）。

为什么单独抽出来：窗口（server_gui）和网页（templates/manual.html）都要回答
「我勾的这组条件，到底是哪几个号」。两边各写一套，同一单就会记出两个结果。
这里不自己实现规则，号码集合一律交给 bet_rules.parse_local 反推
（「红波各1」认出来的是哪些号，就是哪些号），界面只负责"选中了什么"。

拼成原文也在这里：号码 → 「01 06 各50」；生肖 → 「牛马各100」；
玩法 → 「大单各50」（认不出组合名就退回号码串）。
"""

import bet_rules

# (分组名, [(按钮文字, 玩法写法)])
GROUPS = [
    ("单双", [("单", "单"), ("双", "双")]),
    ("大小", [("大", "大"), ("小", "小")]),
    ("波色", [("红波", "红波"), ("蓝波", "蓝波"), ("绿波", "绿波")]),
    ("波色单双", [("红单", "红单"), ("红双", "红双"),
                 ("蓝单", "蓝单"), ("蓝双", "蓝双"),
                 ("绿单", "绿单"), ("绿双", "绿双")]),
    ("波色大小", [("红小", "红小"), ("红大", "红大"),
                 ("蓝小", "蓝小"), ("蓝大", "蓝大"),
                 ("绿小", "绿小"), ("绿大", "绿大")]),
    ("合数", [("合数单", "合数单"), ("合数双", "合数双")]),
    ("尾数", [(str(i) + "尾", str(i) + "尾") for i in range(10)]),
    ("头数", [(str(i) + "头", str(i) + "头") for i in range(5)]),
    ("五行", [("金", "金"), ("木", "木"), ("水", "水"),
             ("火", "火"), ("土", "土")]),
    ("其它", [("家禽", "家禽"), ("野兽", "野兽")]),
]

# 单个玩法凑不出组合名时，用这几个固定搭配（「大」+「单」= 大单）
_COMBOS = {
    frozenset(["大", "单"]): "大单",
    frozenset(["大", "双"]): "大双",
    frozenset(["小", "单"]): "小单",
    frozenset(["小", "双"]): "小双",
}
_WAVE = {"红波": "红", "蓝波": "蓝", "绿波": "绿"}
_ODSIZE = ("单", "双", "大", "小")

# 拼原文时生肖按这个顺序连写（客户写「牛马」，不写「马牛」）
ZODIAC_ORDER = ["鼠", "牛", "虎", "兔", "龙", "蛇",
                "马", "羊", "猴", "鸡", "狗", "猪"]


def zodiac_order(mapping):
    """这一年实际有的生肖，按传统顺序；表里多出来的附在后面，不丢。"""
    m = mapping or {}
    seq = [z for z in ZODIAC_ORDER if z in m]
    seq += [z for z in m if z not in seq]
    return seq


def nums_of_play(code, mapping=None, cache=None):
    """某个玩法写法对应的号码集合 —— 直接问解析器，保证和文本记账一致。

    先试「红波各1」，再试「红波1」；还要求每号金额正好 1 元，
    否则「5尾1」可能被当成「号码 1」而不是「5尾」。
    """
    if cache is not None and code in cache:
        return cache[code]
    out = set()
    for cand in ("%s各1" % code, "%s1" % code):
        r = bet_rules.parse_local(cand, mapping)
        if r.get("ok") and r.get("bets"):
            if all(abs(float(v) - 1.0) < 1e-9 for v in r["bets"].values()):
                out = set(r["bets"].keys())
                break
    if cache is not None:
        cache[code] = out
    return out


def combo_name(plays):
    """多玩法 → 一个能写进原文的组合名（大单 / 红单 / 红小），凑不出返回空串。"""
    p = set(plays or [])
    if not p:
        return ""
    if len(p) == 1:
        return next(iter(p))
    name = _COMBOS.get(frozenset(p))
    if name:
        return name
    w = [k for k in p if k in _WAVE]
    o = [k for k in p if k not in _WAVE]
    if len(w) == 1 and len(o) == 1 and o[0] in _ODSIZE:
        return _WAVE[w[0]] + o[0]
    return ""


def target(mapping, nums=None, zodiacs=None, plays=None, cache=None):
    """选中的条件 → 号码集合。

    点具体号码时，生肖/玩法条件让位；多个生肖取并集；
    多个玩法取交集；生肖和玩法之间也取交集。
    """
    nums = list(nums or [])
    zodiacs = list(zodiacs or [])
    plays = list(plays or [])
    if nums:
        return set(nums)
    zs = set()
    for z in zodiacs:
        zs |= set((mapping or {}).get(z) or [])
    if not plays:
        return zs
    ps = None
    for k in plays:
        s = nums_of_play(k, mapping, cache)
        ps = set(s) if ps is None else (ps & s)
    if ps is None:
        return zs
    if zs:
        ps = ps & zs
    return ps


def compose_text(mapping, nums=None, zodiacs=None, plays=None, order=None,
                 target_nums=None):
    """这一注写进原文的话。拼不出规则能懂的写法，就退回号码串。"""
    nums = list(nums or [])
    zodiacs = list(zodiacs or [])
    plays = list(plays or [])
    tgt = set(target_nums if target_nums is not None else
              target(mapping, nums, zodiacs, plays))
    if not tgt:
        return ""
    if nums:
        return " ".join(sorted(tgt))
    if zodiacs and not plays:
        seq = order or zodiac_order(mapping)
        return "".join(z for z in seq if z in set(zodiacs))
    if plays and not zodiacs:
        name = combo_name(plays)
        return name or " ".join(sorted(tgt))
    return " ".join(sorted(tgt))


def label_of(nums=None, zodiacs=None, plays=None, order=None, target_nums=None,
             mapping=None):
    """清单里显示成「大单（13 个号）」这种。"""
    nums = list(nums or [])
    zodiacs = list(zodiacs or [])
    plays = list(plays or [])
    tgt = set(target_nums or [])
    parts = []
    if nums:
        parts.append("号码")
    else:
        if zodiacs:
            seq = order or zodiac_order(mapping) or list(zodiacs)
            parts.append("+".join(z for z in seq if z in set(zodiacs)))
        if plays:
            parts.append("∩".join(sorted(plays)))
    preview = "/".join(sorted(tgt)) if len(tgt) <= 8 else "%d 个号" % len(tgt)
    return "%s（%s）" % ("·".join(parts) or "号码", preview)


def amount_str(v):
    return ("%.2f" % float(v)).rstrip("0").rstrip(".")


def join_text(notes):
    """整张清单 → 一句原文：牛各100；大单各50。"""
    return "；".join("%s各%s" % (n["text"], amount_str(n["each"]))
                     for n in notes if n.get("text"))


def verify_join(mapping, notes, joined):
    """整句拼一起后，解析结果得等于清单合并的结果（重叠号金额要累加）。"""
    expect = {}
    for n in notes:
        for num in (n.get("nums") or []):
            expect[num] = round(expect.get(num, 0) + float(n["each"]), 2)
    r = bet_rules.parse_local(joined, mapping)
    if not r.get("ok"):
        return False
    bets = r.get("bets") or {}
    if set(bets.keys()) != set(expect.keys()):
        return False
    return all(abs(float(bets[k]) - expect[k]) < 1e-6 for k in expect)


def verify(mapping, text, nums, amt):
    """拼出来的话，解析器得认成同一组号、同一个单价。"""
    s = "%s各%s" % (text, amount_str(amt))
    r = bet_rules.parse_local(s, mapping)
    if not r.get("ok"):
        return False
    bets = r.get("bets") or {}
    if set(bets.keys()) != set(nums):
        return False
    return all(abs(float(bets[n]) - amt) < 1e-6 for n in nums)


def build(mapping, nums=None, zodiacs=None, plays=None, amount=0.0,
          order=None, cache=None):
    """窗口和网页「加入清单」共用的这一下：算出号、拼出话、自检一遍。

    返回 dict：{ok, text, label, nums, each, total, reason}
    """
    try:
        amt = float(amount)
    except Exception:
        return {"ok": False, "reason": "金额写个数字"}
    if not (0 < amt <= 1e7):
        return {"ok": False, "reason": "金额写个数字"}
    amt = round(amt, 2)
    tgt = target(mapping, nums, zodiacs, plays, cache)
    if not tgt:
        return {"ok": False, "reason": "这组条件是空的，先选号码或换个玩法"}
    text = compose_text(mapping, nums, zodiacs, plays, order, tgt)
    if not verify(mapping, text, tgt, amt):
        text = " ".join(sorted(tgt))
        if not verify(mapping, text, tgt, amt):
            return {"ok": False, "reason": "这组号拼不出规则能懂的话，换个选法再试"}
    return {
        "ok": True,
        "text": text,
        "label": label_of(nums, zodiacs, plays, order, tgt, mapping),
        "nums": sorted(tgt),
        "each": amt,
        "total": round(amt * len(tgt), 2),
    }


def meta(mapping, order=None, cache=None):
    """给网页的一次性配置：分组玩法、生肖、号码盘（含波色，用来上色）。"""
    out_groups = []
    for gname, items in GROUPS:
        rows = []
        for disp, code in items:
            rows.append({"label": disp, "code": code,
                         "nums": sorted(nums_of_play(code, mapping, cache))})
        out_groups.append({"group": gname, "items": rows})
    zs = []
    for z in (order or list((mapping or {}).keys())):
        if z in (mapping or {}):
            zs.append({"label": z, "nums": sorted(mapping[z])})
    wave = {}
    for name in ("红波", "蓝波", "绿波"):
        for n in nums_of_play(name, mapping, cache):
            wave[n] = name
    return {"groups": out_groups, "zodiacs": zs, "wave": wave}
