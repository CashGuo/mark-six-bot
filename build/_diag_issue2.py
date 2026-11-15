# -*- coding: utf-8 -*-
"""逐条列出本期流水，找出哪笔没被抵掉。"""
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
        if ln:
            rows.append(json.loads(ln))

revd = {str(r.get("reversal_of") or "") for r in rows if r.get("type") == "reversal"}
print("%-3s %-18s %-8s %8s %8s  %s" % ("#", "msg_id", "type", "total", "ap_sum", "state"))
acc = 0.0
for i, r in enumerate(rows):
    ap = r.get("applied")
    s = sum(float(v or 0) for v in ap.values()) if isinstance(ap, dict) and ap else 0.0
    acc += s
    rid = str(r.get("rec_id") or "")
    st = "撤销" if r.get("type") == "reversal" else ("已撤" if rid in revd else "活账")
    print("%-3d %-18s %-8s %8.2f %8.2f  %s  acc=%.2f" % (
        i, str(r.get("msg_id"))[:18], r.get("type") or "bet",
        float(r.get("total") or 0), s, st, acc))

print("\n合计 applied =", round(acc, 2))
print("活账条数 =", sum(1 for r in rows
                     if r.get("type") != "reversal"
                     and str(r.get("rec_id") or "") not in revd))
