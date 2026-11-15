# -*- coding: utf-8 -*-
"""12/13/14 解析结果速览：看 play / kind / zodiacs / 金额 对不对。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bet_rules
from main import get_current_lunar_year

_, _cur, zodiac = get_current_lunar_year()
zodiac = {k: list(v) for k, v in zodiac.items()}

CASES = [
    "平特鼠100", "平特鼠牛100", "平特鼠牛各100", "平码鼠50", "平肖牛各20",
    "平鼠30", "香港 平特马50", "平特鼠牛五十",
    "鼠二连肖100", "二连肖鼠100", "鼠牛二连肖100", "2连肖鼠牛50", "鼠3连肖300",
    "鼠五连肖500", "鼠牛三连肖各100", "连肖鼠50", "鼠牛虎三连肖100",
    "猪二连肖100", "猴三连肖200",
    "鼠牛二中二100", "二中二鼠牛100", "鼠牛三中二100", "鼠牛二中二各100",
    "鼠牛虎三中三200", "三中三鼠牛虎200", "澳门 三中三 鼠牛虎 200",
    "港 鼠牛虎三中三各50", "鼠牛虎兔四中四50", "鼠牛虎兔龙五中五200",
    "鼠牛虎兔龙蛇六中六100", "鼠牛虎蛇四中二100",
    "平特鼠牛各100 鼠牛二中二100",
]

for text in CASES:
    r = bet_rules.parse_local(text, zodiac)
    if not r.get("ok"):
        print("%-26s ✗ %s fatal=%s" % (text, r.get("reason"), r.get("fatal")))
        continue
    ex = r.get("extras") or []
    seg = " | ".join("%s kind=%s zs=%s each=%s amt=%s"
                     % (json.dumps(e["play"], ensure_ascii=False), e["kind"],
                        e["zodiacs"], e["each"], e["amount"]) for e in ex)
    print("%-26s %s  市场=%s 号码盘=%d个 合计=%s"
          % (text, seg, r.get("market"), len(r.get("bets") or {}), r.get("flat_total")))
