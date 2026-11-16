# -*- coding: utf-8 -*-
"""验证「开奖时间封盘」：过点不收注、次日自动进下一期、手工可补录。

分四段：
  A. 判定函数：用固定日期断言（不看这台机器现在几点，也不改系统时间）
  B. 端到端「已封盘」：消息时间在开奖后 → 不记账 + 回执说明 + 流水留痕
  C. 端到端「未封盘」：消息时间在开奖前 → 正常记账
  D. 手工补录：allow_after_close=True 时，同一个封盘时刻也能记

用法（在工程根）：
    python build\\check_draw_cutoff.py
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
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "build" / "venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

SEC = "7" * 64
WID = "cut1"
DRAW = "21:30"
# 2026-11-14 是周六（开奖日）：21:40 已过开奖点，21:20 还没到。
# 用固定历史日期，测试结果跟"今天星期几"无关。
TS_CLOSED = int(datetime(2026, 11, 14, 21, 40).timestamp())
TS_OPEN = int(datetime(2026, 11, 14, 21, 20).timestamp())


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


def push(port, msg_id, text, create_time):
    body = {
        "batch_id": "%d-%s" % (int(time.time() * 1000), uuid.uuid4().hex[:8]),
        "ts": int(time.time()), "account": WID, "count": 1,
        "messages": [{
            "msg_id": msg_id, "target_id": "chat_cut", "chat_name": "封盘测试群",
            "sender": 1, "sender_name": "客户", "msg_type": "文本",
            "create_time": create_time, "content": text,
            "account": WID, "collected_at": "",
        }],
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "Connection": "close",
               "X-Wx-Timestamp": ts, "X-Wx-Signature": sign(SEC, ts, raw),
               "X-Wx-Cust": WID}
    sess = requests.Session()
    try:
        r = sess.post("http://127.0.0.1:%d/wx/hook" % port, data=raw,
                      headers=headers, timeout=15)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": type(e).__name__}
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


def start_server(draw_time=DRAW):
    home = Path(tempfile.mkdtemp(prefix="msb_cut_"))
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "server.json").write_text(json.dumps({
        "port": port, "host": "127.0.0.1", "wx_push_secret": "",
        "wx_account_secrets": "", "wx_skew_sec": 300,
        "llm_api_key": "sk-cutcheck", "llm_timeout": 20, "llm_thinking": 0,
        "draw_time": draw_time,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg / "wx_clients.json").write_text(json.dumps({
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
    return proc, home, port, base, wait_health(base)


def dash(base):
    with urllib.request.urlopen(base + "/api/dashboard", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def messages_with(home, kw):
    d = home / "data" / "messages"
    out = []
    if not d.exists():
        return out
    for f in d.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if kw in str(row.get("reason") or ""):
                out.append(row)
    return out


def logic_checks(fails):
    home = Path(tempfile.mkdtemp(prefix="msb_cut_logic_"))
    script = home / "run.py"
    script.write_text(
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import main\n"
        "cases = json.loads(sys.argv[1])\n"
        "out = []\n"
        "for at, want_close, want_issue in cases:\n"
        "    st = main.draw_cutoff_state(at)\n"
        "    out.append([at, bool(st['closed']), st['issue'], st['draw_time'],\n"
        "                want_close, want_issue])\n"
        "print(json.dumps(out, ensure_ascii=False))\n" % str(ROOT),
        encoding="utf-8")
    cases = [
        ["2026-11-17 21:20:00", False, "2026-11-17"],   # 开奖前 10 分钟：收
        ["2026-11-17 21:30:00", True, "2026-11-17"],    # 刚好到点：封
        ["2026-11-17 21:40:00", True, "2026-11-17"],    # 开奖后：封
        ["2026-11-17 23:59:00", True, "2026-11-17"],    # 当晚剩余时间都封
        ["2026-11-18 00:10:00", False, "2026-11-19"],   # 次日：自动进周四期，开
        ["2026-11-16 10:00:00", False, "2026-11-17"],   # 周一（非开奖日）：给周二那期收
        ["2026-11-19 21:45:00", True, "2026-11-19"],    # 周四开奖后：封
        ["2026-11-21 21:45:00", True, "2026-11-21"],    # 周六开奖后：封
        ["2026-11-22 10:00:00", False, "2026-11-24"],   # 周日：给下周二那期收
    ]
    env = dict(os.environ)
    env["MSB_HOME"] = str(Path(tempfile.mkdtemp(prefix="msb_cut_logic_home_")))
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([PY, str(script), json.dumps(cases)],
                          capture_output=True, env=env, timeout=180)
    try:
        rows = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    except Exception:
        print(proc.stdout.decode("utf-8", "replace")[-1200:])
        print(proc.stderr.decode("utf-8", "replace")[-1200:])
        fails.append(("逻辑段脚本跑不起来", "", ""))
        return
    print("=== A. 判定函数（固定日期，跟现在几点无关）===")
    for at, closed, issue, draw, want_c, want_i in rows:
        ok = (closed == want_c) and (issue == want_i)
        print("%-20s 期次 %s 开奖 %s 封盘 %-5s 期望 封盘%-5s/期%s  %s"
              % (at, issue, draw, closed, want_c, want_i, "OK" if ok else "FAIL"))
        if not ok:
            fails.append(("draw_cutoff_state(%s)" % at, closed, want_c))
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(env["MSB_HOME"], ignore_errors=True)


def main():
    keep = "--keep" in sys.argv
    fails = []

    def note(ok, label, extra=""):
        print("%s %s %s" % ("OK  " if ok else "FAIL", label, extra))
        if not ok:
            fails.append(label)

    logic_checks(fails)

    # ---------- B. 消息时间在开奖后 ----------
    print("\n=== B. 消息时间 2026-11-14 21:40（周六开奖后）===")
    proc = home = None
    try:
        proc, home, port, base, ok = start_server()
        if not ok:
            print("服务没起来")
            try:
                proc.kill()
                print((proc.stdout.read() or b"").decode("utf-8", "replace")[-1500:])
            except Exception:
                pass
            return 1
        d = dash(base)
        print("     看板：draw_time=%s（现在是否封盘取决于今天是不是开奖日）" % d.get("draw_time"))
        note(d.get("draw_time") == DRAW, "看板带上了配置的开奖时间", d.get("draw_time"))
        note("closed" in d and "seconds_left" in d, "看板返回封盘状态和剩余秒数")

        mid = "cut-b-%d" % int(time.time() * 1000)
        code, j = push(port, mid, "01 100", TS_CLOSED)
        rs = j.get("replies") or []
        print("     推送 → HTTP %s recorded=%s skipped=%s replies=%s"
              % (code, j.get("recorded"), j.get("skipped"),
                 rs[0]["text"] if rs else "无"))
        note(j.get("recorded") == 0 and j.get("skipped") == 1, "开奖后的注不记账")
        note(len(rs) == 1 and "已开奖" in rs[0]["text"],
             "客户收到「已开奖、没记上」的说明", rs[0]["text"] if rs else "")
        note(float(dash(base).get("grand_total") or 0) == 0.0, "看板金额没变")

        rows = messages_with(home, "已开奖")
        note(len(rows) >= 1, "消息流水里留了这条（老板能对账）", "流水 %d 条" % len(rows))

        mid2 = "cut-b2-%d" % int(time.time() * 1000)
        code2, j2 = push(port, mid2, "01 100", TS_CLOSED)
        note(j2.get("skipped") == 1 and j2.get("recorded") == 0,
             "同内容重发仍按「已开奖」，没被指纹误判成跨号重复")
    finally:
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
        if home and not keep:
            shutil.rmtree(home, ignore_errors=True)

    # ---------- C. 消息时间在开奖前 ----------
    print("\n=== C. 消息时间 2026-11-14 21:20（周六开奖前 10 分钟）===")
    proc = home = None
    try:
        proc, home, port, base, ok = start_server()
        if not ok:
            print("服务没起来")
            return 1
        mid = "cut-c-%d" % int(time.time() * 1000)
        code, j = push(port, mid, "01 100", TS_OPEN)
        rs = j.get("replies") or []
        print("     推送 → HTTP %s recorded=%s replies=%s"
              % (code, j.get("recorded"), rs[0]["text"] if rs else "无"))
        note(j.get("recorded") == 1, "开奖前发的注照常记账（采集端推晚了也不误杀）")
        note(len(rs) == 1 and "已开奖" not in rs[0]["text"], "回执是正常记账文案")
    finally:
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except Exception:
                pass
        if home and not keep:
            shutil.rmtree(home, ignore_errors=True)

    # ---------- D. 手工补录 ----------
    print("\n=== D. 手工补录（同一个封盘时刻，allow_after_close=True）===")
    home = Path(tempfile.mkdtemp(prefix="msb_cut_manual_"))
    script = home / "run.py"
    script.write_text(
        "import json, sys\n"
        "sys.path.insert(0, %r)\n"
        "import main\n"
        "ts = int(sys.argv[1])\n"
        "a = main.record_bet('01 100', 'm1', chat_name='x', create_time=ts)\n"
        "b = main.record_bet('01 100', 'm2', chat_name='x', create_time=ts,\n"
        "                    allow_after_close=True)\n"
        "print(json.dumps({'push': a, 'manual': b}, ensure_ascii=False))\n" % str(ROOT),
        encoding="utf-8")
    env = dict(os.environ)
    env["MSB_HOME"] = str(home / "home")
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run([PY, str(script), str(TS_CLOSED)],
                          capture_output=True, env=env, timeout=180)
    try:
        res = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    except Exception:
        print(proc.stdout.decode("utf-8", "replace")[-1200:])
        print(proc.stderr.decode("utf-8", "replace")[-1200:])
        res = {}
    push_r = res.get("push") or {}
    man_r = res.get("manual") or {}
    print("     推送口径 → ok=%s closed=%s reason=%s"
          % (push_r.get("ok"), push_r.get("closed"), push_r.get("reason")))
    print("     手工口径 → ok=%s total=%s" % (man_r.get("ok"), man_r.get("total")))
    note(push_r.get("ok") is False and push_r.get("closed") is True,
         "推切口径在开奖后拒绝")
    note(man_r.get("ok") is True, "手工口径（allow_after_close）照常补录")
    shutil.rmtree(home, ignore_errors=True)

    print("\n结论：%s" % ("全部通过" if not fails else "有失败项 %s" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
