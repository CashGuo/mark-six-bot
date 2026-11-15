# -*- coding: utf-8 -*-
"""把 docs\\买法示例.txt 里的每条示例跑一遍规则解析，比对人写的「结果」。

用法：
    python build\\check_buylist.py

文件格式（每行一条）：
    1、单 100，结果：单数 25 个号各 100，共 2500
    66、鼠5尾，结果：❌ 判死…      <- 带 ❌ 的表示"应当解析失败"
    70、红波 鼠各10，结果：⚠ …     <- 带 ⚠ 的表示"当前行为可能不合客户预期"，只展示
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bet_rules


def zodiac_table():
    """当年生肖表。直接问 main.py，保证和账本用的是同一份。"""
    from main import get_current_lunar_year
    _, cur, mapping = get_current_lunar_year()
    return cur, {k: list(v) for k, v in mapping.items()}


def cases_from_file(path: Path):
    """只认以「编号、」开头的行；标题、说明、分隔线都跳过。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        m = re.match(r"^(\d+)[、.]\s*(.+)$", s)
        if not m:
            continue
        body = m.group(2)
        if "结果" in body:
            text = body.split("结果")[0]
        else:
            text = body
        text = text.strip().rstrip("，,：:").strip()
        if text:
            out.append((text, body))
    return out


def describe(r):
    if not r.get("ok"):
        return "✗ %s%s" % (r.get("reason") or "", "（判死，不送模型）" if r.get("fatal") else "")
    seg = []
    for it in r.get("items") or []:
        seg.append("%s%s 各%s 共%s（%d 个号）"
                   % (it["desc"] + " " if it["desc"] else "", it["nums"],
                      it["unit"], it["amount"], it["count"]))
    for e in r.get("extras") or []:
        seg.append("%s 整笔 %s（不进号码盘）" % (e["play"], e["amount"]))
    total = sum(it["amount"] for it in r.get("items") or []) + sum(
        e["amount"] for e in r.get("extras") or [])
    return "✓ [%s] %s｜合计 %s｜%d 个号" % (
        r.get("market"), "；".join(seg), total, len(r.get("bets") or {}))


def main():
    zodiac = zodiac_table()[1]
    print("当年生肖：%s\n" % zodiac_table()[0])
    path = ROOT / "docs" / "买法示例.txt"
    cases = cases_from_file(path)
    bad = []
    for text, body in cases:
        res = describe(bet_rules.parse_local(text, zodiac))
        expect_fail = "❌" in body
        note = ""
        if expect_fail and not res.startswith("✗"):
            note = "   <<< 期望失败但解析成功了"
            bad.append(text)
        if not expect_fail and res.startswith("✗"):
            note = "   <<< 期望能解析但失败了"
            bad.append(text)
        print("%-24s → %s%s" % (text, res, note))
    print("\n共 %d 条，不符 %d 条%s"
          % (len(cases), len(bad), ("：" + "、".join(bad)) if bad else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
