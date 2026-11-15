# -*- coding: utf-8 -*-
"""给截图备两行：一笔活账（可撤销），一笔已撤销。"""
import sys
import time

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
import io

if sys.stdout is None:
    sys.stdout = io.StringIO()

from probe_reverse import post, post_bet  # noqa: E402

mid_a = "shot-live-%d" % int(time.time())
print("活账 =", post_bet("06 100", mid_a))

time.sleep(0.4)
mid_b = "shot-revd-%d" % int(time.time())
print("已撤销那笔 =", post_bet("18 100", mid_b))
code, r = post("/api/reverse", {"msg_id": mid_b, "note": "截图准备"})
print("撤掉它 =", code, r.get("total"))
print("LIVE_MID=" + mid_a)
