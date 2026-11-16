# -*- coding: utf-8 -*-
"""验证 IP 白名单在「127.0.0.1 / localhost / 回环双栈」上的实际行为。

两段：
  A. 端到端：临时 MSB_HOME 起源码版服务，按采集端的报文签名推送，看放行/拒绝。
  B. 纯逻辑：直接调 ip_allowed()，覆盖本机没有 IPv6 环境时测不到的 ::1 情况。

用法（在工程根）：
    python build\\check_ip_allow.py
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
import urllib.request
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "build" / "venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

# 每条客户端一把独立密钥，白名单不同
CLIENTS = [
    ("ip127", "1" * 64, "127.0.0.1"),      # 本机 IPv4 写法
    ("iplocal", "2" * 64, "localhost"),    # 域名写法
    ("ipboth", "3" * 64, "127.0.0.1, ::1"),
    ("ipv6only", "4" * 64, "::1"),         # 只写 IPv6 回环
    ("iptail", "5" * 64, "10.0.0.0/8"),    # 完全不相干的网段
]


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


def push(host, port, wid, secret, text="这条只是验签测试"):
    body = {
        "batch_id": "%d-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:8]),
        "ts": int(time.time()),
        "account": "ipcheck", "count": 1,
        "messages": [{
            "msg_id": "ip-%d" % int(time.time() * 1000000),
            "target_id": "filehelper", "chat_name": "验签测试",
            "sender": 1, "msg_type": "文本", "create_time": int(time.time()),
            "content": text, "account": "ipcheck", "collected_at": "",
        }],
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json", "Connection": "close",
        "X-Wx-Timestamp": ts, "X-Wx-Signature": sign(secret, ts, raw),
        "X-Wx-Cust": wid,
    }
    sess = requests.Session()
    try:
        r = sess.post("http://%s:%d/wx/hook" % (host, port), data=raw,
                      headers=headers, timeout=15)
        return r.status_code, r.text
    except Exception as e:
        return 0, "EXC %s" % type(e).__name__
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


def logic_checks():
    """纯逻辑断言：本机没 IPv6 环境，::1 只能在函数层验。"""
    home = Path(tempfile.mkdtemp(prefix="msb_ip_logic_"))
    script = home / "run.py"
    script.write_text(
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import main\n"
        "cases = json.loads(sys.argv[1])\n"
        "out = []\n"
        "for allow, ip, want in cases:\n"
        "    got = main.ip_allowed({'ip_allow': allow}, ip)\n"
        "    out.append([allow, ip, want, bool(got)])\n"
        "print(json.dumps(out, ensure_ascii=False))\n" % str(ROOT),
        encoding="utf-8")
    cases = [
        ["127.0.0.1", "127.0.0.1", True],
        ["127.0.0.1", "::1", True],            # 本机就是本机，不分 v4/v6
        ["::1", "127.0.0.1", True],
        ["localhost", "127.0.0.1", True],      # 域名写法当回环别名
        ["localhost", "::1", True],
        ["127.0.0.0/8", "::1", True],          # 回环网段同样覆盖本机
        ["127.0.0.1", "10.1.2.3", False],      # 非本机来源不放
        ["::1", "192.168.1.9", False],
        ["10.0.0.0/8", "127.0.0.1", False],    # 普通网段不认本机
        ["", "10.1.2.3", True],                # 没填白名单 = 全放行
        ["127.0.0.1, ::1", "10.1.2.3", False],
        ["127.0.0.1", "not-an-ip", False],     # 来源取不到就不放
    ]
    env = dict(os.environ)
    env["MSB_HOME"] = str(Path(tempfile.mkdtemp(prefix="msb_ip_logic_home_")))
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([PY, str(script), json.dumps(cases)],
                          capture_output=True, env=env, timeout=180)
    fails = []
    try:
        rows = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    except Exception:
        print(proc.stdout.decode("utf-8", "replace")[-1500:])
        print(proc.stderr.decode("utf-8", "replace")[-1500:])
        return [("逻辑段脚本跑不起来", "", "")]
    print("=== A. 纯逻辑（含本机测不到的 ::1）===")
    for allow, ip, want, got in rows:
        ok = got == want
        print("%-16s 来源 %-12s 期望 %-5s 实得 %-5s %s"
              % (allow or "(空)", ip, want, got, "OK" if ok else "FAIL"))
        if not ok:
            fails.append(("ip_allowed(%r, %r)" % (allow, ip), got, want))
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(env["MSB_HOME"], ignore_errors=True)
    return fails


def main():
    keep = "--keep" in sys.argv
    fails = logic_checks()

    home = Path(tempfile.mkdtemp(prefix="msb_ip_"))
    port = free_port()
    base = "http://127.0.0.1:%d" % port

    cfg_dir = home / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "server.json").write_text(json.dumps({
        "port": port, "host": "0.0.0.0", "wx_push_secret": "",
        "wx_account_secrets": "", "wx_skew_sec": 300,
        "llm_api_key": "sk-ipcheck", "llm_timeout": 20, "llm_thinking": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg_dir / "wx_clients.json").write_text(json.dumps({
        "updated_at": "2026-11-16 10:00:00",
        "clients": [{"wechat_id": w, "secret": s, "enabled": True,
                     "expire_at": "9999-12-31", "ip_allow": a} for w, s, a in CLIENTS],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    env["MSB_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("WX_SKIP_VERIFY", None)
    proc = subprocess.Popen([PY, str(ROOT / "main.py"), "--headless", "--quiet"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    try:
        if not wait_health(base):
            print("服务没起来")
            try:
                proc.kill()
                print((proc.stdout.read() or b"").decode("utf-8", "replace")[-2000:])
            except Exception:
                pass
            return 1

        print("\n=== localhost 在本机怎么解析 ===")
        for item in socket.getaddrinfo("localhost", port, 0, socket.SOCK_STREAM):
            fam = "IPv4" if item[0] == socket.AF_INET else "IPv6"
            print("  %-6s %s" % (fam, item[4]))

        print("\n=== B. 端到端（真实 HTTP）===")
        cases = [
            ("白名单=127.0.0.1，连 127.0.0.1", "ip127", CLIENTS[0][1], "127.0.0.1", 200),
            ("白名单=127.0.0.1，连 localhost", "ip127", CLIENTS[0][1], "localhost", 200),
            ("白名单=localhost，连 127.0.0.1", "iplocal", CLIENTS[1][1], "127.0.0.1", 200),
            ("白名单=localhost，连 localhost", "iplocal", CLIENTS[1][1], "localhost", 200),
            ("白名单=127.0.0.1,::1，连 localhost", "ipboth", CLIENTS[2][1], "localhost", 200),
            ("白名单=::1，连 127.0.0.1", "ipv6only", CLIENTS[3][1], "127.0.0.1", 200),
            ("白名单=10.0.0.0/8，连 127.0.0.1", "iptail", CLIENTS[4][1], "127.0.0.1", 401),
        ]
        for name, wid, sec, host, want in cases:
            code, text = push(host, port, wid, sec)
            if code == 0:
                code, text = push(host, port, wid, sec)   # dev server 偶尔读超时
            ok = (code == want)
            print("%-38s → HTTP %-4s %s" % (name, code or "连不上", "OK" if ok else "FAIL"))
            if not ok:
                fails.append((name, code, text[:150]))
            time.sleep(0.4)

        print("\n=== 服务端日志里的来源与拒绝原因 ===")
        time.sleep(0.4)
        logs = list((home / "data" / "logs").glob("*.log")) if (home / "data" / "logs").exists() else []
        for f in logs:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if "拒绝" in line and "来源=" in line:
                    print("  " + line.strip()[:160])

        print("\n结论：%s" % ("全部符合预期" if not fails else "有不符合项 %s" % fails))
        return 0 if not fails else 1
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        if keep:
            print("临时目录：%s" % home)
        else:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
