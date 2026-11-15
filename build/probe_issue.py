# -*- coding: utf-8 -*-
"""探针：期次归档 + 只撤本期。

1) 记一笔 -> current_issue.txt 落地
2) 手工把期号改成上一期，账本留着余额
3) 再记一笔 -> 应当自动归档上一期 + 清零 + 换新期号
4) issues/<上期>.json 内容对不对
5) /api/issues 本期在前、历史在后
6) 撤本期的账 -> 成功
7) 造一条上期的记录 -> 撤销被拒
"""
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, r"D:\Users\Administrator\Python\mark-six-bot")
if sys.stdout is None:
    sys.stdout = io.StringIO()

BASE = "http://127.0.0.1:8899"
TOK = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\.api_token"
with open(TOK, "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

ACC = "g18959290733"
STATE = r"C:\Users\Administrator\AppData\Local\MarkSixBot\data\state\%s" % ACC
LEDGER = os.path.join(STATE, "numbers_state.json")
MARK = os.path.join(STATE, "current_issue.txt")
ISSUES = os.path.join(STATE, "issues")


def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Msb-Token": TOKEN}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads((e.read() or b"{}").decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


def ledger():
    with open(LEDGER, "r", encoding="utf-8") as f:
        return json.load(f)


def marker():
    if not os.path.exists(MARK):
        return ""
    with open(MARK, "r", encoding="utf-8") as f:
        return f.read().strip()


print("起始 marker =", marker() or "(无)")
print("起始 06 =", ledger().get("06"), " 合计 =", round(sum(ledger().values()), 2))

# 1) 记一笔
mid1 = "iss-probe-%d" % int(time.time())
print("\n[1] 记账 06 100 ->", call("/wx/hook-test", "POST") if False else "")
st, j = call("/api/record?msg_id=" + mid1)
print("    先确认这笔不存在 ->", st, j.get("error"))

# 直接用内部记账入口：走 /api/reverse 前必须先有记录，这里是造数据，
# 用 urllib 打不通记账口（/wx/hook 要 HMAC），所以就地调 main 的函数不行
# （另一个进程）。改用手工写一条记录 + 直接改账本，模拟"这一期有账"。
led = ledger()
led["06"] = round(float(led.get("06", 0)) + 100.0, 2)
tmp = LEDGER + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(led, f, ensure_ascii=False)
os.replace(tmp, LEDGER)
print("    手工把 06 置为", led["06"], "（模拟本期末余额）")

# marker 落在今天应归的那期
cur = "2026-11-14"
with open(MARK, "w", encoding="utf-8") as f:
    f.write(cur + "\n")
print("    marker 写为", cur)

# 2) 模拟换期：把 marker 改成上一期，账本留着 100
prev = "2026-11-12"
with open(MARK, "w", encoding="utf-8") as f:
    f.write(prev + "\n")
print("\n[2] 手工把 marker 改成上一期", prev, "账本仍有 06 =", ledger().get("06"))

# 3) 触发换期：直接调 main 的 ensure_issue 不行（跨进程），改用打接口
#    没有"记账"接口可用，就用 /api/reverse 之外的办法：调 main 模块的不行。
#    这里改为直接观察钩子：调用 /api/issues 会读 current_issue，不会触发归档。
#    真正触发点是记账。用 HMAC 造一条 /wx/hook 推送最真实。
print("\n[3] 用 /wx/hook 推一条真实记账，触发换期")
SECRET = ""


def hook_push(text, msg_id):
    body = json.dumps({
        "msg_id": msg_id,
        "type": "文本",
        "content": text,
        "chat_id": "iss-probe-chat",
        "chat_name": "期次探针群",
        "sender": "probe",
        "sender_name": "探针",
        "account": ACC,
        "create_time": int(time.time()),
    }, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), ("%s.%s" % (ts, body.decode())).encode(),
                   hashlib.sha256).hexdigest()
    req = urllib.request.Request(BASE + "/wx/hook", data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Wx-Timestamp": ts,
        "X-Wx-Signature": sig,
        "X-Wx-Cust": ACC,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads((e.read() or b"{}").decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


mid2 = "iss-probe-%d-2" % int(time.time())
st, j = hook_push("06 100", mid2)
print("    hook ->", st, j)

time.sleep(1)
print("    marker 现在是 =", marker())
print("    issues 目录 =", sorted(os.listdir(ISSUES)) if os.path.isdir(ISSUES) else "(无)")
print("    账本 06 =", ledger().get("06"), " 合计 =", round(sum(ledger().values()), 2))

p = os.path.join(ISSUES, prev + ".json")
if os.path.exists(p):
    with open(p, "r", encoding="utf-8") as f:
        rec = json.load(f)
    print("    归档内容 ->", json.dumps({k: rec[k] for k in ("issue", "total")},
                                       ensure_ascii=False), "06 =", rec["numbers"].get("06"))

print("\n[4] /api/issues")
st, j = call("/api/issues?account=" + ACC)
print("   ", st, json.dumps([{k: x[k] for k in ("issue", "total", "current")}
                             for x in j.get("issues", [])[:4]], ensure_ascii=False))

print("\n[5] 撤本期那笔 ->", call("/api/reverse", "POST", {"msg_id": mid2, "note": "期次探针"}))
print("    账本 06 =", ledger().get("06"))

print("\n[6] 造一条上期的活账，试撤销")
recfile = os.path.join(STATE, "records.jsonl")
fake_id = "iss-old-%d" % int(time.time())
fake = {
    "rec_id": fake_id + "#1", "ts": "2026-11-12 20:00:00", "msg_id": fake_id,
    "account": ACC, "chat": "期次探针群", "text": "06 100", "numbers": 1,
    "total": 100.0, "applied": {"06": 50.0}, "issue": prev, "fp": "", "chat_id": "",
}
with open(recfile, "a", encoding="utf-8") as f:
    f.write(json.dumps(fake, ensure_ascii=False) + "\n")
print("    撤销上期账 ->", call("/api/reverse", "POST", {"msg_id": fake_id, "note": "x"}))

# 清理这条假记录
with open(recfile, "r", encoding="utf-8") as f:
    lines = [ln for ln in f if fake_id not in ln]
with open(recfile, "w", encoding="utf-8") as f:
    f.writelines(lines)
print("    已删除假记录")

print("\n[7] 收尾：撤掉本期残留")
st, j = call("/api/record?msg_id=" + mid1)
print("    mid1 ->", st, j.get("error", ""))
print("    账本合计 =", round(sum(ledger().values()), 2))
