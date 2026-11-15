#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟 WxPushTool 向本程序推送，验证验签、去重、记账链路。

用法：
    python tests/verify_push.py [--url http://127.0.0.1:8899/wx/hook]
                                [--secret <64位hex>]
                                [--text "马鼠鸡各号15米"]
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name} {detail}")


def sign(secret: str, ts: int, body: bytes) -> str:
    return hmac.new(
        secret.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()


def post(url: str, body: dict, secret: str, ts: int = None, sig: str = None,
         cust: str = "C0001", raw: bytes = None) -> tuple:
    ts = int(time.time()) if ts is None else ts
    raw = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    sig = sign(secret, ts, raw) if sig is None else sig
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Wx-Timestamp", str(ts))
    req.add_header("X-Wx-Signature", sig)
    req.add_header("X-Wx-Cust", cust)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def get(url: str) -> tuple:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def make_body(text: str, msg_id: str, chat: str = "测试群") -> dict:
    return {
        "batch_id": f"test-{int(time.time())}",
        "ts": int(time.time()),
        "account": "j693395002",
        "count": 1,
        "messages": [{
            "msg_id": msg_id,
            "target_id": "12345678@chatroom",
            "chat_name": chat,
            "chat_id": "12345678@chatroom",
            "sender": "wxid_tester",
            "msg_type": "文本",
            "create_time": int(time.time()),
            "content": text,
            "account": "j693395002",
            "collected_at": "2026-11-12 14:00:00",
        }],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8899/wx/hook")
    ap.add_argument("--secret", required=True)
    ap.add_argument("--text", default="马鼠鸡各号15米")
    ap.add_argument("--cust", default="C0001")
    args = ap.parse_args()

    base = args.url.rsplit("/wx/hook", 1)[0]

    print("=== 1. 正常推送（应 200 且 recorded=1）===")
    body = make_body(args.text, f"test-{int(time.time())}")
    st, resp = post(args.url, body, args.secret, cust=args.cust)
    check("正常推送返回 200", st == 200, f"status={st} resp={resp}")
    ai_ok = resp.get("recorded") == 1
    if ai_ok:
        check("AI 解析并记账", True, str(resp))
    else:
        print("[WARN] AI 未返回答复（DeepSeek key 无效 / 额度用尽 / 网络不通），本条已落 unparsed.jsonl")
        check("消息被受理未丢弃", resp.get("unparsed") == 1, str(resp))

    print("=== 2. 观测 /top ===")
    st, txt = get(f"{base}/top?format=text")
    print(txt)
    if ai_ok:
        check("top 有数据", st == 200 and "号" in txt and "暂无" not in txt, f"status={st}")
    else:
        check("top 接口可用", st == 200, f"status={st}")

    print("=== 3. 重复 msg_id（应 skipped=1）===")
    st, resp = post(args.url, body, args.secret, cust=args.cust)
    check("重复消息被跳过", resp.get("skipped") == 1, str(resp))

    print("=== 4. 密钥错误（应 401）===")
    wrong = "0" * 64
    st, resp = post(args.url, make_body("马鼠鸡各号15米", f"bad-{int(time.time())}"), wrong)
    check("错误密钥被拒", st == 401, f"status={st} resp={resp}")

    print("=== 5. 时间戳过期（应 401）===")
    old = int(time.time()) - 3600
    st, resp = post(args.url, make_body("马鼠鸡各号15米", f"old-{int(time.time())}"), args.secret, ts=old)
    check("过期时间戳被拒", st == 401, f"status={st} resp={resp}")

    print("=== 6. 非文本消息（应 skipped，不调用 AI）===")
    b = make_body("x", f"img-{int(time.time())}")
    b["messages"][0]["msg_type"] = "图片"
    st, resp = post(args.url, b, args.secret)
    check("图片消息被跳过", st == 200 and resp.get("skipped") == 1, str(resp))

    print("=== 7. /status 与 /recent ===")
    st, txt = get(f"{base}/status")
    print(txt)
    check("status 接口可用", st == 200 and "grand_total" in txt, f"status={st}")
    st, txt = get(f"{base}/recent?n=3")
    check("recent 接口可用", st == 200 and "records" in txt, f"status={st}")

    print("\n=== 汇总 ===")
    ok_all = all(r[1] for r in _results)
    for name, ok, _ in _results:
        print(f"  {PASS if ok else FAIL}  {name}")
    print(f"总计 {sum(1 for r in _results if r[1])}/{len(_results)} 通过")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
