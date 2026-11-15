# -*- coding: utf-8 -*-
"""验证「最近记账」的单笔撤销：会话列有值、撤销后该行标记为已撤销、总额只减这一笔。"""
import io
import json
import sys
import urllib.request

if sys.stdout is None:
    sys.stdout = io.StringIO()

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")

import msb_view as v

TOKEN = open(r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\.api_token",
             "r", encoding="utf-8").read().strip()
PORT = 8899


def call(path, body=None):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "X-Msb-Token": TOKEN,
        "Content-Type": "application/json; charset=utf-8",
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


acc = "g18959290733"
rows = v.recent_records(acc, 10)
sys.stdout.write("--- 撤销前 ---\n")
for r in rows:
    sys.stdout.write("%s chat=%r total=%s rev=%s mid=%s\n"
                     % (r.get("ts"), r.get("chat"), r.get("total"),
                        r.get("reversed"), r.get("msg_id")))

target = None
for r in rows:
    if str(r.get("msg_id") or "").startswith("acc-new-") and not r.get("reversed"):
        target = r
        break
if not target:
    sys.stdout.write("没有可撤的测试记录\n")
    sys.exit(0)

mid = target["msg_id"]
sys.stdout.write("\n--- 撤销 %s（total=%s）---\n" % (mid, target.get("total")))
before = call("/api/dashboard?account=%s" % acc)["grand_total"]
sys.stdout.write("撤销前总额=%s\n" % before)

rec = call("/api/record?msg_id=" + mid)
sys.stdout.write("明细 applied=%s\n" % json.dumps(rec.get("applied"), ensure_ascii=False))

res = call("/api/reverse", {"msg_id": mid, "note": "probe_recent2"})
sys.stdout.write("撤销返回: %s\n" % json.dumps(res, ensure_ascii=False))

after = call("/api/dashboard?account=%s" % acc)["grand_total"]
sys.stdout.write("撤销后总额=%s 差=%s\n" % (after, round(before - after, 2)))

rows2 = v.recent_records(acc, 10)
sys.stdout.write("\n--- 撤销后 ---\n")
for r in rows2:
    if r.get("msg_id") == mid:
        sys.stdout.write("该行 reversed=%s\n" % r.get("reversed"))
