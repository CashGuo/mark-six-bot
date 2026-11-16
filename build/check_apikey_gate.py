# -*- coding: utf-8 -*-
"""验证白名单的三道闸：API Key / 到期时间 / IP 白名单。

不碰真实账本：自己造一个临时 MSB_HOME，在随机端口起源码版服务，
然后按不同签名推送，断言 200/401 以及拒绝原因对得上。

用法（在工程根）：
    python build\\check_apikey_gate.py            # 跑完自动清理临时目录
    python build\\check_apikey_gate.py --keep     # 留下临时目录方便看日志
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
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "build" / "venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

# 四条测试条目 —— 三条用来验证拒绝，一条验证正确放行
OK_SEC = "a" * 64
EXP_SEC = "b" * 64
IPX_SEC = "c" * 64
IPOK_SEC = "d" * 64
NOEXP_SEC = "n" * 64
YDAY_SEC = "y" * 64
TODAY_SEC = "t" * 64

# 「到期就断」是按服务端本机日期算的：昨天已经过期必须拒，今天到期当天仍可用。
YDAY = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
TODAY = date.today().strftime("%Y-%m-%d")

CLIENTS = {
    "updated_at": "2026-11-16 10:00:00",
    "clients": [
        {"wechat_id": "ok1", "secret": OK_SEC, "enabled": True,
         "expire_at": "2027-11-16", "ip_allow": ""},
        {"wechat_id": "exp1", "secret": EXP_SEC, "enabled": True,
         "expire_at": "2020-01-01", "ip_allow": ""},
        {"wechat_id": "ipx1", "secret": IPX_SEC, "enabled": True,
         "expire_at": "2027-11-16", "ip_allow": "10.0.0.1"},
        {"wechat_id": "ipok", "secret": IPOK_SEC, "enabled": True,
         "expire_at": "2027-11-16", "ip_allow": "127.0.0.1"},
        # 界面「新增」的默认有效期就是这个：加容差天数会让 date 溢出，
        # 曾经把每一次推送都变成 500，这条用例专门守住。
        {"wechat_id": "noexp", "secret": NOEXP_SEC, "enabled": True,
         "expire_at": "9999-12-31", "ip_allow": ""},
        # 没有 3 天缓冲：昨天到期 → 拒；今天到期（当天为止）→ 收
        {"wechat_id": "yday", "secret": YDAY_SEC, "enabled": True,
         "expire_at": YDAY, "ip_allow": ""},
        {"wechat_id": "todayx", "secret": TODAY_SEC, "enabled": True,
         "expire_at": TODAY, "ip_allow": ""},
        {"wechat_id": "old1", "secret": "e" * 64, "enabled": True},   # 老条目：无到期无 IP
    ],
}


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


def push(base, wid, secret, text="这条只是验签测试"):
    body = {
        "batch_id": "%d-%s" % (int(time.time()), uuid.uuid4().hex[:8]),
        "ts": int(time.time()),
        "account": "gatecheck",
        "count": 1,
        "messages": [{
            "msg_id": "gate-%d" % int(time.time() * 1000000),
            "target_id": "filehelper", "chat_name": "验签测试",
            "sender": 1, "msg_type": "文本", "create_time": int(time.time()),
            "content": text, "account": "gatecheck", "collected_at": "",
        }],
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "Connection": "close",
        "X-Wx-Timestamp": ts,
        "X-Wx-Signature": sign(secret, ts, raw),
        "X-Wx-Cust": wid,
    }
    t0 = time.time()
    sess = requests.Session()          # 每条一个新连接：连接池复用到
    try:                               # 服务端已关闭的 socket 会一路读到超时
        r = sess.post(base + "/wx/hook", data=raw, headers=headers, timeout=15)
        return r.status_code, r.text, time.time() - t0
    except Exception as e:
        return 0, "EXC %r" % (e,), time.time() - t0
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


def main():
    keep = "--keep" in sys.argv
    home = Path(tempfile.mkdtemp(prefix="msb_gate_"))
    port = free_port()
    base = "http://127.0.0.1:%d" % port

    cfg_dir = home / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "server.json").write_text(json.dumps({
        "port": port, "host": "127.0.0.1", "wx_push_secret": "",
        "wx_account_secrets": "", "wx_skew_sec": 300,
        "llm_api_key": "sk-gatecheck", "llm_timeout": 20, "llm_thinking": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg_dir / "wx_clients.json").write_text(
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
            print("服务没起来，日志如下：")
            try:
                proc.kill()
                print((proc.stdout.read() or b"").decode("utf-8", "replace")[-3000:])
            except Exception:
                pass
            return 1

        cases = [
            ("正确密钥 + 不限期 IP", "ok1", OK_SEC, 200, ""),
            ("正确密钥（另一条）", "ipok", IPOK_SEC, 200, ""),
            ("默认不限期 9999-12-31", "noexp", NOEXP_SEC, 200, ""),
            ("今天到期（当天为止）", "todayx", TODAY_SEC, 200, ""),
            ("昨天到期（无缓冲，必拒）", "yday", YDAY_SEC, 401, "有效期"),
            ("老条目：没有 expire_at / ip_allow", "old1", "e" * 64, 200, ""),
            ("来源 IP 不在白名单", "ipx1", IPX_SEC, 401, "IP"),
            ("密钥不对", "ok1", "f" * 64, 401, ""),
            ("不在名单里", "ghost", "g" * 64, 401, ""),
        ]
        for i, (name, wid, sec, want, why) in enumerate(cases, 1):
            code, text, dt = push(base, wid, sec)
            if code == 0:
                # 本机 dev server 在这种毫秒级连续新连接下，第 10 个请求偶尔会读超时
                # （服务端 access log 里其实已经回了 401）。真机推送 2 秒一批不会这样，
                # 所以这里重试一次以排除环境抖动；两次都超时才算产品问题。
                c2, t2, d2 = push(base, wid, sec)
                print("     第 %d 条读超时，重试一次 → %s" % (i, c2 or "仍超时"))
                code, text, dt = c2, t2, d2
            time.sleep(0.5)
            ok = (code == want)
            print("%2d %-28s 期望 %d 实得 %d  %.2fs  %s"
                  % (i, name, want, code, dt, "OK" if ok else "FAIL"))
            if not ok:
                fails.append((name, code, text[:200]))
            if why:
                # 拒绝原因只写进日志，响应体是统一 unauthorized；这里从日志核对
                pass

        # 从日志里核对拒绝原因写对了（客户排查全靠这一行）
        time.sleep(0.5)
        logs = list((home / "data" / "logs").glob("*.log")) if (home / "data" / "logs").exists() else []
        joined = ""
        for f in logs:
            joined += f.read_text(encoding="utf-8", errors="replace")
        for expect, label in (("有效期", "过期条目要写明有效期"), ("IP", "IP 拦截要写明来源 IP")):
            if expect not in joined:
                print("日志缺少拒绝原因：%s" % label)
                fails.append((label, 0, ""))

        if fails:
            print("\n失败项：")
            for f in fails:
                print("  ", f)
            return 1
        print("\n全部通过（%d 项）" % len(cases))
        return 0
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        if keep:
            print("临时目录保留：%s" % home)
        else:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
