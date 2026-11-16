# -*- coding: utf-8 -*-
"""服务端响应很慢（要等大模型）时，采集端会怎样 —— 端到端实测。

做法：
  1. 起一个「假大模型」：收到请求后先睡 SLOW_SEC 秒，再返回一份合法 JSON。
  2. 起源码版服务，把 llm_base_url 指向假模型，llm_timeout 设 60 秒。
  3. 用采集端的口径推送（timeout=8 秒，和 pusher 默认一致）：
     第 1 次必然超时；立刻重发同一批（同 msg_id）——这就是采集端的第一次重试。
  4. 等假模型返回，查账本和回执，看「账有没有记上、回执有没有发出去」。

用法（在工程根）：
    python build\\check_slow_response.py
"""

import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "build" / "venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

CLIENT_TIMEOUT = 8.0     # 采集端 pusher 默认 timeout
SLOW_SEC = 15.0          # 假模型拖多久
SEC = "9" * 64
WID = "slow1"

# 模型对这个输入应当给出的答案（十二连 → 连肖，整笔 100）
MODEL_CONTENT = json.dumps({
    "items": [{"type": "lianxiao", "zodiacs": ["鼠", "牛"], "amount": 100, "each": True,
               "text": "连肖 各100"}]
}, ensure_ascii=False)


