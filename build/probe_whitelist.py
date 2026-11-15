# -*- coding: utf-8 -*-
"""验签四态实测：名单内对、名单内错、名单外、没带微信号。只用标准库。"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8899/wx/hook"
REAL = ""
FAKE = "0" * 64


def post(wechat, secret):
    payload = {"batch_id": "probe", "ts": int(time.time()),
               "account": wechat or "", "count": 0, "messages": []}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"),
                   (ts + "." + body).encode("utf-8"), hashlib.sha256).hexdigest()
    h = {"Content-Type": "application/json", "X-Wx-Timestamp": ts,
         "X-Wx-Signature": sig}
    if wechat is not None:
        h["X-Wx-Cust"] = wechat
    req = urllib.request.Request(URL, data=body.encode("utf-8"), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, (r.read() or b"").decode("utf-8", "replace")[:90]
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")[:90]
    except Exception as e:
        return -1, str(e)[:90]


cases = [
    ("名单内 + 正确密钥", "g18959290733", REAL),
    ("名单内 + 错误密钥", "g18959290733", FAKE),
    ("名单外 + 正确密钥", "someone_else", REAL),
    ("没带微信号 + 正确密钥", None, REAL),
]

for name, wid, sec in cases:
    code, txt = post(wid, sec)
    print("%-22s -> %s  %s" % (name, code, txt))
