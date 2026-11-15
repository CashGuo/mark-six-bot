# -*- coding: utf-8 -*-
"""探针：期次归档 + 只撤本期（重写版，走真实 /wx/hook）。

流程
  1 记一笔 -> 期号落地、账本有余额
  2 手工把期号退回上一期（模拟"隔了几天才来第一单"）
  3 再推一笔 -> 应当自动把那本装满的归档成 issues/<上期>.json 并清零
  4 /api/issues 本期在前、历史在后
  5 撤本期的账 -> 成功，账本只减这一笔
  6 造一条上期的活账 -> 撤销被拒
"""
import hashlib
import hmac
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
if sys.stdout is None:
    sys.stdout = io.StringIO()

BASE = "http://127.0.0.1:8899"
TOK = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\.api_token"
with open(TOK, "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

ACC = "g18959290733"
SECRET = ""
STATE = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state\%s" % ACC
LEDGER = os.path.join(STATE, "numbers_state.json")
MARK = os.path.join(STATE, "current_issue.txt")
ISSUES = os.path.join(STATE, "issues")
RECS = os.path.join(STATE, "records.jsonl")

CUR = "2026-11-14"
PREV = "2026-11-12"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"X-Msb-Token": TOKEN}
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads((e.read() or b"{}").decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def hook_push(text, msg_id, create_time=None):
    ct = int(create_time if create_time is not None else time.time())
    body = json.dumps({
        "account": ACC,
        "messages": [{
            "msg_id": msg_id,
            "type": "文本",
            "content": text,
            "chat_id": "iss-probe-chat",
            "chat_name": "期次探针群",
            "sender": "issprobe",
            "sender_name": "探针",
            "create_time": ct,
        }],
    }, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), ("%s.%s" % (ts, body.decode())).encode(),
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(BASE + "/wx/hook", data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Wx-Timestamp": ts,
        "X-Wx-Signature": sig,
        "X-Wx-Cust": ACC,
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads((e.read() or b"{}").decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def ledger():
    with open(LEDGER, "r", encoding="utf-8") as f:
        return json.load(f)


def marker():
    if not os.path.exists(MARK):
        return ""
    with open(MARK, "r", encoding="utf-8") as f:
        return f.read().strip()


t0 = int(time.time())
CUR = "2026-11-14"
PREV = "2026-11-12"
print("起始 marker =", marker() or "(无)", " 合计 =", round(sum(ledger().values()), 2))

# 0) 把期号摆成上一期，模拟"这台机器上次记的是上期，然后歇了几天"
with open(MARK, "w", encoding="utf-8") as f:
    f.write(PREV + "\n")
print("\n[0] 期号摆成上一期", PREV)

print("\n[1] 推一笔 06 100（应落在 %s）" % PREV)
mid1 = "issp-%d-1" % t0
print("   ->", hook_push("06 100", mid1, create_time=None))
time.sleep(0.6)
print("   marker =", marker(), " 06 =", ledger().get("06"),
      " 合计 =", round(sum(ledger().values()), 2))

print("\n[2] 再推一笔 06 100（今天是 %s，归 %s）" % ("2026-11-13", CUR))
mid2 = "issp-%d-2" % t0
print("   ->", hook_push("06 100", mid2))
time.sleep(0.8)
print("   marker =", marker())
print("   issues =", sorted(os.listdir(ISSUES)) if os.path.isdir(ISSUES) else "(无)")
print("   账本 06 =", ledger().get("06"), " 合计 =", round(sum(ledger().values()), 2))

p = os.path.join(ISSUES, PREV + ".json")
if os.path.exists(p):
    with open(p, "r", encoding="utf-8") as f:
        rec = json.load(f)
    print("   归档 ", os.path.basename(p), " total =", rec["total"],
          " 06 =", rec["numbers"].get("06"), " archived_at =", rec["archived_at"])
else:
    print("   !! 没有生成归档")

print("\n[4] /api/issues")
st, j = api("/api/issues?account=" + ACC)
print("   ", st, json.dumps(
    [{k: x.get(k) for k in ("issue", "total", "current", "count")}
     for x in j.get("issues", [])], ensure_ascii=False))

print("\n[5] 撤本期的账（mid2）")
print("   ->", api("/api/reverse", "POST", {"msg_id": mid2, "note": "期次探针"}))
print("   账本 06 =", ledger().get("06"), " 合计 =", round(sum(ledger().values()), 2))

print("\n[5b] 再撤上期那笔（mid1）-> 应被拒")
print("   ->", api("/api/reverse", "POST", {"msg_id": mid1, "note": "试撤上期"}))

print("\n[6] 造一条上期的活账，试撤销")
fake_id = "issp-old-%d" % t0
fake = {
    "rec_id": fake_id + "#1", "ts": "2026-11-12 20:00:00", "msg_id": fake_id,
    "account": ACC, "chat": "期次探针群", "sender": "issprobe",
    "text": "06 100", "numbers": 1, "total": 100.0, "applied": {"06": 100.0},
    "issue": PREV, "fp": "", "chat_id": "iss-probe-chat",
}
with open(RECS, "a", encoding="utf-8") as f:
    f.write(json.dumps(fake, ensure_ascii=False) + "\n")
print("   ->", api("/api/reverse", "POST", {"msg_id": fake_id, "note": "x"}))

with open(RECS, "r", encoding="utf-8") as f:
    lines = [ln for ln in f if fake_id not in ln]
with open(RECS, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("   假记录已删")

print("\n[7] 收尾状态")
print("   marker =", marker(), " 合计 =", round(sum(ledger().values()), 2))
print("   issues =", sorted(os.listdir(ISSUES)) if os.path.isdir(ISSUES) else "(无)")
