# -*- coding: utf-8 -*-
"""停用态实测：把号停用后，用正确密钥推应被拒；再启用应恢复。"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

P = r"D:\Users\Administrator\Python\mark-six-bot\config\wx_clients.json"
URL = "http://127.0.0.1:8899/wx/hook"
REAL = ""
WID = "g18959290733"


def set_enabled(v):
    with open(P, "r", encoding="utf-8-sig") as f:
        d = json.load(f)
    for x in d["clients"]:
        if x["wechat_id"] == WID:
            x["enabled"] = v
    with open(P + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(P + ".tmp", P)


def post():
    payload = {"batch_id": "probe2", "ts": int(time.time()),
               "account": WID, "count": 0, "messages": []}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()))
    sig = hmac.new(REAL.encode(), (ts + "." + body).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(URL, data=body.encode(), method="POST", headers={
        "Content-Type": "application/json", "X-Wx-Timestamp": ts,
        "X-Wx-Signature": sig, "X-Wx-Cust": WID})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, (r.read() or b"").decode()[:80]
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode()[:80]


set_enabled(False)
print("停用后推送 ->", post())
set_enabled(True)
print("启用后推送 ->", post())
