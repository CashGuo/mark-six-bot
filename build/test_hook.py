# -*- coding: utf-8 -*-
"""端到端验一遍规则解析：按真实报文格式 POST /wx/hook，看服务端记账结果和回复文案。

用独立的 selftest 账号，不碰真实账本。
"""

import hashlib
import hmac
import json
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
from msb_paths import CONFIG_PATH

cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
# 白名单是唯一可信来源，用名单里那个微信号和它的密钥来签
WID, secret = "g18959290733", ""
wl_path = CONFIG_PATH.parent / "wx_clients.json"
if wl_path.exists():
    wl = json.loads(wl_path.read_text(encoding="utf-8"))
    for c in wl.get("clients") or []:
        if c.get("enabled", True):
            WID, secret = c["wechat_id"], c["secret"]
            break
if not secret:
    secret = cfg.get("wx_push_secret", "")

CASES = [
    "鼠50",
    "牛马各10",
    "红单绿双各10",
    "1*13*34*7各10",
    "5尾各20 猴各25",
    "鼠5尾各10",
    "平特鼠100",
    "今天天气不错",
]

url = "http://127.0.0.1:8899/wx/hook"
for i, content in enumerate(CASES):
    ts = int(time.time())
    payload = {
        "batch_id": "%d-%d" % (ts, i),
        "ts": ts,
        "account": "selftest",
        "count": 1,
        "messages": [{
            "msg_id": "rules-test-%d-%d" % (ts, i),
            "target_id": "selftest@chatroom",
            "chat_name": "规则测试",
            "sender": 1,
            "msg_type": "文本",
            "create_time": ts * 1000,
            "content": content,
            "account": "selftest",
            "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), ("%d.%s" % (ts, body.decode("utf-8"))).encode("utf-8"),
                   hashlib.sha256).hexdigest()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Wx-Timestamp", str(ts))
    req.add_header("X-Wx-Signature", sig)
    req.add_header("X-Wx-Cust", WID)

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("%-18s 请求失败：%s" % (content, e))
        continue

    results = resp.get("results") or []
    replies = resp.get("replies") or []
    one = results[0] if results else {}
    text = replies[0].get("text") if replies else ""
    print("%-18s ok=%-5s source=%-4s %s" % (
        content, one.get("ok"), one.get("source"), text or one.get("reason", "")))
    time.sleep(0.3)
