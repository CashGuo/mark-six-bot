# -*- coding: utf-8 -*-
"""验控制台「文本记账」用到的两个接口：/api/parse 与 /api/quick_bet。"""

import json
import os
import sys
import urllib.request
from pathlib import Path

DATA = Path(os.environ["LOCALAPPDATA"]) / "MarkSixBot" / "data"
TOK = (DATA / ".api_token").read_text(encoding="utf-8").strip()
URL = "http://127.0.0.1:8899"
ACC = "selftest"


def post(path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Msb-Token", TOK)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8") or "{}")


print("=== /api/parse（只解析，不入账）===")
for t in ["鼠50", "牛马各10", "红单绿双各10", "小双50", "小双20",
          "小双50 大双50", "红波100", "小双20元", "鼠20元", "各50元 鼠",
          "鼠5尾各10", "平特鼠100", "乱七八糟 30", "今天天气不错"]:
    d = post("/api/parse", {"text": t})
    if d.get("ok"):
        print("%-14s ok   %s ｜ %s 个号 ｜ 合计 %s"
              % (t, d.get("detail"), d.get("numbers"), d.get("total")))
    else:
        print("%-14s fail %s ｜ 会转模型=%s"
              % (t, d.get("reason"), d.get("can_llm")))

print("\n=== /api/quick_bet（真记账，账号 %s）===" % ACC)
for t in ["鼠50", "牛马各10", "5尾各20 猴各25"]:
    d = post("/api/quick_bet", {"text": t, "account": ACC})
    if d.get("ok"):
        print("%-16s 已记账 [%s] %s ｜ %s 个号 ｜ 合计 %s ｜ 来源 %s"
              % (t, d.get("account"), d.get("detail"), d.get("numbers"),
                 d.get("total"), d.get("source")))
    else:
        print("%-16s 没记上 %s" % (t, d.get("reason") or d.get("error")))
