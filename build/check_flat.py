# -*- coding: utf-8 -*-
"""验 12/13/14：只记总额的玩法（平特整肖、二中二）不进号码盘，
但总金额必须把它们算上；撤销也要能把这部分扣回去。

跑之前服务端要开着（8899）。跑完自动删掉 selftest 账本。
"""

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(os.environ["LOCALAPPDATA"]) / "MarkSixBot" / "data"
STATE = DATA / "state" / "selftest"
TOK = (DATA / ".api_token").read_text(encoding="utf-8").strip()
URL = "http://127.0.0.1:8899"
ACC = "selftest"


def post(path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Msb-Token", TOK)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8") or "{}")


def load(name):
    p = STATE / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


if STATE.exists():
    shutil.rmtree(STATE)

print("=== 记账 ===")
for t in ["平特鼠100", "二连肖鼠牛各20", "二中二 5 17 各20", "鼠50"]:
    d = post("/api/quick_bet", {"text": t, "account": ACC})
    if d.get("ok"):
        print("%-18s 合计 %-8s 不计号 %-6s 号数 %s | %s | 累计 %s"
              % (t, d.get("total"), d.get("flat_total"), d.get("numbers"),
                 d.get("detail"), d.get("grand_total")))
    else:
        print("%-18s 没记上 %s" % (t, d.get("reason") or d.get("error")))

nums = load("numbers_state.json")
others = load("others_state.json")
board = round(sum(float(v or 0) for v in nums.values()), 2)
flat = round(float(others.get("total") or 0), 2)
print("\n=== 账本 ===")
print("号码盘合计 %s ｜ 不计号 %s ｜ 应付总金额 %s" % (board, flat, round(board + flat, 2)))
print("号码盘明细：%s" % {k: v for k, v in nums.items() if v})
print("不计号明细：%s" % [(x.get("play"), x.get("amount"))
                         for x in (others.get("items") or [])])

# 期望（2026 马年：马 5 个号，其余生肖 4 个号）
#   号盘     = 鼠50 的 4 号各 50 → 200（连肖改成整笔后不再落盘）
#   不计号   = 平特 100 + 二连肖 鼠20+牛20=40 + 二中二 20 → 160
#   合计     = 360
exp_board, exp_flat = 200.0, 160.0
good = abs(board - exp_board) < 0.01 and abs(flat - exp_flat) < 0.01

print("\n=== 撤销二中的那一笔 ===")
recs = []
p = STATE / "records.jsonl"
if p.exists():
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
target = next((r for r in recs if r.get("extras")), None)
if target:
    d = post("/api/reverse", {"msg_id": target.get("msg_id"), "note": "自测撤销"})
    print("撤销 %s → ok=%s 累计 %s" % (target.get("msg_id"), d.get("ok"), d.get("grand_total")))
    others2 = load("others_state.json")
    flat2 = round(float(others2.get("total") or 0), 2)
    # 第一条带 extras 的是「平特鼠100」，撤掉它 → 160 - 100 = 60
    print("撤销后不计号 %s（应为 60）" % flat2)
    good = good and abs(flat2 - 60.0) < 0.01 and d.get("ok")
else:
    good = False
    print("流水里找不到带 extras 的记录")

shutil.rmtree(STATE, ignore_errors=True)
print("\n%s，selftest 账本已删" % ("通过" if good else "失败"))
sys.exit(0 if good else 1)
