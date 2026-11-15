# -*- coding: utf-8 -*-
import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

print("python  =", sys.executable)
for name in ("lunardate", "LunarDate"):
    try:
        m = __import__(name, fromlist=["LunarDate"])
        print("import %-10s OK -> %s" % (name, m))
        try:
            d = m.LunarDate.fromSolarDate(2026, 11, 13)
            print("   lunar.year =", d.year)
        except Exception as e:
            print("   fromSolarDate 失败:", type(e).__name__, e)
    except Exception as e:
        print("import %-10s 失败 -> %s: %s" % (name, type(e).__name__, e))
