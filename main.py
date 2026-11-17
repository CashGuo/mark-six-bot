#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""六合彩记账 —— 微信监听推送版

消息源：WxPushTool 客户端监听 Windows 微信，把指定群/好友的新消息 POST 过来。
        本程序验签 → 解析 AI → 累加记账 → 落盘。

与 Telegram 版的区别：
  - 不再有 /start /top 这类聊天命令（微信侧只能单向推，发不出回复）
  - 查询改成 HTTP 接口：/top /status /recent /logs，用浏览器或 curl 看
  - 记账结果写日志 + records.jsonl，解析失败的单独落 unparsed.jsonl 供人工核对

推送报文（详见 WxPushTool/docs/第三方对接说明书.md）：

  POST /wx/hook
  X-Wx-Timestamp: 1794448277
  X-Wx-Signature: hex(HMAC_SHA256(secret, "{ts}.{原始body}"))
  X-Wx-Cust:      C0001            （可选，客户编号，仅用于排查）

  {"batch_id":"1794448277-5","ts":1794448277,"account":"j693395002","count":1,
   "messages":[{"msg_id":"...","target_id":"...@chatroom","chat_name":"华东群",
                "sender":100,"msg_type":"文本","create_time":...,"content":"...",
                "account":"j693395002","collected_at":"..."}]}
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import bet_rules
# 玩法表 + 选号逻辑跟控制台窗口共用一份，网页版不会算出第二个结果
import manual_plays
import msb_view
import msb_paths
from msb_paths import APP_DIR, DATA_DIR, CONFIG_PATH

# 源码模式注入 venv（打包后不会命中）
SITE_PACKAGES = APP_DIR / "venv" / "Lib" / "site-packages"
if SITE_PACKAGES.exists() and str(SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(SITE_PACKAGES))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class _NullIO:
    """--windows-console-mode=disable 打包后 sys.stdout/stderr 是 None。

    代码里任何一句 print()（配置缺失提示、老数据迁移提示、Flask 启动日志）
    都会因此抛 AttributeError 直接退出码 1 —— 表现为"双击没反应/开机自启悄悄
    不启动"。这里兜一层黑洞，真正的输出仍走 logs\\bot_*.log 文件处理器。
    """

    def write(self, *args, **kwargs):
        return 0

    def writelines(self, *args, **kwargs):
        return None

    def flush(self):
        return None

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullIO()
if sys.stderr is None:
    sys.stderr = _NullIO()


import lunardate as LunarDate
from dotenv import load_dotenv
from flask import Flask, jsonify, request, render_template, Response
from openai import OpenAI

# ======================== 配置 ========================
# 配置优先级：真实环境变量 > config\server.json > 内置默认。
# 只有 server.json 不存在时才读 .env（兼容老部署），否则客户在界面上改的值
# 会被 .env 里的旧值悄悄盖掉，排查起来极其难受。

DEFAULT_LLM_BASE = ("https://llm-o8ou6he0iyxcz0ns.cn-beijing.maas.aliyuncs.com"
                    "/compatible-mode/v1")


def load_server_config(path: Path = None) -> dict:
    p = Path(path or CONFIG_PATH)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        print(f"[config] 读取 {p} 失败：{e}（改用默认配置）")
        return {}


SERVER_CFG = load_server_config()
if not SERVER_CFG:
    load_dotenv()          # 老部署：只有 server.json 不存在时才回落到 .env


def cfg(json_key: str, env_key: str, default: str = "") -> str:
    v = (os.getenv(env_key) or "").strip()
    if v:
        return v
    jv = SERVER_CFG.get(json_key)
    if jv is not None and str(jv).strip() != "":
        return str(jv).strip()
    return default


WX_PUSH_SECRET = cfg("wx_push_secret", "WX_PUSH_SECRET")
WX_SKEW_SEC = int(cfg("wx_skew_sec", "WX_SKEW_SEC", "300") or 300)
WX_SKIP_VERIFY = cfg("wx_skip_verify", "WX_SKIP_VERIFY", "0") == "1"


def _parse_account_secrets(s: str) -> Dict[str, str]:
    """WX_ACCOUNT_SECRETS=C0001:密钥A,C0002:密钥B → {C0001: 密钥A, ...}"""
    out: Dict[str, str] = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        c, k = part.split(":", 1)
        c, k = c.strip(), k.strip()
        if c and k:
            out[c] = k
    return out


# 多采集端：每个客户编号一把密钥，按 X-Wx-Cust 选。
# 单账号部署不用填，退回 WX_PUSH_SECRET（完全向后兼容）。
ACCOUNT_SECRETS = _parse_account_secrets(
    cfg("wx_account_secrets", "WX_ACCOUNT_SECRETS"))

# ---- 采集端白名单：一个微信号一把钥匙 ----
# 独立文件 config\wx_clients.json。现在两个来源都可以写：
#   1. 服务端「白名单」页新增 —— 密钥由服务端生成，配 IP 白名单和到期时间
#   2. 厂商签发工具（客户就是厂商自己时）
# X-Wx-Cust 里放的是微信号（客户端自己声明，可伪造，但伪造只会让服务端换把
# 钥匙去算 HMAC，不知道密钥照样过不了，所以它只当索引用，不当凭证）。
#
# 放行规则（顺序判断）：
#   1. 名单里有这个微信号且 enabled、未过期、来源 IP 在白名单内 → 用它的 secret 验签
#   2. 名单里有这个微信号但已停用 / 已过期 / IP 不符        → 拒（返回空密钥）
#   3. 名单里没有，但名单里有别的在用条目                  → 拒（严格模式，不回退全局）
#   4. 名单整个为空                                      → 回退 WX_PUSH_SECRET /
#                                                          wx_account_secrets（初始部署兼容）
CLIENTS_PATH = Path(CONFIG_PATH).parent / "wx_clients.json"

_WL_LOCK = threading.Lock()
_WL: Dict[str, object] = {"mtime": None, "map": {}, "enabled": 0, "error": "",
                          "loaded": False, "updated_at": "",
                          "path": str(CLIENTS_PATH)}
# 本次运行内每个微信号最后一次推送成功的时间（GUI 显示"还活着吗"用）
_LAST_PUSH: Dict[str, float] = {}


def load_clients(force: bool = False) -> Dict[str, object]:
    """读白名单，按 mtime 缓存 —— 签发工具改完文件不用重启服务。"""
    try:
        mt = CLIENTS_PATH.stat().st_mtime
    except OSError:
        mt = None
    with _WL_LOCK:
        if not force and _WL["loaded"] and mt == _WL["mtime"]:
            return _WL
        mp: Dict[str, dict] = {}
        n, err, upd = 0, "", ""
        if mt is not None:
            try:
                with open(CLIENTS_PATH, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                upd = str(d.get("updated_at") or "")
                for it in (d.get("clients") or []):
                    if not isinstance(it, dict):
                        continue
                    wid = str(it.get("wechat_id") or "").strip()
                    sec = str(it.get("secret") or "").strip()
                    if not wid or not sec:
                        continue
                    mp[wid] = it
                    if bool(it.get("enabled", True)):
                        n += 1
            except Exception as e:      # 文件坏了别把服务拖死，退回兼容模式
                err = "%s: %s" % (type(e).__name__, e)
        _WL.update(mtime=mt, map=mp, enabled=n, error=err,
                   updated_at=upd, loaded=True)
        return _WL


def whitelist_mode() -> str:
    """strict = 只收白名单内的；legacy = 名单为空，还在用全局密钥。"""
    return "strict" if int(load_clients()["enabled"]) else "legacy"

LLM_API_KEY = cfg("llm_api_key", "LLM_API_KEY") or cfg("llm_api_key", "DEEPSEEK_API_KEY")

# 任意 OpenAI 兼容端点：默认指向阿里云百炼的 qwen3.8-flash
LLM_BASE_URL = cfg("llm_base_url", "LLM_BASE_URL", DEFAULT_LLM_BASE)
LLM_MODEL = cfg("llm_model", "LLM_MODEL", "qwen3.8-flash")
LLM_TIMEOUT = int(cfg("llm_timeout", "LLM_TIMEOUT", "60") or 60)
# qwen3.8 默认开思考：实测 8 条下注文本 86.9s、且 03头 解析成 "0"；
# 关掉后 8.6s、8/8 正确。所以默认关闭，可用 LLM_THINKING=1 打开做对照。
LLM_THINKING = cfg("llm_thinking", "LLM_THINKING", "0") == "1"

PORT = int(cfg("port", "PORT", "8899") or 8899)
HOST = cfg("host", "HOST", "0.0.0.0") or "0.0.0.0"


def config_problems() -> List[str]:
    """启动前自检。返回人话问题列表，空列表代表可以启动。

    这些以前是模块级的 raise ValueError —— 打包成 EXE 后表现为双击闪退、
    连一行提示都看不到，所以改成返回清单，由调用方弹框。
    """
    out: List[str] = []
    if not LLM_API_KEY:
        out.append("没填大模型 API Key（LLM_API_KEY / llm_api_key）")
    wl = load_clients()
    if wl["error"]:
        out.append("白名单文件读不出来（%s），请检查：%s" % (wl["error"], CLIENTS_PATH))
    if not WX_SKIP_VERIFY and int(wl["enabled"]) == 0 \
            and not WX_PUSH_SECRET and not ACCOUNT_SECRETS:
        out.append("还没有任何可用的采集端密钥：去「白名单」页确认已导入，"
                   "或先在配置页填一个推送密钥")
    return out


# ---- 多采集端：按账号分目录 ----
# 每个微信号一套账本/记录/清单，互不覆盖。单账号部署也不受影响（只会多一层 state/<账号>/）。
DEFAULT_ACCOUNT = "default"
STATE_DIR = DATA_DIR / "state"
CONTACTS_DIR = DATA_DIR / "contacts"
TARGETS_DIR = DATA_DIR / "targets"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
LOCK_FILE = DATA_DIR / ".bot.lock"

# 全局去重：msg_id 本身含会话 ID，跨账号命中正好挡掉"同一群两个号收到同一条"
PROCESSED_FILE = DATA_DIR / "processed_ids.json"
# 跨账号内容指纹：同一群两个号收到同一条消息时，msg_id 里的消息序号可能不同，
# 只靠 msg_id 挡不住 → 用 chat_id + create_time + sender + content 的摘要兜底
FINGERPRINT_FILE = DATA_DIR / "seen_fingerprints.json"
MAX_FINGERPRINTS = 50000

# 旧版（单账号）平铺文件，启动时迁移（现在指数据目录内的老位置）
LEGACY_DATA = DATA_DIR / "numbers_state.json"
LEGACY_RECORD = DATA_DIR / "records.jsonl"
LEGACY_UNPARSED = DATA_DIR / "unparsed.jsonl"
LEGACY_CONTACTS = DATA_DIR / "contacts.json"
LEGACY_TARGETS = DATA_DIR / "listener_targets.json"

_ADOPTED = msb_paths.adopt_old_layout()
if _ADOPTED:
    print(f"[data] 已把程序目录里的老数据复制到 {DATA_DIR}（{_ADOPTED}）")

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)
CONTACTS_DIR.mkdir(exist_ok=True)
TARGETS_DIR.mkdir(exist_ok=True)

DATA_LOCK = threading.Lock()
MAX_PROCESSED = 2000
MAX_RECORDS_IN_MEMORY = 50

_ACC_SAFE = re.compile(r"[^A-Za-z0-9_@.\-]")


def resolve_account(x) -> str:
    """把报文里的 account 净化成可安全当目录名的串。

    account 来自 HTTP 报文，直接拼路径会出路径穿越（../../），必须先过滤字符。
    """
    s = _ACC_SAFE.sub("", str(x or "").strip())[:64].strip(".- ")
    if not s or s in (".", ".."):
        return DEFAULT_ACCOUNT
    return s


def account_files(account=None) -> Dict[str, Path]:
    """某账号的账本文件路径。account 为空 → DEFAULT_ACCOUNT。

    香港和澳门是两本账：老数据（numbers_state.json）按澳门算，
    香港另开一份，金额和号码盘都不混。没有标明市场的消息都算澳门。
    """
    a = resolve_account(account)
    base = STATE_DIR / a
    return {
        "state": base / "numbers_state.json",        # 澳门盘
        "state_hk": base / "numbers_state_hk.json",  # 香港盘
        "records": base / "records.jsonl",
        "unparsed": base / "unparsed.jsonl",
        "backup": BACKUP_DIR / a,
        "contacts": CONTACTS_DIR / (a + ".json"),
        "targets": TARGETS_DIR / (a + ".json"),
        "issue": base / "current_issue.txt",
        "issues": base / "issues",
        # 平特整肖、二中二/三中三这类只记总额的玩法：金额在这儿，不进 49 号盘
        "others": base / "others_state.json",
        "others_hk": base / "others_state_hk.json",
    }


def all_accounts() -> List[str]:
    """已真正接入的账号：有账本，或上报过会话清单。

    不看 targets/*.json —— 只有一份空的下发配置不算"这个号接入过"，
    否则界面上会冒出一个幽灵账号。

    港澳两本账都要看：只记过香港单的账号，澳门账本可能整个不存在，
    只看 numbers_state.json 会把这个账号从列表里漏掉（流水也就找不到了）。
    """
    out = set()
    for pat in ("*/numbers_state.json", "*/numbers_state_hk.json",
                "*/others_state.json", "*/others_state_hk.json"):
        for p in STATE_DIR.glob(pat):
            out.add(p.parent.name)
    for p in CONTACTS_DIR.glob("*.json"):
        out.add(p.stem)
    return sorted(out)


# ======================== 进程锁 ========================

def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
            import psutil
            if psutil.pid_exists(old_pid):
                print(f"错误：程序已在运行中（进程 ID: {old_pid}）")
                return False
            LOCK_FILE.unlink()
        except Exception as e:
            print(f"读取锁文件失败：{e}")
            return False
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        print(f"创建锁文件失败：{e}")
        return False


def release_lock():
    if LOCK_FILE.exists():
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass


# ======================== 日志 ========================

def cleanup_old_files(directory: Path, days: int, pattern: str):
    cutoff_date = datetime.now() - timedelta(days=days)
    for file in directory.glob(pattern):
        if file.is_file():
            file_time = datetime.fromtimestamp(file.stat().st_mtime)
            if file_time < cutoff_date:
                file.unlink()
                print(f"已删除旧文件：{file.name}")


def setup_logging():
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"bot_{today}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    cleanup_old_files(LOG_DIR, days=30, pattern="bot_*.log")
    return logging.getLogger(__name__)


logger = setup_logging()


