# -*- coding: utf-8 -*-
"""排查「小双50」这类「玩法词 + 裸金额」的写法。"""

import json
import sys

sys.path.insert(0, "D:/Users/Administrator/Python/mark-six-bot")
import bet_rules

# 只要生肖字在，够触发 _zodiac_plays；大小单双不依赖生肖表
Z = {s: ["01", "02", "03", "04"] for s in
     ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]}
Z["马"].append("49")

for t in ["小双50", "小双 50", "小双各50", "小双50 大双50",
          "大单50", "小单50", "小单50 小双50",
          "红单50", "红波50", "5尾50", "0头50", "合数单50",
          "鼠50", "牛马各10", "虎5 17各20"]:
    d = bet_rules.parse_local(t, Z)
    if d["ok"]:
        print("%-14s ok   %-40s %d 个号 合计 %s" %
              (t, d["detail"], len(d["bets"]), sum(d["bets"].values())))
    else:
        print("%-14s FAIL %s" % (t, d["reason"]))
