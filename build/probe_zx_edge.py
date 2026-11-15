# -*- coding: utf-8 -*-
"""12/13/14 边界写法：看规则现在怎么处理（决定哪些能进文档、哪些要问客户）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bet_rules
from main import get_current_lunar_year

_, _cur, z = get_current_lunar_year()
zodiac = {k: list(v) for k, v in z.items()}

CASES = [
    "平特 鼠 100", "平特鼠牛各 50", "平码鼠牛各20", "平肖 鼠牛虎 各30",
    "平特鼠牛虎兔各25", "香港 平特鼠牛各100", "澳 平特马各50",
    "平特鼠牛一百", "平特鼠牛各一百", "平特鼠 50 平特牛 60",
    "平特鼠牛各10 平特鼠100", "平特各鼠牛100", "平特鼠鼠100",
    "平特鼠5尾100", "平特鼠 07 100", "平特鼠牛虎兔龙蛇马各10",
    "鼠二连肖各100", "马三连肖各50", "鸡四连肖200", "猪二连肖各100",
    "3连肖 鼠 100", "连肖 鼠牛 各50", "香港 鼠二连肖100",
    "鼠牛虎三连肖各10", "鼠二连肖100 鼠牛二连肖100", "鼠一连肖100",
    "鼠十连肖100", "鼠牛四连肖各50",
    "鼠牛虎兔四中四各50", "鼠牛虎兔龙蛇六中六各10",
    "鼠牛虎蛇四中二100", "鼠牛虎蛇猴五中三200", "三中二鼠牛100",
    "四中四鼠牛虎兔50", "港 鼠牛虎兔四中四各50", "澳门 六中六 鼠牛虎兔龙蛇 100",
    "鼠牛中二100", "鼠牛四中二100", "鼠二中二各100", "鼠牛三中三各100",
    "蛇50 平特蛇100", "平特鼠100 鼠50",
    "平特鼠牛各100 港 鼠牛二中二100",
    "港 平特鼠100 澳 平特牛100",
    "平特鼠100 鼠牛二中二100 鼠牛虎三中三200",
    "平特鼠牛各100，鼠牛二中二各100",
]

for text in CASES:
    r = bet_rules.parse_local(text, zodiac)
    if not r.get("ok"):
        print("%-30s ✗ %s fatal=%s" % (text, r.get("reason"), r.get("fatal")))
        continue
    ex = r.get("extras") or []
    seg = " | ".join("%s/%s/zs=%s/each=%s/%s"
                     % (e["play"], e["kind"], e["zodiacs"], e["each"], e["amount"]) for e in ex)
    print("%-30s %s  市场=%s 号盘=%d 合计=%s"
          % (text, seg, r.get("market"), len(r.get("bets") or {}), r.get("flat_total")))
