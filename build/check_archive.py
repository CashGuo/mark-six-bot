# -*- coding: utf-8 -*-
"""验换期归档：港澳两本盘 + 两边不计号，一起写进 issues/<期号>.json 再清零。

做法：先手造一份「上一期」的账（含香港盘），再记一笔记到本期，
ensure_issue 会把它归档。跑完删 selftest 账本。
"""

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import msb_view

DATA = Path(os.environ["LOCALAPPDATA"]) / "MarkSixBot" / "data"
STATE = DATA / "state" / "selftest"
TOK = (DATA / ".api_token").read_text(encoding="utf-8").strip()
URL = "http://127.0.0.1:8899"
ACC = "selftest"
OLD_ISSUE = "2026-11-10"


def post(path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Msb-Token", TOK)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8") or "{}")


if STATE.exists():
    shutil.rmtree(STATE)
STATE.mkdir(parents=True, exist_ok=True)

boards = {f"{i:02d}": 0.0 for i in range(1, 50)}
boards["07"] = 200.0
hk = {f"{i:02d}": 0.0 for i in range(1, 50)}
hk["01"] = 300.0
(STATE / "numbers_state.json").write_text(json.dumps(boards), encoding="utf-8")
(STATE / "numbers_state_hk.json").write_text(json.dumps(hk), encoding="utf-8")
(STATE / "others_state.json").write_text(
    json.dumps({"total": 20.0, "items": [{"rec_id": "x", "play": "二中二", "amount": 20.0}]}),
    encoding="utf-8")
(STATE / "others_state_hk.json").write_text(
    json.dumps({"total": 100.0, "items": [{"rec_id": "y", "play": "平特鼠", "amount": 100.0}]}),
    encoding="utf-8")
(STATE / "current_issue.txt").write_text(OLD_ISSUE, encoding="utf-8")

print("旧期 %s：澳 200 + 20，港 300 + 100" % OLD_ISSUE)
d = post("/api/quick_bet", {"text": "澳门 牛20", "account": ACC})
print("记一笔本期 → ok=%s 市场=%s 累计=%s" % (d.get("ok"), d.get("market"), d.get("grand_total")))
print("返回里的换期提示：期号 %s ／ 上一期金额 %s（应为 %s / 620）"
      % (d.get("archived_issue"), d.get("archived_total"), OLD_ISSUE))
hint_ok = (d.get("archived_issue") == OLD_ISSUE
           and abs(float(d.get("archived_total") or 0) - 620) < 0.01)

arch = STATE / "issues" / ("%s.json" % OLD_ISSUE)
ok = arch.exists()
if ok:
    j = json.loads(arch.read_text(encoding="utf-8"))
    mo = (j.get("markets") or {}).get("MO") or {}
    hk_b = (j.get("markets") or {}).get("HK") or {}
    print("归档 %s：澳号盘 %s 澳不计号 %s ｜ 港号盘 %s 港不计号 %s ｜ 合计 %s"
          % (OLD_ISSUE, mo.get("total"), mo.get("flat_total"),
             hk_b.get("total"), hk_b.get("flat_total"), j.get("grand_total")))
    ok = (abs(float(mo.get("total") or 0) - 200) < 0.01
          and abs(float(mo.get("flat_total") or 0) - 20) < 0.01
          and abs(float(hk_b.get("total") or 0) - 300) < 0.01
          and abs(float(hk_b.get("flat_total") or 0) - 100) < 0.01
          and abs(float(j.get("grand_total") or 0) - 620) < 0.01)
else:
    print("没生成归档文件")

after_mo = sum(float(v or 0) for v in json.loads((STATE / "numbers_state.json").read_text()).values())
after_hk = sum(float(v or 0) for v in json.loads((STATE / "numbers_state_hk.json").read_text()).values())
cold = json.loads((STATE / "others_state.json").read_text()).get("total")
hkold = json.loads((STATE / "others_state_hk.json").read_text()).get("total")
print("归档后账本：澳盘 %s（应为 80，牛4号各20）｜ 港盘 %s（应为 0）｜ 不计号 %s / %s（应为 0/0）"
      % (after_mo, after_hk, cold, hkold))
ok = ok and hint_ok and abs(after_mo - 80) < 0.01 and abs(after_hk) < 0.01 \
    and abs(float(cold or 0)) < 0.01 and abs(float(hkold or 0)) < 0.01

shutil.rmtree(STATE, ignore_errors=True)
print("\n%s，selftest 账本已删" % ("通过" if ok else "失败"))
sys.exit(0 if ok else 1)