def migrate_legacy_files() -> str:
    """单账号时代的平铺文件 → state/<账号>/。只搬一次，搬完原文件就不在了。

    账号取旧 contacts.json 里的 account（那才是真实微信号）；取不到就用 default。
    """
    acc = DEFAULT_ACCOUNT
    if LEGACY_CONTACTS.exists():
        try:
            with open(LEGACY_CONTACTS, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("account"):
                acc = resolve_account(d.get("account"))
        except Exception:
            pass

    pairs = [(LEGACY_DATA, "state"), (LEGACY_RECORD, "records"),
             (LEGACY_UNPARSED, "unparsed"),
             (LEGACY_CONTACTS, "contacts"), (LEGACY_TARGETS, "targets")]
    moved = []
    for src, key in pairs:
        dst = account_files(acc)[key]
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved.append(src.name)
            except Exception as e:
                logger.warning(f"迁移 {src.name} 失败：{e}")
    if moved:
        logger.info(f"单账号数据已迁移到 state/{acc}/：{', '.join(moved)}")
    return acc


CURRENT_ACCOUNT = migrate_legacy_files()


# ======================== 农历年份 ========================

def get_current_lunar_year() -> Tuple[int, str, Dict[str, List[str]]]:
    today = datetime.now()
    try:
        lunar = LunarDate.LunarDate.fromSolarDate(today.year, today.month, today.day)
        lunar_year = lunar.year
        zodiac_animals = ["鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪"]
        zodiac_index = (lunar_year - 4) % 12
        current_zodiac = zodiac_animals[zodiac_index]
        zodiac_mapping = {}
        for i, animal in enumerate(zodiac_animals):
            offset = (i - zodiac_index) % 12
            if offset == 0:
                offset = 12
            remainder = (13 - offset) % 12
            if remainder == 0:
                remainder = 12
            numbers = []
            for num in range(1, 50):
                if num % 12 == (remainder % 12):
                    numbers.append(f"{num:02d}")
            zodiac_mapping[animal] = numbers
        logger.info(f"当前农历年份：{lunar_year}年{current_zodiac}年")
        return lunar_year, current_zodiac, zodiac_mapping
    except Exception as e:
        logger.error(f"农历计算失败：{e}")
        return 2026, "马", {"马": ["01", "13", "25", "37", "49"]}


_zodiac_cache = {"day": None, "value": None}
_ZODIAC_LOCK = threading.Lock()


def current_zodiac_mapping() -> Dict[str, List[str]]:
    """按天缓存农历生肖表：看板 5 秒刷一次，避免重复计算和刷日志。

    这里必须"先算好再一次换上新值"。早先的写法是先写 day、再算 value：
    两个线程同一秒进来，后一个看到 day 已经是今天、value 还是 None，
    直接 TypeError 把整批推送打成 500 —— 那一批消息就全丢了。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    v = _zodiac_cache["value"]
    if _zodiac_cache["day"] == today and v:
        return v[2]
    with _ZODIAC_LOCK:
        v = _zodiac_cache["value"]
        if _zodiac_cache["day"] == today and v:
            return v[2]
        v = get_current_lunar_year()
        _zodiac_cache["value"] = v       # 先给值
        _zodiac_cache["day"] = today     # 再认日期：别的线程读不到半成品
        return v[2]


# ======================== 号码对照表 ========================

COLOR_MAP = {
    "红波": ["01", "02", "07", "08", "12", "13", "18", "19", "23", "24", "29", "30", "34", "35", "40", "45", "46"],
    "蓝波": ["03", "04", "09", "10", "14", "15", "20", "25", "26", "31", "36", "37", "41", "42", "47", "48"],
    "绿波": ["05", "06", "11", "16", "17", "21", "22", "27", "28", "32", "33", "38", "39", "43", "44", "49"],
}

COLOR_PARITY_MAP = {
    "红单": ["01", "07", "13", "19", "23", "29", "35", "45"],
    "红双": ["02", "08", "12", "18", "24", "30", "34", "40", "46"],
    "蓝单": ["03", "09", "15", "25", "31", "37", "41", "47"],
    "蓝双": ["04", "10", "14", "20", "26", "36", "42", "48"],
    "绿单": ["05", "11", "17", "21", "27", "33", "39", "43", "49"],
    "绿双": ["06", "16", "22", "28", "32", "38", "44"],
}

SIZE_PARITY_MAP = {
    "小数": [f"{i:02d}" for i in range(1, 25)],
    "大数": [f"{i:02d}" for i in range(25, 50)],
    "小单": [f"{i:02d}" for i in range(1, 25) if i % 2 != 0],
    "小双": [f"{i:02d}" for i in range(1, 25) if i % 2 == 0],
    "大单": [f"{i:02d}" for i in range(25, 50) if i % 2 != 0],
    "大双": [f"{i:02d}" for i in range(25, 50) if i % 2 == 0],
    "单号": [f"{i:02d}" for i in range(1, 50) if i % 2 != 0],
    "双号": [f"{i:02d}" for i in range(1, 50) if i % 2 == 0],
}

HEAD_MAP = {
    "0": [f"{i:02d}" for i in range(1, 10)],
    "1": [f"{i:02d}" for i in range(10, 20)],
    "2": [f"{i:02d}" for i in range(20, 30)],
    "3": [f"{i:02d}" for i in range(30, 40)],
    "4": [f"{i:02d}" for i in range(40, 50)],
}


def norm_head(h) -> str:
    """把模型给出的头组值归一到 "0"~"4"。

    实测 qwen 对 "03头" 会输出 "03" 或 "0"：前者指 3 字头（03 是号码写法），
    后者是 0 字头。靠首位是否为 "0" 区分：两位且首位 0 → 取个位。
    """
    s = str(h).strip()
    if not s.isdigit():
        return ""
    if len(s) == 1:
        return s if s in HEAD_MAP else ""
    if len(s) == 2:
        if s[0] == "0":
            return s[1] if s[1] in HEAD_MAP else ""
        return s if s in HEAD_MAP else ""
    return ""


def lookup_groups(item: dict, zodiac_mapping: Dict[str, List[str]]) -> List[Tuple[str, List[str]]]:
    """把一条下注拆成若干「组」，每个组是一个独立的注。

    金额是**每个组各多少**，组内再摊到它的号码上：
      「牛100」     → 1 组（牛 4 个号），100 摊开 = 每号 25
      「牛马各100」 → 2 组，牛 100（每号 25）、马 100（每号 20），合计 200
      「1*13*34*7 各10」→ 4 组（每号一组），每号各 10，合计 40

    所以号码必须一个号一组 —— 合成一组再摊，就变成 10 摊到 4 个号、
    每号 2.5，那是错的。生肖/波色/头组才是「一个名字一组」。
    """
    t = item.get("type", "")
    groups: List[Tuple[str, List[str]]] = []

    if t == "zodiac":
        for name in item.get("names", []):
            if name in zodiac_mapping:
                groups.append((str(name), list(zodiac_mapping[name])))
            else:
                logger.warning(f"未知生肖：{name}")
    elif t == "color":
        for name in item.get("names", []):
            if name in COLOR_MAP:
                groups.append((str(name), list(COLOR_MAP[name])))
            else:
                logger.warning(f"未知波色：{name}")
    elif t == "color_parity":
        for name in item.get("names", []):
            if name in COLOR_PARITY_MAP:
                groups.append((str(name), list(COLOR_PARITY_MAP[name])))
            else:
                logger.warning(f"未知波色单双：{name}")
    elif t == "size_parity":
        for name in item.get("names", []):
            if name in SIZE_PARITY_MAP:
                groups.append((str(name), list(SIZE_PARITY_MAP[name])))
            else:
                logger.warning(f"未知大小单双：{name}")
    elif t == "head":
        for h in item.get("heads", []):
            hh = norm_head(h)
            if hh:
                groups.append((hh, list(HEAD_MAP[hh])))
            else:
                logger.warning(f"未知头组：{h}（应为 0~4 或 01~04）")
    elif t == "number":
        for n in item.get("numbers", []):
            n = str(n).zfill(2)
            if n.isdigit() and 1 <= int(n) <= 49:
                groups.append((n, [n]))

    return groups


def _split_into(result: Dict[str, float], numbers: List[str], amount: float) -> None:
    """把一组的金额摊到组内号码上，最后一位补差，保证合计不差分。

    100 摊 4 个号 → 25 各；100 摊 5 个号 → 20 各；100 摊 17 个号 →
    5.88 × 16 + 5.92，合计仍是 100。摊不整时不追求每个号一模一样，
    追求的是这一笔加起来就等于客户报的数。
    """
    n = len(numbers)
    if n <= 0 or amount == 0:
        return
    per = round(amount / n, 2)
    last = round(amount - per * (n - 1), 2)
    for i, num in enumerate(numbers):
        part = last if i == n - 1 else per
        result[num] = round(result.get(num, 0) + part, 2)


def expand_bets(items: List[dict], zodiac_mapping: Dict[str, List[str]]) -> Dict[str, float]:
    """把解析结果展开成「号码 → 金额」。

    全类统一「单价 × 号码个数」：牛100 → 06/18/30/42 各 100，合计 400；
    牛马各100 → 9 个号各 100，合计 900。

    这里**不再把生肖总额摊开**。早先的版本是「牛100 摊成 4 个号各 25」，
    与客户实际报账的口径相反 —— 客户说的是"每个号 100"。
    """
    result: Dict[str, float] = {}
    for item in items:
        amount = float(item.get("amount", 0))
        for _name, numbers in lookup_groups(item, zodiac_mapping):
            for num in numbers:
                result[num] = round(result.get(num, 0) + amount, 2)
    return result


# ======================== 数据管理 ========================

def load_data(account=None, market: str = "MO") -> Dict[str, float]:
    p = account_files(account)["state_hk" if market == "HK" else "state"]
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return init_data()
    return init_data()


def load_data_merged(market: str = "") -> Dict[str, float]:
    """所有账号合计的 49 号码总账（"合并总览"用）。

    market 空 → 香港+澳门都算；给 MO/HK 就只算那一本。
    """
    total = init_data()
    for acc in all_accounts():
        for m in (["MO", "HK"] if not market else [market]):
            for n, v in load_data(acc, m).items():
                if n in total:
                    total[n] += float(v or 0)
    return total


def init_data() -> Dict[str, float]:
    return {f"{i:02d}": 0.0 for i in range(1, 50)}


def save_data(data: Dict[str, float], account=None, market: str = "MO"):
    p = account_files(account)["state_hk" if market == "HK" else "state"]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def load_others(account=None, market: str = "MO") -> dict:
    """不落号码盘的玩法（平特整肖、二中二/三中二/三中三）本期累计。

    结构 {"total": 100.0, "items": [{rec_id, play, text, amount, ts}]}。
    这些钱不能进 49 号盘（号码盘是"每号多少钱"），但合计必须算进去，
    否则总金额会比客户报的少。
    """
    p = account_files(account)["others_hk" if market == "HK" else "others"]
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("total", 0.0)
                d.setdefault("items", [])
                return d
        except Exception:
            pass
    return {"total": 0.0, "items": []}


def save_others(d: dict, account=None, market: str = "MO"):
    p = account_files(account)["others_hk" if market == "HK" else "others"]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def others_total(account=None, market: str = "MO") -> float:
    try:
        return round(float(load_others(account, market).get("total") or 0), 2)
    except Exception:
        return 0.0


def others_total_merged(market: str = "") -> float:
    """所有账号合计的不计号金额（"合并总览"用）。market 空 = 港+澳。"""
    ms = ["MO", "HK"] if not market else [market]
    return round(sum(others_total(a, m) for a in all_accounts() for m in ms), 2)


def market_totals(account=None) -> dict:
    """两个分盘各多少钱：{MO: {board, flat, total}, HK: {...}, ALL: 合计}

    account 为空 → 所有账号合并。
    """
    accs = [resolve_account(account)] if account else all_accounts()
    out = {}
    for m in ("MO", "HK"):
        board = round(sum(sum(float(v or 0) for v in load_data(a, m).values())
                          for a in accs), 2)
        flat = round(sum(others_total(a, m) for a in accs), 2)
        out[m] = {"board": board, "flat": flat, "total": round(board + flat, 2)}
    out["ALL"] = {k: round(out["MO"][k] + out["HK"][k], 2)
                  for k in ("board", "flat", "total")}
    return out


def backup_data(manual=False, account=None) -> str:
    try:
        f = account_files(account)
        today = datetime.now().strftime("%Y-%m-%d")
        f["backup"].mkdir(parents=True, exist_ok=True)
        # 港、澳两本账 + 两边的不计号，一起备份，缺一份恢复出来就是错的
        saved: List[str] = []
        for key, name in (("state", "numbers_state_%s.json" % today),
                          ("state_hk", "numbers_state_hk_%s.json" % today),
                          ("others", "others_state_%s.json" % today),
                          ("others_hk", "others_state_hk_%s.json" % today)):
            src = f[key]
            if not src.exists():
                continue
            dst = f["backup"] / name
            shutil.copy2(src, dst)
            saved.append(str(dst))
        if saved:
            cleanup_old_files(f["backup"], days=7, pattern="numbers_state_*.json")
            cleanup_old_files(f["backup"], days=7, pattern="others_state_*.json")
            return saved[0]
        return ""
    except Exception:
        return ""


def backup_all() -> List[str]:
    out = []
    for acc in all_accounts() or [DEFAULT_ACCOUNT]:
        s = backup_data(account=acc)
        if s:
            out.append(s)
    return out


def restore_data(date_str: str, account=None) -> bool:
    """从备份恢复。日期那天有港盘/澳盘就一起恢复，哪本有就恢复哪本。"""
    try:
        f = account_files(account)
        pairs = ((f["backup"] / ("numbers_state_%s.json" % date_str), f["state"]),
                 (f["backup"] / ("numbers_state_hk_%s.json" % date_str), f["state_hk"]),
                 (f["backup"] / ("others_state_%s.json" % date_str), f["others"]),
                 (f["backup"] / ("others_state_hk_%s.json" % date_str), f["others_hk"]))
        restored = False
        with DATA_LOCK:
            for src, dst in pairs:
                if not src.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                restored = True
        return restored
    except Exception:
        return False


def top_numbers(data: Dict[str, float], n: int = 10) -> List[Tuple[str, float]]:
    return sorted(
        [(num, amount) for num, amount in data.items() if amount > 0],
        key=lambda x: x[1], reverse=True,
    )[:n]


# ======================== DeepSeek API ========================

# 注意：openai SDK 构造时就会校验 api_key，缺 key 直接抛 OpenAIError。
# 放在模块级的话，打包成 EXE 后"配置没填"的表现就是双击闪退/静默退出码 1
# （连 config_problems() 都执行不到），所以改成首次调用时才建。
_client = None


def get_client() -> "OpenAI":
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client



def build_system_prompt(zodiac_mapping: Dict[str, List[str]]) -> str:
    zodiac_names = "、".join(zodiac_mapping.keys())

    return f"""解析六合彩下注文本为JSON。只输出JSON，不要其他文字。

【类型】
zodiac=生肖（{zodiac_names}）
color=波色（红波/蓝波/绿波）
color_parity=波色单双（红单/红双/蓝单/蓝双/绿单/绿双）
size_parity=大小单双（小数/大数/小单/小双/大单/大双/单号/双号）
head=头组（0头/1头/2头/3头/4头）。heads 必须是单个数字 "0"~"4"，禁止写 "03" 这种两位形式。
      "03头"是 3 字头 → heads:["3"]（不是 0 头）；只有"00头"才是 0 头 → heads:["0"]
number=直接号码

【号码规则·重要】
- 号码范围01-49，必须输出两位字符串
- 单个数字补零：1→"01"，7→"07"，9→"09"
- 用户写的每一个号码都要识别，一个都不能漏，输出前数一遍数量是否和输入一致

【金额】
- 数字后任意字（#井米文蚊点元斤两块A等）都是金额单位，取数字
- "各5"没单位时也按金额5处理
- amount 一律是**每个号码各多少**，照用户报的原数填，不要自己乘、也不要摊开：
  · 生肖："牛100" → amount 100（牛 4 个号各 100，合计 400）；"鼠50" → amount 50（合计 200）
  · 波色 / 波色单双 / 大小单双 / 头组：同样每个号各 amount
  · 直接号码："1*13*34*7各10" → amount 10（每个号各 10）
- 几个名字合写、"各"在中间时，amount 是**每个名字各多少**，组内每个号仍是这个数：
  "牛马各100" → 一个 item，names ["牛","马"]，amount 100（9 个号各 100，合计 900）
  "马鼠鸡各号15" → 一个 item，names ["马","鼠","鸡"]，amount 15

【分隔符】逗号、句号、斜杠/、横杠-、星号*、空格 都是号码分隔符

【忽略】澳门/奥门/香港/澳/共XX/合计XX 这些词直接跳过

【同号累加】同一号码多次出现，程序会自动累加，你只需如实列出

【示例】
牛100→{{"type":"zodiac","names":["牛"],"amount":100}}
牛马各100→{{"type":"zodiac","names":["牛","马"],"amount":100}}
马鼠鸡各号15米→{{"type":"zodiac","names":["马","鼠","鸡"],"amount":15}}
红双蓝单各号5#→{{"type":"color_parity","names":["红双","蓝单"],"amount":5}}
1*13*34*7各10A→{{"type":"number","numbers":["01","13","34","07"],"amount":10}}

【整笔玩法：平特 / 连肖 / N中M】
这三种是「整笔」：金额不摊到号码上，也绝对不能拆成号码。type 只能是
pingte（平特/平码/平肖）、lianxiao（连肖）、zhong（N中M，如二中二/三中二/四中四）。
- 平特：写几个肖就是这几个肖「一共」多少钱；只有写「各」才是每肖各。
  只要出现「各」，就必须 each:true（漏了会少记一半钱）。
  "平特鼠100" → zodiacs ["鼠"]，each false，amount 100
  "平特鼠牛各100" → zodiacs ["鼠","牛"]，each true，amount 100（每肖 100，整笔 200）
- 连肖：从写的生肖起，按 鼠牛虎兔龙蛇马羊猴鸡狗猪 往后连 N 个；写不够 N 个自动补，到尾回绕。
  位数一到十二都认（"鼠十连肖"=十连、"鼠十二连肖"=十二连）；**不写位数一律按二连**。
  zodiacs 必须写补位后的完整生肖（"连肖鼠50" → ["鼠","牛"]，不是 ["鼠"]）。
  带「各」时同样 each:true（"鼠牛三连肖各100" → 三肖各 100，整笔 300）。
  "鼠二连肖100" → zodiacs ["鼠","牛"]，amount 100
  "鼠牛三连肖各100" → zodiacs ["鼠","牛","虎"]，each true，amount 100
  "鸡四连肖200" → zodiacs ["鸡","狗","猪","鼠"]，amount 200
  "连肖鼠50" → zodiacs ["鼠","牛"]，amount 50
- N中M：写成 "四中四 / 三中二 / 六中六"，中字前面的数 = 必须写满几个肖，整注一笔。
  "鼠牛虎兔四中四50" → zodiacs 四个，amount 50
  "鼠牛虎蛇四中二100" → zodiacs 四个，amount 100（四肖里中两个）
  组合注不写「各」；写了「各」、或者肖数和前面那个数字对不上，一律 items:[]（不许猜）。

【输出格式】
{{"items":[...]}}
整笔玩法的 item 写法：{{"type":"pingte","zodiacs":["鼠","牛"],"each":true,"amount":100}}
each=true 时 amount 填**每肖的单价**，由程序乘肖数；each=false 或省略时 amount 就是整笔金额。
无法解析返回：{{"items":[]}}"""


def _extract_items(result: str) -> List[dict]:
    """模型偶尔不按 {"items":[...]} 输出，会直接吐数组或换个键名，这里统一归一化。"""
    v = json.loads(result)
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        if isinstance(v.get("items"), list):
            return [x for x in v["items"] if isinstance(x, dict)]
        for k in ("data", "result", "bets", "list"):
            if isinstance(v.get(k), list):
                return [x for x in v[k] if isinstance(x, dict)]
    return []


# 模型给的整笔玩法 type → 我们的 kind
_FLAT_TYPE = {
    "pingte": "pingte", "ping": "pingte", "平特": "pingte", "平码": "pingte", "平肖": "pingte",
    "lianxiao": "lianxiao", "lian": "lianxiao", "连肖": "lianxiao",
    "zhong": "zhong", "zhongm": "zhong", "组合": "zhong", "组合注": "zhong",
}
_FLAT_LABEL = {"pingte": "平特", "lianxiao": "连肖", "zhong": "组合注"}


def _items_to_extras(items: List[dict], zodiac_mapping: Dict[str, List[str]]) -> List[dict]:
    """把模型输出里的整笔玩法（平特/连肖/N中M）抽成 extras。

    模型给的金额一律不信：带「各」时由程序算 单价×肖数；生肖不合法、金额 ≤0
    的整笔项直接丢掉（宁可这条记不上，也不能记一笔说不清的账）。
    """
    out: List[dict] = []
    for it in items:
        kind = _FLAT_TYPE.get(str(it.get("type") or "").strip().lower())
        if not kind:
            continue
        raw = it.get("zodiacs") or it.get("names") or []
        if isinstance(raw, str):
            raw = list(raw)
        zs = [c for c in raw if c in zodiac_mapping]
        zs = [c for i, c in enumerate(zs) if c not in zs[:i]]
        try:
            amt = float(it.get("amount") or 0)
        except Exception:
            amt = 0.0
        if not zs or amt <= 0:
            logger.warning(f"模型整笔项不合规，丢弃：{it}")
            continue
        each = bool(it.get("each"))
        if each:
            amt = round(amt * len(zs), 2)
        out.append({
            "play": _FLAT_LABEL.get(kind, kind),
            "text": str(it.get("text") or "") or ("%s%s%s"
                                                  % (_FLAT_LABEL.get(kind, kind),
                                                     "".join(zs), amt)),
            "amount": round(amt, 2),
            "nums": "",
            "kind": kind,
            "zodiacs": "".join(zs),
            "each": each,
        })
    return out


def parse_bet_text_full(text: str, zodiac_mapping: Dict[str, List[str]]) -> tuple:
    """调模型解析，返回 ({号码: 金额}, [整笔注...])。失败返回 ({}, [])。"""
    kwargs = dict(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": build_system_prompt(zodiac_mapping)},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=800,
        timeout=LLM_TIMEOUT,
    )
    if not LLM_THINKING:
        kwargs["extra_body"] = {"enable_thinking": False}

    last_err = None
    for attempt in (1, 2):
        try:
            response = get_client().chat.completions.create(**kwargs)
            result = response.choices[0].message.content
            logger.info(f"AI返回：{result}")
            items = _extract_items(result)
            bets = expand_bets(items, zodiac_mapping)
            extras = _items_to_extras(items, zodiac_mapping)
            logger.info(f"展开结果：{len(bets)} 个号码，共 ${sum(bets.values()):.2f}"
                        + ("，整笔 %d 笔" % len(extras) if extras else ""))
            return bets, extras
        except Exception as e:
            last_err = e
            logger.warning(f"第{attempt}次解析失败：{e}")
            time.sleep(1 if attempt == 1 else 0)

    logger.error(f"API调用失败：{last_err}")
    return {}, []


def parse_bet_text(text: str, zodiac_mapping: Dict[str, List[str]]) -> Dict[str, float]:
    """调用 OpenAI 兼容模型解析下注文本，返回 {号码: 金额}；失败返回空 dict。"""
    return parse_bet_text_full(text, zodiac_mapping)[0]


# ======================== 消息去重（持久化）========================

_processed: List[str] = []
_processed_set = set()
_MSG_LOCK = threading.Lock()          # 查重 + 占位要一步做完，见 claim_message
_PERSIST_LOCK = threading.Lock()      # 两个文件都不能被两个线程同时写


def _atomic_write_json(path: Path, data) -> None:
    """写 JSON：临时名带 pid/线程号 + 原子替换。

    以前临时名是固定的 `xxx.json.tmp`：两个线程同时保存去重记录会撞车，
    日志里能看到 Permission denied / WinError 32。去重记录没落盘，
    进程重启后同一批消息会被当成新的再记一遍账 —— 那就是多记账。
    """
    tmp = path.with_name("%s.%d.%d.tmp"
                         % (path.name, os.getpid(), threading.get_ident()))
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _load_processed():
    global _processed, _processed_set
    if PROCESSED_FILE.exists():
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                ids = json.load(f)
            if isinstance(ids, list):
                _processed = [str(x) for x in ids][-MAX_PROCESSED:]
                _processed_set = set(_processed)
        except Exception:
            pass


def _save_processed():
    try:
        with _PERSIST_LOCK:
            _atomic_write_json(PROCESSED_FILE, _processed[-MAX_PROCESSED:])
    except Exception as e:
        logger.warning(f"保存去重记录失败：{e}")


def is_message_processed(msg_id: str) -> bool:
    return msg_id in _processed_set


def claim_message(msg_id: str) -> bool:
    """查重和占位一次做完：两个线程抢同一条消息，只有一个能拿到 True。

    拆成 is_message_processed() + mark_message_processed() 两步时中间有个缝：
    同一 msg_id 的两条推送同时进来，两边都查到"没处理过"，于是各记一次账。
    """
    global _processed
    with _MSG_LOCK:
        if msg_id in _processed_set:
            return False
        _processed_set.add(msg_id)
        _processed.append(msg_id)
        if len(_processed) > MAX_PROCESSED:
            drop = len(_processed) - MAX_PROCESSED
            for x in _processed[:drop]:
                _processed_set.discard(x)
            _processed = _processed[drop:]
    _save_processed()
    return True


def mark_message_processed(msg_id: str):
    global _processed
    with _MSG_LOCK:
        if msg_id in _processed_set:
            return
        _processed_set.add(msg_id)
        _processed.append(msg_id)
        if len(_processed) > MAX_PROCESSED:
            drop = len(_processed) - MAX_PROCESSED
            for x in _processed[:drop]:
                _processed_set.discard(x)
            _processed = _processed[drop:]
    _save_processed()


def unmark_message_processed(msg_id: str) -> bool:
    """撤销一笔时把消息号放出来，好让采集端重推能重新解析入账。

    不放出来的话，客户说「你记错了，我重发一遍」会被判成重复推送直接扔掉。
    """
    global _processed
    with _MSG_LOCK:
        if msg_id not in _processed_set:
            return False
        _processed_set.discard(msg_id)
        try:
            _processed = [x for x in _processed if x != msg_id]
        except Exception:
            pass
    _save_processed()
    return True


_load_processed()


# ---- 跨账号内容指纹（同一群被两个微信号同时监听时挡重复）----

_fp: List[str] = []
_fp_set = set()
_FP_LOCK = threading.Lock()


def _load_fingerprints():
    global _fp, _fp_set
    if FINGERPRINT_FILE.exists():
        try:
            with open(FINGERPRINT_FILE, "r", encoding="utf-8") as f:
                ids = json.load(f)
            if isinstance(ids, list):
                _fp = [str(x) for x in ids][-MAX_FINGERPRINTS:]
                _fp_set = set(_fp)
        except Exception:
            pass


def _save_fingerprints():
    try:
        with _PERSIST_LOCK:
            _atomic_write_json(FINGERPRINT_FILE, _fp[-MAX_FINGERPRINTS:])
    except Exception as e:
        logger.warning(f"保存指纹失败：{e}")


def msg_fingerprint(chat_id: str, create_time, sender, content: str) -> str:
    """同一条消息在两个微信账号里 msg_id 可能不同（消息序号各自独立），
    但 会话ID + 发送时间 + 发送人 + 正文 是一致的 → 用它跨账号判重。
    """
    raw = f"{chat_id}|{create_time}|{sender}|{(content or '').strip()}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:32]


def claim_fingerprint(fp: str) -> bool:
    """指纹查重 + 占位一步做完。

    两个微信号监听同一个群时，同一条内容会到两次（msg_id 不同）：
    先 is_fingerprint_seen 再 mark_fingerprint 的话，两条同时到时都会判"没见过"，
    于是同一个群被记两笔账。
    """
    global _fp
    if not fp:
        return True
    with _FP_LOCK:
        if fp in _fp_set:
            return False
        _fp_set.add(fp)
        _fp.append(fp)
        if len(_fp) > MAX_FINGERPRINTS:
            drop = len(_fp) - MAX_FINGERPRINTS
            for x in _fp[:drop]:
                _fp_set.discard(x)
            _fp = _fp[drop:]
    _save_fingerprints()
    return True


def is_fingerprint_seen(fp: str) -> bool:
    with _FP_LOCK:
        return fp in _fp_set


def mark_fingerprint(fp: str):
    global _fp
    with _FP_LOCK:
        if fp in _fp_set:
            return
        _fp_set.add(fp)
        _fp.append(fp)
        if len(_fp) > MAX_FINGERPRINTS:
            drop = len(_fp) - MAX_FINGERPRINTS
            for x in _fp[:drop]:
                _fp_set.discard(x)
            _fp = _fp[drop:]
        _save_fingerprints()


def unmark_fingerprint(fp: str) -> bool:
    """撤销时把内容指纹放出来，否则同一个群另一个号重推还会被判成跨账号重复。"""
    global _fp
    if not fp:
        return False
    with _FP_LOCK:
        if fp not in _fp_set:
            return False
        _fp_set.discard(fp)
        _fp = [x for x in _fp if x != fp]
        _save_fingerprints()
    return True


_load_fingerprints()


# ======================== 回执补发（采集端超时后重新来取）========================
#
# 采集端 pusher 的 timeout 只有 8 秒，而一条要走大模型的消息可能要十几秒。
# 第一次请求超时后采集端会重推同一条 —— 那时 msg_id 已经被 claim 挡住，
# 走的是"重复推送，跳过"分支，原来那条回执就再也发不出去了：
# 账记上了，客户却没收到"已记账 ¥200"。
#
# 所以每个 msg_id 的回执在这里留一份：
#   还在算  → 告诉采集端 pending，让它稍后再来（别当成功走掉）
#   算完了  → 把当初没收到的回执直接补给它
# 只留最近几百条，够覆盖采集端的重试窗口（退避最长 300 秒 × 8 次）。

_REPLY_LOCK = threading.Lock()
_reply_cache: "OrderedDict[str, dict]" = OrderedDict()
_REPLY_CACHE_MAX = 500
_pending_msgs = set()


def mark_pending(msg_id: str):
    with _REPLY_LOCK:
        _pending_msgs.add(msg_id)


def clear_pending(msg_id: str):
    with _REPLY_LOCK:
        _pending_msgs.discard(msg_id)


def cache_reply(msg_id: str, reply: dict):
    with _REPLY_LOCK:
        _reply_cache[msg_id] = reply
        _reply_cache.move_to_end(msg_id)
        while len(_reply_cache) > _REPLY_CACHE_MAX:
            _reply_cache.popitem(last=False)


def fetch_reply_state(msg_id: str) -> Tuple[str, dict]:
    """重推时问一句：这条还在算吗？算完了当初的回执是什么？

    返回 ("pending", None) / ("done", 回执或 None)。
    """
    with _REPLY_LOCK:
        if msg_id in _pending_msgs:
            return "pending", None
        return "done", _reply_cache.get(msg_id)


# ======================== 消息流水（客户要看"到底收到了什么"） ========================
#
# 和采集端的 messages_*.log 是一对：那边记"我采到了它"，这边记"我拿它干了什么"。
# 只留 7 天，按天一个文件，过期自动删 —— 客户不会来清，得自己长记性。

MSG_DIR = DATA_DIR / "messages"
MSG_KEEP_DAYS = 7


def _msg_dir() -> Path:
    MSG_DIR.mkdir(parents=True, exist_ok=True)
    return MSG_DIR


def _msg_path(day: str) -> Path:
    return _msg_dir() / ("msg_%s.jsonl" % day)


def append_messages(rows: List[dict]):
    """一次一批写进去。写失败只丢流水，绝不能影响记账。"""
    if not rows:
        return 0
    day = datetime.now().strftime("%Y-%m-%d")
    try:
        p = _msg_path(day)
        with open(p, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(rows)
    except Exception as e:
        logger.warning(f"写消息流水失败：{e}")
        return 0


def prune_messages(keep_days: int = MSG_KEEP_DAYS) -> int:
    """删掉过期那几天的流水文件。返回删了几个。"""
    removed = 0
    try:
        cutoff = time.time() - max(1, int(keep_days)) * 86400
        for p in sorted(_msg_dir().glob("msg_*.jsonl")):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except Exception:
                continue
    except Exception:
        return 0
    return removed


def tail_messages(limit: int = 200) -> List[dict]:
    """最近 limit 条，新的在前。从最新的一天往回读，凑够就停。"""
    got: List[dict] = []
    try:
        files = sorted(_msg_dir().glob("msg_*.jsonl"), reverse=True)
    except Exception:
        return []
    for p in files:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = [ln for ln in f if ln.strip()]
        except Exception:
            continue
        need = limit - len(got)
        if need <= 0:
            break
        for ln in reversed(lines[-need:]):
            try:
                got.append(json.loads(ln))
            except Exception:
                continue
        if len(got) >= limit:
            break
    return got


# ======================== 记账核心 ========================

_recent_records: List[dict] = []


def _append_record(rec: dict, unparsed: bool = False, account=None):
    p = account_files(account)["unparsed" if unparsed else "records"]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"写记录失败：{e}")
    if not unparsed:
        _recent_records.append(rec)
        if len(_recent_records) > MAX_RECORDS_IN_MEMORY:
            del _recent_records[: len(_recent_records) - MAX_RECORDS_IN_MEMORY]


DRAW_WEEKDAYS = (1, 3, 5)          # 周二 / 周四 / 周六，六合彩一周三期


def _as_date(ts=None):
    """把消息时间洗成一个 date。

    采集端传的是 "YYYY-MM-DD HH:MM:SS" 字符串，探针脚本有时直接传 epoch 秒，
    两种都得认 —— 认不出来就退回今天，绝不能因为时间格式让整条账记不进去。
    """
    if not ts:
        return datetime.now().date()
    if isinstance(ts, bool):
        return datetime.now().date()
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).date()
        except Exception:
            return datetime.now().date()
    s = str(ts).strip()
    if not s:
        return datetime.now().date()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(float(s)).date()
    except Exception:
        return datetime.now().date()


def _as_dt(ts=None):
    """和 _as_date 认同样的格式，但保留时分 —— 封盘要看"几点"，不能只看哪天。"""
    if not ts or isinstance(ts, bool):
        return datetime.now()
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts))
        except Exception:
            return datetime.now()
    s = str(ts).strip()
    if not s:
        return datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(float(s))
    except Exception:
        return datetime.now()


# 开奖时间：过这个点本期就不再收注，次日 0 点自动进下一期。
# 期号本来就按开奖日（周二/周四/周六）算，所以这里只需要一个「当天几点封盘」。
DRAW_TIME_DEFAULT = "21:30"


def _parse_hhmm(s):
    """'21:30' / '9:30' / '2130' → (21, 30)；认不出来返回 (None, None)。"""
    t = str(s or "").strip().replace("：", ":").replace(".", ":")
    if not t:
        return None, None
    if ":" not in t and t.isdigit() and len(t) in (3, 4):
        t = t[:-2] + ":" + t[-2:]
    try:
        hh, mm = t.split(":")[:2]
        hh, mm = int(hh), int(mm)
    except Exception:
        return None, None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None, None
    return hh, mm


DRAW_TIME_RAW = cfg("draw_time", "MSB_DRAW_TIME", DRAW_TIME_DEFAULT)
DRAW_TIME_HH, DRAW_TIME_MM = _parse_hhmm(DRAW_TIME_RAW)
if DRAW_TIME_HH is None:
    logger.warning(f"开奖时间写法不对（{DRAW_TIME_RAW}），按 {DRAW_TIME_DEFAULT} 处理")
    DRAW_TIME_RAW = DRAW_TIME_DEFAULT
    DRAW_TIME_HH, DRAW_TIME_MM = _parse_hhmm(DRAW_TIME_DEFAULT)
DRAW_TIME = "%02d:%02d" % (DRAW_TIME_HH, DRAW_TIME_MM)


def draw_cutoff_state(at=None) -> dict:
    """这一期还收不收注。

    at 传消息时间就用消息时间判 —— 采集端积压几分钟再推上来时，客户明明是在
    开奖前发的注，不能因为服务器收到得晚就退回去。不传按服务器当前时间判。
    """
    now = _as_dt(at)
    issue = issue_of(now)
    try:
        d = datetime.strptime(issue, "%Y-%m-%d").date()
    except Exception:
        d = now.date()
    close_at = datetime(d.year, d.month, d.day, DRAW_TIME_HH, DRAW_TIME_MM)
    return {
        "issue": issue,
        "draw_time": DRAW_TIME,
        "close_at": close_at,
        "closed": now >= close_at,
        "now": now,
    }


def issue_of(ts=None) -> str:
    """这条账归哪一期，用开奖日做期号。

    从今天（含）往后找最近的开奖日：周一收到的东西归周二那期，不归周四；
    周日归周二。一期最长就跨 3 天（周六 → 周二）。

    切换时点按自然日 0 点：周二 23:59 收的仍算周二期，周三 0 点起算周四期。
    六合彩实际开奖在晚上，这里不跟真实开奖时刻对齐 —— 期号只是分账的桶，
    不是开奖凭证。
    """
    d = _as_date(ts)
    for i in range(4):
        cand = d + timedelta(days=i)
        if cand.weekday() in DRAW_WEEKDAYS:
            return cand.strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


# ======================== 期次归档 ========================
#
# 账本（numbers_state.json）只装"当前这一期"。换期的时候把装满的那本原样
# 抄进 issues/<期号>.json，再把当前这本清零，current_issue.txt 记下新期号。
#
# 历史期只读：撤销只允许撤本期的账。上一期已经结算，改它只会让归档和流水
# 对不上，业务上也没这个需求。

def current_issue(account=None) -> str:
    """这台账本现在记的是哪一期。

    文件不存在（老部署或第一单还没来）就按今天算，顺便把文件补上 ——
    不然每次记账都要判一次空。
    """
    p = account_files(account)["issue"]
    try:
        if p.exists():
            v = (p.read_text(encoding="utf-8") or "").strip()
            if v:
                return v
    except Exception:
        pass
    return issue_of()


def _write_issue_marker(account, issue: str):
    p = account_files(account)["issue"]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(issue + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"写期号失败（{account} {issue}）：{e}")


def archive_issue(account=None, issue: str = "") -> bool:
    """把当前账本原样存进 issues/<期号>.json，然后清零。

    返回 True 表示真的归档了（有内容或期号变了）；False 表示本来就不用动。
    空账本也写一份 —— 期号连续起来才好查"那一期到底有没有人下注"。
    """
    a = resolve_account(account)
    files = account_files(a)
    old = issue or current_issue(a)

    d = files["issues"]
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"建期次目录失败：{e}")
        return False

    path = d / (old + ".json")
    # 港、澳各一本，归档时两本一起走，谁也不能丢
    books = {}
    for m in ("MO", "HK"):
        dm = load_data(a, m)
        om = load_others(a, m)
        board = round(sum(float(v or 0) for v in dm.values()), 2)
        flat = round(float(om.get("total") or 0), 2)
        books[m] = {
            "total": board,
            "flat_total": flat,
            "grand_total": round(board + flat, 2),
            "numbers": {k: round(float(v or 0), 2) for k, v in dm.items()},
            "others": om.get("items") or [],
        }
    rec = {
        "issue": old,
        "account": a,
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # 顶层保留澳门这一本，老看板/老脚本读得动
        "total": books["MO"]["total"],
        "flat_total": books["MO"]["flat_total"],
        "grand_total": round(books["MO"]["grand_total"] + books["HK"]["grand_total"], 2),
        "numbers": books["MO"]["numbers"],
        "others": books["MO"]["others"],
        "markets": books,
    }
    try:
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, str(path))
    except Exception as e:
        logger.warning(f"归档期次失败（{a} {old}）：{e}")
        return False

    for m in ("MO", "HK"):
        save_data(init_data(), a, m)
        save_others({"total": 0.0, "items": []}, a, m)
    logger.info(f"期次归档：{a} {old} 澳 {books['MO']['grand_total']:.2f} + "
                f"港 {books['HK']['grand_total']:.2f}，账本已清零")
    return True


def ensure_issue(account=None, issue: str = "", info: dict = None) -> str:
    """记账前调：确保账本对得上这次要记的期号，对不上就先归档再清零。

    返回归档后应当使用的期号。跨期只会在"隔了好几天才来第一单"时触发一次。

    期号文件不存在时（老部署、或这个号从没记过账）直接认下这一期，不拿现有
    账本去归档 —— 那种情况下的余额来路不清，硬归档只会造出一份假的"历史期"。
    """
    a = resolve_account(account)
    want = issue or issue_of()
    p = account_files(a)["issue"]
    have = ""
    try:
        if p.exists():
            have = (p.read_text(encoding="utf-8") or "").strip()
    except Exception:
        pass
    if have == want:
        return want
    # 期号只能往前走。采集端积压时可能补推几天前的消息，那时 want 比 have 小；
    # 照 want 归期等于把账本倒回上一期并清零，刚收的账全会被冲掉。
    # 这种迟到的旧账就记在当期，日志里留痕。
    if have and want < have:
        logger.warning(f"迟到消息：{a} 消息归 {want}，账本已在 {have}，按当期记")
        return have
    if have:
        logger.info(f"期次切换：{a} {have} → {want}")
        # 归档会清零，先把清零前的数留一份，界面才能告诉用户「上一期多少钱走了」
        try:
            before = market_totals(a)["ALL"]["total"]
        except Exception:
            before = None
        archive_issue(a, have)
        if info is not None:
            info["archived_issue"] = have
            info["archived_total"] = before
    _write_issue_marker(a, want)
    return want


def issue_summary(account=None, limit: int = 60) -> Dict[str, object]:
    """历史期一览：归档文件 + 流水的笔数/金额，按新到旧排。"""
    a = resolve_account(account)
    files = account_files(a)
    cur = current_issue(a)

    out = []
    d = files["issues"]
    if d.exists():
        for p in sorted(d.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception:
                continue
            out.append({
                "issue": rec.get("issue") or p.stem,
                "total": rec.get("total", 0),
                "archived_at": rec.get("archived_at") or "",
                "current": False,
            })

    # 本期从流水现算，因为账本只存"每个号多少"，笔数要看记录。
    # 用 applied 求和而不是 total：一笔命中 4 个号时 total 是 400，
    # 但如果有号不在 1~49 就没落进账本，此时 total 和实际入账对不上。
    # 冲正记录的 applied 本身就是负数，直接加，不要再减一次。
    live = 0.0
    cnt = 0
    for r in iter_records(a):
        if (r.get("issue") or "") != cur:
            continue
        ap = r.get("applied")
        if isinstance(ap, dict) and ap:
            live += sum(float(v or 0) for v in ap.values())
        else:
            live += float(r.get("total") or 0)      # 老记录没有 applied，只能退回用 total
        if str(r.get("type") or "") != "reversal":
            cnt += 1

    out.insert(0, {
        "issue": cur,
        "total": round(live, 2),
        "count": cnt,
        "archived_at": "",
        "current": True,
    })
    return {"account": a, "current": cur, "issues": out}


def record_bet(text: str, msg_id: str, chat_name: str = "", sender: str = "",
               sender_name: str = "", account: str = "",
               fp: str = "", chat_id: str = "", create_time=None,
               market: str = "", allow_after_close: bool = False) -> dict:
    """一单：解析 → 累加 → 落盘。返回结果 dict（供接口/日志用）。

    account 决定落到哪个账号的账本；空则落 DEFAULT_ACCOUNT。
    fp / chat_id / create_time 只为了以后能撤销这一笔：撤销要把消息号和
    内容指纹都放出来，不存下来就找不回去。
    market 是盘口覆盖：控制台手工记账已经用「澳门/香港」开关明确选了，
    就按它落账，不再看文本里有没有「港」；留空仍按文本判（微信推送走这条）。
    allow_after_close 只有手工记账会传 True —— 开奖后老板补记客户电话/纸单
    是正常业务，微信推送不能开这个口子。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "空文本"}

    # 开奖时间闸门。放在解析之前：封盘了就别再花模型额度和几百毫秒。
    if not allow_after_close:
        st = draw_cutoff_state(create_time)
        if st["closed"]:
            logger.info(f"[已开奖] {chat_name or '?'} {text[:40]}"
                        f"（本期 {st['issue']} 已过 {st['draw_time']}）")
            return {"ok": False, "closed": True,
                    "reason": "本期已开奖（%s）" % st["draw_time"]}

    acc = resolve_account(account)
    zodiac_mapping = current_zodiac_mapping()

    # 先用纯规则解析：能算的绝不花模型额度和那几百毫秒。
    # 规则算不了的才退回模型（模型现在也认 12/13/14 整笔玩法）；
    # 判死项（中特/特肖、鼠5尾、平特写号码、组合注带各/肖数不符、没数字）不送模型。
    local = bet_rules.parse_local(text, zodiac_mapping)
    # 香港/澳门两本账：含 香/港/巷 算香港，没标的算澳门（文档第 15 条）
    forced = (market or "").strip().upper()
    if forced in ("HK", "MO"):
        market = forced
    else:
        market = local.get("market") or bet_rules.detect_market(text)
    if market not in ("HK", "MO"):
        market = "MO"
    detail = ""
    extras: List[dict] = []
    parcels: List[dict] = []
    if local["ok"]:
        bets = local["bets"]
        extras = list(local.get("extras") or [])
        parcels = list(local.get("parcels") or [])
        # 一条消息里港、澳都写了：顶层 bets 是空的（跨盘同号不能合并），
        # 这里先汇总一份只用于判断"有没有认出东西"和强制单盘的情况，
        # 真正的落账走下面的 segs。
        if len(parcels) > 1:
            merged: Dict[str, float] = {}
            for p in parcels:
                for n, a in (p.get("bets") or {}).items():
                    merged[n] = round(merged.get(n, 0.0) + a, 2)
            bets = merged
            extras = [e for p in parcels for e in (p.get("extras") or [])]
        detail = local["detail"]
        source = "规则"
    elif local.get("fatal") or "暂不支持" in (local.get("reason") or ""):
        # 这类模型也认不准（鼠5尾 有歧义、纯寒暄、组合注写法不对），
        # 判死比让它猜一个更可靠，也省一次调用。
        bets = {}
        source = "不支持"
    else:
        # 规则吃不下的才问模型。模型现在也认 12/13/14（prompt 里有整笔玩法口径），
        # 整笔注走 extras，金额由程序校验，不采信模型自己算的合计。
        bets, model_extras = parse_bet_text_full(text, zodiac_mapping)
        if model_extras:
            extras = model_extras
        source = "模型"

    if not bets and not extras:
        why = local.get("reason") or "解析失败"
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "msg_id": msg_id, "chat": chat_name, "sender": sender,
            "sender_name": sender_name, "account": acc,
            "text": text, "reason": why,
        }
        _append_record(rec, unparsed=True, account=acc)
        logger.warning(f"[未解析] [{acc}] {chat_name} {text[:60]} → {why}")
        return {"ok": False, "reason": why}

    rec_id = "%s#%d" % (msg_id, int(time.time() * 1000))

    # 一条消息里港、澳都写了（客户说正常不会这么报，但要求支持）：按段各落一笔账。
    # 撤销时一次撤掉这条消息的所有段，每段扣各自的盘，不会把香港的钱扣到澳门去。
    parcels = list(local.get("parcels") or [])
    if local["ok"] and len(parcels) > 1 and forced not in ("HK", "MO"):
        segs: List[dict] = [{
            "market": p.get("market") if p.get("market") in ("HK", "MO") else "MO",
            "bets": p.get("bets") or {},
            "extras": p.get("extras") or [],
            "detail": p.get("detail") or "",
            "rec_id": "%s-%d" % (rec_id, idx + 1),
        } for idx, p in enumerate(parcels)]
    else:
        segs = [{"market": market, "bets": bets, "extras": extras,
                 "detail": detail, "rec_id": rec_id}]

    moved: dict = {}
    written: List[dict] = []
    for seg in segs:
        seg_market = seg["market"]
        with DATA_LOCK:
            # 换期就在这里发生：账本如果还是上一期的，先归档再清零。
            issue = ensure_issue(acc, issue_of(create_time), info=moved)
            data = load_data(acc, seg_market)
            applied = {}
            for number, amount in sorted((seg["bets"] or {}).items()):
                if number in data:
                    data[number] += amount
                    applied[number] = amount
            save_data(data, acc, seg_market)
            # 平特整肖、连肖、N中M 只记总额，不进 49 号盘（号盘是"每号多少钱"）。
            # 这笔钱单独存，合计要带上，不然总金额会比客户报的少一截。
            extra_total = 0.0
            if seg["extras"]:
                od = load_others(acc, seg_market)
                od["issue"] = issue
                for e in seg["extras"]:
                    amt = round(float(e.get("amount") or 0), 2)
                    od["items"].append({
                        "rec_id": seg["rec_id"],
                        "play": e.get("play") or "",
                        "text": e.get("text") or "",
                        "amount": amt,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        # 这三个字段让总览/网页看板不必再猜原文：哪类注、哪几个肖、是否各付
                        "kind": e.get("kind") or "",
                        "zodiacs": e.get("zodiacs") or "",
                        "each": bool(e.get("each")),
                    })
                    extra_total = round(extra_total + amt, 2)
                od["total"] = round(float(od.get("total") or 0) + extra_total, 2)
                save_others(od, acc, seg_market)
            # 总金额 = 澳门盘 + 香港盘 + 两边不计号
            grand_total = market_totals(acc)["ALL"]["total"]
            top = top_numbers(data, 10)

        total_amount = round(sum(applied.values()) + extra_total, 2)
        rec = {
            # 撤销后客户可能重发一遍，同一条 msg_id 于是会有多笔记账记录。
            # rec_id 才是这一笔的唯一身份，撤销认它，不认 msg_id。
            "rec_id": seg["rec_id"],
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "msg_id": msg_id, "chat": chat_name, "sender": sender,
            "sender_name": sender_name, "account": acc,
            "text": text,
            "market": seg_market,
            "numbers": len(applied),
            "total": round(total_amount, 2),
            # 这一笔实际给哪几个号各加了多少。撤销照着它逐号减回去，
            # 不能只减 total —— 一笔命中 4 个号时，400 不知道该落到哪儿。
            "applied": {n: round(a, 2) for n, a in applied.items()},
            # 不进号盘的那部分（平特整肖、连肖、N中M），撤销按 rec_id 从这儿扣
            "extras": seg["extras"], "extra_total": round(extra_total, 2),
            # 撤销要把消息号和指纹放开，得先把它们存下来
            "fp": fp, "chat_id": chat_id, "create_time": create_time,
            "issue": issue,
            "grand_total": round(grand_total, 2),
            "top": [{"num": n, "amount": round(a, 2)} for n, a in top],
            # 这单是规则算的还是模型算的，出账争议时得能查
            "source": source,
            "detail": seg["detail"],
        }
        if moved.get("archived_issue"):
            rec["archived_issue"] = moved.get("archived_issue")
            rec["archived_total"] = moved.get("archived_total")
        _append_record(rec, account=acc)
        logger.info(f"记账成功（{source}）：[{acc}] {chat_name} ${total_amount:.2f}，"
                    f"{len(applied)}个号码" + (f"+{extra_total:.2f}(不计号)" if seg["extras"] else "")
                    + f"，{seg_market}，累计 ${grand_total:.2f}")
        written.append({"market": seg_market, "numbers": len(applied),
                        "total": total_amount, "flat_total": extra_total,
                        "extras": seg["extras"], "detail": seg["detail"],
                        "rec_id": seg["rec_id"]})

    if len(written) == 1:
        w = written[0]
        if not detail:
            detail = w["detail"] or "%d个号 共计%s" % (w["numbers"], _fmt_money(w["total"]))
        return {"ok": True, "numbers": w["numbers"], "total": round(w["total"], 2),
                "flat_total": round(w["flat_total"], 2), "extras": w["extras"],
                "market": w["market"],
                "grand_total": round(grand_total, 2), "account": acc,
                "source": source, "detail": detail,
                "archived_issue": moved.get("archived_issue"),
                "archived_total": moved.get("archived_total")}

    # 分段落了两笔以上：返回合计，同时把每段明细给出去
    return {"ok": True,
            "numbers": sum(w["numbers"] for w in written),
            "total": round(sum(w["total"] for w in written), 2),
            "flat_total": round(sum(w["flat_total"] for w in written), 2),
            "extras": [e for w in written for e in (w["extras"] or [])],
            "market": written[0]["market"],
            "segments": [{"market": w["market"], "numbers": w["numbers"],
                          "total": round(w["total"], 2), "rec_id": w["rec_id"]}
                         for w in written],
            "grand_total": round(grand_total, 2), "account": acc,
            "source": source,
            "detail": "；".join("%s %s" % ("香港" if w["market"] == "HK" else "澳门", w["detail"])
                               for w in written if w["detail"]) or detail,
            "archived_issue": moved.get("archived_issue"),
            "archived_total": moved.get("archived_total")}


