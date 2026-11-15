# -*- coding: utf-8 -*-
"""诊断：本期（2026-11-14）的流水为什么不是 0。"""
import io
import json
import sys

if sys.stdout is None:
    sys.stdout = io.StringIO()

RECS = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state\g18959290733\records.jsonl"
CUR = "2026-11-14"

rows = []
with open(RECS, "r", encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass

print("总记录 %d 条" % len(rows))

issues = {}
for r in rows:
    issues[r.get("issue") or "(无)"] = issues.get(r.get("issue") or "(无)", 0) + 1
print("issue 分布:", issues)

tot_applied = 0.0
tot_total = 0.0
no_ap = []
for r in rows:
    if (r.get("issue") or "") != CUR:
        continue
    ap = r.get("applied")
    if isinstance(ap, dict) and ap:
        s = sum(float(v or 0) for v in ap.values())
        tot_applied += s
    else:
        no_ap.append(r)
        tot_total += float(r.get("total") or 0)

print("\n本期 applied 求和 =", round(tot_applied, 2))
print("本期 无 applied 的条数 =", len(no_ap), " 其 total 求和 =", round(tot_total, 2))
print("本期 两种口径合计 =", round(tot_applied + tot_total, 2))

for r in no_ap:
    print("  无applied:", r.get("type") or "bet", r.get("msg_id"),
          "total=", r.get("total"), "text=", str(r.get("text"))[:30])
