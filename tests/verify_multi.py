# -*- coding: utf-8 -*-
"""多采集端（多个微信账号）验证 —— 纯 HTTP + 少量进程内断言，不打 AI。

用法：
  .\\venv\\Scripts\\python.exe tests\\verify_multi.py --secret <WX_PUSH_SECRET> [--base http://127.0.0.1:8899]

只往 t_a / t_b 两个测试账号写数据，跑完自动删除，不碰真实账号的账本。
（用标准库 urllib：这个 venv 里没有 requests）
"""
import argparse
import hashlib
import hmac
import json
import shutil
import sys
import time
import urllib.error as urlerr
import urllib.request as urlreq
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import main  # noqa: E402

PASS, FAIL = [], []
ACC_A, ACC_B = "t_a", "t_b"


class Resp:
    def __init__(self, code, text):
        self.status_code = code
        self.text = text

    def json(self):
        return json.loads(self.text)


def _do(req):
    try:
        with urlreq.urlopen(req, timeout=12) as r:
            return Resp(r.status, r.read().decode("utf-8", "replace"))
    except urlerr.HTTPError as e:
        return Resp(e.code, e.read().decode("utf-8", "replace"))


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  | " + str(detail)) if detail else ""))


def sign_post(secret, ts, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body,
                    hashlib.sha256).hexdigest()


def post(base, path, secret, obj, cust=""):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    ts = int(time.time())
    h = {"Content-Type": "application/json; charset=utf-8",
         "X-Wx-Timestamp": str(ts), "X-Wx-Signature": sign_post(secret, ts, body)}
    if cust:
        h["X-Wx-Cust"] = cust
    return _do(urlreq.Request(base + path, data=body, headers=h, method="POST"))


def get(base, path, account=None):
    url = base + path + ("?account=" + quote(account, safe="") if account else "")
    return _do(urlreq.Request(url, method="GET"))


def push(base, secret, account, msg_id, content, chat="测试群", sender=100,
         create_time=None, cust="C0001"):
    ct = create_time or int(time.time())
    body = {
        "batch_id": "%d-%s" % (int(time.time()), msg_id),
        "ts": int(time.time()),
        "account": account,
        "count": 1,
        "messages": [{
            "msg_id": msg_id, "target_id": "12345678@chatroom", "chat_name": chat,
            "chat_id": "12345678@chatroom", "sender": sender, "sender_name": "张三",
            "msg_type": "文本", "create_time": ct, "content": content,
            "account": account, "collected_at": "2026-11-12 16:00:00",
        }],
    }
    return post(base, "/wx/hook", secret, body, cust)


def money(base, account=None):
    return get(base, "/api/dashboard", account).json()["grand_total"]


def cleanup():
    for acc in (ACC_A, ACC_B):
        p = main.STATE_DIR / acc
        if p.exists() and p.name in (ACC_A, ACC_B):
            shutil.rmtree(p, ignore_errors=True)
        for d in (main.CONTACTS_DIR, main.TARGETS_DIR):
            f = d / (acc + ".json")
            if f.exists():
                f.unlink()


