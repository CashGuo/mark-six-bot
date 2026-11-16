# -*- coding: utf-8 -*-
"""端到端：采集端「测试地址」那条消息真发过去，服务端能不能收下并记账。

不碰真实数据：临时 MSB_HOME + 随机端口起源码版服务，报文与 GUI「测试地址」按钮
完全一致（同样的 body 形状、同样的 X-Wx-* 头），Key 用白名单里那条。
"""

import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "build" / "venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

KEY = "k" * 64
WID = "g18959290733"
CLIENTS = {
    "updated_at": "2026-11-16 10:00:00",
    "clients": [{"wechat_id": WID, "secret": KEY, "enabled": True,
                 "expire_at": "9999-12-31", "ip_allow": ""}],
}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post(base, path, raw, headers):
    req = urllib.request.Request(base + path, data=raw, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")


def get(base, path, token=""):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("X-Msb-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")


def wait_health(base, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main():
    home = Path(tempfile.mkdtemp(prefix="msb_addr_"))
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "server.json").write_text(json.dumps({
        "port": port, "host": "127.0.0.1", "wx_push_secret": "",
        "wx_account_secrets": "", "wx_skew_sec": 300,
        "llm_api_key": "sk-addrcheck", "llm_timeout": 20, "llm_thinking": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg / "wx_clients.json").write_text(
        json.dumps(CLIENTS, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    env["MSB_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("WX_SKIP_VERIFY", None)
    proc = subprocess.Popen([PY, str(ROOT / "main.py"), "--headless", "--quiet"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fails = []
    try:
        if not wait_health(base):
            print("服务没起来")
            print((proc.stdout.read() or b"").decode("utf-8", "replace")[-2500:])
            return 1

        # ---- 1. 完全按采集端「测试地址」的报文发一条
        ts = int(time.time())
        msg_id = "addrtest-%d" % ts
        body = json.dumps({
            "batch_id": "addrtest-%d" % ts, "ts": ts, "account": WID, "count": 1,
            "messages": [{
                "msg_id": msg_id, "target_id": "addrtest", "chat_name": "地址测试",
                "chat_id": "addrtest", "sender": "addrtest", "sender_name": "地址测试",
                "msg_type": "文本", "create_time": ts, "content": "01 100",
                "account": WID,
            }],
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(KEY.encode(), ("%d." % ts).encode() + body,
                       hashlib.sha256).hexdigest()
        code, text = post(base, "/wx/hook", body, {
            "Content-Type": "application/json; charset=utf-8",
            "X-Wx-Timestamp": str(ts), "X-Wx-Signature": sig, "X-Wx-Cust": WID})
        print("[1] 测试地址推送 → HTTP %s  %s" % (code, text[:160]))
        if code != 200:
            fails.append("测试地址推送被拒")

        # ---- 2. 服务端消息流水里能不能找到这条
        token = ""
        tp = home / "data" / ".api_token"
        if tp.exists():
            token = tp.read_text(encoding="utf-8").strip()
        time.sleep(2)
        c2, t2 = get(base, "/api/messages?limit=20", token)
        hit = None
        if c2 == 200:
            doc = json.loads(t2)
            for row in doc.get("messages") or []:
                if str(row.get("msg_id")) == msg_id:
                    hit = row
                    break
        print("[2] 消息流水 code=%s 找到本条=%s" % (c2, bool(hit)))
        if hit:
            print("    result=%s  content=%s" % (hit.get("result"), hit.get("content")))
            if str(hit.get("result") or "") != "记账":
                fails.append("这条消息没被记账（result=%s）" % hit.get("result"))
        else:
            fails.append("消息流水里没有这条")

        # ---- 3. 账本金额是不是真加上了 100
        c4, t4 = get(base, "/api/dashboard", token)
        if c4 == 200:
            d = json.loads(t4)
            print("[3] dashboard grand_total=%s 号码=%s 期号=%s"
                  % (d.get("grand_total"), d.get("numbers_with_bets"), d.get("issue")))
            got = d.get("grand_total")
            if not isinstance(got, (int, float)):
                fails.append("dashboard 上没有金额字段")
            elif got != 100:
                fails.append("记账金额不是 100（实得 %s）" % got)
        else:
            print("[3] dashboard code=%s %s" % (c4, t4[:160]))
            fails.append("dashboard 读不到")

        # ---- 4. 换成采集端真在用的 Pusher 再发一条：这条走的是运行时同一条出口
        sys.path.insert(0, r"D:\Users\Administrator\Python\WxPushTool\src")
        try:
            from wxpush.pusher import Pusher
        except Exception as e:
            print("[4] 载入 Pusher 失败：%r" % (e,))
            fails.append("Pusher 载入失败")
            Pusher = None
        if Pusher:
            wdir = home / "pusher"
            p = Pusher(webhook=base + "/wx/hook", secret=KEY, batch=1,
                       timeout=10, account=WID,
                       dead_path=str(wdir / "dead.jsonl"),
                       state_dir=str(wdir / "logs"), log=lambda s: None)
            p.submit({"msg_id": "pusher-%d" % int(time.time() * 1000),
                      "target_id": "addrtest", "chat_name": "地址测试",
                      "chat_id": "addrtest", "sender": "addrtest",
                      "sender_name": "地址测试", "msg_type": "文本",
                      "create_time": int(time.time()), "content": "02 100",
                      "account": WID})
            for _ in range(20):
                if p.snapshot()["batches"] >= 1:
                    break
                time.sleep(0.3)
            s = p.snapshot()
            print("[4] Pusher 实发：sent=%s failed=%s last_code=%s last_error=%s"
                  % (s["sent"], s["failed"], s["last_code"], s["last_error"]))
            if s["sent"] != 1:
                fails.append("Pusher 实发没有成功（sent=%s）" % s["sent"])
            time.sleep(1.5)
            c5, t5 = get(base, "/api/messages?limit=20", token)
            if c5 == 200:
                doc5 = json.loads(t5)
                rows = [r for r in (doc5.get("messages") or [])
                        if str(r.get("msg_id") or "").startswith("pusher-")]
                print("[5] Pusher 那条在流水里：%s（%s）"
                      % (bool(rows), rows[0].get("result") if rows else "-"))
                if not rows:
                    fails.append("Pusher 发的那条没进流水")
            c6, t6 = get(base, "/api/dashboard", token)
            if c6 == 200:
                print("[6] 两条之后 grand_total=%s 号码=%s"
                      % (json.loads(t6).get("grand_total"),
                         json.loads(t6).get("numbers_with_bets")))

        # ---- 7. 没填 Key 的采集端连签名都凑不出来（GUI 层拦，不发请求）
        print("[7] 采集端无 Key 时 GUI 直接拦下（_push_sign_key 已覆盖）")

        if fails:
            print("\n失败：%s" % fails)
            return 1
        print("\n端到端 OK")
        return 0
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        print("临时目录：%s" % home)


if __name__ == "__main__":
    sys.exit(main())
