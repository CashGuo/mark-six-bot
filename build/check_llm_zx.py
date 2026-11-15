# -*- coding: utf-8 -*-
"""用大模型把 12/13/14 的用例跑两轮，核对它的解析结果。

用途：确认「12/13/14 这套整笔玩法能不能交给模型」。
现在系统里 12/13/14 走的是本地规则、判死不送模型（main.py can_llm=False），
本脚本就是拿真实用例去问模型，看它到底行不行。

用法：
    python build\\check_llm_zx.py            # 全部用例，两轮
    python build\\check_llm_zx.py --limit 5  # 先试前 5 条
    python build\\check_llm_zx.py --workers 4

输出：docs\\12-13-14-大模型核对.md（每次覆盖）
"""

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "build"))

import main as app  # noqa: E402
from check_zx_plays import CASES  # 同一份期望表，规则侧已经跑过 65/65

PROMPT = """你是六合彩（1-49）下注记账解析器。把用户这一条下注消息解析成 JSON，只输出 JSON，不要解释。

只处理这三种「整笔玩法」，别的都不用管：
1) 平特（也叫平码/平肖）：写几个生肖，就是这几个生肖「一共」多少钱；
   只有写「各」才是每个生肖各多少（两肖各100 = 200）。
2) 连肖：从写的生肖开始，按 鼠牛虎兔龙蛇马羊猴鸡狗猪 的顺序往后连 N 个生肖，
   不够 N 个自动补；位数可写「二连肖」或「2连肖」，不写位数按二连；
   只有写「各」才是每个生肖各多少。
3) N中M（二中二、三中二、三中三、四中四、五中三、六中六……都算）：
   写几个生肖就是这几个生肖的组合注，整注一笔；带「各」才是每个生肖各多少。

共同点：这三种都是整笔金额，不要摊到号码上，不要输出号码。
如果一条消息里还夹着普通生肖注或号码注（例如「平特鼠100 鼠50」里的「鼠50」），
那些不用管，只输出这三种整笔玩法的那几注。

输出格式：
{"bets":[{"play":"原文里的玩法名","kind":"pingte|lianxiao|zhong","zodiacs":["鼠","牛"],"each":true,"amount":100,"total":200}]}
字段说明：
  kind     ：pingte=平特，lianxiao=连肖，zhong=N中M
  zodiacs  ：这注涉及的生肖（连肖要写出补位后的完整生肖，按顺序）
  each     ：是否写了「各」
  amount   ：每个生肖各多少（没写「各」时就是整笔金额）
  total    ：这注实际要记的钱（带「各」= amount × 生肖个数）
一条消息里有多注就输出多个 bets。

示例：
平特鼠牛100 → {"bets":[{"play":"平特鼠牛","kind":"pingte","zodiacs":["鼠","牛"],"each":false,"amount":100,"total":100}]}
平特鼠牛各100 → {"bets":[{"play":"平特鼠牛","kind":"pingte","zodiacs":["鼠","牛"],"each":true,"amount":100,"total":200}]}
鼠3连肖300 → {"bets":[{"play":"鼠3连肖","kind":"lianxiao","zodiacs":["鼠","牛","虎"],"each":false,"amount":300,"total":300}]}
鼠牛虎三中三200 → {"bets":[{"play":"鼠牛虎三中三","kind":"zhong","zodiacs":["鼠","牛","虎"],"each":false,"amount":200,"total":200}]}"""

_LOCK = threading.Lock()


def ask(text: str) -> dict:
    kwargs = dict(
        model=app.LLM_MODEL,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": text}],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=600,
        timeout=app.LLM_TIMEOUT,
    )
    if not app.LLM_THINKING:
        kwargs["extra_body"] = {"enable_thinking": False}
    r = app.get_client().chat.completions.create(**kwargs)
    raw = r.choices[0].message.content or ""
    try:
        return {"ok": True, "raw": raw, "data": json.loads(raw)}
    except Exception as e:
        return {"ok": False, "raw": raw, "error": "JSON 解析失败：%s" % e}


def normalize_bets(data):
    """把模型输出归一化成 [(kind, 生肖串, total)]，顺序无关。"""
    if not isinstance(data, dict):
        return None
    bets = data.get("bets") if isinstance(data.get("bets"), list) else data.get("items")
    if not isinstance(bets, list):
        return None
    out = []
    for b in bets:
        if not isinstance(b, dict):
            return None
        kind = str(b.get("kind") or "").strip().lower()
        if kind not in ("pingte", "lianxiao", "zhong"):
            # 模型可能只写 play，从 play 里猜
            play = str(b.get("play") or "")
            if "平特" in play or "平码" in play or "平肖" in play:
                kind = "pingte"
            elif "连肖" in play:
                kind = "lianxiao"
            elif re.search(app_r_zhong(), play):
                kind = "zhong"
            else:
                kind = "?" + play
        zs = b.get("zodiacs")
        if isinstance(zs, str):
            zs = list(zs)
        zs = "".join(str(x).strip() for x in (zs or []))
        try:
            total = float(b.get("total"))
        except Exception:
            try:
                total = float(b.get("amount"))
            except Exception:
                total = None
        out.append((kind, zs, total))
    return sorted(out, key=lambda x: (x[0], x[1], x[2] if x[2] is not None else 0))


def app_r_zhong():
    import bet_rules
    return bet_rules._ZHONG_WORDS


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
            bad.append("没给 total")
        elif abs(g[2] - w[2]) > 0.01:
            bad.append("金额 %s≠%s" % (g[2], w[2]))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "docs" / "12-13-14-大模型核对.md"))
    args = ap.parse_args()

    cases = [(t, e) for t, _m, _n, e in CASES if e != "fail"]
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
            got = normalize_bets(r.get("data")) if r.get("ok") else None
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

    # ---- 统计 ----
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

    # ---- 写报告 ----
    lines = []
    lines.append("# 12/13/14（平特 / 连肖 / N中M）大模型两轮核对\n")
    lines.append("模型：`%s`（`%s`，思考模式%s，temperature 0.1）\n"
                 % (app.LLM_MODEL, app.LLM_BASE_URL, "开" if app.LLM_THINKING else "关"))
    lines.append("用例来源：`build\\check_zx_plays.py` 的期望表（规则侧 65/65 通过）。")
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
        lines.append("### 只有一轮对的（说明模型不稳定，同一句话换个时间就错）\n")
        for t in one_ok:
            lines.append("- `%s`" % t)
        lines.append("")
    if both_bad:
        lines.append("### 两轮都错的（模型系统性理解不了）\n")
        for t in both_bad:
            lines.append("- `%s`" % t)
        lines.append("")
    if unstable:
        lines.append("### 两轮结果不一样的\n")
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

    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("报告：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