class FakeLLM(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        time.sleep(SLOW_SEC)
        body = json.dumps({
            "id": "fake", "object": "chat.completion", "created": int(time.time()),
            "model": "fake-slow",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": MODEL_CONTENT}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sign(secret, ts, raw):
    return hmac.new(secret.encode("utf-8"),
                    ("%s." % ts).encode("utf-8") + raw,
                    hashlib.sha256).hexdigest()


def build_body(msg_id, text):
    return {
        "batch_id": "%d-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:8]),
        "ts": int(time.time()),
        "account": WID,
        "count": 1,
        "messages": [{
            "msg_id": msg_id, "target_id": "chat_slow", "chat_name": "慢响应测试群",
            "sender": 1, "sender_name": "客户", "msg_type": "文本",
            "create_time": int(time.time()), "content": text,
            "account": WID, "collected_at": "",
        }],
    }


def push(base, msg_id, text="十二连 100", timeout=CLIENT_TIMEOUT):
    body = build_body(msg_id, text)
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json", "Connection": "close",
        "X-Wx-Timestamp": ts, "X-Wx-Signature": sign(SEC, ts, raw), "X-Wx-Cust": WID,
    }
    sess = requests.Session()
    t0 = time.time()
    try:
        r = sess.post(base + "/wx/hook", data=raw, headers=headers, timeout=timeout)
        return r.status_code, r.text, time.time() - t0
    except Exception as e:
        return 0, "EXC %s" % type(e).__name__, time.time() - t0
    finally:
        sess.close()


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


def dashboard_total(base):
    """看板金额。dashboard 不需要 token。"""
    try:
        with urllib.request.urlopen(base + "/api/dashboard", timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d.get("grand_total")
    except Exception as e:
        return "读取失败 %r" % (e,)


def main():
    keep = "--keep" in sys.argv
    home = Path(tempfile.mkdtemp(prefix="msb_slow_"))
    llm_port = free_port()
    port = free_port()
    base = "http://127.0.0.1:%d" % port

    srv = ThreadingHTTPServer(("127.0.0.1", llm_port), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    cfg_dir = home / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "server.json").write_text(json.dumps({
        "port": port, "host": "127.0.0.1", "wx_push_secret": "",
        "wx_account_secrets": "", "wx_skew_sec": 300,
        "llm_api_key": "sk-fake-slow",
        "llm_base_url": "http://127.0.0.1:%d/v1" % llm_port,
        "llm_model": "fake-slow", "llm_thinking": 0, "llm_timeout": 60,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg_dir / "wx_clients.json").write_text(json.dumps({
        "updated_at": "2026-11-16 10:00:00",
        "clients": [{"wechat_id": WID, "secret": SEC, "enabled": True,
                     "expire_at": "9999-12-31", "ip_allow": ""}],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    env["MSB_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("WX_SKIP_VERIFY", None)
    proc = subprocess.Popen([PY, str(ROOT / "main.py"), "--headless", "--quiet"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    fails = []

    def note(ok, label, extra=""):
        print("%s %s %s" % ("OK  " if ok else "FAIL", label, extra))
        if not ok:
            fails.append(label)

    try:
        if not wait_health(base):
            print("服务没起来")
            try:
                proc.kill()
                print((proc.stdout.read() or b"").decode("utf-8", "replace")[-2000:])
            except Exception:
                pass
            return 1

        mid = "slow-%d" % int(time.time() * 1000)

        print("=== 第 1 次推送（采集端 timeout=%.0f 秒，服务端要等模型 %.0f 秒）==="
              % (CLIENT_TIMEOUT, SLOW_SEC))
        code1, text1, dt1 = push(base, mid)
        print("     结果：HTTP %s  用时 %.1f 秒  %s" % (code1 or "超时", dt1, text1[:80]))
        note(code1 == 0, "采集端会在超时后判失败（这是它的重试前提）",
             "HTTP %s / %.1fs" % (code1 or "超时", dt1))

        print("\n=== 采集端立刻重试（同一批、同 msg_id，服务端还在算）===")
        code2, text2, dt2 = push(base, mid)
        print("     结果：HTTP %s  用时 %.1f 秒" % (code2 or "超时", dt2))
        try:
            j2 = json.loads(text2)
        except Exception:
            j2 = {}
        print("     skipped=%s  pending=%s  replies=%s"
              % (j2.get("skipped"), j2.get("pending"), len(j2.get("replies") or [])))
        note(code2 == 200, "重试请求能立刻拿到回应（不会真的重复记账）")
        note(mid in (j2.get("pending") or []),
             "服务端答「还在处理」——采集端据此不当成功，退避后再来",
             "pending=%s" % j2.get("pending"))
        note(not (j2.get("replies") or []), "这一次还拿不到回执（正常，账还没算完）")

        print("\n=== 采集端再退避一次（服务端仍然没算完）===")
        time.sleep(2)
        code3, text3, dt3 = push(base, mid)
        try:
            j3 = json.loads(text3)
        except Exception:
            j3 = {}
        print("     HTTP %s  pending=%s" % (code3, j3.get("pending")))
        note(mid in (j3.get("pending") or []), "仍然等，不误判为失败")

        print("\n=== 等服务端处理完，再来取一次回执 ===")
        time.sleep(SLOW_SEC + 4)
        total = dashboard_total(base)
        print("     看板金额 grand_total=%s" % total)
        note(float(total or 0) == 200.0, "账其实已经记上了（连肖两肖各 100 = 200）",
             "grand_total=%s" % total)

        code4, text4, dt4 = push(base, mid)
        try:
            j4 = json.loads(text4)
        except Exception:
            j4 = {}
        rs = j4.get("replies") or []
        print("     HTTP %s  补发回执 %d 条：%s"
              % (code4, len(rs), (rs[0].get("text") if rs else "无")))
        note(len(rs) == 1, "这次终于把当初漏掉的回执补上了（这是修复的关键）")
        note(not (j4.get("pending") or []), "已经算完，pending 清空")
        note(float(dashboard_total(base) or 0) == 200.0, "补发回执不会重复记账",
             "总额=%s" % dashboard_total(base))

        print("\n=== 服务端日志时间线 ===")
        logs = list((home / "data" / "logs").glob("*.log")) if (home / "data" / "logs").exists() else []
        for f in logs:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if any(k in line for k in ("收到推送", "AI返回", "记账", "跳过",
                                           "展开结果", "重推", "仍在处理", "补发")):
                    print("  " + line.strip()[:150])

        print("\n结论：%s" % ("全部符合预期" if not fails else "有不符合项 %s" % fails))
        return 0 if not fails else 1
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        srv.shutdown()
        if keep:
            print("临时目录：%s" % home)
        else:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
