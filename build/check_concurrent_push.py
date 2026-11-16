# -*- coding: utf-8 -*-
"""并发推送验收：同一秒多个群一起推，账本会不会丢、会不会多、回复会不会串。

不碰真实数据：临时 MSB_HOME + 随机端口起源码版服务。

检查项：
  1. 并发 8 个群 × 3 条 = 24 条，全部 200
  2. records.jsonl 正好 24 行，每行都是能解析的完整 JSON（没有半行交错）
  3. 看板 grand_total / 号码数 与预期一致（不多不少）
  4. 每条推送的回执 replies 都对应自己的 msg_id 和群，不串台
  5. 重复推同一批（同 msg_id）→ 全部跳过，金额不变
  6. 两个号监听同一个群（同内容不同 msg_id）→ 只记一次
  7. 去重文件 processed.json 写完仍是合法 JSON
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / "build" / "venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

KEY = "c" * 64
WID = "g18959290733"
CLIENTS = {"updated_at": "", "clients": [
    {"wechat_id": WID, "secret": KEY, "enabled": True,
     "expire_at": "9999-12-31", "ip_allow": ""}]}

GROUPS = 8
PER_GROUP = 3
AMOUNT = 100        # 三位金额避免歧义：两位金额会被当成号码读走


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sign(raw, ts):
    return hmac.new(KEY.encode(), ("%s." % ts).encode() + raw,
                    hashlib.sha256).hexdigest()


def push(base, wid, msgs):
    ts = int(time.time())
    body = json.dumps({"batch_id": "cc-%d-%s" % (ts, msgs[0]["msg_id"]),
                       "ts": ts, "account": wid, "count": len(msgs),
                       "messages": msgs}, ensure_ascii=False).encode("utf-8")
    s = requests.Session()
    try:
        r = s.post(base + "/wx/hook", data=body, timeout=20, headers={
            "Content-Type": "application/json",
            "X-Wx-Timestamp": str(ts), "X-Wx-Signature": sign(body, ts),
            "X-Wx-Cust": wid})
        return r.status_code, (r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {})
    except Exception as e:
        return 0, {"err": repr(e)}
    finally:
        s.close()


def main():
    home = Path(tempfile.mkdtemp(prefix="msb_conc_"))
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "config" / "server.json").write_text(json.dumps({
        "port": port, "host": "127.0.0.1", "wx_push_secret": "",
        "wx_account_secrets": "", "wx_skew_sec": 300,
        "llm_api_key": "sk-conc", "llm_timeout": 10, "llm_thinking": 0,
    }, ensure_ascii=False), encoding="utf-8")
    (home / "config" / "wx_clients.json").write_text(
        json.dumps(CLIENTS, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ, MSB_HOME=str(home), PYTHONPATH=str(ROOT))
    env.pop("WX_SKIP_VERIFY", None)
    proc = subprocess.Popen([PY, str(ROOT / "main.py"), "--headless", "--quiet"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = []

    def note(ok, label, extra=""):
        print("%s %s %s" % ("OK  " if ok else "FAIL", label, extra))
        if not ok:
            fails.append(label + " " + str(extra))

    try:
        up = False
        for _ in range(150):
            try:
                if requests.get(base + "/health", timeout=2).status_code == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not up:
            print("服务没起来")
            return 1

        token = (home / "data" / ".api_token").read_text(encoding="utf-8").strip()

        # ---------- 构造 8 个群 × 3 条，号码不重叠，金额都 10 ----------
        plan = []           # [(group_idx, [msg, msg, msg])]
        num = 1
        for g in range(GROUPS):
            chat_id = "chat%02d" % g
            chat_name = "测试群%02d" % g
            msgs = []
            for k in range(PER_GROUP):
                msgs.append({
                    "msg_id": "cc-%02d-%d-%d" % (g, k, int(time.time() * 1000)),
                    "target_id": chat_id, "chat_id": chat_id,
                    "chat_name": chat_name,
                    "sender": "u%02d" % g, "sender_name": "客户%02d" % g,
                    "msg_type": "文本", "create_time": int(time.time()),
                    "content": "%02d %d" % (num, AMOUNT),   # 如 "01 10"
                    "account": WID,
                })
                num += 1
            plan.append(msgs)

        # ---------- 1. 同时发 ----------
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=GROUPS) as ex:
            results = list(ex.map(lambda ms: push(base, WID, ms), plan))
        el = time.time() - t0
        all_200 = all(code == 200 for code, _ in results)
        note(all_200, "8 个群并发推送全部 200",
             "%s  %.2fs" % ([c for c, _ in results], el))

        recorded = sum(d.get("recorded", 0) for _, d in results)
        replied = sum(len(d.get("replies") or []) for _, d in results)
        note(recorded == GROUPS * PER_GROUP, "记账条数对得上",
             "期望 %d 实得 %d" % (GROUPS * PER_GROUP, recorded))
        note(replied == recorded, "回执条数 = 记账条数",
             "回复 %d / 记账 %d" % (replied, recorded))

        # ---------- 2. 每条回执都属于自己那条消息 ----------
        by_msg = {}
        for idx, (_, d) in enumerate(results):
            for rp in d.get("replies") or []:
                by_msg[rp["msg_id"]] = rp
        bad = []
        for g, msgs in enumerate(plan):
            for m in msgs:
                rp = by_msg.get(m["msg_id"])
                if not rp:
                    bad.append("缺回执 %s" % m["msg_id"])
                elif rp.get("chat") != m["chat_name"] or rp.get("chat_id") != m["chat_id"]:
                    bad.append("串台 %s → %s/%s" % (m["msg_id"], rp.get("chat"), rp.get("chat_id")))
                # 服务端文案会把 01 显示成 1（去前导零），所以比 "1 " 而不是 "01"
                elif "%s " % str(int(str(m["content"]).split()[0])) not in rp.get("text", ""):
                    bad.append("文案对不上 %s「%s」→「%s」"
                               % (m["msg_id"], m["content"], rp.get("text")))
        note(not bad, "每张回执都对得上自己的消息和群", bad[:3])

        # ---------- 3. 账本：行数 / JSON 完整性 / 金额 ----------
        time.sleep(1.0)
        recs = []
        bad_json = 0
        acc_dir = home / "data"
        files = list(acc_dir.rglob("records*.jsonl"))
        for f in files:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    bad_json += 1
        note(bad_json == 0, "records 每行都是完整 JSON", "坏行 %d" % bad_json)
        note(len(recs) == GROUPS * PER_GROUP, "records 行数正好 24",
             "实得 %d（文件 %d 个）" % (len(recs), len(files)))

        uniq = set(r.get("msg_id") for r in recs)
        note(len(uniq) == len(recs), "账本里没有重复 msg_id",
             "唯一 %d / 共 %d" % (len(uniq), len(recs)))

        amts = [r0.get("total", r0.get("amount")) for r0 in recs]
        note(all(v == AMOUNT for v in amts), "每条账的金额都对",
             "出现过的金额 %s" % sorted(set(amts))[:6])

        r = requests.get(base + "/api/dashboard", timeout=10,
                         headers={"X-Msb-Token": token}).json()
        expect_total = GROUPS * PER_GROUP * AMOUNT
        note(r.get("grand_total") == expect_total, "看板金额等于预期",
             "期望 %s 实得 %s" % (expect_total, r.get("grand_total")))

        # ---------- 4. 重复推同一批：应全部跳过，金额不变 ----------
        with ThreadPoolExecutor(max_workers=GROUPS) as ex:
            dup = list(ex.map(lambda ms: push(base, WID, ms), plan))
        skipped = sum(d.get("skipped", 0) for _, d in dup)
        note(skipped == GROUPS * PER_GROUP, "重推同一批全部跳过",
             "跳过 %d / 共 %d" % (skipped, GROUPS * PER_GROUP))
        time.sleep(0.6)
        r2 = requests.get(base + "/api/dashboard", timeout=10,
                          headers={"X-Msb-Token": token}).json()
        note(r2.get("grand_total") == expect_total, "重推之后金额没变",
             "实得 %s" % r2.get("grand_total"))

        # ---------- 5. 两个号监听同一个群：同内容不同 msg_id ----------
        same = dict(plan[0][0])
        same["msg_id"] = "cross-acct-%d" % int(time.time() * 1000)
        same["account"] = "another"
        code, d = push(base, WID, [same])
        note(code == 200 and d.get("skipped") == 1, "两个号同一条内容只记一次",
             "code=%s skipped=%s recorded=%s"
             % (code, d.get("skipped"), d.get("recorded")))

        # ---------- 6. 去重文件仍是合法 JSON ----------
        pf = home / "data" / "processed.json"
        ok_pf = True
        if pf.exists():
            try:
                json.loads(pf.read_text(encoding="utf-8"))
            except Exception as e:
                ok_pf = False
                note(False, "processed.json 被写坏", repr(e))
        note(ok_pf, "去重文件仍是合法 JSON", str(pf.name))

        # ---------- 7. 消息流水 ----------
        ms = requests.get(base + "/api/messages?limit=200", timeout=10,
                          headers={"X-Msb-Token": token}).json()
        rows = ms.get("messages") or []
        first_ids = set(m["msg_id"] for msgs in plan for m in msgs)
        rec_rows = [x for x in rows
                    if x.get("msg_id") in first_ids and str(x.get("result")) == "记账"]
        from collections import Counter
        cnt = Counter(x.get("msg_id") for x in rec_rows)
        note(len(rec_rows) == GROUPS * PER_GROUP, "首轮 24 条都有一条记账流水",
             "实得 %d" % len(rec_rows))
        note(cnt and all(v == 1 for v in cnt.values()), "每条消息只记一次账",
             cnt.most_common(3))

        # ---------- 8. 解析不了的不能被锁死 ----------
        bad_id = "unparsable-%d" % int(time.time() * 1000)
        badmsg = {"msg_id": bad_id, "target_id": "chatX", "chat_id": "chatX",
                  "chat_name": "测试群X", "sender": "u", "sender_name": "客户",
                  "msg_type": "文本", "create_time": int(time.time()),
                  "content": "早上好，今天有空吗", "account": WID}
        c1, d1 = push(base, WID, [badmsg])
        note(d1.get("unparsed") == 1, "无关内容判未解析", d1.get("unparsed"))
        # 同 msg_id 换成能记账的内容：没被锁死才记得上
        c2, d2 = push(base, WID, [dict(badmsg, content="01 100")])
        note(d2.get("recorded") == 1, "同 msg_id 改成可解析后能记账（没被锁死）",
             "recorded=%s skipped=%s" % (d2.get("recorded"), d2.get("skipped")))

        print("\n结论：%s" % ("全部通过" if not fails else "有失败项"))
        for f in fails:
            print("  -", f)
        return 0 if not fails else 1
    finally:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass
        if "--keep" in sys.argv:
            print("临时目录：%s" % home)
        else:
            import shutil
            shutil.rmtree(str(home), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
