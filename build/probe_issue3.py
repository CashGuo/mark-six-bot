# -*- coding: utf-8 -*-
"""探针：期次（终版），覆盖真实顺序 + 迟到消息。

0 清干净：账本 0、期号 2026-11-14、无归档、无记录
1 推一笔正常 -> 记在 2026-11-14
2 造"上一期末尾有余额"的状态：手工把账本填 300、期号改回 2026-11-12
3 推一笔 -> 自动归档 2026-11-12(total=300) + 清零 + 换到 2026-11-14
4 /api/issues 本期/历史
5 迟到消息（create_time 是 11-10）-> 账本不能倒回去
6 撤本期账 -> 成功
7 手工造本期旧账 -> 撤销被拒（issue 是上期）
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

CUR, PREV = "2026-11-14", "2026-11-12"


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
            "msg_id": msg_id, "type": "文本", "content": text,
            "chat_id": "iss-probe-chat", "chat_name": "期次探针群",
            "sender": "issprobe", "sender_name": "探针", "create_time": ct,
        }],
    }, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), ("%s.%s" % (ts, body.decode())).encode(),
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(BASE + "/wx/hook", data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Wx-Timestamp": ts, "X-Wx-Signature": sig, "X-Wx-Cust": ACC,
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


def save_ledger(d):
    with open(LEDGER + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(LEDGER + ".tmp", LEDGER)


def marker():
    if not os.path.exists(MARK):
        return ""
    with open(MARK, "r", encoding="utf-8") as f:
        return f.read().strip()


def set_marker(v):
    with open(MARK, "w", encoding="utf-8") as f:
        f.write(v + "\n")


t0 = int(time.time())

print("[0] 清干净")
led = ledger()
save_ledger({k: 0.0 for k in led})
set_marker(CUR)
for fn in (os.listdir(ISSUES) if os.path.isdir(ISSUES) else []):
    os.remove(os.path.join(ISSUES, fn))
if os.path.isdir(ISSUES) and not os.listdir(ISSUES):
    os.rmdir(ISSUES)
with open(RECS, "r", encoding="utf-8") as f:
    lines = [ln for ln in f if "issp-" not in ln]
with open(RECS, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("    账本 %s，期号 %s，记录 %d 条" % (
    round(sum(ledger().values()), 2), marker(), len(lines)))

print("\n[1] 推一笔 06 100")
m1 = "issp-%d-1" % t0
print("   ->", hook_push("06 100", m1))
time.sleep(0.6)
print("    期号 =", marker(), " 06 =", ledger().get("06"),
      " 合计 =", round(sum(ledger().values()), 2))

print("\n[2] 造「上一期末尾有余额」：账本塞 300，期号改回 %s" % PREV)
led = ledger()
led["06"] = 100.0
led["18"] = 100.0
led["42"] = 100.0
save_ledger(led)
set_marker(PREV)
print("    账本合计 =", round(sum(ledger().values()), 2), " 期号 =", marker())

print("\n[3] 推一笔 -> 应归档 %s(合计300) 并清零换到 %s" % (PREV, CUR))
m2 = "issp-%d-2" % t0
print("   ->", hook_push("06 100", m2))
time.sleep(0.8)
print("    期号 =", marker())
print("    issues =", sorted(os.listdir(ISSUES)) if os.path.isdir(ISSUES) else "(无)")
print("    账本 06 =", ledger().get("06"), " 合计 =", round(sum(ledger().values()), 2))
p = os.path.join(ISSUES, PREV + ".json")
if os.path.exists(p):
    with open(p, "r", encoding="utf-8") as f:
        rec = json.load(f)
    print("    归档 total =", rec["total"], " 06 =", rec["numbers"].get("06"),
          " 18 =", rec["numbers"].get("18"))

print("\n[4] /api/issues")
st, j = api("/api/issues?account=" + ACC)
print("   ", st, json.dumps([{k: x.get(k) for k in ("issue", "total", "current", "count")}
                             for x in j.get("issues", [])], ensure_ascii=False))

print("\n[5] 迟到消息：create_time 是 11-10（归 %s），账本不能倒回去" % PREV)
m3 = "issp-%d-3" % t0
print("   ->", hook_push("06 100", m3, create_time=t0 - 3 * 86400))
time.sleep(0.6)
print("    期号 =", marker(), "（应仍是 %s）" % CUR)
print("    账本合计 =", round(sum(ledger().values()), 2),
      " 06 =", ledger().get("06"))

print("\n[6] 撤本期的账 m2")
print("   ->", api("/api/reverse", "POST", {"msg_id": m2, "note": "期次探针"}))
print("    账本 06 =", ledger().get("06"), " 合计 =", round(sum(ledger().values()), 2))

print("\n[7] 造一条 %s 的活账，试撤销" % PREV)
fid = "issp-old-%d" % t0
fake = {"rec_id": fid + "#1", "ts": PREV + " 20:00:00", "msg_id": fid,
        "account": ACC, "text": "06 100", "numbers": 1, "total": 100.0,
        "applied": {"06": 100.0}, "issue": PREV}
with open(RECS, "a", encoding="utf-8") as f:
    f.write(json.dumps(fake, ensure_ascii=False) + "\n")
print("   ->", api("/api/reverse", "POST", {"msg_id": fid, "note": "x"}))
with open(RECS, "r", encoding="utf-8") as f:
    keep = [ln for ln in f if fid not in ln]
with open(RECS, "w", encoding="utf-8") as f:
    f.writelines(keep)
print("    假记录已删")

print("\n[8] 收尾")
print("    期号 =", marker(), " 合计 =", round(sum(ledger().values()), 2))
print("    issues =", sorted(os.listdir(ISSUES)) if os.path.isdir(ISSUES) else "(无)")
