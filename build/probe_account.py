# -*- coding: utf-8 -*-
"""验证「账本目录不许劈成两个账号」这条修复。

两件事：
1. 报文里带 account=微信号 → 账落在 state\\<微信号>；
2. 报文里不带 account（老采集端）→ 也应该落在同一个微信号目录（header 兜底）。
只要 state 下不冒出 wxid_ 开头的目录，就是对的。
"""
import hashlib
import hmac
import io
import json
import os
import sys
import time
import urllib.request

if sys.stdout is None:
    sys.stdout = io.StringIO()

BASE = "http://127.0.0.1:8899"
SECRET = ""
CUST = "g18959290733"
STATE = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state"


def post(payload):
    body = json.dumps(payload, ensure_ascii=False)
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), ("%s.%s" % (ts, body)).encode(),
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        BASE + "/wx/hook", data=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Wx-Timestamp": ts, "X-Wx-Signature": sig,
                 "X-Wx-Cust": CUST},
        method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def mk(tag, content):
    ts = int(time.time() * 1000)
    return {"msg_id": "acc-%s-%d" % (tag, ts), "type": "文本", "content": content,
            "chat_id": "filehelper", "chat_name": "文件传输助手",
            "sender": "wxid_somebody", "sender_name": "某人",
            "create_time": time.strftime("%Y-%m-%d %H:%M:%S")}


def dirs():
    try:
        return sorted(d for d in os.listdir(STATE)
                      if os.path.isdir(os.path.join(STATE, d)))
    except Exception:
        return []


def main():
    print("跑之前 state 下目录：%s" % dirs())

    # 1. 新采集端：报文里带微信号
    m1 = mk("new", "鼠30")
    m1["account"] = CUST
    code, resp = post({"account": CUST, "messages": [m1]})
    print("新报文 HTTP %s account=%r replies=%s"
          % (code, resp.get("account"), json.dumps(resp.get("replies"), ensure_ascii=False)))

    # 2. 老采集端：报文里没有 account，只有 header
    m2 = mk("legacy", "鼠30")
    code, resp = post({"account": CUST, "messages": [m2]})
    print("老报文 HTTP %s account=%r" % (code, resp.get("account")))

    print("跑之后 state 下目录：%s" % dirs())
    for d in dirs():
        p = os.path.join(STATE, d, "records.jsonl")
        if not os.path.exists(p):
            continue
        n = 0
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
        print("  %-24s records=%d" % (d, n))


if __name__ == "__main__":
    main()
