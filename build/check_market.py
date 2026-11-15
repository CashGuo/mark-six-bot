# -*- coding: utf-8 -*-
"""验港澳分盘：同一句「鼠50」，标香港的进香港账，没标的进澳门账，
两本的号码盘、不计号、合计各自独立；撤销只动它那一本。

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


def get(path):
    req = urllib.request.Request(URL + path)
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

CASES = [("鼠50", "MO"), ("香港 鼠50", "HK"), ("香港平特鼠100", "HK"),
         ("澳门 二中二 5 17 各20", "MO")]
print("=== 记账 ===")
for t, want_mk in CASES:
    d = post("/api/quick_bet", {"text": t, "account": ACC})
    print("%-22s 市场 %-3s 合计 %-7s | %s"
          % (t, d.get("market"), d.get("total"), d.get("detail") if d.get("ok") else d.get("reason")))

mo = load("numbers_state.json")
hk = load("numbers_state_hk.json")
mo_o = load("others_state.json")
hk_o = load("others_state_hk.json")
mo_board = round(sum(float(v or 0) for v in mo.values()), 2)
hk_board = round(sum(float(v or 0) for v in hk.values()), 2)
mo_flat = round(float(mo_o.get("total") or 0), 2)
hk_flat = round(float(hk_o.get("total") or 0), 2)

print("\n=== 账本 ===")
print("澳门：号盘 %s + 不计号 %s = %s" % (mo_board, mo_flat, round(mo_board + mo_flat, 2)))
print("香港：号盘 %s + 不计号 %s = %s" % (hk_board, hk_flat, round(hk_board + hk_flat, 2)))
print("合计：%s" % round(mo_board + hk_board + mo_flat + hk_flat, 2))

ov = get("/api/dashboard?account=%s&market=ALL" % ACC)
print("接口 markets：%s" % json.dumps(ov.get("markets") or {}, ensure_ascii=False))

# 期望：澳 200 + 20 = 220；港 200 + 100 = 300；合计 520
exp = {"MO": 220.0, "HK": 300.0, "ALL": 520.0}
got = {m: float(((ov.get("markets") or {}).get(m) or {}).get("total") or 0)
       for m in ("MO", "HK", "ALL")}
good = all(abs(got[m] - exp[m]) < 0.01 for m in exp)
print("接口合计 %s（期望 %s）" % (got, exp))

print("\n=== 撤销一笔香港的（香港鼠50）===")
recs = []
p = STATE / "records.jsonl"
if p.exists():
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
target = next((r for r in recs if r.get("market") == "HK" and not r.get("extras")), None)
if target:
    d = post("/api/reverse", {"msg_id": target.get("msg_id"), "note": "自测撤销"})
    hk2 = round(sum(float(v or 0) for v in load("numbers_state_hk.json").values()), 2)
    print("撤销 %s → ok=%s 港盘现为 %s（应为 0）" % (target.get("msg_id"), d.get("ok"), hk2))
    good = good and bool(d.get("ok")) and abs(hk2) < 0.01
else:
    good = False
    print("找不到香港那笔")

shutil.rmtree(STATE, ignore_errors=True)
print("\n%s，selftest 账本已删" % ("通过" if good else "失败"))
sys.exit(0 if good else 1)