# ======================== 撤销（冲正） ========================
#
# 原则：账本只增不改。撤一笔 = 往流水里追加一条负数记录，逐号减回去。
# 不回头改原来那条 —— 改了就没有痕迹，也无法从流水重算出当前账本。

def iter_records(account=None) -> List[dict]:
    """按时间从旧到新读流水。account 为空就读所有账号。"""
    accs = [account] if account else all_accounts()
    out: List[dict] = []
    for a in accs:
        try:
            p = account_files(a)["records"]
            if not p.exists():
                continue
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if isinstance(r, dict):
                        r.setdefault("account", a)
                        out.append(r)
        except Exception:
            continue
    out.sort(key=lambda r: str(r.get("ts") or ""))
    return out


def _rec_id(r: dict) -> str:
    """这一笔记账记录的身份。老记录没有 rec_id，退回用 msg_id。"""
    return str(r.get("rec_id") or r.get("msg_id") or "")


def find_records_by_msg(msg_id: str) -> List[dict]:
    """这条消息的所有记账记录（不含冲正记录）。

    撤销后重推会产生多条 —— 所以不能见到一条就当只剩一条。
    """
    msg_id = (msg_id or "").strip()
    if not msg_id:
        return []
    return [r for r in iter_records()
            if str(r.get("type") or "") != "reversal"
            and str(r.get("msg_id") or "") == msg_id]


