# -*- coding: utf-8 -*-
"""探针：推几条不同类型的消息，验证服务端消息流水落盘与结果分类。"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8899/wx/hook"
REAL = ""
WID = "g18959290733"
TS = int(time.time())

msgs = [
    # 1) 图片：非文本，应跳过
    {"msg_id": "probe-img-%d" % TS, "msg_type": "图片", "content": "[图片]",
     "chat_id": "21203160099@chatroom", "chat_name": "滨海大酒店营销福利总群",
     "sender": "wxid_a", "sender_name": "张三", "create_time": TS, "account": WID},
    # 2) 占位符：没内容，应跳过
    {"msg_id": "probe-ph-%d" % TS, "msg_type": "文本", "content": "[探针]",
     "chat_id": "21203160099@chatroom", "chat_name": "滨海大酒店营销福利总群",
     "sender": "wxid_a", "sender_name": "张三", "create_time": TS, "account": WID},
    # 3) 缺 msg_id：应跳过
    {"msg_id": "", "msg_type": "文本", "content": "这条没有消息号看服务端怎么办",
     "chat_id": "21203160099@chatroom", "chat_name": "滨海大酒店营销福利总群",
     "sender": "wxid_b", "sender_name": "李四", "create_time": TS, "account": WID},
    # 4) 正常文本但不像下注：会被 AI 判空 -> 未解析
    {"msg_id": "probe-txt-%d" % TS, "msg_type": "文本",
     "content": "今天天气不错，出来散步的人挺多的",
     "chat_id": "21203160099@chatroom", "chat_name": "滨海大酒店营销福利总群",
     "sender": "wxid_c", "sender_name": "王五", "create_time": TS, "account": WID},
]

payload = {"batch_id": "probe-msglist-%d" % TS, "ts": TS, "account": WID,
           "count": len(msgs), "messages": msgs}
body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
ts_s = str(TS)
sig = hmac.new(REAL.encode(), (ts_s + "." + body).encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request(URL, data=body.encode("utf-8"), method="POST", headers={
    "Content-Type": "application/json; charset=utf-8",
    "X-Wx-Timestamp": ts_s, "X-Wx-Signature": sig, "X-Wx-Cust": WID})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print("推送 ->", r.status, (r.read() or b"").decode()[:200])
except urllib.error.HTTPError as e:
    print("推送失败 ->", e.code, (e.read() or b"").decode()[:200])

time.sleep(1)
with urllib.request.urlopen("http://127.0.0.1:8899/api/messages?limit=10", timeout=5) as r:
    j = json.loads(r.read().decode("utf-8", "replace"))
print("流水条数 =", j.get("count"), " 文件数 =", j.get("files"))
for m in j.get("messages") or []:
    print("  %s | %-12s | %-9s | %s | %s" % (
        m.get("ts", "")[11:], m.get("account") or m.get("wechat"),
        m.get("result"), m.get("reason"), (m.get("content") or "")[:26]))
