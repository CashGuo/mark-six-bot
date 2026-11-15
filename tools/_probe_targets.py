# -*- coding: utf-8 -*-
"""探针：按采集端的口径调用 GET /wx/targets，看服务端到底返回什么。"""
import hashlib
import hmac
import json
import time
import urllib.request

SECRET = ""  # C0002 / g18959290733
BASE = "http://127.0.0.1:8899"
PATH = "/wx/targets"

for acc in ("j693395002", "guochangjin1", ""):
    ts = str(int(time.time()))
    msg = "GET.%s.%s" % (PATH, ts)
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    q = ("?account=" + acc) if acc else ""
    req = urllib.request.Request(BASE + PATH + q, method="GET")
    req.add_header("X-Wx-Timestamp", ts)
    req.add_header("X-Wx-Signature", sig)
    req.add_header("X-Wx-Cust", "C0001")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            print("account=%-14s HTTP %s  %s" % (acc or "(空)", r.status, r.read().decode("utf-8")))
    except Exception as e:
        print("account=%-14s ERR %r" % (acc or "(空)", e))