def _reversed_ids() -> set:
    return {str(r.get("reversal_of") or "") for r in iter_records()
            if str(r.get("type") or "") == "reversal"}


def find_record(msg_id: str) -> dict:
    """找这条消息最近一条、还没被撤销过的记账记录。"""
    cands = find_records_by_msg(msg_id)
    if not cands:
        return {}
    revd = _reversed_ids()
    for r in reversed(cands):
        if _rec_id(r) not in revd:
            return r
    return {}


def latest_record(msg_id: str) -> dict:
    """给界面查的版本：优先最近一条没撤的，全撤了就返回最后一条（好让人看见"已撤销"）。"""
    cands = find_records_by_msg(msg_id)
    if not cands:
        return {}
    revd = _reversed_ids()
    for r in reversed(cands):
        if _rec_id(r) not in revd:
            return r
    return cands[-1]


def is_reversed(msg_id: str) -> bool:
    """这条消息的所有记账记录都被撤掉了才算撤销过。

    只撤了其中一条（比如撤销后重推进来的那条还没撤），仍算"有活账"。
    """
    cands = find_records_by_msg(msg_id)
    if not cands:
        return False
    revd = _reversed_ids()
    return all(_rec_id(r) in revd for r in cands)


def reverse_record(msg_id: str, note: str = "") -> dict:
    """撤销一条消息。逐号减回去，追加冲正记录，放开消息号和指纹。

    一条消息可能落多笔账（例如「港 平特鼠100 澳 平特牛100」按词分盘各落一笔），
    所以这里一次把这条消息下**所有还没撤的记录**都撤掉，每段减各自的盘；
    只要有一段不满足撤销条件（旧账没存明细、跨期）就整条拒绝，不留半截残账。
    """
    msg_id = (msg_id or "").strip()
    if not msg_id:
        return {"ok": False, "error": "缺 msg_id"}
    cands = find_records_by_msg(msg_id)
    if not cands:
        return {"ok": False, "error": "流水里找不到这笔账（可能已经清理掉了）"}

    revd = _reversed_ids()
    todo = [r for r in cands if _rec_id(r) not in revd]
    if not todo:
        return {"ok": False, "error": "这笔已经撤销过了，不能撤两次"}

    # 先把每一段都校验一遍：任何一段不合格就整条不动
    plan = []
    acc0 = ""
    for rec in todo:
        applied = rec.get("applied")
        if not isinstance(applied, dict):
            applied = {}
        extras = rec.get("extras") or []
        if not applied and not extras:
            return {"ok": False,
                    "error": "这笔是旧版本记的账，没存号码明细，不敢猜着减。请手工改 numbers_state.json"}
        acc = resolve_account(str(rec.get("account") or ""))
        if acc0 and acc != acc0:
            return {"ok": False, "error": "这条消息的几笔不在同一个账号下，不敢一起撤"}
        acc0 = acc
        market = str(rec.get("market") or "MO")
        if market not in ("HK", "MO"):
            market = "MO"
        # 只撤本期的账。上一期已经归档结算，改它会让归档和流水对不上；
        # 数据已经在 issues/<期号>.json 里定版，要改就手工改那一份。
        cur = current_issue(acc)
        rec_issue = str(rec.get("issue") or "")
        if rec_issue and rec_issue != cur:
            return {"ok": False,
                    "error": f"这笔是 {rec_issue} 那期的账，本期是 {cur}。上期已结算归档，不能撤在本期"}
        plan.append((acc, market, rec, applied, extras))

    segs = []
    grand_total = 0.0
    with DATA_LOCK:
        # 后落的先撤，撤完账本状态和"逐条手撤"完全一致
        for acc, market, rec, applied, extras in reversed(plan):
            data = load_data(acc, market)
            done = {}
            for num, amt in applied.items():
                key = str(num)
                if key not in data:
                    continue
                try:
                    data[key] = round(float(data[key]) - float(amt), 2)
                except Exception:
                    continue
                done[key] = round(-float(amt), 2)
            save_data(data, acc, market)
            # 不计号的那部分（平特整肖、二中二…）按 rec_id 从 others 里摘掉
            cut = 0.0
            if extras:
                od = load_others(acc, market)
                keep = []
                for it in (od.get("items") or []):
                    if str(it.get("rec_id") or "") == _rec_id(rec):
                        cut = round(cut + float(it.get("amount") or 0), 2)
                    else:
                        keep.append(it)
                od["items"] = keep
                od["total"] = round(float(od.get("total") or 0) - cut, 2)
                save_others(od, acc, market)
            grand_total = market_totals(acc)["ALL"]["total"]

            rev = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "reversal",
                "reversal_of": _rec_id(rec),          # 认记录，不认消息号
                "origin_msg_id": msg_id,
                # 一条消息可能同时撤好几段，秒级时间戳会撞号，用毫秒 + 段序号
                "msg_id": "reversal-%s-%d-%d" % (msg_id, int(time.time() * 1000), len(segs) + 1),
                "account": acc,
                "chat": rec.get("chat") or "",
                "sender": rec.get("sender") or "",
                "sender_name": rec.get("sender_name") or "",
                "text": rec.get("text") or "",
                "market": market,
                "numbers": len(done),
                "total": round(sum(done.values()) - cut, 2),
                "applied": done,
                "extras": extras, "extra_total": round(-cut, 2),
                "issue": rec.get("issue") or issue_of(),
                "grand_total": grand_total,
                "note": note,
            }
            _append_record(rev, account=acc)
            segs.insert(0, {"market": market, "numbers": len(done),
                            "total": rev["total"], "rec_id": _rec_id(rec),
                            "applied": done, "text": rev["text"]})
            logger.warning(f"[撤销] {acc} {msg_id} 原文={rev['text']} 盘={market} "
                           f"减={rev['total']} {len(done)}个号 备注={note or '-'}")

    # 放开消息号和指纹：客户说「记错了重发一遍」时，重推才能重新入账
    unmark_message_processed(msg_id)
    for rec in todo:
        unmark_fingerprint(str(rec.get("fp") or ""))

    total = round(sum(s["total"] for s in segs), 2)
    res = {"ok": True, "msg_id": msg_id, "account": acc0,
           "text": segs[0]["text"] if segs else "", "total": total,
           "grand_total": grand_total, "count": len(segs)}
    if len(segs) == 1:
        res["market"] = segs[0]["market"]
        res["applied"] = segs[0]["applied"]
        res["numbers"] = segs[0]["numbers"]
    else:
        res["segments"] = [{k: s[k] for k in ("market", "numbers", "total", "rec_id")}
                           for s in segs]
        logger.warning(f"[撤销] {acc0} {msg_id} 一条消息共 {len(segs)} 笔 合计={total} "
                       f"累计={grand_total}")
    return res


