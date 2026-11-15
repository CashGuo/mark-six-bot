#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证：会话清单上报 → 界面勾选 → 采集端拉取。

不依赖微信、不消耗 AI 额度，只要服务在跑：
    python tests/verify_contacts.py --secret <WX_PUSH_SECRET>
    python tests/verify_contacts.py --secret ... --base http://127.0.0.1:8899
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  | " + detail) if detail else ""))


def _call(req):
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8") or "{}"
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, {"raw": body}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": repr(e)}


def post(base, path, secret, payload, cust="T0001", sign=True):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8", "X-Wx-Cust": cust}
    if sign:
        ts = int(time.time())
        h["X-Wx-Timestamp"] = str(ts)
        h["X-Wx-Signature"] = hmac.new(
            secret.encode("utf-8"), ("%d." % ts).encode("utf-8") + body,
            hashlib.sha256).hexdigest()
    return _call(urllib.request.Request(base + path, data=body, method="POST", headers=h))


def get(base, path, secret="", sign=False):
    h = {}
    if sign:
        ts = int(time.time())
        h["X-Wx-Timestamp"] = str(ts)
        h["X-Wx-Signature"] = hmac.new(
            secret.encode("utf-8"), ("GET.%s.%d" % (path, ts)).encode("utf-8"),
            hashlib.sha256).hexdigest()
    return _call(urllib.request.Request(base + path, method="GET", headers=h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    s = args.secret
    print("目标：%s" % base)

    items = [
        {"chat_id": "12345678@chatroom", "name": "华东代理群", "kind": "群",
         "alias": "", "last_msg_time": int(time.time()) - 60},
        {"chat_id": "wxid_zhang", "name": "张经理", "kind": "好友",
         "alias": "j693395002", "last_msg_time": int(time.time()) - 300},
        {"chat_id": "filehelper", "name": "文件传输助手", "kind": "文件助手",
         "alias": "", "last_msg_time": int(time.time()) - 600},
    ]
    payload = {"account": "j693395002", "cust": "T0001", "generated_at": int(time.time()),
               "total": len(items), "hash": "abc123", "items": items}

    # 1. 无签名 → 401
    code, _ = post(base, "/wx/contacts", s, payload, sign=False)
    ok("无签名上报被拒(401)", code == 401, "HTTP %s" % code)

    # 2. 正常上报
    code, d = post(base, "/wx/contacts", s, payload)
    ok("清单上报成功(200)", code == 200 and d.get("ok"), "HTTP %s %s" % (code, d))
    ok("上报条数正确", d.get("total") == 3, "total=%s" % d.get("total"))

    # 3. 界面读取
    code, d = get(base, "/api/contacts")
    ok("界面可读清单", code == 200 and d.get("ok"), "HTTP %s" % code)
    ok("清单落盘 3 项", len(d.get("items") or []) == 3, "%s 项" % len(d.get("items") or []))
    ok("记录同步时间", bool(d.get("received_at")), d.get("received_at") or "-")
    ok("初始全部未勾选",
       all(not x.get("selected") for x in (d.get("items") or [])))

    v0 = int(d.get("targets_version") or 0)

    # 4. 勾选 2 个
    code, d2 = post(base, "/api/targets", s,
                    {"targets": ["12345678@chatroom", "wxid_zhang"]}, sign=False)
    ok("保存勾选成功(200)", code == 200 and d2.get("ok"), "HTTP %s %s" % (code, d2))
    ok("勾选数=2", d2.get("count") == 2, "count=%s" % d2.get("count"))
    ok("version 递增", int(d2.get("version") or 0) == v0 + 1, "v%s→v%s" % (v0, d2.get("version")))

    # 5. 采集端拉取：无签名 401
    code, _ = get(base, "/wx/targets")
    ok("targets 无签名被拒(401)", code == 401, "HTTP %s" % code)

    # 6. 采集端拉取：带 GET 签名
    code, t = get(base, "/wx/targets", s, sign=True)
    ok("targets 拉取成功(200)", code == 200, "HTTP %s" % code)
    ok("targets 带 version", int(t.get("version") or 0) == v0 + 1, "v%s" % t.get("version"))
    names = {x.get("id"): x.get("name") for x in (t.get("targets") or [])}
    ok("只写 id 也补齐了名字", names.get("wxid_zhang") == "张经理",
       "wxid_zhang -> %s" % names.get("wxid_zhang"))
    ok("拉取项数=2", len(names) == 2, "%s 项" % len(names))

    # 7. 改选 1 个 → version 再 +1
    code, d3 = post(base, "/api/targets", s, {"targets": ["12345678@chatroom"]}, sign=False)
    ok("改选后 version 继续递增", int(d3.get("version") or 0) == v0 + 2,
       "v%s" % d3.get("version"))

    # 8. 重复上报同一清单（幂等）
    code, d4 = post(base, "/wx/contacts", s, payload)
    ok("重复上报仍 200（幂等）", code == 200 and d4.get("total") == 3,
       "HTTP %s total=%s" % (code, d4.get("total")))

    # 9. 非法报文
    code, _ = post(base, "/wx/contacts", s, {"items": "not-a-list"})
    ok("items 非数组被拒(400)", code == 400, "HTTP %s" % code)

    # 10. 勾选状态随清单刷新保持一致
    code, d5 = get(base, "/api/contacts")
    sel = [x.get("chat_id") for x in (d5.get("items") or []) if x.get("selected")]
    ok("勾选状态回读一致", sel == ["12345678@chatroom"], "selected=%s" % sel)

    print("\n通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "; ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
