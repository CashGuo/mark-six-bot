# -*- coding: utf-8 -*-
"""把探针弄脏的东西清掉：账本归零、期号写正、删假归档、删假记录。"""
import io
import json
import os
import shutil
import sys

if sys.stdout is None:
    sys.stdout = io.StringIO()

ACC = "g18959290733"
STATE = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state\%s" % ACC
LEDGER = os.path.join(STATE, "numbers_state.json")
MARK = os.path.join(STATE, "current_issue.txt")
ISSUES = os.path.join(STATE, "issues")
RECS = os.path.join(STATE, "records.jsonl")

# 账本归零
with open(LEDGER, "r", encoding="utf-8") as f:
    led = json.load(f)
zeroed = {k: 0.0 for k in led}
with open(LEDGER + ".tmp", "w", encoding="utf-8") as f:
    json.dump(zeroed, f, ensure_ascii=False)
os.replace(LEDGER + ".tmp", LEDGER)
print("账本已归零，合计 =", round(sum(zeroed.values()), 2))

# 期号写正：今天 11-13 周五，最近开奖日是 11-14 周六
with open(MARK, "w", encoding="utf-8") as f:
    f.write("2026-11-14\n")
print("期号 =", open(MARK, encoding="utf-8").read().strip())

# 删探针造的归档
if os.path.isdir(ISSUES):
    for fn in os.listdir(ISSUES):
        os.remove(os.path.join(ISSUES, fn))
        print("删归档", fn)
    if not os.listdir(ISSUES):
        os.rmdir(ISSUES)

# 删探针造的记录。
# 这几个前缀之后的所有记录（含对应的冲正记录）都是测试产物，没有真实业务账，
# 留着会让"本期金额"算出一个不存在的数。
PREFIXES = ("probe-", "shot-", "issp-")
keep, dropped = [], 0
with open(RECS, "r", encoding="utf-8") as f:
    for ln in f:
        if any(pf in ln for pf in PREFIXES):
            dropped += 1
            continue
        keep.append(ln)
with open(RECS, "w", encoding="utf-8") as f:
    f.writelines(keep)
print("删测试记录 %d 条，剩 %d 条" % (dropped, len(keep)))

# 校验：活账是否真的为零
revd, recs = set(), {}
with open(RECS, "r", encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("type") == "reversal":
            revd.add(str(r.get("reversal_of") or ""))
        else:
            recs.setdefault(str(r.get("msg_id") or ""), []).append(
                str(r.get("rec_id") or r.get("msg_id") or ""))
live = [m for m, ids in recs.items() if any(i not in revd for i in ids)]
print("清理后仍有活账的消息号:", live or "无")