_reversed_cache: Dict[str, object] = {"key": None, "val": set()}


def reversed_msg_ids() -> set:
    """所有记账记录都已被撤销的消息号集合。

    消息页每几秒刷新一次，要是每次把全部 records.jsonl 扫一遍，账本越长越卡。
    按文件 mtime 缓存：没写新记录就不重算。
    """
    key = []
    for a in all_accounts():
        try:
            p = account_files(a)["records"]
            key.append((str(p), p.stat().st_mtime if p.exists() else 0))
        except Exception:
            pass
    key = tuple(key)
    if _reversed_cache.get("key") == key:
        return _reversed_cache.get("val") or set()

    recs: Dict[str, List[str]] = {}
    revd = set()
    for r in iter_records():
        if str(r.get("type") or "") == "reversal":
            revd.add(str(r.get("reversal_of") or ""))
        else:
            recs.setdefault(str(r.get("msg_id") or ""), []).append(_rec_id(r))
    result = {mid for mid, ids in recs.items() if ids and all(i in revd for i in ids)}
    _reversed_cache["key"] = key
    _reversed_cache["val"] = result
    return result


def recalc_account(account=None) -> dict:
    """把流水里所有 applied 加起来，和 state 快照比对。

    没存 applied 的旧记录加不进去，单独计数 —— 所以老账存在时必然对不上，
    这不是 bug，是"从某个版本起才可重算"。
    """
    acc = resolve_account(account or "")
    # 账本只装当期，所以只比当期。换期之后拿全历史去比，必然差出一整个往期。
    cur = current_issue(acc)
    calc = init_data()
    no_detail = 0
    for r in iter_records(acc):
        if (r.get("issue") or "") != cur:
            continue
        ap = r.get("applied")
        if not isinstance(ap, dict) or not ap:
            if str(r.get("type") or "") != "reversal":
                no_detail += 1
            continue
        for num, amt in ap.items():
            key = str(num)
            if key in calc:
                try:
                    calc[key] = round(calc[key] + float(amt), 2)
                except Exception:
                    continue
    snap = load_data(acc)
    diffs = []
    for k in sorted(calc.keys()):
        a = round(float(calc.get(k) or 0), 2)
        b = round(float(snap.get(k) or 0), 2)
        if abs(a - b) > 0.009:
            diffs.append({"num": k, "from_records": a, "from_state": b,
                          "diff": round(b - a, 2)})
    return {
        "account": acc,
        "records_total": round(sum(calc.values()), 2),
        "state_total": round(sum(snap.values()), 2),
        "diff": round(sum(snap.values()) - sum(calc.values()), 2),
        "records_without_detail": no_detail,
        "diff_numbers": diffs[:20],
        "match": not diffs,
    }


# ======================== 验签 ========================

# ---- 白名单条目的两个可选把关：有效期、来源 IP ----
# 这两个检查都在服务端做，客户把客户端本地校验拆了也没用。
# 到期就断，不留缓冲：有效期至 2027-11-16，那就到 2027-11-16 当天为止，
# 次日 00:00 起一律拒收。服务端自己按本机日期算，跟客户机的时间无关。
EXPIRE_GRACE_DAYS = 0


def _allow_ip_list(text) -> List[str]:
    """IP 白名单是手填的，逗号 / 分号 / 空格 / 换行都当分隔符。"""
    return [p for p in re.split(r"[,;\s]+", str(text or "").strip()) if p]


def ip_allowed(it: Dict[str, object], ip: str) -> bool:
    """条目没填 ip_allow = 全放行；填了就只认列出来的 IP 或 CIDR。

    本机回环单独当一家人：服务端和采集端装在同一个电脑上时最常见，
    采集端填 `localhost` 可能被解析成 IPv6 的 ::1，也可能落到 127.0.0.1，
    取决于服务端监听的是 IPv4 还是双栈。客户不该为了这个去猜地址族，
    所以 127.0.0.1 / ::1 / localhost 三种写法互相都认。
    """
    allow = _allow_ip_list(it.get("ip_allow"))
    if not allow:
        return True
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False               # 拿不到来源 IP（或格式不对）一律不能放进白名单之外
    for item in allow:
        low = item.strip().lower()
        if low in ("localhost", "loopback"):
            if addr.is_loopback:
                return True        # 写 localhost = 只收本机
            continue
        try:
            if "/" in item:
                net = ipaddress.ip_network(item, strict=False)
                if addr in net:
                    return True
                # 127.0.0.0/8 这类回环网段：::1 天然不在里面，单独补上
                if addr.is_loopback and net.network_address.is_loopback \
                        and net.broadcast_address.is_loopback:
                    return True
            else:
                item_addr = ipaddress.ip_address(item)
                if addr == item_addr:
                    return True
                # 写 127.0.0.1、来源是 ::1（或反过来）也是同一个本机
                if addr.is_loopback and item_addr.is_loopback:
                    return True
        except ValueError:
            continue               # 某一条写错了不影响其它条目，别整条白名单失效
    return False