def get_targets(base, secret, account, cust="C0001"):
    path = "/wx/targets"
    ts = int(time.time())
    sig = hmac.new(secret.encode("utf-8"),
                   ("GET.%s.%s" % (path, ts)).encode("utf-8"),
                   hashlib.sha256).hexdigest()
    url = base + path + ("?account=" + quote(account, safe="") if account else "")
    h = {"X-Wx-Timestamp": str(ts), "X-Wx-Signature": sig}
    if cust:
        h["X-Wx-Cust"] = cust
    return _do(urlreq.Request(url, headers=h, method="GET"))


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secret", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    args = ap.parse_args()
    base, secret = args.base.rstrip("/"), args.secret.strip()
    # msg_id 带时间戳：去重表是持久的，测试重跑不能被上一次的 msg_id 挡住
    TAG = str(int(time.time()))

    print("\n=== A. 多密钥路由（进程内断言）===")
    old_map, old_secret, old_skip = main.ACCOUNT_SECRETS, main.WX_PUSH_SECRET, main.WX_SKIP_VERIFY
    try:
        main.WX_SKIP_VERIFY = False
        main.ACCOUNT_SECRETS = {"C0001": "aa" * 32, "C0002": "bb" * 32}
        ok("C0001 取到自己的密钥", main.secret_for("C0001") == "aa" * 32)
        ok("C0002 取到自己的密钥", main.secret_for("C0002") == "bb" * 32)
        ok("未知客户退回全局密钥", main.secret_for("C9999") == old_secret)

        raw, ts = b'{"probe":1}', str(int(time.time()))
        sig_a = sign_post("aa" * 32, ts, raw)
        ok("A 的签名过 A 的验签", main.verify_signature(raw, ts, sig_a, "C0001") is True)
        ok("A 的签名过不了 B 的验签", main.verify_signature(raw, ts, sig_a, "C0002") is False)
        ok("时间戳超窗被拒",
           main.verify_signature(raw, str(int(time.time()) - 400), sig_a, "C0001") is False)
    finally:
        main.ACCOUNT_SECRETS, main.WX_PUSH_SECRET, main.WX_SKIP_VERIFY = old_map, old_secret, old_skip

    print("\n=== B. 账号路径净化 ===")
    ok("普通账号名保持原样", main.resolve_account("j693395002") == "j693395002")
    ok("路径穿越被净化", main.resolve_account("../../evil") == "evil",
       main.resolve_account("../../evil"))
    ok("空账号落 default", main.resolve_account("") == main.DEFAULT_ACCOUNT)
    ok("斜杠冒号星号被剔除", main.resolve_account("a/b\\c:d*e") == "abcde",
       main.resolve_account("a/b\\c:d*e"))

    print("\n=== C. 分账号记账隔离 ===")
    cleanup()
    before_all = money(base)

    r1 = push(base, secret, ACC_A, "mul-a-1-" + TAG, "马各号10米")
    ok("A 推送 200 且记账 1 条",
       r1.status_code == 200 and r1.json().get("recorded") == 1, r1.text[:150])
    a1 = money(base, ACC_A)
    ok("A 累计 = 50（马 5 个号各 10）", abs(a1 - 50) < 0.01, a1)

    r2 = push(base, secret, ACC_B, "mul-b-1-" + TAG, "马各号10米")
    ok("B 推送 200 且记账 1 条",
       r2.status_code == 200 and r2.json().get("recorded") == 1, r2.text[:150])
    b1 = money(base, ACC_B)
    ok("B 累计独立 = 50", abs(b1 - 50) < 0.01, b1)
    ok("A 账本没被 B 影响", abs(money(base, ACC_A) - a1) < 0.01)
    ok("合计 = 原有 + A + B", abs(money(base) - (before_all + 100)) < 0.01,
       "%s vs %s" % (money(base), before_all + 100))

    print("\n=== D. 跨账号内容指纹去重 ===")
    ct = int(time.time()) - 5
    push(base, secret, ACC_A, "mul-a-fp1-" + TAG, "鼠各号10米", create_time=ct)
    a_after = money(base, ACC_A)
    r_dup = push(base, secret, ACC_B, "mul-b-fp-" + TAG, "鼠各号10米", create_time=ct)
    d = r_dup.json()
    ok("第二个号收到同一条 → 跳过不记账",
       d.get("skipped") == 1 and d.get("recorded") == 0, d)
    ok("A 账本没因重复加钱", abs(money(base, ACC_A) - a_after) < 0.01)
    ok("B 账本也没多记这单", abs(money(base, ACC_B) - b1) < 0.01)
    r_same = push(base, secret, ACC_A, "mul-a-fp1-" + TAG, "鼠各号10米", create_time=ct)
    ok("同账号重复 msg_id 被跳过", r_same.json().get("skipped") == 1, r_same.json())

    print("\n=== E. 监听配置按账号分开 ===")
    ra = post(base, "/api/targets", secret, {"targets": ["aaa@chatroom"], "account": ACC_A})
    rb = post(base, "/api/targets", secret, {"targets": ["bbb@chatroom"], "account": ACC_B})
    ok("A 保存监听成功", ra.status_code == 200, ra.text[:120])
    ok("B 保存监听成功", rb.status_code == 200, rb.text[:120])
    ok("A、B 的 version 各自独立（都为 1）",
       ra.json().get("version") == 1 and rb.json().get("version") == 1,
       "%s / %s" % (ra.json().get("version"), rb.json().get("version")))

    ta = get_targets(base, secret, ACC_A)
    tb = get_targets(base, secret, ACC_B)
    ok("A 只拉到自己的监听对象",
       ta.status_code == 200 and [x["id"] for x in ta.json()["targets"]] == ["aaa@chatroom"],
       ta.text[:150])
    ok("B 只拉到自己的监听对象",
       tb.status_code == 200 and [x["id"] for x in tb.json()["targets"]] == ["bbb@chatroom"],
       tb.text[:150])
    tn = get_targets(base, secret, None)
    ok("不带 account 不会误返回别人的配置",
       "aaa@chatroom" not in tn.text and "bbb@chatroom" not in tn.text, tn.text[:120])

    ts = int(time.time())
    url = base + "/wx/targets?account=" + quote(ACC_A)
    req = urlreq.Request(url, headers={"X-Wx-Timestamp": str(ts),
                                       "X-Wx-Signature": "x" * 64}, method="GET")
    ok("targets 错误签名被拒 401", _do(req).status_code == 401)

    print("\n=== F. 账号列表 ===")
    names = [a["account"] for a in get(base, "/api/accounts").json()["accounts"]]
    ok("账号列表出现 A", ACC_A in names, names)
    ok("账号列表出现 B", ACC_B in names, names)
    ok("真实账号仍在列表里", "guochangjin1" in names, names)

    print("\n=== G. 单账号兼容（不传 account）===")
    # 多账号时"不传 account"会落 default，跑完要还原，别留一份空配置
    dft = main.TARGETS_DIR / (main.DEFAULT_ACCOUNT + ".json")
    dft_old = dft.read_text(encoding="utf-8") if dft.exists() else None
    only = post(base, "/api/targets", secret, {"targets": []})
    ok("不传 account 也能保存", only.status_code == 200, only.text[:120])
    if dft_old is None:
        if dft.exists():
            dft.unlink()
    else:
        dft.write_text(dft_old, encoding="utf-8")

    cleanup()
    print("\n通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(run())
