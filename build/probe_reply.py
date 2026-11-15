# -*- coding: utf-8 -*-
"""验证 /wx/hook 响应体里带 replies（记账成功后给客户的回复文案）。

只打接口，不碰微信、不发消息 —— 发不发由采集端决定。
"""
import hashlib
import hmac
import io
import json
import sys
import time
import urllib.request

sys.stdout = sys.stdout or io.StringIO()
if sys.stdout is None:
    sys.stdout = io.StringIO()

BASE = "http://127.0.0.1:8899"
SECRET = ""
CUST = "g18959290733"


def post(path, payload):
    body = json.dumps(payload, ensure_ascii=False)
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), ("%s.%s" % (ts, body)).encode(),
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        BASE + path, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Wx-Timestamp": ts, "X-Wx-Signature": sig,
                 "X-Wx-Cust": CUST},
        method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def mk(content, chat="Cash很👌🏻", chat_id="wxid_cash_test"):
    ts = int(time.time() * 1000)
    return {"msg_id": "probe-%d" % ts, "type": "文本", "content": content,
            "chat_id": chat_id, "chat_name": chat,
            "sender": "wxid_cash_test", "sender_name": "Cash",
            "create_time": time.strftime("%Y-%m-%d %H:%M:%S")}


def main():
    cases = ["牛马各100", "牛100", "这是一句普通聊天，不是下注"]
    for c in cases:
        try:
            code, resp = post("/wx/hook", {"account": CUST, "messages": [mk(c)]})
        except Exception as e:
            print("!! %r" % (e,))
            continue
        print("=" * 60)
        print("发送: %s" % c)
        print("HTTP %s" % code)
        print("响应: %s" % json.dumps(resp, ensure_ascii=False))
        rp = resp.get("replies")
        if rp:
            for one in rp:
                print("  → 回复 %r 到 %r" % (one.get("text"), one.get("chat")))
        else:
            print("  → 无 replies（跳过或未解析，按设计不该回）")


if __name__ == "__main__":
    main()
