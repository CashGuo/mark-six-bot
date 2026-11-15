# -*- coding: utf-8 -*-
"""探针：验证 msb_view 能从数据目录算出与 /api/dashboard 同口径的数据。

用法：build\\venv\\Scripts\\python.exe tools\\_probe_view.py [账号]
"""

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import msb_view as v

acc = sys.argv[1] if len(sys.argv) > 1 else ""

print("accounts =", v.all_accounts())
s = v.summary(acc)
print("account  =", acc or "(全部账号合计)")
for k in ("grand_total", "numbers_with_bets", "records_today", "unparsed_today",
          "zodiac", "zodiac_year"):
    print("  %-18s %s" % (k, s[k]))
print("grid rows =", len(s["grid"]))
for row in s["grid"]:
    print("   %s: %s" % (row["zodiac"],
          " ".join("%s(%s%s)" % (c["num"], c["color"] or "-",
                                 "" if not c["amount"] else " %.0f" % c["amount"])
                   for c in row["cells"])))
print("risk rows =", len(s["risk"]))
for r in s["risk"]:
    print("   #%d %s %s %.1f元 %s%%" % (r["rank"], r["num"], r["zodiac"],
                                        r["amount"], r["share"]))
print("recent =", len(s["recent"]))
print("unparsed =", len(s["unparsed"]))
if acc:
    c = v.contact_rows(acc)
    print("contacts =", len(c["items"]), "| received_at =", c["received_at"],
          "| version = v%d" % c["version"], "| all_mode =", c["all_mode"])
    for x in c["items"][:5]:
        print("   [%s] %s %s" % ("x" if x["selected"] else " ", x["kind"], x["name"]))
