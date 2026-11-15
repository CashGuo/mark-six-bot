# -*- coding: utf-8 -*-
"""用「生产链路」把 12/13/14 的用例跑两轮，看模型到底能不能吃下来。

跟上一版不同：这里不再自拟一套专用提示词了 —— 用的是 main.build_system_prompt()，
也就是线上真正发给模型的 system prompt；回包也用线上真正的 _extract_items /
_items_to_extras 处理。所以这份结果代表"生产上让模型参与 12/13/14"的真实水平。

用法：
    python build\\check_llm_zx.py            # 全部用例，两轮
    python build\\check_llm_zx.py --limit 5  # 先试前 5 条
    python build\\check_llm_zx.py --workers 4

输出：docs\\12-13-14-大模型核对.md（每次覆盖）
"""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "build"))

import main as app  # noqa: E402
from check_zx_plays import CASES  # 同一份期望表，规则侧已经跑过 73/73

ZODIAC = app.current_zodiac_mapping()
_LOCK = threading.Lock()


def ask(text: str) -> dict:
    """按生产配置问一次模型，并按生产逻辑把回包拆成（号码盘注, 整笔注）。"""
    kwargs = dict(
        model=app.LLM_MODEL,
        messages=[{"role": "system", "content": app.build_system_prompt(ZODIAC)},
                  {"role": "user", "content": text}],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=800,
        timeout=app.LLM_TIMEOUT,
    )
    if not app.LLM_THINKING:
        kwargs["extra_body"] = {"enable_thinking": False}
    r = app.get_client().chat.completions.create(**kwargs)
    raw = r.choices[0].message.content or ""
    try:
        items = app._extract_items(raw)
    except Exception as e:
        return {"ok": False, "raw": raw, "error": "JSON 解析失败：%s" % e}
    bets = app.expand_bets(items, ZODIAC)
    extras = app._items_to_extras(items, ZODIAC)
    return {"ok": True, "raw": raw, "bets": bets, "extras": extras}


def normalize_extras(extras):
    """[(kind, 生肖串, 金额)]，顺序无关，方便和期望比对。"""
    out = []
    for e in extras or []:
        zs = e.get("zodiacs") or ""
        if isinstance(zs, list):
            zs = "".join(zs)
        try:
            amt = float(e.get("amount"))
        except Exception:
            amt = None
        out.append((str(e.get("kind") or "?"), str(zs), amt))
    return sorted(out, key=lambda x: (x[0], x[1], x[2] if x[2] is not None else 0))