def client_expired(it: Dict[str, object], now=None) -> bool:
    """expire_at 为空 = 不限期（老条目兼容）；到期后不接受任何推送。

    界面「新增」的默认值是 9999-12-31（也就是不限期）。这个日期本身能 parse，
    但再加 3 天就超出 date 的上限 —— date(9999,12,31) + timedelta(3) 直接
    OverflowError，会让**每一次推送**都 500。所以容差那步单独兜住：加不上就是
    已经到天了，当作不限期。
    """
    exp = str(it.get("expire_at") or "").strip()
    if not exp:
        return False
    try:
        d = datetime.strptime(exp[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    today = (now or datetime.now()).date()
    try:
        limit = d + timedelta(days=EXPIRE_GRACE_DAYS)
    except OverflowError:
        return False                         # 日期大到加不动 = 等于不限期
    return today > limit


def secret_for(wechat: str, ip: str = "") -> str:
    """按微信号取该采集端的密钥。空字符串 = 拒绝。

    白名单是唯一可信来源；名单为空时才回退旧的全局配置，保证
    "刚装上还没签发" 的部署不会一上来就全断。
    """
    wl = load_clients()
    wid = (wechat or "").strip()
    it = (wl["map"] or {}).get(wid)
    if it:
        if not bool(it.get("enabled", True)):
            return ""                       # 已停用：立刻失效
        if client_expired(it):
            return ""                       # 到期：客户端改本地日期也没用
        if not ip_allowed(it, ip):
            return ""                       # 来源 IP 不在名单里
        return str(it.get("secret") or "")
    if int(wl["enabled"]):
        return ""                           # 严格模式：不在名单 → 拒
    # 名单为空：兼容旧部署（客户编号映射 / 全局单密钥）
    return ACCOUNT_SECRETS.get(wid) or WX_PUSH_SECRET


def _client_ip() -> str:
    """来源 IP。默认只认 TCP 对端地址。

    以前优先看 X-Forwarded-For，可这个头任何人都能自己写 —— 一旦用它
    判 IP 白名单，等于给了别人一把"填个 127.0.0.1 就进来"的钥匙。
    只有确实挂了反代（且反代自己会覆盖这个头）时才靠它，用环境变量显式开。
    """
    if (os.environ.get("MSB_TRUST_XFF") or "").strip().lower() in ("1", "true", "yes"):
        xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if xff:
            return xff
    return request.remote_addr or "?"


def _reject_reason(wechat: str, ip: str = "") -> str:
    """把「为什么拒」说清楚，方便排查是不是白名单没配上。"""
    wl = load_clients()
    wid = (wechat or "").strip()
    if not wid:
        return "没带 X-Wx-Cust（微信号没传上来：采集端的授权码还没粘好或没生效，服务端靠它查白名单）"
    it = (wl["map"] or {}).get(wid)
    if not it:
        return ("不在白名单（严格模式只收名单内的。核对这个微信号与服务端白名单里填的是否一模一样 —— "
                "采集端顶部「微信账号」那格右边有复制按钮）"
                if int(wl["enabled"]) else "白名单为空且没配全局密钥")
    if not bool(it.get("enabled", True)):
        return "该微信号在白名单里已被停用"
    if client_expired(it):
        return "该客户端的有效期已过（%s），服务端按 expire_at 判定" % (
            it.get("expire_at") or "-")
    if not ip_allowed(it, ip):
        return "来源 IP %s 不在该客户端的 IP 白名单里（%s）" % (
            ip or "?", it.get("ip_allow") or "")
    return "签名或时间戳不对"


def _log_reject(kind: str, wechat: str, ts: str) -> None:
    # 别人知道 URL 来试探时，这条日志是唯一的痕迹
    ip = _client_ip()
    logger.warning("拒绝%s：来源=%s 微信号=%s 原因=%s ts=%s"
                   % (kind, ip, wechat or "(空)", _reject_reason(wechat, ip), ts))


def verify_signature(raw: bytes, ts: str, sig: str, wechat: str = "") -> bool:
    if WX_SKIP_VERIFY:
        return True
    secret = secret_for(wechat, _client_ip())
    if not secret or not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > WX_SKEW_SEC:
            logger.warning(f"时间戳超窗：ts={ts}")
            return False
    except Exception:
        return False
    expect = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.".encode("utf-8") + raw,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expect, sig)


def verify_get_signature(ts: str, sig: str, path: str, method: str = "GET",
                         wechat: str = "") -> bool:
    """GET 类接口的签名：HMAC_SHA256(secret, "{METHOD}.{path}.{ts}")。

    和推送报文的 "{ts}.{原始body}" 是**两套规则**——因为 GET 没有 body。
    采集端（WxPushTool 的 TargetDispatcher）就是这么签的，别改成一样。
    """
    if WX_SKIP_VERIFY:
        return True
    secret = secret_for(wechat)
    if not secret or not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > WX_SKEW_SEC:
            logger.warning(f"GET 验签时间戳超窗：ts={ts}")
            return False
    except Exception:
        return False
    expect = hmac.new(
        secret.encode("utf-8"),
        f"{method}.{path}.{ts}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expect, sig)


# ======================== Flask ========================

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
app = Flask(__name__, template_folder=str(_TEMPLATE_DIR))
# 中文原样输出，不转成 \uXXXX。采集端把回执当纯文本打印给客户看
# （比如 401 的 reason），转义了就成了天书；浏览器那边 JSON 一样能解析。
app.json.ensure_ascii = False

TEXT_TYPES = {"文本"}
SKIP_TYPES = {"系统消息"}


# ======================== 消息接收 ========================
#
# 采集端把消息推过来 → 这里验签、解析、记账。响应体里同时下发
# 「给客户的回复文案」，采集端照着发就行。

def _fmt_money(v) -> str:
    """金额显示成 200 而不是 200.0，两个号差 0.5 时也不能丢小数。"""
    try:
        f = float(v)
    except Exception:
        return "0"
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return ("%.2f" % f).rstrip("0").rstrip(".")


@app.route("/wx/hook", methods=["POST"])
def wx_hook():
    raw = request.get_data()                       # 必须用原始字节，不能 request.json 重组
    ts = request.headers.get("X-Wx-Timestamp", "")
    sig = request.headers.get("X-Wx-Signature", "")
    wechat = request.headers.get("X-Wx-Cust", "")

    if not verify_signature(raw, ts, sig, wechat):
        ip = _client_ip()
        reason = _reject_reason(wechat, ip)
        _log_reject("推送", wechat, ts)
        # 把原因带回采集端：客户装完第一件事就是点「测试地址」，
        # 只回一个 401 他没法知道是号不对、过期了、还是 IP 没放行，
        # 只能跑来问。原因里不含密钥，也不含别人的条目，说清楚更省事。
        return jsonify({"ok": False, "error": "unauthorized", "reason": reason}), 401
    _LAST_PUSH[(wechat or "").strip()] = time.time()

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return jsonify({"ok": False, "error": "bad json"}), 400

    messages = payload.get("messages") or []
    default_acc = payload.get("account") or ""
    accepted = 0
    recorded = 0
    unparsed = 0
    skipped = 0

    msg_rows: List[dict] = []
    # 只对「记账成功」的消息下发回复文案。跳过和未解析一律不回 ——
    # 客户发的是下注内容，回一句「收到」就是确认动作；没记上账还回「收到」
    # 等于骗人，比不回更糟。
    replies: List[dict] = []
    # 重推时补发给采集端的：算完了的回执（当初那次超时没送到）和还在算的消息号
    replay_replies: List[dict] = []
    pending_ids: List[str] = []

    def _row(m, result, reason="", acc="", amount=None):
        """一条流水。跳过的行没有落定账号，account 留空，界面退回显示微信号。"""
        return {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "msg_id": str(m.get("msg_id") or ""),
            "wechat": wechat,
            "account": acc,
            "chat": str(m.get("chat_name") or m.get("chat_id") or ""),
            "sender": str(m.get("sender_name") or m.get("sender") or ""),
            "mtype": str(m.get("msg_type") or ""),
            "content": (m.get("content") or "").strip()[:400],
            "result": result,
            "reason": reason,
            "amount": amount,
        }

    for m in messages:
        msg_id = str(m.get("msg_id") or "")
        if not msg_id:
            skipped += 1
            msg_rows.append(_row(m, "跳过", "缺 msg_id"))
            continue
        if not claim_message(msg_id):
            # 大概率是采集端上次请求超时后的重推。分三种情况：
            #   还在算   → 让它别当成功，退避后再来（不记流水，不然列表全是重复噪音）
            #   算完了   → 把当初那份回执补给它，客户这次才能收到回复
            #   早处理完且缓存过期 → 只能算了（真重复，前面已经回过）
            state, cached = fetch_reply_state(msg_id)
            if state == "pending":
                pending_ids.append(msg_id)
                continue
            skipped += 1
            msg_rows.append(_row(m, "跳过", "重复推送"))
            if cached:
                replay_replies.append(cached)
            continue

        # 从这里开始算「处理中」，重推的请求会被上面判成 pending
        mark_pending(msg_id)
        try:
            mtype = str(m.get("msg_type") or "")
            if mtype in SKIP_TYPES:
                skipped += 1
                msg_rows.append(_row(m, "跳过", "不记账的会话"))
                continue
            if mtype and mtype not in TEXT_TYPES:
                skipped += 1                      # 图片/文件等暂不记账
                msg_rows.append(_row(m, "跳过", "不是文字（%s）" % mtype))
                continue

            content = (m.get("content") or "").strip()
            # 采集端空内容会补成 "[图片]" 这类占位符，直接跳过，别浪费 AI 额度
            if not content or (len(content) <= 12 and content.startswith("[")
                               and content.endswith("]")):
                skipped += 1
                msg_rows.append(_row(m, "跳过", "没内容"))
                continue

            acc = resolve_account(m.get("account") or default_acc)
            # 同一群被两个微信号同时监听时：msg_id 各自独立，靠内容指纹挡第二份
            fp = msg_fingerprint(str(m.get("target_id") or m.get("chat_id") or ""),
                                 m.get("create_time"), str(m.get("sender") or ""), content)
            if not claim_fingerprint(fp):
                logger.info(f"[跨账号重复] {acc} {content[:40]}")
                skipped += 1
                msg_rows.append(_row(m, "跳过", "另一个号已经收过", acc=acc))
                continue

            r = record_bet(content, msg_id, m.get("chat_name") or "",
                           str(m.get("sender") or ""), str(m.get("sender_name") or ""),
                           account=acc, fp=fp,
                           chat_id=str(m.get("target_id") or m.get("chat_id") or ""),
                           create_time=m.get("create_time"))
            # 本期已开奖：不记账，但消息要留痕（老板要对账），还要回一句说清楚，
            # 不然客户以为记上了，开奖后对不上账。
            if r.get("closed"):
                skipped += 1
                msg_rows.append(_row(m, "跳过", r.get("reason") or "已开奖", acc=acc))
                # 指纹放回去：这条没记上账，客户在群里重发一遍应当还能被认
                unmark_fingerprint(fp)
                chat = str(m.get("chat_name") or "")
                chat_id = str(m.get("target_id") or m.get("chat_id") or "")
                if chat or chat_id:
                    replies.append({
                        "msg_id": msg_id,
                        "chat": chat or chat_id,
                        "chat_id": chat_id,
                        "text": "本期已开奖（%s），这单没记上，下期再下"
                                % DRAW_TIME,
                    })
                continue

            accepted += 1
            # 指纹在解析前就占住了（不占的话同一条内容可能被两个号各记一笔）；
            # 解析失败要放回去，让另一个号 / 重推还能重新走一遍。
            if r.get("ok"):
                recorded += 1
                msg_rows.append(_row(m, "记账", "¥%s" % r.get("total"), acc=acc,
                                     amount=r.get("total")))
                # 回复文案在这里定，采集端只负责发、不拼文案：
                # 金额和号码数都是服务端算出来的，采集端拿不到这个上下文。
                chat = str(m.get("chat_name") or "")
                chat_id = str(m.get("target_id") or m.get("chat_id") or "")
                # 以前这里判的是 `if chat:` —— 群名取不到（客户刚拉群、名字还没同步）
                # 就静默不回，账记上了但客户没收到回执。只要有一个标识就回。
                if chat or chat_id:
                    reply = {
                        "msg_id": msg_id,
                        "chat": chat or chat_id,
                        "chat_id": chat_id,
                        # 客户要的是"收到了、怎么算的"一句话：鼠 各50共计200。
                        # 规则算出来的 detail 就是这个形态，模型兜底的用简单版。
                        "text": r.get("detail") or ("已记账 ¥%s"
                                                    % _fmt_money(r.get("total"))),
                    }
                    replies.append(reply)
                    # 万一这次响应因为超时送不到采集端，重推时还能原样再给一次
                    cache_reply(msg_id, reply)
            else:
                unparsed += 1
                # 解析失败不能把这单锁死：客户在群里重发一遍是很自然的补救，
                # 锁住消息号就等于永久放弃这一单。消息号和指纹都放回去，
                # 让重推（甚至另一个号重推）能重新走一遍解析。
                unmark_message_processed(msg_id)
                unmark_fingerprint(fp)
                msg_rows.append(_row(m, "未解析", r.get("reason") or "解析失败", acc=acc))
        finally:
            # 成功、跳过、失败都要退出「处理中」：否则采集端重推永远等不到结果
            clear_pending(msg_id)

    append_messages(msg_rows)

    # 补发的回执排在本批新回执后面：采集端按 msg_id 各自发各自的群，顺序无所谓
    replies.extend(replay_replies)
    if pending_ids:
        logger.info(f"重推时仍在处理，让采集端稍后再取：{len(pending_ids)} 条")
    if replay_replies:
        logger.info(f"重推补发回执：{len(replay_replies)} 条")

    logger.info(f"收到推送 batch={payload.get('batch_id')} 共{len(messages)}条 "
                f"记账{recorded} 未解析{unparsed} 跳过{skipped} 微信号={wechat} acc={default_acc}")
    return jsonify({"ok": True, "count": len(messages), "recorded": recorded,
                    "unparsed": unparsed, "skipped": skipped,
                    "account": resolve_account(default_acc),
                    "replies": replies,
                    # 这些消息号服务端还在算（多半在等模型），别当成功：
                    # 过一会儿带同样的 msg_id 再来一次，就能把回执拿走。
                    "pending": pending_ids}), 200


# ======================== 监听对象（可选，通用能力） ========================

def load_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: Path, data):
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _to_epoch(v) -> int:
    """把时间字段容忍地转成秒级时间戳。

    采集端发的是 int 时间戳，但别人对接时可能发 "2026-11-12 16:18:04" 这类字符串，
    不能因为格式不一致就 500。
    """
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return 0


def _clean_contact(it: dict) -> dict:
    def s(k, n=64):
        return str(it.get(k) or "")[:n]
    return {
        "chat_id": s("chat_id"),
        "name": s("name"),
        "kind": s("kind", 8),
        "alias": s("alias"),
        "last_msg_time": _to_epoch(it.get("last_msg_time")),
    }


def load_targets_doc(account=None) -> dict:
    p = account_files(account)["targets"]
    d = load_json_file(p, {"version": 0, "updated_at": "", "targets": []})
    if not isinstance(d, dict):
        d = {"version": 0, "updated_at": "", "targets": []}
    d.setdefault("version", 0)
    d.setdefault("updated_at", "")
    if not isinstance(d.get("targets"), list):
        d["targets"] = []
    return d


def pick_account(explicit: str = "") -> str:
    """确定"这次操作针对哪个账号"。

    显式给了就用；没给且只有一个账号就给它；否则落 DEFAULT_ACCOUNT。
    这样单账号部署下前端不用传任何参数，多账号时前端必须传。
    """
    if explicit:
        return resolve_account(explicit)
    accs = all_accounts()
    if len(accs) == 1:
        return accs[0]
    return DEFAULT_ACCOUNT


def _local_only() -> bool:
    """配置类接口默认只允许本机访问；WX_CONFIG_LOCAL_ONLY=0 可放开给局域网。"""
    if (os.getenv("WX_CONFIG_LOCAL_ONLY") or "1").strip() != "1":
        return True
    return (request.remote_addr or "") in ("127.0.0.1", "::1", "localhost")


def _local_strict() -> bool:
    """改账的接口只能本机，不受 WX_CONFIG_LOCAL_ONLY 影响。

    那个开关本意是让看板在局域网里只读看数；一旦它把撤销也带开放，
    同一个局域网里谁都能把别人的账撤掉 —— 这个口子不能留。
    """
    return (request.remote_addr or "") in ("127.0.0.1", "::1", "localhost")


# ---------------- 控制台令牌 ----------------
#
# 控制台（server_gui）和 Flask 是两个进程，只能靠 HTTP 说话。光看"来源是本机"
# 不够：端口转发或反向代理都会把来源变成 127.0.0.1，那时谁都能调。
#
# 但令牌不能发给网页看板 —— 看板是本机浏览器里的 JS，它要是能拿到令牌，
# 那令牌等于写在页面上，代理场景照样泄露。所以这里分两层：
#   · 看板要用的接口（dashboard / contacts / targets / accounts）只认本机来源
#   · 控制台专用接口（读聊天原文、读密钥、撤销、清空、备份、日志）再加令牌
#
API_TOKEN_PATH = DATA_DIR / ".api_token"
_API_TOKEN = ""

# 这些只有桌面控制台会调，网页看板用不到，所以额外要令牌
_TOKEN_REQUIRED = {
    "/api/messages", "/api/record", "/api/reverse", "/api/clients",
    "/api/recalc", "/api/issues", "/api/parse", "/api/quick_bet",
    "/reset", "/backup", "/logs",
}
_TOKEN_REQUIRED_PREFIX = ("/restore/",)

# 这些必须本机来源。默认值的开关仍然管不着它们 —— 改账的口子不能放开。
_LOCAL_REQUIRED_PREFIX = ("/api/",)
_LOCAL_REQUIRED = {"/", "/manual", "/reset", "/backup", "/logs", "/top", "/status", "/recent"}


def _load_or_create_api_token():
    """令牌只生成一次，之后一直复用：GUI 重启不用换，令牌文件换了才换。"""
    global _API_TOKEN
    try:
        if API_TOKEN_PATH.exists():
            _API_TOKEN = (API_TOKEN_PATH.read_text(encoding="utf-8") or "").strip()
    except Exception as e:
        logger.warning(f"读控制台令牌失败：{e}")
    if _API_TOKEN:
        return
    _API_TOKEN = secrets.token_hex(32)
    try:
        API_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = API_TOKEN_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(_API_TOKEN)
        os.replace(tmp, API_TOKEN_PATH)
        try:
            os.chmod(API_TOKEN_PATH, 0o600)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"写控制台令牌失败：{e}")
    logger.info("已生成控制台令牌")


def _need_token(path: str) -> bool:
    if path in _TOKEN_REQUIRED:
        return True
    return any(path.startswith(p) for p in _TOKEN_REQUIRED_PREFIX)


def _need_local(path: str) -> bool:
    if path in _LOCAL_REQUIRED:
        return True
    return any(path.startswith(p) for p in _LOCAL_REQUIRED_PREFIX)


@app.before_request
def _guard_requests():
    """统一拦截，不逐个接口改。

    /wx/* 走 HMAC 验签（采集端用），这里不碰；其余按上两张表判断。
    """
    p = request.path or ""
    if p.startswith("/wx/") or p == "/health":
        return None
    if not _need_local(p):
        return None
    if not _local_strict():
        return jsonify({"ok": False, "error": "only local"}), 403
    if not _need_token(p):
        return None
    if not _API_TOKEN:
        return None                     # 令牌没生成出来，退回只认本机
    tok = (request.headers.get("X-Msb-Token") or "").strip()
    if not tok or not hmac.compare_digest(tok, _API_TOKEN):
        return jsonify({"ok": False, "error": "bad token"}), 401
    return None


@app.route("/wx/contacts", methods=["POST"])
def wx_contacts():
    """采集端上报会话清单。签名规则与 /wx/hook 完全一致；整份覆盖，天然幂等。

    只包含「有过会话」的对象——没有会话的对象消息不会落库，监听了也收不到。
    """
    raw = request.get_data()
    ts = request.headers.get("X-Wx-Timestamp", "")
    sig = request.headers.get("X-Wx-Signature", "")
    wechat = request.headers.get("X-Wx-Cust", "")

    if not verify_signature(raw, ts, sig, wechat):
        _log_reject("会话清单", wechat, ts)
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return jsonify({"ok": False, "error": "bad json"}), 400

    items = payload.get("items")
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "items 必须是数组"}), 400

    acc = resolve_account(payload.get("account"))
    cleaned = [_clean_contact(x) for x in items
               if isinstance(x, dict) and (x.get("chat_id") or x.get("name"))]
    data = {
        "account": acc,
        "cust": wechat or str(payload.get("cust") or "")[:32],
        "hash": str(payload.get("hash") or "")[:32],
        "generated_at": _to_epoch(payload.get("generated_at")),
        "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(cleaned),
        "items": cleaned,
    }
    p = account_files(acc)["contacts"]
    p.parent.mkdir(parents=True, exist_ok=True)
    save_json_file(p, data)
    logger.info(f"收到会话清单 [{acc}] {len(cleaned)} 项 hash={data['hash']} 微信号={wechat}")
    return jsonify({"ok": True, "total": len(cleaned), "account": acc}), 200


@app.route("/wx/targets", methods=["GET"])
def wx_targets():
    """供采集端拉取监听对象。version 递增采集端才覆盖本地列表。

    多采集端时按 account 区分：采集端 GET 带 ?account=<微信号>。
    """
    ts = request.headers.get("X-Wx-Timestamp", "")
    sig = request.headers.get("X-Wx-Signature", "")
    wechat = request.headers.get("X-Wx-Cust", "")
    if not verify_get_signature(ts, sig, request.path, "GET", wechat):
        _log_reject("targets 拉取", wechat, ts)
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    acc = pick_account(request.args.get("account") or "")
    d = load_targets_doc(acc)
    return jsonify({
        "version": int(d.get("version") or 0),
        "updated_at": d.get("updated_at") or "",
        "targets": d.get("targets") or [],
        "account": acc,
    })


@app.route("/api/contacts", methods=["GET"])
def api_contacts():
    """界面用：会话清单 + 当前已勾选状态。多账号时用 ?account= 指定。"""
    if not _local_only():
        return jsonify({"ok": False, "error": "仅允许本机查看会话清单"}), 403
    acc = pick_account(request.args.get("account") or "")
    d = load_json_file(account_files(acc)["contacts"], {})
    td = load_targets_doc(acc)
    selected = {str(t.get("id") or "") for t in td.get("targets") or []}
    all_mode = "*" in selected
    items = []
    for x in (d.get("items") or []):
        if not isinstance(x, dict):
            continue
        row = dict(x)
        # 全部会话模式下，界面按 * 统一显示，不必逐条标 selected
        row["selected"] = (not all_mode) and (row.get("chat_id") in selected)
        items.append(row)
    return jsonify({
        "ok": True,
        "account": acc,
        "accounts": all_accounts(),
        "received_at": d.get("received_at") or "",
        "total": d.get("total") or 0,
        "items": items,
        "targets_version": int(td.get("version") or 0),
        "targets_updated_at": td.get("updated_at") or "",
        "targets_count": (0 if all_mode else len(selected)),
        "targets_ids": sorted(selected),
        "targets_mode": "all" if all_mode else "selected",
    })


@app.route("/api/targets", methods=["POST"])
def api_targets():
    """保存勾选结果并 version+1。**只写 id**：名字会改、会重名，写名字下发必踩坑。"""
    if not _local_only():
        return jsonify({"ok": False, "error": "仅允许本机修改监听配置"}), 403

    payload = request.get_json(silent=True) or {}
    acc = pick_account(payload.get("account") or request.args.get("account") or "")
    raw = payload.get("targets")
    if raw is None:
        raw = payload.get("ids") or []
    if not isinstance(raw, list):
        return jsonify({"ok": False, "error": "targets 必须是数组"}), 400

    contacts = load_json_file(account_files(acc)["contacts"], {})
    by_id = {x.get("chat_id"): x for x in (contacts.get("items") or [])
             if isinstance(x, dict) and x.get("chat_id")}

    out = []
    for it in raw:
        if isinstance(it, str):
            cid, name = it.strip(), ""
        elif isinstance(it, dict):
            cid = str(it.get("id") or "").strip()
            name = str(it.get("name") or "").strip()
        else:
            continue
        if not cid:
            continue
        if not name:
            name = str((by_id.get(cid) or {}).get("name") or "")
        out.append({"id": cid, "name": (name[:64] or cid)})

    old = load_targets_doc(acc)
    doc = {
        "version": int(old.get("version") or 0) + 1,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "targets": out,
    }
    p = account_files(acc)["targets"]
    p.parent.mkdir(parents=True, exist_ok=True)
    save_json_file(p, doc)
    logger.info(f"监听配置已更新：[{acc}] {len(out)} 项，version={doc['version']}")
    return jsonify({"ok": True, "version": doc["version"], "count": len(out),
                    "account": acc}), 200


def _tail_jsonl(path: Path, n: int) -> List[dict]:
    """读 jsonl 文件最后 n 条。"""
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        out = []
        for ln in lines:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    pass
        return out
    except Exception:
        return []


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


def build_grid(data: Dict[str, float], zodiac_mapping: Dict[str, List[str]]) -> List[dict]:
    """按参照图版式：行 = 生肖（顺序取号码 01~12 各自所属生肖），列 = 该生肖名下的号码。"""
    num_to_zodiac = {}
    for z, nums in zodiac_mapping.items():
        for n in nums:
            num_to_zodiac[n] = z

    color_of = {}
    for cname, nums in COLOR_MAP.items():
        for n in nums:
            color_of[n] = cname

    rows = []
    seen = set()
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
                "color": color_of.get(n, ""),
            } for n in sorted(zodiac_mapping.get(z, []))],
        })
    return rows


def _count_today_multi(accs: List[str], key: str) -> int:
    return sum(_count_today(account_files(a)[key]) for a in accs)


