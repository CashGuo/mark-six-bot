# -*- coding: utf-8 -*-
"""验证金额分摊：一笔账的金额摊到它的号码上，而不是每个号都记全额。"""
import io
import sys

if sys.stdout is None:
    sys.stdout = io.StringIO()

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
import main

_, _, zm = main.get_current_lunar_year()
print("牛 = %s" % zm.get("牛"))
print("马 = %s" % zm.get("马"))
print("鸡 = %s" % zm.get("鸡"))
print("鼠 = %s" % zm.get("鼠"))
print("")


def show(label, items):
    bets = main.expand_bets(items, zm)
    tot = sum(bets.values())
    parts = ", ".join("%s=%s" % (k, ("%g" % round(v, 2))) for k, v in sorted(bets.items())[:6])
    print("%-18s 号码%2d个   合计%9.2f   %s" % (label, len(bets), tot, parts))


show("牛100", [{"type": "zodiac", "names": ["牛"], "amount": 100}])
show("马100", [{"type": "zodiac", "names": ["马"], "amount": 100}])
show("牛马各100", [{"type": "zodiac", "names": ["牛", "马"], "amount": 100}])
show("马鼠鸡各号15", [{"type": "zodiac", "names": ["马", "鼠", "鸡"], "amount": 15}])
show("1*13*34*7各10", [{"type": "number", "numbers": ["01", "13", "34", "07"], "amount": 10}])
show("06 100", [{"type": "number", "numbers": ["06"], "amount": 100}])
show("红波10", [{"type": "color", "names": ["红波"], "amount": 10}])
show("0头3", [{"type": "head", "heads": ["0"], "amount": 3}])
show("牛100 + 06 50", [{"type": "zodiac", "names": ["牛"], "amount": 100},
                       {"type": "number", "numbers": ["06"], "amount": 50}])
show("牛马各100 + 鼠50", [{"type": "zodiac", "names": ["牛", "马"], "amount": 100},
                         {"type": "zodiac", "names": ["鼠"], "amount": 50}])
