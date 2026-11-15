# -*- coding: utf-8 -*-
"""探针：撤销的三个边界。

1) 一笔命中多个号码时，撤销要把每个号都减回去，账本总额精确归零
2) 老记录（没存 applied）必须拒绝，不许拿 top 猜着减
3) 撤销后同一条消息重推，要能重新入账（这是"记错了重发一遍"的核心场景）
"""
import json
import sys
import time

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
import io

if sys.stdout is None:
    sys.stdout = io.StringIO()

import main as M  # noqa: E402
from probe_reverse import get, post, post_bet  # noqa: E402

ACC = "g18959290733"
ALL = [f"{i:02d}" for i in range(1, 50)]


def snap(acc):
    d = M.load_data(acc)
    return {k: round(float(d.get(k) or 0), 2) for k in ALL}


def diff(a, b):
    return {k: round(b[k] - a[k], 2) for k in ALL if abs(b[k] - a[k]) > 0.009}


print("=" * 60)
print("[1] 一笔多号码：牛100")
mid = "probe-multi-%d" % int(time.time())
b = snap(ACC)
r = post_bet("牛100", mid)
print("推送 =", r)
a = snap(ACC)
print("增量号码 =", diff(b, a), " 合计 %.2f" % sum(diff(b, a).values()))
one = get("/api/record?msg_id=" + mid)
print("明细 =", one.get("applied"))

code, rev = post("/api/reverse", {"msg_id": mid, "note": "多号码撤销"})
print("撤销 ->", code)
c = snap(ACC)
print("撤销后与撤销前的差 =", diff(b, c), " 完全归零 =", not diff(b, c))
print("每个号减的数 =", rev.get("applied"))

print("=" * 60)
print("[2] 老记录（没有 applied）必须拒绝")
old = "guochangjin1-1794534211000"
code2, r2 = post("/api/reverse", {"msg_id": old, "note": "试撤老账"})
print("老账撤销 ->", code2, "|", r2.get("error"))

print("=" * 60)
print("[3] 撤销后同一条重推，要能重新入账")
mid3 = "probe-refill-%d" % int(time.time())
r3 = post_bet("06 100", mid3)
print("第一次推送 =", r3)
print("06 =", snap("g18959290733")["06"])
if r3.get("recorded"):
    code3, _ = post("/api/reverse", {"msg_id": mid3})
    print("撤销 ->", code3, " 06 =", snap("g18959290733")["06"])
    time.sleep(1)
    r4 = post_bet("06 100", mid3)          # 同一条消息号重推
    print("重推同一条 =", r4)
    print("06 =", snap("g18959290733")["06"], "（预期 100，说明撤销后放开了）")
    code4, _ = post("/api/reverse", {"msg_id": mid3})
    print("再撤一次 ->", code4, " 06 =", snap("g18959290733")["06"], "（预期 0）")
else:
    print("!! 第一次就没解析出来，跳过重推验证")

print("=" * 60)
print("[4] 解析失败的单不许锁死：重推要能重新解析")
mid4 = "probe-unpar-%d" % int(time.time())
r5 = post_bet("06 50", mid4)
print("第一次 =", r5)
time.sleep(1)
r6 = post_bet("06 50", mid4)
print('重推 =', r6, '（skipped 应为 0，不能是「重复推送」）')
