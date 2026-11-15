# -*- coding: utf-8 -*-
"""看 /api/messages 与总览口径的差：各 result 条数、手工记账占比、期别分布。"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, "D:\\Users\\Administrator\\Python\\mark-six-bot")
import msb_view

TOKEN_PATH = os.path.join(os.environ["LOCALAPPDATA"], "MarkSixBot", "data", ".api_token")
tok = ""
try:
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        tok = f.read().strip()
except Exception as e:
    print("读令牌失败", e)

url = "http://127.0.0.1:8899/api/messages?limit=200"
req = urllib.request.Request(url, headers={"X-Msb-Token": tok})
with urllib.request.urlopen(req, timeout=5) as r:
    j = json.loads(r.read().decode("utf-8", "replace"))

rows = j.get("messages") or []
print("消息总数：", len(rows))
print("meta keys：", sorted(j.keys()))
if rows:
    print("单条字段：", sorted(rows[0].keys()))

from collections import Counter
c = Counter(str(m.get("result") or "") for m in rows)
print("按 result 统计：", dict(c))

s = msb_view.summary("g18959290733")
cur = s["issue"]
print("当前期：", cur)
print("总览：未解析本期=%s 全部=%s ／ 今日已记账=%s ／ recent=%s"
      % (s.get("unparsed_current"), len(s["unparsed"]), s["records_today"], len(s["recent"]) ))

# 消息里按 result 分期的分布
by_issue = {}
for m in rows:
    iss = msb_view.stage_of_ts(m.get("ts")) if hasattr(msb_view, "stage_of_ts") else ""
    by_issue.setdefault(iss, Counter())[str(m.get("result") or "")] += 1
print("按时间折算的期别分布：")
for k in sorted(by_issue, reverse=True):
    print("   ", k or "(未知)", dict(by_issue[k]))

print("消息里带 issue 字段的条数：",
      sum(1 for m in rows if m.get("issue")))
print("手工记账条数：", sum(1 for m in rows if str(m.get("chat") or "") == "手工记账"))
print("记账类里手工记账：",
      sum(1 for m in rows if str(m.get("result") or "") == "记账"
          and str(m.get("chat") or "") == "手工记账"))
