# -*- coding: utf-8 -*-
"""网页版手工记账回归：/manual 页面 + /api/manual/meta + /api/manual/build。

只读，不记账。验的是三件事：
  1. 网页拿到的玩法表跟窗口同一份（43 个），每个玩法的号数跟文档表一致；
  2. build 返回的「原文 / 号集 / 每号 / 合计」跟窗口算的一样；
  3. 交集为空时给的是拒绝，不是空清单。

跑法（工作目录 = 工程根，服务要在跑）：
    python build\\check_web_manual.py
"""
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8899"
DATA = os.path.join(os.environ["LOCALAPPDATA"], "MarkSixBot", "data")


def token():
    try:
        with io.open(os.path.join(DATA, ".api_token"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Msb-Token": token()})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "ignore")


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Msb-Token": token()})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "ignore") or "{}")


bad = []


def chk(cond, what):
    if not cond:
        bad.append(what)


# ---- 0. 页面能打开 ----
try:
    page = get("/manual")
except Exception as e:
    print("页面打不开：%r" % (e,))
    sys.exit(1)
for key in ('id="numpad"', 'id="zrow"', 'id="playzone"', 'id="amt"',
            'id="who"', "连续记账", "/api/manual/build", "/api/manual/submit"):
    chk(key in page, "页面里缺 %s" % key)
print("页面 %d 字节" % len(page))

# ---- 1. meta 结构 ----
m = json.loads(get("/api/manual/meta"))
chk(m.get("ok"), "meta 不 ok")
groups = m.get("groups") or []
plays = [(g["group"], it["code"], len(it["nums"])) for g in groups for it in g["items"]]
print("meta：%d 个分组 / %d 个玩法 / %d 生肖 / %d 个号 / 账号 %s / 对象 %d 个"
      % (len(groups), len(plays), len(m.get("zodiacs") or []),
         len(m.get("numbers") or []), m.get("accounts"),
         len(m.get("chats") or [])))
chk(len(plays) == 43, "玩法数 %d，应 43" % len(plays))
chk(len(m.get("zodiacs") or []) == 12, "生肖数不对")
chk(len(m.get("numbers") or []) == 49, "号码盘不是 49 个")
chk(m.get("default_market") == "MO", "默认市场不是澳门")

# 号数对照文档表
EXPECT = {
    "单": 25, "双": 24, "大": 25, "小": 24,
    "红波": 17, "蓝波": 16, "绿波": 16,
    "红单": 8, "红双": 9, "蓝单": 8, "蓝双": 8, "绿单": 9, "绿双": 7,
    "红小": 10, "红大": 7, "蓝小": 7, "蓝大": 9, "绿小": 7, "绿大": 9,
    "合数单": 25, "合数双": 24,
    "0尾": 4, "1尾": 5, "2尾": 5, "3尾": 5, "4尾": 5,
    "5尾": 5, "6尾": 5, "7尾": 5, "8尾": 5, "9尾": 5,
    "0头": 9, "1头": 10, "2头": 10, "3头": 10, "4头": 10,
    "金": 10, "木": 10, "水": 9, "火": 12, "土": 8,
    "家禽": 25, "野兽": 24,
}
for gname, code, n in plays:
    want = EXPECT.get(code)
    if want is not None and n != want:
        bad.append("[%s] %s 号数 %d，应 %d" % (gname, code, n, want))

# ---- 2. build 与文档口径一致 ----
cases = [
    ({"zodiacs": ["牛"]}, 100, "牛", 4, 400),
    ({"zodiacs": ["牛", "马"]}, 10, "牛马", 9, 90),
    ({"plays": ["大", "单"]}, 50, "大单", 13, 650),
    ({"plays": ["红波", "单"]}, 20, "红单", 8, 160),
    ({"nums": ["06"]}, 50, "06", 1, 50),
    ({"nums": ["06", "18"]}, 50, "06 18", 2, 100),
]
for args, amt, text, n, total in cases:
    r = post("/api/manual/build", dict(args, amount=amt))
    if not r.get("ok"):
        bad.append("build %s 失败：%s" % (args, r.get("reason")))
        continue
    if r.get("text") != text:
        bad.append("build %s 原文「%s」，应「%s」" % (args, r.get("text"), text))
    if len(r.get("nums") or []) != n:
        bad.append("build %s 号数 %d，应 %d" % (args, len(r.get("nums") or []), n))
    if abs(float(r.get("each") or 0) - amt) > 1e-6:
        bad.append("build %s 每号 %s，应 %s" % (args, r.get("each"), amt))
    if abs(float(r.get("total") or 0) - total) > 1e-6:
        bad.append("build %s 合计 %s，应 %s" % (args, r.get("total"), total))

# ---- 3. 空交集要拒绝 ----
for args in ({"plays": ["单", "双"]}, {"zodiacs": ["牛"], "plays": ["大", "单"]},
             {"nums": []}):
    r = post("/api/manual/build", dict(args, amount=50))
    if r.get("ok") and not r.get("nums"):
        bad.append("空交集没被挡：%s" % (args,))
    if not r.get("ok") and not str(r.get("reason") or "").strip():
        bad.append("被挡了但没给原因：%s" % (args,))

# 牛 ∩ 大单 本来就空（牛全偶数或小于 25），应当被挡
r = post("/api/manual/build", {"zodiacs": ["牛"], "plays": ["大", "单"], "amount": 50})
chk((not r.get("ok")) or r.get("nums"), "牛∩大单 应被挡或非空")

print("\n结论：%s" % ("全部通过" if not bad else "不符 %d 项" % len(bad)))
for b in bad:
    print("  ✗ %s" % b)
sys.exit(1 if bad else 0)
