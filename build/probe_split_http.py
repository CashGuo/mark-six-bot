# -*- coding: utf-8 -*-
"""探针：走真实 /wx/hook 链路验证「生肖总额分摊」。

牛马各100 → 牛 4 个号各 25、马 5 个号各 20，合计 200
马鼠鸡各号15 → 马 5 号各 3、鼠 4 号各 3.75、鸡 4 号各 3.75，合计 45
波色/头组不动，维持每号各多少
"""
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.stdout = sys.stdout or io.StringIO()
sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")

BASE = "http://127.0.0.1:8899"
TOK = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\.api_token"
with open(TOK, "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

ACC = "g18959290733"
SECRET = ""
STATE = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state\%s" % ACC
LEDGER = os.path.join(STATE, "numbers_state.json")


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


def push(text, msg_id):
    ct = int(time.time())
    body = json.dumps({
        "account": ACC,
        "messages": [{
            "msg_id": msg_id, "type": "文本", "content": text,
            "chat_id": "split-probe-chat", "chat_name": "分摊探针群",
            "sender": "splitprobe", "sender_name": "探针", "create_time": ct,
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
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads((e.read() or b"{}").decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def ledger():
    with open(LEDGER, "r", encoding="utf-8") as f:
        return json.load(f)


def show(tag, data):
    live = {k: v for k, v in data.items() if v}
    print("   %-10s 合计 %.2f   有注号码 %d 个  %s"
          % (tag, sum(data.values()), len(live),
             ", ".join("%s=%g" % (k, round(v, 2)) for k, v in sorted(live.items()))))


print("[0] 清起始")
if os.path.isdir(STATE):
    shutil.rmtree(STATE)
print("   已删 %s" % STATE)

print("\n[1] 真实 LLM 解析 + 记账：牛马各100")
st, body = push("牛马各100", "split-1")
print("   -> %s %s" % (st, body))
d = ledger()
show("账本", d)
expect = {}
for n in ["06", "18", "30", "42"]:
    expect[n] = 25.0
for n in ["01", "13", "25", "37", "49"]:
    expect[n] = 20.0
bad = [(k, d.get(k), v) for k, v in expect.items() if abs(float(d.get(k) or 0) - v) > 0.001]
print("   牛4号各25 + 马5号各20 = 200 ：%s" % ("对" if not bad else "错 %s" % bad))
tot1 = sum(d.values())
print("   合计 200：%s" % ("对" if abs(tot1 - 200) < 0.01 else ("错 %.2f" % tot1)))

print("\n[2] 再推一条青蛇各50（换个生肖，验证同样摊）")
st, body = push("青蛇各50", "split-2")
print("   -> %s %s" % (st, body))
d = ledger()
show("账本", d)

print("\n[3] 撤销第一笔")
msgs = api("/api/messages?limit=5")[1]
rec = msgs.get("messages") or []
for m in rec[:3]:
    print("   msg_id=%s 结果=%s %s" % (m.get("msg_id"), m.get("result"), m.get("reason")))
st, body = api("/api/reverse", "POST", {"msg_id": "split-1"})
print("   撤销 -> %s %s" % (st, body))
d = ledger()
show("账本", d)
print("   撤销后只剩青蛇：%s" % ("对" if abs(sum(d.values()) - 50) < 0.01 else "错 %.2f" % sum(d.values())))

print("\n[4] 对账")
st, body = api("/api/recalc?account=%s" % ACC)
print("   /api/recalc -> %s %s" % (st, body))

print("\n[5] 收尾")
if os.path.isdir(STATE):
    shutil.rmtree(STATE)
print("   已清 %s" % STATE)