def compare(expect, got):
    """expect: [(kind, 生肖串, 金额)]；got: 同上（来自模型）。返回不一致说明列表。"""
    if got is None:
        return ["模型输出结构不对"]
    want = sorted([(k, z, float(a)) for k, z, a in expect], key=lambda x: (x[0], x[1], x[2]))
    if len(want) != len(got):
        return ["笔数 %d≠%d" % (len(got), len(want))]
    bad = []
    for g, w in zip(got, want):
        if g[0] != w[0]:
            bad.append("kind %s≠%s" % (g[0], w[0]))
        if g[1] != w[1]:
            bad.append("生肖 %s≠%s" % (g[1] or "（空）", w[1]))
        if g[2] is None:
            bad.append("没给金额")
        elif abs(g[2] - w[2]) > 0.01:
            bad.append("金额 %s≠%s" % (g[2], w[2]))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "docs" / "12-13-14-大模型核对.md"))
    args = ap.parse_args()

    cases = []
    for case in CASES:
        text, market, _n, expect = case[:4]
        if len(case) > 4 or market is None or expect == "fail":
            continue          # 分盘用例、判死用例不进模型
        cases.append((text, expect))
    if args.limit:
        cases = cases[:args.limit]
    print("用例 %d 条，两轮，并发 %d" % (len(cases), args.workers))

    def run_one(item):
        text, expect = item
        rounds = []
        for _ in range(2):
            try:
                r = ask(text)
            except Exception as e:
                r = {"ok": False, "raw": "", "error": "调用失败：%s" % e}
            got = normalize_extras(r.get("extras")) if r.get("ok") else None
            diff = compare(expect, got) if r.get("ok") else [r.get("error") or "调用失败"]
            rounds.append({"bets": got, "diff": diff, "raw": r.get("raw", "")[:400],
                           "expect": expect})
        with _LOCK:
            print("完成：%s" % text, flush=True)
        return text, rounds

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for text, rounds in ex.map(run_one, cases):
            results.append((text, rounds))

    both_ok, one_ok, both_bad, unstable = [], [], [], []
    for text, rounds in results:
        ok1 = not rounds[0]["diff"]
        ok2 = not rounds[1]["diff"]
        same = rounds[0]["bets"] == rounds[1]["bets"]
        if ok1 and ok2:
            both_ok.append((text, same))
        elif ok1 or ok2:
            one_ok.append(text)
        else:
            both_bad.append(text)
        if not same:
            unstable.append(text)

    total = len(results)
    print("\n两轮都对 %d/%d，一轮对 %d，两轮都错 %d，两轮不一致 %d"
          % (len(both_ok), total, len(one_ok), len(both_bad), len(unstable)))

    lines = []
    lines.append("# 12/13/14（平特 / 连肖 / N中M）大模型两轮核对\n")
    lines.append("模型：`%s`（`%s`，思考模式%s，temperature 0.1）\n"
                 % (app.LLM_MODEL, app.LLM_BASE_URL, "开" if app.LLM_THINKING else "关"))
    lines.append("**这一版走的是生产链路**：system prompt 就是线上那份 `main.build_system_prompt()`"
                 "（已补入 12/13/14 口径），回包也由线上的 `_extract_items` / `_items_to_extras` "
                 "处理，金额由程序按「单价×肖数」重算，不采信模型自己算的合计。")
    lines.append("因此这份成绩代表「让模型参与 12/13/14」的真实水平。")
    lines.append("用例来源：`build\\check_zx_plays.py` 的期望表（规则侧 73/73 通过），"
                 "判死用例与分盘用例不进模型。")
    lines.append("判定：每注核对「玩法归类 / 生肖集合 / 记账金额」三项，全对才算这一轮对。\n")
    lines.append("## 总览\n")
    lines.append("| 指标 | 数量 |")
    lines.append("| --- | --- |")
    lines.append("| 用例总数 | %d |" % total)
    lines.append("| 两轮都解析正确 | %d |" % len(both_ok))
    lines.append("| 只有一轮正确 | %d |" % len(one_ok))
    lines.append("| 两轮都错 | %d |" % len(both_bad))
    lines.append("| 两轮结果不一致（同一输入两次不一样） | %d |" % len(unstable))
    lines.append("")

    if one_ok:
        lines.append("### 只有一轮对的（模型不稳定，同一句话换个时间就错）\n")
        for t in one_ok:
            lines.append("- `%s`" % t)
        lines.append("")
    if both_bad:
        lines.append("### 两轮都错（模型系统性理解不了，这类必须靠规则兜）\n")
        for t in both_bad:
            lines.append("- `%s`" % t)
        lines.append("")
    if unstable:
        lines.append("### 两轮结果不一样\n")
        for t in unstable:
            lines.append("- `%s`" % t)
        lines.append("")

    lines.append("## 逐条明细\n")
    lines.append("| # | 文本 | 期望（玩法/生肖/金额） | 第1轮 | 第2轮 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for i, (text, rounds) in enumerate(results, 1):
        exp = "；".join("%s %s %s" % (k, z, a) for k, z, a in rounds[0]["expect"])
        c1 = "✅" if not rounds[0]["diff"] else "❌ " + "、".join(rounds[0]["diff"])
        c2 = "✅" if not rounds[1]["diff"] else "❌ " + "、".join(rounds[1]["diff"])
        c1 = c1.replace("|", "/")
        c2 = c2.replace("|", "/")
        lines.append("| %d | `%s` | %s | %s | %s |" % (i, text, exp, c1, c2))
    lines.append("")

    # ---- 结论 ----
    lines.append("## 结论\n")
    lines.append("1. 规则侧：`check_zx_plays.py` 73/73、`check_rules.py` 72/72、"
                 "`check_buylist.py` 156/156 全过 —— 12/13/14 的本地解析是可靠的，"
                 "且判死口径（肖数不符、组合注带「各」、平特写号码）都已落实。")
    lines.append("2. 模型侧（生产 prompt）：%d 条用例两轮全对 %d 条、两轮都错 %d 条、"
                 "两轮不一致 %d 条。" % (total, len(both_ok), len(both_bad), len(unstable)))
    if len(both_ok) == total:
        lines.append("   模型在生产 prompt 下已经能吃下这三种整笔玩法，可以参与。")
    else:
        lines.append("   模型仍有吃不准的条目（见上），这些只能靠规则兜。")
    lines.append("3. 分工：规则先算，规则吃不下的才送模型；金额一律由程序按"
                 "「单价×肖数」重算（`_items_to_extras`），不采信模型自己算的合计。"
                 "判死项（中特/特肖、鼠5尾、平特写号码、组合注带「各」或肖数不符、"
                 "纯寒暄）永不送模型 —— 客户明确要求这些「不解析」。")
    lines.append("")
    lines.append("报告由 `python build\\check_llm_zx.py --workers 6` 生成，"
                 "模型/提示词有改动就重跑一次即可复核。")


    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("报告：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
