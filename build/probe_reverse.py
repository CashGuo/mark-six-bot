# -*- coding: utf-8 -*-
"""探针：完整验证一笔账的 记账 → 明细落盘 → 撤销冲正 → 幂等拒绝。

会真的往账本写一笔再撤掉，两头金额必须回到原值。
"""
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
import io

if sys.stdout is None:
    sys.stdout = io.StringIO()

import main as M  # noqa: E402

BASE = "http://127.0.0.1:8899"
REAL = ""
WID = "g18959290733"
ACC = "g18959290733"
NUM = "06"


def post_bet(text, msg_id):
    ts = int(time.time())
    payload = {"batch_id": "probe-rev-%d" % ts, "ts": ts, "account": WID, "count": 1,
               "messages": [{"msg_id": msg_id, "msg_type": "文本", "content": text,
                             "chat_id": "21203160099@chatroom",
                             "chat_name": "滨海大酒店营销福利总群",
                             "sender": "wxid_probe", "sender_name": "探针",
                             "create_time": ts, "account": WID}]}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    s = str(ts)
    sig = hmac.new(REAL.encode(), (s + "." + body).encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(BASE + "/wx/hook", data=body.encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Wx-Timestamp": s, "X-Wx-Signature": sig,
                                          "X-Wx-Cust": WID})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode())


def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads((e.read() or b"{}").decode())


def main():
    before = round(float(M.load_data(ACC).get(NUM, 0) or 0), 2)
    print("撤销前 %s = %.2f" % (NUM, before))

    mid = "probe-rev-%d" % int(time.time())
    r = post_bet("06 100", mid)
    print("推送结果 =", r)

    after = round(float(M.load_data(ACC).get(NUM, 0) or 0), 2)
    print("记账后 %s = %.2f   增量 = %+.2f" % (NUM, after, after - before))

    one = get("/api/record?msg_id=" + mid)
    print("明细 total =", one.get("total"), " applied =", one.get("applied"))
    print("有明细 =", one.get("has_detail"), " 已撤销 =", one.get("reversed"),
          " 期次 =", one.get("issue"))

    code, rev = post("/api/reverse", {"msg_id": mid, "note": "探针撤销"})
    print("撤销 ->", code, rev)

    back = round(float(M.load_data(ACC).get(NUM, 0) or 0), 2)
    print("撤销后 %s = %.2f   与撤销前一致 = %s" % (NUM, back, back == before))

    code2, rev2 = post("/api/reverse", {"msg_id": mid, "note": "再撤一次"})
    print("重复撤销 ->", code2, rev2.get("error"))

    print("已撤销标记 =", get("/api/record?msg_id=" + mid).get("reversed"))
    print("消息号还在 processed 吗 =", M.is_message_processed(mid))

    rc = get("/api/recalc?account=" + ACC)
    print("重算对账 -> match=%s 流水合计=%.2f 快照合计=%.2f 差=%.2f 无明细条数=%d"
          % (rc.get("match"), rc.get("records_total"), rc.get("state_total"),
             rc.get("diff"), rc.get("records_without_detail")))


if __name__ == "__main__":
    main()