def zodiac_board(account=None, market: str = "MO") -> dict:
    """生肖金额表（平特 + 连肖合并）+ 组合注（二中二/三中三）的展示归集。

    口径只有一份：msb_view.zodiac_board —— 桌面控制台的总览和网页看板都调它，
    免得两边算法各走各的、数字对不上。
    """
    return msb_view.zodiac_board(account or "", market)


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """account 为空 → 所有账号合计的总览；给了 → 只看该账号。

    ?market=MO|HK|ALL 决定号码盘/风险榜看哪一本，默认澳门；
    两边的金额汇总（markets）每次都带上，不用来回切。
    """
    req_acc = (request.args.get("account") or "").strip()
    mk = (request.args.get("market") or "MO").upper()
    if mk not in ("MO", "HK", "ALL"):
        mk = "MO"
    accs = all_accounts()
    if req_acc:
        acc = resolve_account(req_acc)
        if mk == "ALL":
            dmo, dhk = load_data(acc, "MO"), load_data(acc, "HK")
            data = {k: round(float(dmo.get(k, 0) or 0) + float(dhk.get(k, 0) or 0), 2)
                    for k in dmo}
        else:
            data = load_data(acc, mk)
        records_today = _count_today(account_files(acc)["records"])
        unparsed_today = _count_today(account_files(acc)["unparsed"])
        recent = _tail_jsonl(account_files(acc)["records"], 20)[::-1]
        unparsed_recent = _tail_jsonl(account_files(acc)["unparsed"], 10)[::-1]
    else:
        acc = ""
        data = load_data_merged("" if mk == "ALL" else mk)
        records_today = _count_today_multi(accs, "records")
        unparsed_today = _count_today_multi(accs, "unparsed")
        recent, unparsed_recent = [], []
        for a in accs:
            recent += _tail_jsonl(account_files(a)["records"], 20)
            unparsed_recent += _tail_jsonl(account_files(a)["unparsed"], 10)
        recent = sorted(recent, key=lambda x: str(x.get("ts") or ""))[-20:][::-1]
        unparsed_recent = sorted(unparsed_recent,
                                 key=lambda x: str(x.get("ts") or ""))[-10:][::-1]

    zodiac_mapping = current_zodiac_mapping()

    num_to_zodiac = {}
    for z, nums in zodiac_mapping.items():
        for n in nums:
            num_to_zodiac[n] = z

    total = sum(data.values()) or 1.0
    top = top_numbers(data, 10)
    flat_total = others_total(acc, mk) if (acc and mk != "ALL") else \
        (others_total_merged() if not acc and mk == "ALL" else
         others_total_merged(mk))
    flat_items = (load_others(acc, mk).get("items") or [])[-8:] if (acc and mk != "ALL") else []
    banks = market_totals(acc or None)

    # 这一期还收不收注。按服务器当前时间算，前端拿它显示「还剩多久 / 已封盘」。
    cut = draw_cutoff_state()

    return jsonify({
        "grand_total": banks[mk]["total"],
        "flat_total": round(flat_total, 2),
        "flat_items": flat_items,
        "market": mk,
        "markets": banks,
        "numbers_with_bets": sum(1 for v in data.values() if v > 0),
        "records_today": records_today,
        "records_current": msb_view.current_bet_count(acc or ""),
        "unparsed_today": unparsed_today,
        "zodiac_year": _zodiac_cache["value"][1] if _zodiac_cache["value"] else "",
        "grid": build_grid(data, zodiac_mapping),
        "risk": [{
            "rank": i + 1,
            "num": num,
            "zodiac": num_to_zodiac.get(num, ""),
            "amount": round(amt, 2),
            "share": round(amt / total * 100, 1),
        } for i, (num, amt) in enumerate(top)],
        "recent": recent,
        "unparsed_recent": unparsed_recent,
        # 总览用：生肖金额表（平特 + 连肖）+ 组合注，港澳各一份
        "zodiac_board": {
            "MO": zodiac_board(acc, "MO"),
            "HK": zodiac_board(acc, "HK"),
        },
        "account": acc,
        "accounts": accs,
        # 这一屏的数字属于哪一期。账本只装本期，换期时自动归档并清零。
        "issue": current_issue(acc) if acc else issue_of(),
        # 开奖时间封盘：过了点不再收注（微信推送会被拒），次日 0 点自动进下一期。
        # seconds_left 让前端不必信任自己电脑的时钟。
        "draw_time": cut["draw_time"],
        "closed": cut["closed"],
        "close_at": cut["close_at"].strftime("%m-%d %H:%M"),
        "seconds_left": max(0, int((cut["close_at"] - cut["now"]).total_seconds())),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": LLM_MODEL,
        "verify_enabled": not WX_SKIP_VERIFY,
        "verify_keys": (len(ACCOUNT_SECRETS) + (1 if WX_PUSH_SECRET else 0)),
    })


def _board_issue_options(acc: str = "") -> list:
    """可选账期：本期 + 已归档的前两期。与桌面控制台的 `_issue_choices` 同一口径。"""
    cur = current_issue(acc) if acc else issue_of()
    opts = [{"label": "本期 %s" % cur, "issue": cur}] if cur else []
    arch = []
    for a in ([acc] if acc else all_accounts()):
        try:
            arch += [p.stem for p in msb_view.issue_files(a)]
        except Exception:
            pass
    arch = sorted(set(arch), reverse=True)
    for i, iss in enumerate([x for x in arch if x and x != cur][:2]):
        opts.append({"label": ("前一期 %s" if i == 0 else "前二期 %s") % iss,
                     "issue": iss})
    return opts


@app.route("/api/board", methods=["GET"])
def api_board():
    """网页看板一屏要的东西，一次全给。

    口径全部走 `msb_view.summary` —— 桌面控制台调的就是同一个函数，所以网页和
    GUI 的数字永远一致，不会各算各的。澳门、香港两本盘面 + 两本风险榜一起给
    （GUI 也是同时显示的）；另外带上消息流水，网页要靠它数「本期未解析」、
    列未解析明细、以及点撤销。
    """
    req_acc = (request.args.get("account") or "").strip()
    acc = resolve_account(req_acc) if req_acc else ""
    try:
        s = msb_view.summary(acc)
    except Exception as e:
        return jsonify({"ok": False, "error": "读账本失败：%s" % e}), 500

    cut = draw_cutoff_state()
    rows = tail_messages(500)
    try:
        revd = reversed_msg_ids()
    except Exception:
        revd = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("msg_id") or "")
        if str(row.get("result") or "") == "记账":
            row["reversed"] = bool(mid) and mid in revd
        if not row.get("issue"):
            # 老流水没存期号，按时间反推 —— 网页按期过滤就靠它
            row["issue"] = msb_view.stage_of_ts(row.get("ts"))

    return jsonify({
        "ok": True,
        "account": acc,
        "accounts": s.get("accounts") or [],
        "issue": s.get("issue") or "",
        "zodiac": s.get("zodiac") or "",
        "zodiac_year": s.get("zodiac_year") or "",
        "grand_total": s.get("grand_total") or 0,
        "markets": s.get("markets") or {},
        "flat_total": s.get("flat_total") or 0,
        "numbers_with_bets_all": s.get("numbers_with_bets_all") or 0,
        "records_current": s.get("records_current") or 0,
        "unparsed_current": s.get("unparsed_current") or 0,
        "grids": s.get("grids") or {"MO": [], "HK": []},
        "risks": s.get("risks") or {"MO": [], "HK": []},
        # 平特 / 连肖 / 组合注不落 49 号盘，单列一块（和控制台同一函数算）
        "zodiac_board": {"MO": zodiac_board(acc, "MO"), "HK": zodiac_board(acc, "HK")},
        "recent": s.get("recent") or [],
        "unparsed": s.get("unparsed") or [],
        "messages": rows,
        "issue_options": _board_issue_options(acc),
        # 开奖时间封盘：过了点不再收注，次日 0 点自动进下一期
        "draw_time": cut["draw_time"],
        "closed": cut["closed"],
        "close_at": cut["close_at"].strftime("%m-%d %H:%M"),
        "seconds_left": max(0, int((cut["close_at"] - cut["now"]).total_seconds())),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/board/reverse", methods=["POST"])
def api_board_reverse():
    """网页看板的「撤销」：账务逻辑与 /api/reverse 完全同一套。

    控制台那个撤销接口要令牌（令牌只在桌面进程手里，网页拿不到），看板是在本机
    浏览器里直接点，所以单开一个口子；仍然只认本机来源 —— /api/ 前缀在
    `_guard_requests` 里已经把非本机请求挡掉了。
    """
    j = request.get_json(silent=True) or {}
    msg_id = str(j.get("msg_id") or "").strip()
    if not msg_id:
        return jsonify({"ok": False, "error": "缺 msg_id"}), 400
    r = reverse_record(msg_id, note=str(j.get("note") or "网页看板撤销"))
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/", methods=["GET"])
def route_index():
    return render_template("dashboard.html")


@app.route("/top", methods=["GET"])
def route_top():
    n = min(49, max(1, int(request.args.get("n", 10))))
    req_acc = (request.args.get("account") or "").strip()
    mk = (request.args.get("market") or "ALL").upper()
    if mk not in ("MO", "HK", "ALL"):
        mk = "ALL"
    data = (load_data(resolve_account(req_acc), "MO" if mk == "ALL" else mk)
            if req_acc else load_data_merged("" if mk == "ALL" else mk))
    if mk == "ALL" and req_acc:
        dmo, dhk = load_data(resolve_account(req_acc), "MO"), load_data(resolve_account(req_acc), "HK")
        data = {k: round(float(dmo.get(k, 0) or 0) + float(dhk.get(k, 0) or 0), 2) for k in dmo}
    top = top_numbers(data, n)
    fmt = request.args.get("format", "json")
    if fmt == "text":
        if not top:
            return Response("当前暂无数据\n", mimetype="text/plain; charset=utf-8")
        body = "🔥 当前风险 Top %d\n\n" % len(top)
        body += "\n".join(f"{i+1}. {num}号 - ${amt:,.2f}" for i, (num, amt) in enumerate(top))
        return Response(body + "\n", mimetype="text/plain; charset=utf-8")
    return jsonify({
        "top": [{"num": num, "amount": round(amt, 2)} for num, amt in top],
        "market": mk,
        "grand_total": market_totals(resolve_account(req_acc) if req_acc else None)[mk]["total"],
    })


@app.route("/status", methods=["GET"])
def route_status():
    req_acc = (request.args.get("account") or "").strip()
    mk = (request.args.get("market") or "ALL").upper()
    if mk not in ("MO", "HK", "ALL"):
        mk = "ALL"
    accs = all_accounts()
    if req_acc:
        acc = resolve_account(req_acc)
        if mk == "ALL":
            dmo, dhk = load_data(acc, "MO"), load_data(acc, "HK")
            data = {k: round(float(dmo.get(k, 0) or 0) + float(dhk.get(k, 0) or 0), 2)
                    for k in dmo}
        else:
            data = load_data(acc, mk)
        rt = _count_today(account_files(acc)["records"])
        ut = _count_today(account_files(acc)["unparsed"])
    else:
        acc = ""
        data = load_data_merged("" if mk == "ALL" else mk)
        rt = _count_today_multi(accs, "records")
        ut = _count_today_multi(accs, "unparsed")
    banks = market_totals(acc or None)
    return jsonify({
        "grand_total": banks[mk]["total"],
        "flat_total": round(banks[mk]["flat"], 2),
        "market": mk,
        "markets": banks,
        "numbers_with_bets": sum(1 for v in data.values() if v > 0),
        "records_today": rt,
        "unparsed_today": ut,
        "account": acc,
        "accounts": accs,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verify_enabled": not WX_SKIP_VERIFY,
    })


@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    """账号列表 + 每个账号的累计、今日单数、最近上报时间（界面下拉用）。"""
    out = []
    for a in all_accounts():
        f = account_files(a)
        d = load_json_file(f["contacts"], {})
        data = load_data(a)
        td = load_targets_doc(a)
        out.append({
            "account": a,
            "grand_total": market_totals(a)["ALL"]["total"],
            "flat_total": round(market_totals(a)["ALL"]["flat"], 2),
            "mo_total": market_totals(a)["MO"]["total"],
            "hk_total": market_totals(a)["HK"]["total"],
            "records_today": _count_today(f["records"]),
            "contacts_total": d.get("total") or 0,
            "contacts_received_at": d.get("received_at") or "",
            "targets_count": len(td.get("targets") or []),
            "targets_version": int(td.get("version") or 0),
        })
    return jsonify({"ok": True, "accounts": out,
                    "verify_keys": len(ACCOUNT_SECRETS) + (1 if WX_PUSH_SECRET else 0)})


@app.route("/api/clients", methods=["GET"])
def api_clients():
    """白名单给控制台用。只本机可读，密钥打码后才出去。

    采集端要拿密钥是自己算的（license 派生），服务端不需要把密钥发出去。
    """
    if not _local_only():
        return jsonify({"ok": False, "error": "only local"}), 403
    wl = load_clients(force=True)
    mp = wl["map"] or {}
    out = []
    for wid, it in mp.items():
        sec = str(it.get("secret") or "")
        out.append({
            "wechat_id": wid,
            "secret_masked": (sec[:6] + "…" + sec[-5:]) if len(sec) > 12 else "…",
            "customer_id": str(it.get("customer_id") or ""),
            "customer": str(it.get("customer") or ""),
            "expire_at": str(it.get("expire_at") or ""),
            "expired": client_expired(it),
            "ip_allow": str(it.get("ip_allow") or ""),
            "enabled": bool(it.get("enabled", True)),
            "added_at": str(it.get("added_at") or ""),
            "note": str(it.get("note") or ""),
            "last_push": (datetime.fromtimestamp(_LAST_PUSH[wid]).strftime("%Y-%m-%d %H:%M:%S")
                          if wid in _LAST_PUSH else ""),
        })
    out.sort(key=lambda x: (not x["enabled"], x["wechat_id"]))
    return jsonify({
        "ok": True,
        "mode": whitelist_mode(),
        "path": str(CLIENTS_PATH),
        "error": wl["error"],
        "updated_at": wl["updated_at"],
        "enabled": int(wl["enabled"]),
        "total": len(out),
        "clients": out,
    })


@app.route("/api/messages", methods=["GET"])
def api_messages():
    """最新收到的消息流水，控制台的「消息」页用。

    只本机可读：这里面是客户聊天原文，比账本还敏感。
    limit 默认 200，最多 1000 —— 一屏也就看几十条，再给多也没人翻。
    """
    if not _local_only():
        return jsonify({"ok": False, "error": "only local"}), 403
    try:
        limit = int(request.args.get("limit") or 200)
    except Exception:
        limit = 200
    limit = max(1, min(1000, limit))
    rows = tail_messages(limit)
    # 记账的那几行要标出「已撤销」，否则撤完在列表里看不出来
    try:
        revd = reversed_msg_ids()
    except Exception:
        revd = set()
    for row in rows:
        mid = str(row.get("msg_id") or "")
        if mid and str(row.get("result") or "") == "记账":
            row["reversed"] = mid in revd
    files_n = len(list(_msg_dir().glob("msg_*.jsonl")))
    return jsonify({
        "ok": True,
        "count": len(rows),
        "keep_days": MSG_KEEP_DAYS,
        "files": files_n,
        "dir": str(MSG_DIR),
        "messages": rows,
    })


@app.route("/api/parse", methods=["POST"])
def api_parse():
    """只解析、不入账 —— 给控制台「文本记账」弹窗看规则认成了什么。

    走的是和微信推送完全同一条规则链，所以这里显示的就是真实记账结果。
    模型兜底不在这里跑：那要几秒，而这一步只是为了核对写法。
    """
    j = request.get_json(silent=True) or {}
    text = str(j.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "空文本"})
    local = bet_rules.parse_local(text, current_zodiac_mapping())
    if local["ok"]:
        flat = round(float(local.get("flat_total") or 0), 2)
        return jsonify({
            "ok": True,
            "source": "规则",
            "detail": local["detail"],
            "items": local.get("items") or [],
            "numbers": len(local["bets"]),
            "total": round(sum(local["bets"].values()) + flat, 2),
            "flat_total": flat,
            "extras": local.get("extras") or [],
            "market": local.get("market") or "",
        })
    return jsonify({
        "ok": False,
        "source": "规则",
        "reason": local.get("reason") or "解析失败",
        # can_llm=False：连模型也不该试（中特/特肖 属真不支持、鼠5尾 歧义、
        # 平特写号码、组合注带「各」/肖数不符、纯寒暄）—— 这些客户已明确"不解析"。
        # 其余规则吃不下的 12/13/14 写法会正常走模型（prompt 里有整笔玩法口径）。
        "can_llm": not local.get("fatal"),
    })


@app.route("/manual", methods=["GET"])
def manual_page():
    """网页版手工记账：跟控制台窗口同一套玩法、同一套规则。"""
    return render_template("manual.html")


def _manual_order():
    """生肖的书写顺序（鼠牛虎兔…）—— 网页按钮排列和拼原文都按它。"""
    mapping = current_zodiac_mapping()
    return mapping, manual_plays.zodiac_order(mapping)


@app.route("/api/manual/meta", methods=["GET"])
def api_manual_meta():
    """网页手工记账的初始化数据。

    每个玩法对应哪几个号，全部由 bet_rules 现算 —— 网页只负责"选中了什么"，
    不自己算号，跟控制台窗口、跟微信推送永远同一套。
    """
    mapping, order = _manual_order()
    m = manual_plays.meta(mapping, order)
    color_of = {}
    for cname, ns in COLOR_MAP.items():
        for n in ns:
            color_of[n] = cname
    nums = [{"num": "%02d" % i, "color": color_of.get("%02d" % i, "")}
            for i in range(1, 50)]
    chats = []
    for r in list(_recent_records)[-80:][::-1]:
        c = str(r.get("chat") or "").strip()
        if c and c not in chats:
            chats.append(c)
    if not chats:
        for r in iter_records()[-60:][::-1]:
            c = str(r.get("chat") or "").strip()
            if c and c not in chats:
                chats.append(c)
    # 默认账号：钱最多的那个（真有账的号排前面），没金额就按名字
    accs = all_accounts()
    default_acc = ""
    try:
        best = -1.0
        for a in accs:
            t = float(market_totals(a)["ALL"]["total"] or 0)
            if t > best:
                best, default_acc = t, a
    except Exception:
        default_acc = accs[0] if accs else ""
    return jsonify({
        "ok": True,
        "groups": m["groups"],
        "zodiacs": m["zodiacs"],
        "numbers": nums,
        "accounts": accs,
        "default_account": default_acc,
        "chats": chats,
        "default_market": "MO",
        "limit_confirm": 1000.0,
        "zodiac_year": (_zodiac_cache["value"][1] if _zodiac_cache["value"] else ""),
        "play_count": sum(len(g["items"]) for g in m["groups"]),
    })


@app.route("/api/manual/build", methods=["POST"])
def api_manual_build():
    """网页端「加入清单」：把勾的条件算成号、拼成原文，并自检一遍。

    返回 text 就是最终要落账的原话（号码/生肖/组合名），网页拿它拼整句提交。
    """
    j = request.get_json(silent=True) or {}
    mapping, order = _manual_order()
    r = manual_plays.build(mapping,
                           nums=[str(x) for x in (j.get("nums") or [])],
                           zodiacs=[str(x) for x in (j.get("zodiacs") or [])],
                           plays=[str(x) for x in (j.get("plays") or [])],
                           amount=j.get("amount") or 0,
                           order=order)
    return jsonify(r)


@app.route("/api/manual/submit", methods=["POST"])
def api_manual_submit():
    """网页版「记账」：清单拼成一句原文落账，跟控制台窗口同一个接口、同一条链路。

    整句解析不通就逐注记 —— 宁可多几条流水，也不把账记错。
    期号归档、港澳门分盘、撤销、指纹去重都由 record_bet 自己处理。
    """
    j = request.get_json(silent=True) or {}
    notes = []
    for n in (j.get("notes") or []):
        t = str((n or {}).get("text") or "").strip()
        try:
            a = round(float((n or {}).get("each") or 0), 2)
        except Exception:
            a = 0.0
        if not t or a <= 0:
            continue
        notes.append({"text": t, "each": a,
                      # 生肖注（平特/连肖/中二）自带整句写法，不参与号码注的拼句
                      "flat": bool((n or {}).get("flat")),
                      "nums": [str(x) for x in ((n or {}).get("nums") or [])]})
    if not notes:
        return jsonify({"ok": False, "error": "清单是空的"})

    acc = resolve_account(j.get("account") or "")
    market = str(j.get("market") or "")
    chat = str(j.get("chat") or "手工记账")
    mapping, _order = _manual_order()

    base = "manual-%d" % int(time.time() * 1000)
    total = 0.0
    numbers = 0
    archived = ""
    detail = []

    def _one(text, mid):
        nonlocal total, numbers, archived
        r = record_bet(text, mid, chat_name=chat, sender="manual",
                       sender_name=str(j.get("sender") or "本人"),
                       account=acc, market=market, allow_after_close=True)
        if r.get("ok"):
            total += float(r.get("total") or 0)
            numbers += int(r.get("numbers") or 0)
            if r.get("archived_issue"):
                archived = str(r.get("archived_issue"))
            if r.get("detail"):
                detail.append(str(r.get("detail")))
        return r

    fails = []
    normal = [n for n in notes if not n["flat"]]
    # 生肖注不进号码盘，每注就是一句完整原文，单独落一笔
    for i, n in enumerate([x for x in notes if x["flat"]]):
        rr = _one(n["text"], "%s-f%d" % (base, i + 1))
        if not rr.get("ok"):
            fails.append("%s：%s" % (n["text"],
                                    rr.get("error") or rr.get("reason") or "失败"))

    if normal:
        joined = manual_plays.join_text(normal)
        if manual_plays.verify_join(mapping, normal, joined):
            r = _one(joined, base)
            if not r.get("ok"):
                fails.append(r.get("error") or r.get("reason") or "记账失败")
        else:
            for i, n in enumerate(normal):
                rr = _one("%s各%s" % (n["text"], manual_plays.amount_str(n["each"])),
                          "%s-%d" % (base, i + 1))
                if not rr.get("ok"):
                    fails.append("第%d注：%s"
                                 % (i + 1, rr.get("error") or rr.get("reason") or "失败"))
    else:
        joined = "；".join(n["text"] for n in notes)
    if fails:
        return jsonify({"ok": False, "error": "；".join(fails),
                        "partial": round(total, 2)})

    out = {"ok": True, "market": market,
           "market_name": "香港" if market == "HK" else "澳门",
           "account": acc, "numbers": numbers, "total": round(total, 2),
           "notes": len(notes), "text": joined}
    if archived:
        out["archived_issue"] = archived
    if detail:
        out["detail"] = "；".join(detail)
    return jsonify(out)


@app.route("/api/quick_bet", methods=["POST"])
def api_quick_bet():
    """控制台里手工贴文本记账，链路和微信推送完全一致：规则优先、模型兜底。"""
    j = request.get_json(silent=True) or {}
    text = str(j.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "空文本"})
    acc = resolve_account(j.get("account") or "")
    # msg_id 用毫秒，别用秒：撤销是"按消息号一次撤掉整条"，同一秒两条手工账
    # 会被当成同一条消息，撤销时一起被撤掉
    r = record_bet(text,
                   "manual-%d" % int(time.time() * 1000),
                   chat_name=str(j.get("chat") or "手工记账"),
                   sender="manual",
                   sender_name=str(j.get("sender") or "本人"),
                   account=acc,
                   market=str(j.get("market") or ""),
                   allow_after_close=True)
    r["account"] = acc
    return jsonify(r)


