# -*- coding: utf-8 -*-
"""探针：统一拦截的四态。

1) 控制台接口 + 正确令牌 -> 200
2) 控制台接口 + 不带令牌 -> 401
3) 控制台接口 + 错令牌   -> 401
4) 看板接口（不带令牌） -> 200，网页看板不能被打死
5) /health 不校验 -> 200
"""
import io
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
if sys.stdout is None:
    sys.stdout = io.StringIO()

BASE = "http://127.0.0.1:8899"
TOK = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\.api_token"

with open(TOK, "r", encoding="utf-8") as f:
    token = f.read().strip()
print("令牌长度 =", len(token))


def call(path, tok=None, method="GET", body=None):
    headers = {}
    data = None
    if tok is not None:
        headers["X-Msb-Token"] = tok
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")[:120]
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")[:120]


print("1 控制台+正确令牌 /api/messages ->", call("/api/messages?limit=3", token))
print("2 控制台+无令牌     /api/messages ->", call("/api/messages?limit=3"))
print("3 控制台+错令牌     /api/messages ->", call("/api/messages?limit=3", "deadbeef"))
print("4 看板无令牌        /api/dashboard ->", call("/api/dashboard"))
print("5 看板无令牌        /api/accounts  ->", call("/api/accounts"))
print("6 /health 不校验                    ->", call("/health"))
print("7 控制台+无令牌     /api/clients  ->", call("/api/clients"))
print("8 控制台+正确令牌   /api/record   ->", call("/api/record?msg_id=nope", token))
