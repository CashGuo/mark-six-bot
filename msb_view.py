# -*- coding: utf-8 -*-
"""服务端控制台用的只读数据层 —— 从数据目录直接算，供 Tk 界面展示。

**故意不 import main**：main 一被导入就会挂日志 FileHandler、建 Flask app、
抢 .bot.lock，面板进程和后台服务是两条进程，碰同一份日志在 Windows 上表现为
后台服务起不来（文件被占用）。

所以这里独立实现一份，口径与 main.py 的 /api/dashboard 对齐：
  · 生肖表按农历当年动态算（马年 01/13/25/37/49 这些）
  · 号码表 = state/<账号>/numbers_state.json 的 49 个号码累计
  · 风险 = 只取 >0 的号码按金额倒序前 N
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import msb_paths as _paths

# 和 main.py 同样的注入：源码模式下 LunarDate 装在项目根的 venv 里，
# 打包后由 Nuitka 一并收进包内，不需要这一步。不注入的话生肖表会走兜底，
# 号码盘只出 3 个生肖（和网页的 12 行对不上）。
_SITE_PACKAGES = _paths.APP_DIR / "venv" / "Lib" / "site-packages"
if _SITE_PACKAGES.exists() and str(_SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_SITE_PACKAGES))

ZODIAC_ANIMALS = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]

COLOR_MAP = {
    "红波": ["01", "02", "07", "08", "12", "13", "18", "19", "23", "24",
             "29", "30", "34", "35", "40", "45", "46"],
    "蓝波": ["03", "04", "09", "10", "14", "15", "20", "25", "26", "31",
             "36", "37", "41", "42", "47", "48"],
    "绿波": ["05", "06", "11", "16", "17", "21", "22", "27", "28", "32",
             "33", "38", "39", "43", "44", "49"],
}

COLOR_OF = {}
for _cname, _nums in COLOR_MAP.items():
    for _n in _nums:
        COLOR_OF[_n] = _cname


# ---------------- 目录 ----------------

def state_dir() -> Path:
    return _paths.DATA_DIR / "state"


def contacts_dir() -> Path:
    return _paths.DATA_DIR / "contacts"


def targets_dir() -> Path:
    return _paths.DATA_DIR / "targets"


def logs_dir() -> Path:
    return _paths.DATA_DIR / "logs"


def backups_dir() -> Path:
    return _paths.DATA_DIR / "backups"


# ---------------- 通用读写 ----------------

def read_json(path, default=None):
    try:
        if Path(path).exists():
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def tail_jsonl(path, n: int) -> list:
    """读 jsonl 最后 n 条（坏行跳过，不整段失败）。"""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f.readlines()[-n:]:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def count_today(path) -> int:
    """今日笔数：按行首 20 字符里是否出现当天日期算（和 main._count_today 同口径）。"""
    p = Path(path)
    if not p.exists():
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if today in line[:20]:
                    n += 1
    except Exception:
        pass
    return n


# ---------------- 账号 ----------------

def all_accounts() -> list:
    """已真正接入的账号：有账本，或上报过会话清单（targets 空壳不算）。"""
    out = set()
    for p in state_dir().glob("*/numbers_state.json"):
        out.add(p.parent.name)
    for p in contacts_dir().glob("*.json"):
        out.add(p.stem)
    return sorted(out)


# ---------------- 生肖与号码 ----------------

_zodiac_cache = {"day": None, "value": None}


def _lunar_class():
    """拿到 LunarDate 类。模块名大小写在不同解释器里写法不一（lunardate / LunarDate），
    新版把方法改成了 from_solar_date，旧版是 fromSolarDate —— 都兼容一下。"""
    for name in ("LunarDate", "lunardate"):
        try:
            mod = __import__(name, fromlist=["LunarDate"])
        except Exception:
            continue
        cls = getattr(mod, "LunarDate", None)
        if cls is not None:
            return cls
    return None


def zodiac_year():
    """(农历年, 当年生肖, {生肖: [号码...]})。按天缓存。"""
    today = datetime.now().strftime("%Y-%m-%d")
    if _zodiac_cache["day"] != today:
        _zodiac_cache["day"] = today
        try:
            cls = _lunar_class()
            if cls is None:
                raise ImportError("lunardate 不可用")
            d = datetime.now()
            fn = getattr(cls, "from_solar_date", None) or getattr(cls, "fromSolarDate")
            lunar = fn(d.year, d.month, d.day)
            lunar_year = lunar.year
            idx = (lunar_year - 4) % 12
            mapping = {}
            for i, animal in enumerate(ZODIAC_ANIMALS):
                offset = (i - idx) % 12
                if offset == 0:
                    offset = 12
                remainder = (13 - offset) % 12
                if remainder == 0:
                    remainder = 12
                mapping[animal] = [f"{n:02d}" for n in range(1, 50)
                                   if n % 12 == (remainder % 12)]
            _zodiac_cache["value"] = (lunar_year, ZODIAC_ANIMALS[idx], mapping)
        except Exception:
            _zodiac_cache["value"] = (2026, "马", {
                "马": ["01", "13", "25", "37", "49"],
                "鼠": ["07", "19", "31", "43"],
                "鸡": ["10", "22", "34", "46"],
            })
    return _zodiac_cache["value"]


def empty_numbers() -> dict:
    return {f"{i:02d}": 0.0 for i in range(1, 50)}


def _board_file(market: str) -> str:
    return "numbers_state_hk.json" if market == "HK" else "numbers_state.json"


def _others_file(market: str) -> str:
    return "others_state_hk.json" if market == "HK" else "others_state.json"


def numbers(account: str = "", market: str = "MO") -> dict:
    """account 为空 → 所有账号合并；给了 → 只看这一个号。

    market: MO 澳门盘 / HK 香港盘 / ALL 两本相加。
    """
    out = empty_numbers()
    ms = ["MO", "HK"] if market == "ALL" else [market]
    accs = [account] if account else all_accounts()
    for acc in accs:
        for m in ms:
            d = read_json(state_dir() / acc / _board_file(m), None)
            if isinstance(d, dict):
                for k, v in d.items():
                    if k in out:
                        out[k] += float(v or 0)
    return {k: round(v, 2) for k, v in out.items()}


def others_total(account: str = "", market: str = "MO") -> float:
    """不进 49 号盘的钱：平特整肖、二中二/三中二/三中三这些只记总额的玩法。

    号盘是"每个号多少钱"，这些玩法落不进去，所以单独记一份；
    总金额必须带上它，否则对不上客户报的数。market 同 numbers。
    """
    def one(acc: str, m: str) -> float:
        d = read_json(state_dir() / acc / _others_file(m), None)
        return float(d.get("total") or 0) if isinstance(d, dict) else 0.0

    ms = ["MO", "HK"] if market == "ALL" else [market]
    accs = [account] if account else all_accounts()
    return round(sum(one(a, m) for a in accs for m in ms), 2)


def market_totals(account: str = "") -> dict:
    """两本账各多少钱：{MO:{board,flat,total}, HK:{...}, ALL:合计}"""
    out = {}
    for m in ("MO", "HK"):
        board = round(sum(float(v or 0) for v in numbers(account, m).values()), 2)
        flat = others_total(account, m)
        out[m] = {"board": board, "flat": flat, "total": round(board + flat, 2)}
    out["ALL"] = {k: round(out["MO"][k] + out["HK"][k], 2)
                  for k in ("board", "flat", "total")}
    return out


# ---------------- 生肖注（平特 / 连肖 / 二中二 / 三中三） ----------------
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def others_items(account: str = "", market: str = "MO") -> list:
    """不计号的钱逐条明细（平特整肖 / 连肖 / 二中二…）。空账号 = 所有账号。"""
    ms = ["MO", "HK"] if market == "ALL" else [market]
    accs = [account] if account else all_accounts()
    out = []
    for acc in accs:
        for m in ms:
            d = read_json(state_dir() / acc / _others_file(m), None)
            if isinstance(d, dict):
                out += [x for x in (d.get("items") or []) if isinstance(x, dict)]
    return out


def extract_zodiacs(text: str) -> list:
    """原文里出现了哪几个生肖字。12 个生肖字不会跟号码、金额撞。"""
    t = str(text or "")
    return [z for z in ZODIAC_ANIMALS if z in t]


def lian_len(text: str) -> int:
    """连肖位数：二/三/四/五 或 2/3/4/5；没写按二连。"""
    m = re.search(r"([一二两三四五六1-6])\s*连肖", str(text or ""))
    if not m:
        return 2
    return _CN_NUM.get(m.group(1)) or int(m.group(1))


def zodiac_board(account: str = "", market: str = "MO") -> dict:
    """总览用：12 生肖金额（平特与连肖合并一列）+ 组合注（二中二/三中三整注）。

    只读推算，不改账。不落号码盘的注现在只存了 text 和 amount，这里按原文还原
    「哪几个肖、每个肖多少钱」：
      · 带「各」= 每个肖各拿「各」后面那个数（账上记的是整笔）
      · 不带「各」= 整笔在几个肖之间分摊，抹到分，余数给第一个肖
      · 连肖按「二/三/四/五连肖」补位，从写的那个肖按传统序往后连（鼠二连肖=鼠+牛）
    等 bet_rules 改了口径、把这些字段直接存进 items，这里就改成读字段。
    """
    mapping = zodiac_year()[2]
    rows = [{"zodiac": z,
             "nums": " ".join(str(n) for n in mapping.get(z, [])),
             "amount": 0.0, "pingte": 0.0, "lianxiao": 0.0}
            for z in ZODIAC_ANIMALS]
    idx = {r["zodiac"]: r for r in rows}

    items = others_items(account, market)
    combo, unknown = [], []
    tot = {"board": 0.0, "combo": 0.0, "unknown": 0.0}

    for it in items:
        text = str(it.get("text") or "")
        try:
            amt = round(float(it.get("amount") or 0), 2)
        except Exception:
            amt = 0.0
        play = str(it.get("play") or "")
        zs = extract_zodiacs(text)
        if play == "zhong" or ("中" in text and "连肖" not in text and "平" not in text):
            combo.append({"text": text, "amount": amt, "zodiacs": zs,
                          "ts": str(it.get("ts") or ""),
                          "need": ("6 个平特号同时有 " + "、".join(zs))
                                  if zs else "按原文核对"})
            tot["combo"] = round(tot["combo"] + amt, 2)
            continue
        if not zs:
            unknown.append({"text": text, "amount": amt,
                            "ts": str(it.get("ts") or "")})
            tot["unknown"] = round(tot["unknown"] + amt, 2)
            continue
        kind = "lianxiao" if (play == "lianxiao" or "连肖" in text) else "pingte"
        if kind == "lianxiao":
            n = lian_len(text)
            if len(zs) < n:
                i = ZODIAC_ANIMALS.index(zs[0])
                zs = [ZODIAC_ANIMALS[(i + k) % 12] for k in range(n)]
        n = len(zs)
        m_each = re.search(r"各\s*(\d+(?:\.\d+)?)", text)
        each_one = None
        if m_each:
            try:
                each_one = float(m_each.group(1))
            except Exception:
                each_one = None
        if each_one is not None:
            per = {z: round(each_one, 2) for z in zs}
        else:
            base = float(int(amt / n * 100)) / 100.0
            per = {z: base for z in zs}
            per[zs[0]] = round(amt - base * (n - 1), 2)
        for z in zs:
            r = idx.get(z)
            if not r:
                continue
            r[kind] = round(r[kind] + per[z], 2)
            r["amount"] = round(r["amount"] + per[z], 2)
        tot["board"] = round(tot["board"] + sum(per.values()), 2)

    return {"rows": rows, "combo": combo[-12:], "unknown": unknown[-6:],
            "totals": tot, "count": len(items)}


def grid_from(data: dict) -> list:
    """号码盘：行 = 生肖（按 01~12 各自所属生肖排序），列 = 该生肖名下号码。"""
    _, _, mapping = zodiac_year()
    num_to_zodiac = {}
    for z, nums in mapping.items():
        for n in nums:
            num_to_zodiac[n] = z
    rows, seen = [], set()
    for i in range(1, 13):
        z = num_to_zodiac.get(f"{i:02d}")
        if not z or z in seen:
            continue
        seen.add(z)
        rows.append({
            "zodiac": z,
            "cells": [{
                "num": n,
                "amount": round(float(data.get(n, 0) or 0), 2),
                "color": COLOR_OF.get(n, ""),
            } for n in sorted(mapping.get(z, []))],
        })
    return rows


def grid_rows(account: str = "", market: str = "MO") -> list:
    return grid_from(numbers(account, market))


def risk_from(data: dict, n: int = 10) -> list:
    """总额倒序前 n 名（只取有金额的号码）。n=49 就是全部有注的号。"""
    _, _, mapping = zodiac_year()
    num_to_zodiac = {}
    for z, nums in mapping.items():
        for num in nums:
            num_to_zodiac[num] = z
    total = sum(data.values()) or 1.0
    top = sorted([(k, v) for k, v in data.items() if v > 0],
                 key=lambda x: x[1], reverse=True)[:n]
    return [{
        "rank": i + 1,
        "num": num,
        "zodiac": num_to_zodiac.get(num, ""),
        "amount": round(amt, 2),
        "share": round(amt / total * 100, 1),
    } for i, (num, amt) in enumerate(top)]


def risk_rows(account: str = "", n: int = 10, market: str = "MO") -> list:
    return risk_from(numbers(account, market), n)


def _reversed_rec_ids(account: str = "") -> set:
    """被撤销过的记账记录 id。

    撤销不是删行，是往流水里追加一条 type=reversal、reversal_of=<原记录 id>。
    这里把被冲正过的 id 收集起来，界面拿它判断哪一行显示「已撤销」。
    口径与 main.py 的 _reversed_ids() 一致。
    """
    accs = [account] if account else all_accounts()
    ids = set()
    for a in accs:
        p = state_dir() / a / "records.jsonl"
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if isinstance(r, dict) and str(r.get("type") or "") == "reversal":
                        ids.add(str(r.get("reversal_of") or ""))
        except Exception:
            continue
    return ids


def recent_records(account: str = "", n: int = 40) -> list:
    """最近记账，最新在上。合计视图下把各账号拼起来再统一排序。

    每条补一个 reversed 字段：这一笔是不是已经被撤销了。
    """
    if account:
        rows = tail_jsonl(state_dir() / account / "records.jsonl", n)
    else:
        rows = []
        for acc in all_accounts():
            rows += tail_jsonl(state_dir() / acc / "records.jsonl", n)
        rows = sorted(rows, key=lambda x: str(x.get("ts") or ""))[-n:]
    try:
        revd = _reversed_rec_ids(account)
    except Exception:
        revd = set()
    out = []
    for r in rows[::-1]:
        if isinstance(r, dict) and str(r.get("type") or "") == "reversal":
            continue        # 冲正记录本身不是一笔账，不占一行
        rid = str(r.get("rec_id") or r.get("msg_id") or "")
        r["reversed"] = rid in revd
        out.append(r)
    return out


def unparsed_records(account: str = "", n: int = 50) -> list:
    if account:
        return tail_jsonl(state_dir() / account / "unparsed.jsonl", n)[::-1]
    rows = []
    for acc in all_accounts():
        rows += tail_jsonl(state_dir() / acc / "unparsed.jsonl", n)
    rows = sorted(rows, key=lambda x: str(x.get("ts") or ""))[-n:]
    return rows[::-1]


def today_counts(account: str = "") -> tuple:
    """(今日已记账, 今日未解析)"""
    accs = [account] if account else all_accounts()
    rec = sum(count_today(state_dir() / a / "records.jsonl") for a in accs)
    unp = sum(count_today(state_dir() / a / "unparsed.jsonl") for a in accs)
    return rec, unp


# ---------------- 期次 ----------------
#
# 账本只装当前这一期；换期时服务端会归档并清零。这里只负责把期号读出来给
# 界面显示。不 import main —— 这个模块是纯读数据层，导入 main 会把整个
# Flask 环境连同配置校验一起拽起来。

DRAW_WEEKDAYS = (1, 3, 5)          # 周二 / 周四 / 周六


def issue_of(ts=None) -> str:
    """这条账归哪一期：从今天（含）往后找最近的开奖日。"""
    import datetime as _dt

    d = _dt.datetime.fromtimestamp(ts).date() if ts else _dt.datetime.now().date()
    for i in range(4):
        cand = d + _dt.timedelta(days=i)
        if cand.weekday() in DRAW_WEEKDAYS:
            return cand.strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


def stage_of_ts(ts) -> str:
    """把「2026-11-14 22:58:39」这种时间串折算成它所属的期号。

    unparsed.jsonl 里没存期号（老数据的锅），界面要按「本期」过滤未解析，
    就只能按时间反推。推不出来返回空串，交给调用方当「期次未知」处理，
    绝不能回退成今天 —— 那会把上几期的记录算到本期头上。
    """
    import datetime as _dt

    s = str(ts or "").strip()[:19]
    if not s:
        return ""
    try:
        t = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return ""
    return issue_of(t)


def current_issue(account: str = "") -> str:
    p = state_dir() / account / "current_issue.txt"
    try:
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    except Exception:
        pass
    return issue_of()


def issue_files(account: str = "") -> list:
    """已归档的历史期，新的在前。"""
    d = state_dir() / account / "issues"
    if not d.exists():
        return []
    return sorted(d.glob("*.json"), reverse=True)


def summary(account: str = "", market: str = "MO") -> dict:
    """一次把总览页要的东西全算出来。

    两本账的号码盘和风险榜一起给（界面上澳门、香港是并排/上下同时显示的），
    market 只决定兼容字段 grid / risk 取哪一本。风险榜给全部有注的号，不截前 10。
    """
    dm = numbers(account, "MO")
    dh = numbers(account, "HK")
    if market == "HK":
        data = dh
    elif market == "ALL":
        data = {k: round(float(dm.get(k, 0) or 0) + float(dh.get(k, 0) or 0), 2)
                for k in dm}
    else:
        data = dm
    _, current, _ = zodiac_year()
    rec, unp = today_counts(account)
    flat = others_total(account, market)
    banks = market_totals(account)
    grids = {"MO": grid_from(dm), "HK": grid_from(dh)}
    risks = {"MO": risk_from(dm, 49), "HK": risk_from(dh, 49)}
    cur_issue = current_issue(account)
    # 未解析的队列里只有时间没有期号，这里补上，界面才能按「本期」过滤
    unp_rows = []
    for r in unparsed_records(account):
        if isinstance(r, dict):
            if not r.get("issue"):
                r["issue"] = stage_of_ts(r.get("ts"))
            unp_rows.append(r)
    unp_cur = sum(1 for r in unp_rows if str(r.get("issue") or "") == cur_issue)
    return {
        "account": account,
        "accounts": all_accounts(),
        "market": market,
        "markets": banks,
        "grand_total": banks["ALL"]["total"],
        "flat_total": flat,
        "numbers_with_bets": sum(1 for v in data.values() if v > 0),
        "records_today": rec,
        "unparsed_today": unp,
        "issue": cur_issue,
        "issue_archived": len(issue_files(account)),
        "zodiac": current,
        "zodiac_year": zodiac_year()[0],
        "grid": grids["HK"] if market == "HK" else grids["MO"],
        "risk": risks["HK"] if market == "HK" else risks["MO"],
        "grids": grids,
        "risks": risks,
        "recent": recent_records(account),
        "unparsed": unp_rows,
        "unparsed_current": unp_cur,
    }


# ---------------- 监听对象 ----------------

def contacts_doc(account: str) -> dict:
    d = read_json(contacts_dir() / (account + ".json"), {})
    return d if isinstance(d, dict) else {}


def targets_doc(account: str) -> dict:
    d = read_json(targets_dir() / (account + ".json"), {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("version", 0)
    d.setdefault("updated_at", "")
    if not isinstance(d.get("targets"), list):
        d["targets"] = []
    return d


def contact_rows(account: str, keyword: str = "", kind: str = "") -> dict:
    """监听对象列表：群 > 好友 > 文件助手 > 公众号，同类按最近消息倒序。

    公众号动辄上百个，不压到后面会把真正要监听的群冲下第一屏。
    """
    doc = contacts_doc(account)
    td = targets_doc(account)
    selected = {str(t.get("id") or "") for t in td.get("targets") or []}
    all_mode = "*" in selected
    order = {"群": 0, "好友": 1, "文件助手": 2, "公众号": 3}
    items = []
    kw = (keyword or "").strip().lower()
    for x in doc.get("items") or []:
        if not isinstance(x, dict):
            continue
        cid = str(x.get("chat_id") or "")
        if not cid:
            continue
        row = {
            "chat_id": cid,
            "name": str(x.get("name") or ""),
            "kind": str(x.get("kind") or ""),
            "alias": str(x.get("alias") or ""),
            "last_msg_time": x.get("last_msg_time") or 0,
            "selected": all_mode or (cid in selected),
        }
        if kw:
            hay = " ".join([row["name"], cid, row["kind"], row["alias"]]).lower()
            if kw not in hay:
                continue
        if kind and row["kind"] != kind:
            continue
        items.append(row)
    items.sort(key=lambda r: (order.get(r["kind"], 9), -(float(r["last_msg_time"] or 0))))
    return {
        "account": account,
        "received_at": doc.get("received_at") or "",
        "total": doc.get("total") or 0,
        "items": items,
        "all_mode": all_mode,
        "version": int(td.get("version") or 0),
        "targets_count": 0 if all_mode else len(selected),
    }