@app.route("/api/record", methods=["GET"])
def api_record():
    """查一笔账的原文和明细，撤销前先给人看清楚要减什么。"""
    if not _local_only():
        return jsonify({"ok": False, "error": "only local"}), 403
    msg_id = (request.args.get("msg_id") or "").strip()
    rec = latest_record(msg_id)
    if not rec:
        return jsonify({"ok": False, "error": "找不到这笔账"}), 404

    # 一条消息可能落多笔（「港 … 澳 …」按词分盘），撤销时是一起撤的，
    # 所以这里把还没撤的每一段都摆出来，界面才好说到底要减哪几个盘。
    revd = _reversed_ids()
    alive = [r for r in find_records_by_msg(msg_id) if _rec_id(r) not in revd]
    segs = [{"market": str(r.get("market") or "MO"),
             "numbers": r.get("numbers"),
             "total": r.get("total"),
             "applied": r.get("applied") or None,
             "extras": len(r.get("extras") or []),
             "rec_id": _rec_id(r)} for r in alive]
    seg_total = round(sum(float(s.get("total") or 0) for s in segs), 2) if segs else rec.get("total")
    has_detail = bool(segs) and all(
        (isinstance(s.get("applied"), dict) and bool(s.get("applied"))) or s.get("extras")
        for s in segs)

    return jsonify({
        "ok": True,
        "msg_id": msg_id,
        "ts": rec.get("ts") or "",
        "account": rec.get("account") or "",
        "chat": rec.get("chat") or "",
        "sender": rec.get("sender_name") or rec.get("sender") or "",
        "text": rec.get("text") or "",
        "total": seg_total,
        "applied": (segs[0]["applied"] if len(segs) == 1 else (rec.get("applied") or None)),
        "has_detail": has_detail,
        "segments": segs,
        "count": len(segs),
        "reversed": is_reversed(msg_id),
        "issue": rec.get("issue") or "",
    })


@app.route("/api/reverse", methods=["POST"])
def api_reverse():
    """撤销一笔账。只动这一笔的金额，不重算整个账本。"""
    if not _local_strict():
        return jsonify({"ok": False, "error": "only local"}), 403
    j = request.get_json(silent=True) or {}
    msg_id = str(j.get("msg_id") or request.form.get("msg_id") or "").strip()
    note = str(j.get("note") or request.form.get("note") or "界面撤销")
    if not msg_id:
        return jsonify({"ok": False, "error": "缺 msg_id"}), 400
    r = reverse_record(msg_id, note=note)
    if not r.get("ok"):
        return jsonify(r), 400
    return jsonify(r), 200


@app.route("/api/recalc", methods=["GET"])
def api_recalc():
    """从流水重算账本，和 state 快照比对。只读。

    平时不用；一旦怀疑账本被污染，这是唯一能分清"流水和快照谁对"的东西。
    """
    if not _local_only():
        return jsonify({"ok": False, "error": "only local"}), 403
    acc = (request.args.get("account") or "").strip()
    return jsonify({"ok": True, **recalc_account(acc or None)})


@app.route("/api/issues", methods=["GET"])
def api_issues():
    """期次一览：本期 + 已归档的历史期，新的在前。

    控制台「总览」页在标题后面显示本期期号；历史期列表留给以后翻账用。
    """
    if not _local_only():
        return jsonify({"ok": False, "error": "only local"}), 403
    acc = (request.args.get("account") or "").strip()
    try:
        limit = max(1, min(400, int(request.args.get("limit", 60))))
    except Exception:
        limit = 60
    try:
        return jsonify({"ok": True, **issue_summary(acc or None, limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"读期次失败：{e}"}), 500


def _count_today(path: Path) -> int:
    if not path.exists():
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if today in line[:20]:
                    n += 1
    except Exception:
        pass
    return n


@app.route("/recent", methods=["GET"])
def route_recent():
    n = min(MAX_RECORDS_IN_MEMORY, max(1, int(request.args.get("n", 10))))
    return jsonify({"records": _recent_records[-n:][::-1]})


def clear_account_dir(account: str) -> dict:
    """整份清掉一个账号的账：账本归零 + 流水 / 未解析 / 期号 / 历史期归档全删。

    只把 numbers_state.json 归零是不够的 —— 那样「最近记账」还列着旧账、
    撤销会把号码减成负数、recalc 又会照着流水把账全部加回来。

    清之前把 state/<账号> 整份拷到 backups/<账号>/_clear_<时间戳>/ 留底。
    contacts / targets 不动：清的是账，不是这个号的接入关系。
    """
    acc = resolve_account(account)
    f = account_files(acc)
    base = STATE_DIR / acc
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ""
    try:
        if base.exists() and any(base.iterdir()):
            f["backup"].mkdir(parents=True, exist_ok=True)
            dst = f["backup"] / ("_clear_" + stamp)
            shutil.copytree(base, dst)
            backup_dir = str(dst)
    except Exception as e:
        logger.warning(f"[{acc}] 清空前留底失败：{e}")

    removed = []
    for key, name in (("records", "records.jsonl"),
                      ("unparsed", "unparsed.jsonl"),
                      ("issue", "current_issue.txt")):
        p = f[key]
        try:
            if p.exists():
                p.unlink()
                removed.append(name)
        except Exception as e:
            logger.warning(f"[{acc}] 删 {name} 失败：{e}")
    try:
        if f["issues"].exists():
            shutil.rmtree(f["issues"])
            removed.append("issues/")
    except Exception as e:
        logger.warning(f"[{acc}] 删 issues/ 失败：{e}")

    # 两个盘、两类账都要归零：
    # numbers_state 是澳门号码盘，numbers_state_hk 是香港号码盘；
    # others_state(_hk) 是平特/连肖/组合注这类整笔注。少清哪个，看板就会
    # 在「已清空」之后仍显示旧的金额（实测清完还挂着 200）。
    save_data(init_data(), acc)
    save_data(init_data(), acc, "HK")
    save_others({"total": 0.0, "items": []}, acc)
    save_others({"total": 0.0, "items": []}, acc, "HK")
    logger.info(f"清空账本：[{acc}] 删了 {len(removed)} 项，留底 {backup_dir or '无'}")
    return {"account": acc, "backup": backup_dir, "removed": removed}


@app.route("/reset", methods=["POST"])
def route_reset():
    """清空账本：指定 account 清一个，不指定就清全部账号。

    清的是这个号的整本账（流水 / 未解析 / 期号 / 归档），不是只把数字抹成 0。
    清全部时连全局去重缓存一起清，否则老消息重推会被当成「已处理」直接跳过。
    """
    body = request.get_json(silent=True) or {}
    raw = str(request.args.get("account") or body.get("account") or "").strip()
    all_accs = all_accounts()
    targets = [resolve_account(raw)] if raw else list(all_accs)
    if not targets:
        return jsonify({"ok": False, "error": "还没有任何账号，没什么可清"}), 400

    with DATA_LOCK:
        results = [clear_account_dir(a) for a in targets]
        every = set(targets) >= set(all_accs)
        if every:
            _processed.clear()
            _processed_set.clear()
            _save_processed()
            with _FP_LOCK:
                _fp.clear()
                _fp_set.clear()
                _save_fingerprints()

    logger.warning(f"账本已被 /reset 清空：{'、'.join(targets)}（全清={every}）")
    return jsonify({
        "ok": True,
        "count": len(targets),
        "accounts": targets,
        "results": results,
        "caches_cleared": every,
        "message": "、".join(targets) + " 的账已清空",
    })


@app.route("/backup", methods=["POST"])
def route_backup():
    files = backup_all()
    if files:
        return jsonify({"ok": True, "count": len(files),
                        "files": [Path(f).name for f in files]}), 200
    return jsonify({"ok": False, "error": "备份失败（没有账本）"}), 500


@app.route("/restore/<date_str>", methods=["POST"])
def route_restore(date_str: str):
    body = request.get_json(silent=True) or {}
    acc = pick_account(request.args.get("account") or body.get("account") or "")
    if restore_data(date_str, acc):
        return jsonify({"ok": True, "message": f"{acc} 已恢复：{date_str}", "account": acc})
    return jsonify({"ok": False, "error": f"备份不存在：{acc} / {date_str}"}), 404


@app.route("/logs", methods=["GET"])
def route_logs():
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"bot_{today}.log"
    if not log_file.exists():
        return Response("今日暂无日志\n", mimetype="text/plain; charset=utf-8")
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-20:])
        return Response(tail, mimetype="text/plain; charset=utf-8")
    except Exception as e:
        return Response(f"读取日志失败：{e}\n", mimetype="text/plain; charset=utf-8")


# ======================== 定时任务 ========================

def daily_backup_worker():
    while True:
        now = datetime.now()
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        time.sleep(max(1, (nxt - now).total_seconds()))
        backup_data()
        logger.info("定时备份完成")
        try:
            n = prune_messages()
            if n:
                logger.info(f"已清理超过 {MSG_KEEP_DAYS} 天的消息流水 {n} 个")
        except Exception as e:
            logger.warning(f"清理消息流水失败：{e}")


# ======================== 主程序 ========================

def _box(text: str, title: str = "记账中台") -> None:
    """打包后没有控制台，出错只能用弹框告诉客户，否则就是静默闪退。"""
    if "--quiet" in sys.argv:
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(text), title, 0x10)
    except Exception:
        pass


def main():
    problems = config_problems()
    if problems:
        msg = "配置不完整，无法启动：\n\n  · " + "\n  · ".join(problems) + \
              f"\n\n请在控制台「配置」页填写，或直接编辑：\n{CONFIG_PATH}"
        print(msg)
        if msb_paths.is_compiled():
            _box(msg)
        return 2

    if not acquire_lock():
        msg = (f"已经有一个服务端在跑（锁文件：{LOCK_FILE}）\n\n"
               f"如果确认没有在跑，删掉这个文件再启动。")
        print(msg)
        if msb_paths.is_compiled():
            _box(msg)
        return 3

    try:
        logger.info("🚀 六合彩记账（微信推送版）启动中...")
        _, current_zodiac, zodiac_mapping = get_current_lunar_year()
        print(f"📅 当年生肖：{current_zodiac} ({', '.join(zodiac_mapping[current_zodiac])})")
        print(f"🔐 验签：{'跳过（调试）' if WX_SKIP_VERIFY else '开启'}"
              f"（密钥 {len(ACCOUNT_SECRETS) + (1 if WX_PUSH_SECRET else 0)} 把）")
        print(f"👤 当前账号：{CURRENT_ACCOUNT}（已接账号：{', '.join(all_accounts()) or '无'}）")
        print(f"📡 接收地址：http://{HOST}:{PORT}/wx/hook")
        print(f"🖥  网页看板：http://127.0.0.1:{PORT}/")
        print(f"🤖 模型：{LLM_MODEL}（思考模式 {'开启' if LLM_THINKING else '关闭'}）")
        print(f"💾 数据目录：{DATA_DIR}")
        # 控制台令牌：桌面控制台启动时读这个文件，接口靠它对暗号
        _load_or_create_api_token()
        print("✅ 已就绪，按 Ctrl+C 停止")

        # 启动时先清一次过期流水：机器可能关机好几天，只靠 0 点那一趟会积压
        try:
            n = prune_messages()
            if n:
                print(f"🧹 已清理超过 {MSG_KEEP_DAYS} 天的消息流水 {n} 个")
        except Exception:
            pass

        threading.Thread(target=daily_backup_worker, daemon=True).start()
        try:
            app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
        except OSError as e:
            logger.error(f"端口 {PORT} 启动失败：{e}")
            msg = (f"端口 {PORT} 起不来：{e}\n\n"
                   f"多半是已经有别的程序占了这个端口（比如另一个服务端实例）。\n"
                   f"在控制台「配置」页换一个端口再启动。")
            print("\n" + msg)
            if msb_paths.is_compiled():
                _box(msg)
            return 4
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    # 双击（无参数）或显式 --gui → 控制台窗口；其余参数（含 --headless）→ 直接起服务
    _argv = sys.argv[1:]
    if not _argv or "--gui" in _argv:
        try:
            from server_gui import run as _run_gui
            sys.exit(_run_gui())
        except Exception as _e:
            print(f"控制台启动失败：{_e}")
            print("可以直接用命令行方式启动：MarkSixBot.exe --headless")
            sys.exit(5)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n👋 已停止")
        release_lock()
    except Exception as e:
        logger.critical(f"致命错误：{e}", exc_info=True)
        print(f"致命错误：{e}")
        release_lock()
        sys.exit(1)
