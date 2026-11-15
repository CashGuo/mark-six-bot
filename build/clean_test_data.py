# -*- coding: utf-8 -*-
"""清测试残留（默认只预览，加 --apply 才真删）。

测试时往账本里记的假单、假账号，会在账号下拉和「挂在谁名下」里堆一堆，
这份脚本一次清干净。清什么：

  1. 测试账号目录（名字在 DROP_ACCS 里，或账本里全是测试单）
  2. 各账号流水 / 未解析里 chat 命中 TESTS 的行
  3. 按剩下的流水重算当期盘面与归档期账本
  4. 当天消息日志里测试名的行

不动的东西：chat 是真实客户名的一律保留。删之前先备份整个 state 目录。

用法（工作目录 = 工程根，服务最好先停）：
    python build\\clean_test_data.py          # 只看，打印会删哪些
    python build\\clean_test_data.py --apply  # 真删
"""
import io
import json
import os
import shutil
import sys

DATA = os.path.join(os.environ["LOCALAPPDATA"], "MarkSixBot", "data")
STATE = os.path.join(DATA, "state")
BACKUPS = os.path.join(os.environ["LOCALAPPDATA"], "MarkSixBot", "backups")
APPLY = "--apply" in sys.argv

# 这些名字出现在 chat 里就算测试单
TESTS = ("手工记账", "网页自测", "界面自测", "地址测试", "selftest",
         "规则测试", "自测", "测试", "test", "probe")
# 这些账号整个删掉
DROP_ACCS = ("default", "selftest")

NUMS = ["%02d" % i for i in range(1, 50)]


def rd_json(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def rd_lines(p):
    if not os.path.exists(p):
        return []
    with io.open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def wr_json(p, obj):
    if APPLY:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)


def wr_lines(p, rows):
    if APPLY:
        with io.open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def is_test(row):
    c = str(row.get("chat") or "")
    return any(t in c for t in TESTS)


def backup():
    if not APPLY:
        return ""
    import time
    dst = os.path.join(BACKUPS, "cleanup-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copytree(STATE, dst)
    return dst


def clean_account(acc):
    d = os.path.join(STATE, acc)
    cur = ""
    cip = os.path.join(d, "current_issue.txt")
    if os.path.exists(cip):
        with io.open(cip, encoding="utf-8") as f:
            cur = f.read().strip()

    rows = rd_lines(os.path.join(d, "records.jsonl"))
    keep = [r for r in rows if not is_test(r)]
    drop = [r for r in rows if is_test(r)]
    print("\n[%s] 流水 %d 行 → 留 %d 行" % (acc, len(rows), len(keep)))
    for r in drop:
        print("   - %s | %s | %s | %s" % (str(r.get("ts") or "")[:16],
                                          r.get("chat"),
                                          str(r.get("text") or "")[:24],
                                          r.get("total")))

    un_p = os.path.join(d, "unparsed.jsonl")
    un = rd_lines(un_p)
    un_keep = [r for r in un if not is_test(r)]
    if un:
        print("   未解析 %d 行 → 留 %d 行" % (len(un), len(un_keep)))

    # 按剩余流水重算：当期盘面 + 归档期
    mo = {k: 0.0 for k in NUMS}
    hk = {k: 0.0 for k in NUMS}
    arch = {}
    for r in keep:
        ap = r.get("applied")
        if not isinstance(ap, dict) or not ap:
            continue
        issue = r.get("issue") or ""
        mkt = "HK" if (r.get("market") or "") == "HK" else "MO"
        if issue == cur:
            tgt = hk if mkt == "HK" else mo
            for k, v in ap.items():
                if str(k) in tgt:
                    tgt[str(k)] = round(tgt[str(k)] + float(v), 2)
        else:
            dd = arch.setdefault(issue, {"MO": {k: 0.0 for k in NUMS},
                                         "HK": {k: 0.0 for k in NUMS}})
            for k, v in ap.items():
                if str(k) in dd[mkt]:
                    dd[mkt][str(k)] = round(dd[mkt][str(k)] + float(v), 2)

    print("   当期 %s → 澳门 %s 元 / 香港 %s 元"
          % (cur or "（无）", round(sum(mo.values()), 2), round(sum(hk.values()), 2)))
    wr_json(os.path.join(d, "numbers_state.json"), mo)
    wr_json(os.path.join(d, "numbers_state_hk.json"), hk)

    for issue, dd in sorted(arch.items()):
        p = os.path.join(d, "issues", issue + ".json")
        if not os.path.exists(p):
            continue
        old = rd_json(p)
        old["numbers"] = {k: round(dd["MO"][k] + dd["HK"][k], 2) for k in NUMS}
        old["total"] = round(sum(old["numbers"].values()), 2)
        old["grand_total"] = round(old["total"] + float(old.get("flat_total") or 0), 2)
        mk = old.setdefault("markets", {})
        for name in ("MO", "HK"):
            m = mk.setdefault(name, {})
            m["numbers"] = dd[name]
            m["total"] = round(sum(dd[name].values()), 2)
            m["grand_total"] = round(m["total"] + float(m.get("flat_total") or 0), 2)
        print("   归档 %s → 总 %s 元（澳门 %s / 香港 %s）"
              % (issue, old["total"], mk["MO"]["total"], mk["HK"]["total"]))
        wr_json(p, old)

    wr_lines(un_p, un_keep)
    wr_lines(os.path.join(d, "records.jsonl"), keep)


def main():
    print("模式：%s" % ("真删真写" if APPLY else "只看不动（加 --apply 才执行）"))
    bk = backup()
    if bk:
        print("备份 → %s" % bk)

    if not os.path.isdir(STATE):
        print("没有 state 目录：%s" % STATE)
        return 1

    for a in DROP_ACCS:
        p = os.path.join(STATE, a)
        if os.path.isdir(p):
            print("删测试账号 %s" % a)
            if APPLY:
                shutil.rmtree(p, ignore_errors=True)

    for a in sorted(os.listdir(STATE)):
        if os.path.isdir(os.path.join(STATE, a)) and a not in DROP_ACCS:
            clean_account(a)

    msg_dir = os.path.join(DATA, "messages")
    if os.path.isdir(msg_dir):
        for fn in sorted(os.listdir(msg_dir)):
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(msg_dir, fn)
            rr = rd_lines(p)
            k = [r for r in rr if not is_test(r)]
            if len(k) != len(rr):
                print("\n消息日志 %s：%d → %d 行" % (fn, len(rr), len(k)))
                wr_lines(p, k)

    print("\n%s" % ("已执行。" if APPLY else "以上只是预览，没有改动任何文件。"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
