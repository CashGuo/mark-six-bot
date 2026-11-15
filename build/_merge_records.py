# -*- coding: utf-8 -*-
"""把挪走的旧 records 合并回 state 目录，按 ts 排序，保持一条一行。"""
import json
import os

OLD = r"C:\Users\Administrator\AppData\Local\MarkSixBot\_testresidue\g18959290733\records.jsonl"
NEW = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state\g18959290733\records.jsonl"


def read(p):
    rows = []
    if not os.path.exists(p):
        return rows
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows


old_rows = read(OLD)
new_rows = read(NEW)
print("旧 %d 条，新 %d 条" % (len(old_rows), len(new_rows)))

# 用 (ts, rec_id/msg_id) 去重，防止重复合并
seen = set()
merged = []
for r in old_rows + new_rows:
    key = (str(r.get("ts") or ""), str(r.get("rec_id") or r.get("msg_id") or ""))
    if key in seen:
        continue
    seen.add(key)
    merged.append(r)

merged.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("rec_id") or "")))
print("合并后 %d 条" % len(merged))

tmp = NEW + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    for r in merged:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
os.replace(tmp, NEW)
print("已写回", NEW, os.path.getsize(NEW), "B")

# 校验：还剩几笔活账
revd = {str(r.get("reversal_of") or "") for r in merged if r.get("type") == "reversal"}
recs = {}
for r in merged:
    if r.get("type") == "reversal":
        continue
    recs.setdefault(str(r.get("msg_id") or ""), []).append(
        str(r.get("rec_id") or r.get("msg_id") or ""))
live = [m for m, ids in recs.items() if any(i not in revd for i in ids)]
print("还有活账的消息号:", live)
print("全部撤销的:", [m for m, ids in recs.items() if all(i in revd for i in ids)])
