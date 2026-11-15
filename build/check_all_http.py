# -*- coding: utf-8 -*-
"""全接口冒烟：把服务端每条路由都真打一遍（默认打编译出的 EXE 起的服务）。

    python build\\check_all_http.py                 # 打 127.0.0.1:8899
    python build\\check_all_http.py --base http://127.0.0.1:8899
    python build\\check_all_http.py --account selftest

覆盖：/health /api/dashboard / /top /status /api/accounts /api/clients /api/messages
/api/parse /manual /api/manual/meta|build|submit /api/quick_bet /api/record
/api/reverse /api/recalc /api/issues /recent /api/contacts /api/targets
/wx/targets /wx/contacts /wx/hook（含验签失败 401）/backup /restore /logs /reset

只用 selftest 账号记账与撤销，跑完把 selftest 清掉，主账号不动。
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

OK, BAD = [], []


def _token():
    p = os.path.join(os.environ["LOCALAPPDATA"], "MarkSixBot", "data", ".api_token")
    with open(p, encoding="utf-8") as f:
        return f.read().strip()


def _client():
    p = os.path.join(os.environ["LOCALAPPDATA"], "MarkSixBot", "config", "wx_clients.json")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return d["clients"][0]


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("%-46s %s %s" % (name, "PASS" if cond else "FAIL", detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    ap.add_argument("--account", default="selftest")
    ap.add_argument("--keep", action="store_true", help="不清理 selftest 账本")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    token = _token()
    cl = _client()
    wx, secret = cl["wechat_id"], cl["secret"]
    acc = args.account

    def call(method, path, body=None, headers=None, timeout=20):
        h = {"X-Msb-Token": token}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                body_text = raw.decode("utf-8", "replace")
                try:
                    return r.status, json.loads(body_text), body_text
                except Exception:
                    return r.status, None, body_text
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(body_text), body_text
            except Exception:
                return e.code, None, body_text

    def sign_post(path, payload, cust=wx):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        hdr = {"X-Wx-Timestamp": ts, "X-Wx-Signature": sig, "X-Wx-Cust": cust,
               "Content-Type": "application/json"}
        req = urllib.request.Request(base + path, data=raw, headers=hdr, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))

    def sign_get(path, cust=wx, query=""):
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode(), f"GET.{path}.{ts}".encode(), hashlib.sha256).hexdigest()
        hdr = {"X-Wx-Timestamp": ts, "X-Wx-Signature": sig, "X-Wx-Cust": cust}
        req = urllib.request.Request(base + path + query, headers=hdr, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))

    print("=== 基础页与状态 ===")
    st, js, txt = call("GET", "/health")
    check("GET /health", st == 200 and "OK" in (txt or ""), txt.strip()[:40])

    st, js, _ = call("GET", "/api/dashboard")
    need = {"grid", "zodiac_board", "flat_items", "markets", "recent", "issue", "accounts"}
    check("GET /api/dashboard", st == 200 and js and need <= set(js.keys()),
          "字段齐全" if js else "无 JSON")

    st, js, txt = call("GET", "/")
    check("GET /", st == 200 and len(txt or "") > 800, "%d 字节" % len(txt or ""))
    st, js, txt = call("GET", "/top?n=5")
    check("GET /top", st == 200 and isinstance(js, dict) and "top" in js,
          "top %s 项" % (len((js or {}).get("top") or [])))
    st, js, txt = call("GET", "/manual")
    check("GET /manual", st == 200 and len(txt or "") > 800, "%d 字节" % len(txt or ""))

    st, js, _ = call("GET", "/status")
    check("GET /status", st == 200 and isinstance(js, dict), str(js)[:60])

    st, js, _ = call("GET", "/api/accounts")
    check("GET /api/accounts", st == 200 and bool(js), str(js)[:80])

    st, js, _ = call("GET", "/api/clients")
    check("GET /api/clients", st == 200 and js is not None, str(js)[:80])

    st, js, _ = call("GET", "/api/messages?limit=20")
    check("GET /api/messages", st == 200 and js is not None, str(js)[:80])

    st, js, _ = call("GET", "/api/issues?limit=5")
    check("GET /api/issues", st == 200 and js and js.get("ok") is not False, str(js)[:80])

    st, js, txt = call("GET", "/recent")
    check("GET /recent", st == 200 and len(txt or "") > 200, "%d 字节" % len(txt or ""))

    st, js, txt = call("GET", "/logs")
    check("GET /logs", st == 200, "%d 字节" % len(txt or ""))

    print("\n=== 解析（不落账） ===")
    for text, want_ok in [("鼠50", True), ("平特鼠100", True), ("鼠牛虎三中三300", True),
                          ("鼠牛三中三300", False),
                          ("中特鼠100", False), ("今天天气不错", False)]:
        st, js, _ = call("POST", "/api/parse", {"text": text})
        got = bool((js or {}).get("ok"))
        check("POST /api/parse %s" % text, st == 200 and got == want_ok,
              (js or {}).get("reason") or (js or {}).get("detail") or "")

    print("\n=== 手工记账（网页版链路） ===")
    st, js, _ = call("GET", "/api/manual/meta")
    check("GET /api/manual/meta", st == 200 and js and len(js.get("groups") or []) >= 8,
          "玩法 %s 个" % (js or {}).get("play_count"))

    st, js, _ = call("POST", "/api/manual/build",
                     {"nums": ["07", "19"], "amount": 10})
    check("POST /api/manual/build", st == 200 and js and js.get("ok", True) is not False,
          str((js or {}).get("text"))[:50])

    st, js, _ = call("POST", "/api/manual/submit",
                     {"account": acc, "market": "MO", "chat": "接口冒烟",
                      "notes": [{"text": "07,19", "each": 10, "nums": ["07", "19"]}]})
    check("POST /api/manual/submit", st == 200 and (js or {}).get("ok") is True,
          "合计 %s" % (js or {}).get("total"))

    print("\n=== 记账 / 查询 / 撤销 ===")
    st, js, _ = call("POST", "/api/quick_bet", {"text": "港 平特鼠100 澳 平特牛100", "account": acc})
    check("POST /api/quick_bet（港澳分盘）",
          st == 200 and (js or {}).get("ok") and len((js or {}).get("segments") or []) == 2,
          "段 %s 合计 %s" % ((js or {}).get("count"), (js or {}).get("total")))
    msg_id = ""
    segs = (js or {}).get("segments") or []
    if segs:
        msg_id = str(segs[0].get("rec_id") or "").split("#")[0]

    st, js, _ = call("GET", "/api/record?msg_id=" + msg_id)
    check("GET /api/record", st == 200 and (js or {}).get("count") == 2,
          "count=%s total=%s has_detail=%s"
          % ((js or {}).get("count"), (js or {}).get("total"), (js or {}).get("has_detail")))

    st, js, _ = call("POST", "/api/reverse", {"msg_id": msg_id, "note": "接口冒烟"})
    check("POST /api/reverse（一次撤整条）",
          st == 200 and (js or {}).get("ok") and (js or {}).get("count") == 2,
          "合计 %s" % (js or {}).get("total"))

    st, js, _ = call("POST", "/api/reverse", {"msg_id": msg_id, "note": "再撤一次"})
    check("POST /api/reverse 二次撤销被拒", st == 400 and not (js or {}).get("ok"),
          (js or {}).get("error") or "")

    st, js, _ = call("GET", "/api/recalc?account=" + acc)
    check("GET /api/recalc", st == 200 and js and js.get("ok") is not False,
          str({k: js.get(k) for k in ("diff", "ok") if isinstance(js, dict)})[:80])

    print("\n=== 会话清单 / 监听对象 / 采集端推送 ===")
    st, js = sign_post("/wx/contacts", {"account": acc, "hash": "smoke",
                                       "items": [{"chat_id": "smoke-chat", "name": "接口冒烟群"}]})
    check("POST /wx/contacts（验签）", st == 200 and (js or {}).get("total") == 1, str(js)[:60])

    st, js = sign_post("/wx/contacts", {"items": []}, cust="wrong-cust")
    check("POST /wx/contacts 错签被拒", st == 401, str(js)[:40])

    st, js, _ = call("GET", "/api/contacts?account=" + acc)
    check("GET /api/contacts", st == 200 and (js or {}).get("ok") is True,
          "会话 %s 项" % (js or {}).get("total"))

    st, js, _ = call("POST", "/api/targets",
                     {"account": acc, "targets": [{"id": "smoke-chat", "name": "接口冒烟群"}]})
    check("POST /api/targets", st == 200 and (js or {}).get("ok") is True,
          "version=%s" % (js or {}).get("version"))

    st, js = sign_get("/wx/targets", query="?account=" + acc)
    check("GET /wx/targets（验签）", st == 200 and (js or {}).get("targets"),
          "version=%s" % (js or {}).get("version"))

    st, js = sign_get("/wx/targets", cust="wrong-cust", query="?account=" + acc)
    check("GET /wx/targets 错签被拒", st == 401, str(js)[:40])

    payload = {"account": acc, "messages": [
        {"msg_id": "smoke-hook-1", "msg_type": "文本", "chat_name": "接口冒烟群",
         "chat_id": "smoke-chat", "sender": "smoke", "sender_name": "冒烟",
         "content": "平特鼠100", "create_time": int(time.time())}]}
    st, js = sign_post("/wx/hook", payload)
    check("POST /wx/hook（验签 + 记账）",
          st == 200 and (js or {}).get("ok") and (js or {}).get("recorded") == 1,
          "记账 %s 未解析 %s" % ((js or {}).get("recorded"), (js or {}).get("unparsed")))

    st, js = sign_post("/wx/hook", payload)
    check("POST /wx/hook 重复推送被跳过",
          st == 200 and (js or {}).get("skipped") == 1, "跳过 %s" % (js or {}).get("skipped"))

    st, js = sign_post("/wx/hook", {"account": acc, "messages": []}, cust="wrong-cust")
    check("POST /wx/hook 错签被拒", st == 401, str(js)[:40])

    st, js, _ = call("GET", "/api/messages?limit=10&account=" + acc)
    check("GET /api/messages（看得到刚才的流水）",
          st == 200 and len(str(js)) > 40, str(js)[:60])

    print("\n=== 备份 / 恢复 / 清账 ===")
    st, js, _ = call("POST", "/backup", {})
    check("POST /backup", st == 200 and (js or {}).get("ok") is True,
          "文件 %s" % ((js or {}).get("files") or [])[:1])

    today = time.strftime("%Y-%m-%d")
    st, js, _ = call("POST", "/restore/" + today, {"account": acc})
    check("POST /restore/<今天>", st == 200 and (js or {}).get("ok") is True,
          (js or {}).get("error") or (js or {}).get("message") or "")

    if not args.keep:
        st, js, _ = call("POST", "/reset?account=" + acc, {"account": acc})
        check("POST /reset（只清测试账号）", st == 200 and (js or {}).get("ok") is True,
              "清了 %s" % (js or {}).get("accounts"))

        # 清完必须真归零：号码盘（港澳两本）+ 整笔注都要删，否则看板会挂着旧金额
        st, js, _ = call("GET", "/api/dashboard?account=" + acc, None)
        g = (js or {}).get("grand_total")
        fat = (js or {}).get("flat_total")
        check("POST /reset 后总金额归零", g in (0, 0.0) and fat in (0, 0.0),
              "总金额=%s 整笔=%s" % (g, fat))

    print("\n===== 汇总：通过 %d，失败 %d =====" % (len(OK), len(BAD)))
    if BAD:
        print("失败项：")
        for b in BAD:
            print("  - " + b)
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
