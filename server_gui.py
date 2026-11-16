# -*- coding: utf-8 -*-
"""服务端控制台 —— 双击 MarkSixBot.exe（不带参数）就打开这个窗口。

单机版：原先网页看板能看的、能点的，这里都有一份，不用开浏览器。

三个页签：
  · 总览 —— 汇总数字 + 号码盘 + 风险排序 + 最近记账 + 未解析
  · 配置 —— 推送密钥 / 大模型 Key / 端口
  · 日志 —— 当天日志尾巴

监听对象已不再由服务端管：好友/群清单属于敏感数据，采集端要求不出本机，
所以那一页连同「下发 targets」整条链路都删掉了。

**故意不 import main**：main 一被导入就会立刻挂日志 FileHandler、
建 Flask app、抢 .bot.lock，GUI 进程会跟后台服务互相打架（日志文件被占用、
锁拿不到）。所以路径、配置、读数据都在 msb_paths / msb_view 里各实现一份。
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

import msb_view
import msb_paths as _paths
# 手工记账要借规则解析器算「某个玩法是哪几个号」，用它保证界面选的号
# 和文本规则算出来的号永远同一套（bet_rules 不碰 main，不会抢锁/建 app）
import bet_rules
# 玩法表 + 选号逻辑跟网页版共用同一份，两边不会算出两个结果
import manual_plays

# 版本号规则：主.次.修订。修 bug / 改文案 → 修订号；加功能 → 次版本；
# 换底层方案（换存储、换协议、旧数据不兼容）→ 主版本。
# 这里只写版本号本身，标题栏由 window_title() 拼上；出包日期由打包脚本
# 注入 MSB_VERSION / MSB_BUILD_DATE 环境变量后覆盖。
APP_VERSION = "1.0.0"
BUILD_DATE = ""


def window_title():
    """标题栏是界面里唯一显示版本号的地方，别在窗口内部重复显示。"""
    ver = (os.environ.get("MSB_VERSION") or "").strip() or APP_VERSION
    date = (os.environ.get("MSB_BUILD_DATE") or "").strip() or BUILD_DATE
    t = "记账中台 v%s" % ver
    if date:
        t += " · %s" % date
    return t


# 采集端白名单：一个微信号一把钥匙，由厂商签发工具写入，服务端只读。
# 路径与 main.CLIENTS_PATH 保持一致（config 目录下），这里不 import main，
# 所以自己按 CONFIG_PATH 推一遍。
WHITELIST_PATH = Path(_paths.CONFIG_PATH).parent / "wx_clients.json"


CLR_BG = "#f6f7f9"
CLR_CARD = "#ffffff"
CLR_LINE = "#dfe3e8"
CLR_OK = "#1a7f37"
CLR_BAD = "#c0392b"
CLR_MUTED = "#6b7280"
CLR_BLUE = "#1f6feb"
CLR_RED = "#c0392b"
CLR_BLUE2 = "#2b6cb0"
# 组合注按玩法上色，一眼分清是二中二还是六中六（首字兜底，认不出的回蓝色）
ZHONG_CLR = {"二中二": "#1f6feb", "三中二": "#0e7490", "三中三": "#7c3aed",
             "四中四": "#c2410c", "五中五": "#c0392b", "六中六": "#0f766e"}
ZHONG_CLR_HEAD = {"二": "#1f6feb", "三": "#7c3aed", "四": "#c2410c",
                  "五": "#c0392b", "六": "#0f766e"}


def zhong_color(label) -> str:
    lb = str(label or "")
    return ZHONG_CLR.get(lb) or ZHONG_CLR_HEAD.get(lb[:1]) or CLR_BLUE
# 中二要 2 个肖，中三 3 个……中六 6 个
ZH_NEED = {"zh2": 2, "zh3": 3, "zh4": 4, "zh5": 5, "zh6": 6}
ZH_NAME = {"zh2": "二中二", "zh3": "三中三", "zh4": "四中四",
           "zh5": "五中五", "zh6": "六中六"}
CLR_GREEN = "#2b8a5f"
CLR_GREY = "#aab2bd"

FONT = ("Microsoft YaHei UI", 11)
FONT_B = ("Microsoft YaHei UI", 11, "bold")
FONT_S = ("Microsoft YaHei UI", 10)
FONT_T = ("Microsoft YaHei UI", 15, "bold")

# 号码盘用等宽数字，看得清
FONT_NUM = ("Consolas", 15, "bold")
FONT_AMT = ("Consolas", 14)
FONT_CELL = ("Microsoft YaHei UI", 13, "bold")


class TabBar(tk.Frame):
    """自绘页签：每个页签固定宽高，选中的用深色底 + 白字。

    为什么不用 ttk.Notebook：ttk 的页签宽度由标题文字长度决定，而「选中」态
    在 vista 主题下会向内缩一圈、在 clam 主题下会向下多探 2 像素去压内容区的
    边框（实测选中 42px、未选中 40px）。几个页签因此总是大小不一，而且选中的
    那个还比未选中的浅。这里改成固定像素的方块，选不选中尺寸完全一样。

    对外接口按 ttk.Notebook 的用法写：add(frame, text=...) / select(frame) /
    current() / index(frame)，另外用 on_change() 代替 <<NotebookTabChanged>>。
    """

    BTN_W = 108          # 每个页签的宽度（像素），与标题长短无关
    BTN_H = 34           # 高度
    ON_BG, ON_FG = "#2b3444", "#ffffff"
    OFF_BG, OFF_FG = "#e7eaee", "#3b4453"

    def __init__(self, master, bg="#f6f7f9"):
        super().__init__(master, bg=bg)
        self._bar = tk.Frame(self, bg=bg)
        self._bar.pack(fill="x")
        tk.Frame(self, height=1, bg=CLR_LINE).pack(fill="x")
        self._body = tk.Frame(self, bg=bg)
        self._body.pack(fill="both", expand=True)
        self._items = []          # [(holder, label, frame)]
        self._cur = -1
        self._cb = None

    def add(self, frame, text="", **_ignored):
        idx = len(self._items)
        holder = tk.Frame(self._bar, width=self.BTN_W, height=self.BTN_H,
                          bg=self.OFF_BG, cursor="hand2")
        holder.pack(side="left", padx=(0, 2))
        holder.pack_propagate(False)
        lab = tk.Label(holder, text=text, font=FONT, bg=self.OFF_BG,
                       fg=self.OFF_FG, cursor="hand2")
        lab.place(relx=0.5, rely=0.5, anchor="center")
        for w in (holder, lab):
            w.bind("<Button-1>", lambda _e, i=idx: self.select(i))
        self._items.append((holder, lab, frame))
        if self._cur < 0:
            self._cur = idx
            self._apply()
        return frame

    def tabs(self):
        return tuple(f for _, _, f in self._items)

    def index(self, frame):
        for i, (_, _, f) in enumerate(self._items):
            if f is frame or str(f) == str(frame):
                return i
        return -1

    def current(self):
        return self._cur

    def select(self, target):
        idx = target if isinstance(target, int) else self.index(target)
        if not (0 <= idx < len(self._items)):
            return
        self._cur = idx
        self._apply()
        if self._cb:
            try:
                self._cb()
            except Exception:
                pass

    def _apply(self):
        for i, (holder, lab, frame) in enumerate(self._items):
            on = (i == self._cur)
            holder.configure(bg=self.ON_BG if on else self.OFF_BG)
            lab.configure(bg=self.ON_BG if on else self.OFF_BG,
                          fg=self.ON_FG if on else self.OFF_FG,
                          font=FONT_B if on else FONT)
            if on:
                frame.pack(in_=self._body, fill="both", expand=True)
            else:
                frame.pack_forget()

    def on_change(self, func):
        """页签切换回调（代替 ttk 的 <<NotebookTabChanged>>）。"""
        self._cb = func

DEFAULT_BASE = ("https://llm-o8ou6he0iyxcz0ns.cn-beijing.maas.aliyuncs.com"
                "/compatible-mode/v1")

DEFAULTS = {
    "port": 8899,
    "host": "0.0.0.0",
    "wx_push_secret": "",
    "wx_account_secrets": "",
    "wx_skew_sec": 300,
    "llm_api_key": "",
    "llm_base_url": DEFAULT_BASE,
    "llm_model": "qwen3.8-flash",
    "llm_thinking": 0,
    "llm_timeout": 60,
    "contacts_interval_sec": 21600,
}


# ---------------- 路径与配置 ----------------

def app_dir() -> Path:
    return _paths.APP_DIR


def data_dir() -> Path:
    return _paths.DATA_DIR


def config_path() -> Path:
    return _paths.CONFIG_PATH


# 打开面板也做一次迁移：否则客户第一眼看到「0 个账号 / 0 元」，会以为账丢了
_ADOPTED = _paths.adopt_old_layout()


def read_cfg() -> dict:
    d = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    d[k] = v
        except Exception:
            pass
    return d


def write_cfg(d: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = dict(read_cfg())
    doc.update(d)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ---------------- 运行状态 ----------------

def self_cmd() -> list:
    """起后台服务用的命令行。打包后就是自己的 EXE。"""
    exe = Path(sys.argv[0])
    try:
        if exe.suffix.lower() == ".exe" and exe.is_file():
            return [str(exe.resolve())]
    except Exception:
        pass
    return [sys.executable, str(Path(__file__).resolve().parent / "main.py")]


def pid_path() -> Path:
    return data_dir() / ".server_gui.pid"


def health(port: int, timeout: float = 2.0):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % int(port),
                                    timeout=timeout) as r:
            body = r.read(64).decode("utf-8", "ignore")
            return (int(getattr(r, "status", 200)) == 200 and body.strip().startswith("OK"),
                    "HTTP %s" % getattr(r, "status", "?"))
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, type(e).__name__


def _pid_alive(pid: int) -> bool:
    """直接问内核，不拉 tasklist（Windows 11 会把控制台程序转到 Windows Terminal，闪黑窗）。"""
    if not pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wintypes.DWORD(0)
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    except Exception:
        return False


def running_pid() -> int:
    try:
        return int(pid_path().read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0


def api_token() -> str:
    """服务端启动时生成的令牌，存在数据目录。控制台每个请求都带上它。

    光靠"来源是本机"不够：端口转发或反向代理会把来源变成 127.0.0.1，
    那时候控制台专用的接口（读聊天原文、撤销、清空）谁都能调。
    """
    try:
        p = data_dir() / ".api_token"
        if p.exists():
            return (p.read_text(encoding="utf-8") or "").strip()
    except Exception:
        pass
    return ""


def api_headers(extra: dict = None) -> dict:
    h = {}
    tok = api_token()
    if tok:
        h["X-Msb-Token"] = tok
    if extra:
        h.update(extra)
    return h


def api_post(path: str, payload=None, port: int = 8899) -> dict:
    """调本地接口。备份 / 清空 / 下发监听对象这些写操作走接口，
    不直接改文件 —— 服务端内存里有缓存，绕过去会打架。"""
    url = "http://127.0.0.1:%d%s" % (int(port), path)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers = api_headers(headers)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "ignore") or "{}")
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def api_get(path: str, port: int = 8899) -> dict:
    """只读接口。和 api_post 一样带令牌，失败也返回字典而不是抛异常。"""
    url = "http://127.0.0.1:%d%s" % (int(port), path)
    req = urllib.request.Request(url, headers=api_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "ignore") or "{}")
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def fmt_money(v) -> str:
    """865.0 → 865；865.5 → 865.5。金额太小的小数点不显示成 .00。"""
    try:
        n = float(v or 0)
    except Exception:
        return "0"
    if abs(n - round(n)) < 1e-9:
        return "%.0f" % n
    return ("%.2f" % n).rstrip("0").rstrip(".")


def zx_amt(v) -> str:
    """生肖格里的金额：格子只有 6 个字符宽，写千分位就会顶出格子压到隔壁。

    所以这里不带千分位；上了百万再用「万/亿」缩写，避免 7 位以上溢出。
    """
    try:
        n = float(v or 0)
    except Exception:
        return ""
    if not n:
        return ""
    if n >= 1e8:
        return ("%.2f" % (n / 1e8)).rstrip("0").rstrip(".") + "亿"
    if n >= 1e6:
        return ("%.0f" % (n / 1e4)) + "万"
    return fmt_money(n)


# ---------------- 主窗口 ----------------

class MainWindow:
    def __init__(self, root):
        self.root = root
        self.cfg = read_cfg()
        self._proc = None
        self.acc = ""                 # 空 = 全部账号合计
        self._acc_sig = None
        # 澳门、香港两本账各一套号码盘/风险表，按盘口分别记指纹
        self._grid_cells = {"MO": [], "HK": []}
        self.grid_holder = {}
        self.tv_risk = {}
        # 号码盘 / 风险表 / 最近记账的「上次数值指纹」。三个表都是销毁重建，
        # 一旦每 5 秒无条件重画，号码盘就是一屏闪烁。数值没变就别动它。
        self._grid_sig = {}
        # 号码盘字号跟着格子大小走：窗口一缩，15 号的字就会挤成一团。
        # 这两个表存「当前字号」和「这一盘有几行」，缩放时只按行高重算字号。
        self._grid_font = {}
        self._grid_rows = {}
        self._risk_sig = {}
        self._rec_sig = None
        # 最近记账每一行对应的消息号 / 是否已撤销。撤销按钮靠这两张表定位。
        self._rec_mid = {}
        self._rec_rev = {}
        # 消息页和总览页都以这份消息流水为准（同一接口、同一份数据），
        # 两边的「未解析」「已记账」才能对上；只缓存最近一次拉到的结果。
        self._msg_rows = []
        self._msg_pick = ""
        self._log_pick = ""
        # 消息页下拉「这一期 / 上一期 / 上上期」的期号表，和总览页共用
        self._issue_opts = []

        root.title(window_title())
        root.configure(bg=CLR_BG)
        root.minsize(1040, 700)

        self._build_ui()
        self._refresh_status()
        self._loop()
        if (os.environ.get("MSB_MANUAL") or "").strip():
            self.root.after(900, self.open_manual)

    # ---- 布局 ----
    def _build_ui(self):
        # 原来这里有一条「MarkSixBot 服务端控制台 + 副标题」的标题条，纯占高度，
        # 程序名在窗口标题栏上已经有了，去掉。
        card = tk.Frame(self.root, bg=CLR_CARD, highlightbackground=CLR_LINE,
                        highlightthickness=1)
        # 顶部状态卡（运行中 / 端口 / 看板地址 / 账号累计今日）不上屏了：
        # 用户说这些不重要、还占高度，省下来给号码盘。控件照旧创建，
        # _refresh_status 照旧 configure，只是不 pack 到窗口上。
        inner = tk.Frame(card, bg=CLR_CARD)
        inner.pack(fill="x", padx=14, pady=10)

        self.lb_state = tk.Label(inner, text="● 检测中",
                                 font=("Microsoft YaHei UI", 13, "bold"),
                                 bg=CLR_CARD, fg=CLR_MUTED)
        self.lb_state.grid(row=0, column=0, sticky="w")
        self.lb_hint = tk.Label(inner, text="", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED)
        self.lb_hint.grid(row=0, column=1, columnspan=4, sticky="w", padx=10)

        cells = [("端口", "lb_port"), ("看板地址", "lb_dash"), ("账号 / 累计 / 今日", "lb_sum")]
        for i, (t, attr) in enumerate(cells):
            wrap = tk.Frame(inner, bg=CLR_CARD)
            wrap.grid(row=1, column=i, sticky="w", padx=(0, 26), pady=(8, 0))
            tk.Label(wrap, text=t, font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED).pack(anchor="w")
            lb = tk.Label(wrap, text="—", font=FONT_B, bg=CLR_CARD, fg="#111827")
            lb.pack(anchor="w")
            setattr(self, attr, lb)

        # 按钮条必须先于 Notebook 占位：Tk 的 pack 按调用顺序分空间，
        # 先 pack 的 expand 控件会把剩余高度全吃掉，后 pack 的底部条就只剩一条缝。
        bar = tk.Frame(self.root, bg=CLR_CARD, highlightbackground=CLR_LINE,
                       highlightthickness=1)
        bar.pack(fill="x", side="bottom")

        nb = TabBar(self.root, bg=CLR_BG)
        nb.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.nb = nb
        self.tab_ov = tk.Frame(nb, bg=CLR_BG)
        self.tab_cfg = tk.Frame(nb, bg=CLR_BG)
        self.tab_wl = tk.Frame(nb, bg=CLR_BG)
        self.tab_log = tk.Frame(nb, bg=CLR_BG)
        nb.add(self.tab_ov, text="总览")
        nb.add(self.tab_cfg, text="配置")
        nb.add(self.tab_wl, text="白名单")
        nb.add(self.tab_log, text="消息")
        self._build_tab_overview(self.tab_ov)
        self._build_tab_cfg(self.tab_cfg)      # 先建配置页：白名单页要用它的端口输入框
        self._build_tab_white(self.tab_wl)
        self._build_tab_log(self.tab_log)
        nb.on_change(self._on_tab)

        # 预选页签（调试 / 自检用，平时不设这个环境变量）
        want = (os.environ.get("MSB_TAB") or "").strip()
        if want.isdigit():
            tabs = [self.tab_ov, self.tab_cfg, self.tab_wl, self.tab_log]
            idx = int(want)
            if 0 <= idx < len(tabs):
                nb.select(tabs[idx])

        def mkbtn(text, cmd, bg, fg, width=10):
            return tk.Button(bar, text=text, font=FONT, bg=bg, fg=fg, relief="flat",
                             bd=0, cursor="hand2", command=cmd, width=width)

        # 启动 / 停止合成一个：文字和颜色跟着状态走，点一下就切
        self.btn_run = mkbtn("启动服务", self.toggle_server, CLR_BLUE, "white", 11)
        self.btn_run.pack(side="left", padx=(12, 6), pady=8)
        mkbtn("手工记账", self.open_manual, "#eef7ff", CLR_BLUE2, 9).pack(side="left", padx=6, pady=8)
        mkbtn("文本记账", self.open_quick, "#eef7ff", CLR_BLUE2, 9).pack(side="left", padx=6, pady=8)
        mkbtn("备份", self.do_backup, "#eef0f3", "#374151", 7).pack(side="left", padx=6, pady=8)
        mkbtn("清空当前账号", self.do_reset, "#fdf4f3", CLR_RED, 13).pack(side="left", padx=6, pady=8)
        mkbtn("打开看板", self.open_dashboard, "#eef0f3", "#374151", 10).pack(side="left", padx=6, pady=8)
        self.v_auto = tk.BooleanVar(value=self._autorun_on())
        tk.Checkbutton(bar, text="开机自动启动", variable=self.v_auto, font=FONT_S,
                       bg=CLR_CARD, fg=CLR_MUTED, activebackground=CLR_CARD,
                       selectcolor=CLR_CARD, command=self.toggle_autorun
                       ).pack(side="right", padx=12)

    # ---------- 总览 ----------
    def _build_tab_overview(self, tab):
        # 顶部汇总条
        sbar = tk.Frame(tab, bg=CLR_CARD, highlightbackground=CLR_LINE,
                        highlightthickness=1)
        sbar.pack(fill="x", padx=10, pady=(10, 8))
        row = tk.Frame(sbar, bg=CLR_CARD)
        row.pack(fill="x", padx=12, pady=9)

        tk.Label(row, text="总金额", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).pack(side="left")
        self.lb_grand = tk.Label(row, text="0", font=("Microsoft YaHei UI", 20, "bold"),
                                 bg=CLR_CARD, fg=CLR_RED)
        self.lb_grand.pack(side="left", padx=(6, 18))

        def chip(label, attr, pack=True, click=None):
            """一个小标签：灰字标题 + 粗体数值。

            pack=False 时只创建不上屏 —— 数值还在刷新，只是不再占顶栏位置。
            click 给了就变成可点的链接样式（下划线太挤，用蓝字 + 手型光标）。
            """
            w = tk.Frame(row, bg=CLR_CARD)
            if pack:
                w.pack(side="left", padx=(0, 16))
            tk.Label(w, text=label, font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED).pack(side="left")
            lb = tk.Label(w, text="—", font=FONT_B, bg=CLR_CARD, fg="#111827")
            lb.pack(side="left", padx=(4, 0))
            if click is not None:
                lb.configure(fg=CLR_BLUE, cursor="hand2")
                lb.bind("<Button-1>", lambda _e: click())
            setattr(self, attr, lb)

        chip("本期", "lb_issue")
        chip("生肖年", "lb_zyear")
        # 澳门、香港各自的金额钉到各号码盘最后一列底部去了，顶栏只留一个合计
        chip("澳门", "lb_mo", pack=False)
        chip("香港", "lb_hk", pack=False)
        chip("有注号码", "lb_kbets", pack=False)
        chip("号码盘", "lb_desk")
        # 平特整肖、二中二这类只记总额、不进号码盘，单独露一个数
        chip("平特", "lb_flat")
        chip("本期已记账", "lb_krec")
        # 未解析只统计本期：上几期没解析的单已经随期归档，不该混进来。
        # 点这个数字直接跳到「消息」那边看是哪几条，不再单放一个按钮
        chip("本期未解析", "lb_kunp", click=self.goto_unparsed)

        # 按钮留着（别处可能还要它显示数字），只是不占顶栏那一行
        self.btn_unp = tk.Button(row, text="未解析 0", font=FONT_S, bg="#fdf4f3",
                                 fg=CLR_RED, relief="flat", bd=0, cursor="hand2",
                                 command=self.show_unparsed)

        tk.Label(row, text="账号", font=FONT_S, bg=CLR_CARD, fg=CLR_MUTED
                 ).pack(side="right", padx=(6, 4))
        self.acc_var = tk.StringVar(value="")
        self.acc_sel = ttk.Combobox(row, textvariable=self.acc_var, state="readonly",
                                    width=22, font=FONT_S)
        self.acc_sel.pack(side="right")
        self.acc_sel.bind("<<ComboboxSelected>>", self._on_acc_change)

        # 版面：左边一列（上 = 澳门/香港两个号码盘，约占 4/5 高；下 = 最近记账），
        # 右边一列（总额排序，上澳门、下香港，一直到底）。
        # 最近记账的宽度就到排序区起点为止，右侧整条留给排序表。
        body = tk.Frame(tab, bg=CLR_BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        left = tk.Frame(body, bg=CLR_BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        # 用 place 按比例钉死：号码盘占上面 49%，剩下 50% 全给最近记账。
        # 这里不能用 grid 权重 —— 12 行大字号会把自己的请求高度顶上去，
        # 权重再小也压不下来（实测给 6:7 也还是 55%）。
        # 左上：澳门、香港两个号码盘，左澳右港同时显示
        # 左上：澳门、香港两个号码盘，左澳右港同时显示。
        # 高度 = 全部高度的 60% 再让出下面「平特」那一块（固定 240），号码盘吃剩下的。
        garea = tk.Frame(left, bg=CLR_BG)
        for mk, title in (("MO", " 澳门 号码盘 "), ("HK", " 香港 号码盘 ")):
            gp = tk.LabelFrame(garea, text=title, font=FONT_B, bg=CLR_BG,
                               fg="#111827", bd=1, relief="groove")
            gp.pack(side="left", fill="both", expand=True, padx=(0, 6))
            holder = tk.Frame(gp, bg=CLR_BG)
            holder.pack(fill="both", expand=True, padx=5, pady=5)
            self.grid_holder[mk] = holder

        # 中段：平特 / 连肖 / 组合注（这几类钱不落 49 号盘，只记总额）。
        # 高度固定 240（内容就 6 行那么高），不再按比例浮动，多的空间全给号码盘。
        zbp = tk.LabelFrame(left, text=" 平特 ",
                            font=("Microsoft YaHei UI", 12, "bold"), bg=CLR_BG,
                            fg="#111827", bd=1, relief="groove")

        # 组合注按整注记，不进生肖表：右侧竖排一列，一行一种玩法，港澳合并，有注才出现。
        # 必须先 pack 右侧再 pack 生肖区，否则 expand 的生肖区会把这一列压到几十像素。
        self.combo_wrap = tk.Frame(zbp, bg="#fbfcfd", highlightbackground="#e3e7ec",
                                   highlightthickness=1, width=336)
        self.combo_wrap.pack(side="right", fill="y", padx=(0, 6), pady=(4, 4))
        self.combo_wrap.pack_propagate(False)
        # 组合注左边拉一条竖向隔线：不然中间的「合计」看着像是组合注那一栏的
        tk.Frame(zbp, bg="#b7c0ca", width=2).pack(side="right", fill="y",
                                                 pady=(8, 8), padx=(4, 0))
        # 组合注右下角的总计：钉在底部不动，组合注再多也只是上面那块内容滚动
        self.lb_combo_sum = tk.Label(self.combo_wrap, text="", bg="#fbfcfd",
                                     fg=CLR_RED, anchor="e",
                                     font=("Microsoft YaHei UI", 11, "bold"))
        self.lb_combo_sum.pack(side="bottom", fill="x", padx=8, pady=(0, 3))
        self.combo_sb = ttk.Scrollbar(self.combo_wrap, orient="vertical")
        self.combo_cv = tk.Canvas(self.combo_wrap, bg="#fbfcfd", highlightthickness=0,
                                  bd=0, yscrollcommand=self.combo_sb.set)
        self.combo_sb.configure(command=self.combo_cv.yview)
        # 平时不露出来：装得下就别在右边挂一条灰杠，塞不下时 _fit_combo_sb 再 pack
        self.combo_box = tk.Frame(self.combo_cv, bg="#fbfcfd")
        self._combo_win = self.combo_cv.create_window((0, 0), window=self.combo_box,
                                                      anchor="nw")
        self.combo_box.bind("<Configure>", self._on_combo_box)
        self.combo_cv.bind("<Configure>", self._on_combo_cv)
        self.combo_cv.pack(side="left", fill="both", expand=True, padx=(8, 2), pady=(3, 0))
        zin = tk.Frame(zbp, bg=CLR_BG)
        zin.pack(side="left", fill="both", expand=True, padx=(6, 2), pady=(4, 2))
        self._zx_cells = {}
        self._zx_total = {}
        for mk, name in (("MO", "澳门"), ("HK", "香港")):
            rf = tk.Frame(zin, bg=CLR_BG)
            rf.pack(fill="x", pady=3)
            tk.Label(rf, text=name, font=("Microsoft YaHei UI", 12, "bold"),
                     bg=CLR_BG, fg="#374151", width=4, anchor="w").pack(side="left")
            cw = tk.Frame(rf, bg=CLR_BG)
            # 不 expand：12 格按内容排完，合计就紧接在后面，不会被甩到最右边
            cw.pack(side="left")
            for c in range(12):
                cw.grid_columnconfigure(c, weight=0, uniform="zxc")
            self._zx_cells[mk] = []
            for c, z in enumerate(msb_view.ZODIAC_ANIMALS):
                f = tk.Frame(cw, bg="#fafbfc", highlightbackground="#e3e7ec",
                             highlightthickness=1)
                f.grid(row=0, column=c, sticky="ew", padx=1)
                # 格子里竖排：生肖字在上、金额在下。横着并排 12 格时宽度根本不够，
                # 金额会被 Tk 从中间截掉（只剩一个「0」），竖排后每格只要一个字宽。
                tk.Label(f, text=z, font=("Microsoft YaHei UI", 12, "bold"),
                         bg="#fafbfc", fg=CLR_BLUE).pack()
                amt = tk.Label(f, text="", font=("Consolas", 11, "bold"), width=6,
                               bg="#fafbfc", fg=CLR_RED)
                amt.pack()
                self._zx_cells[mk].append(amt)
            t = tk.Label(rf, text="", font=("Microsoft YaHei UI", 14, "bold"),
                         bg=CLR_BG, fg=CLR_RED, anchor="w")
            t.pack(side="left", padx=(8, 0))
            self._zx_total[mk] = t
        # 左下：最近记账，接在「平特」下面一直到底
        bp = tk.LabelFrame(left, text=" 最近记账 ", font=FONT_B, bg=CLR_BG,
                           fg="#111827", bd=1, relief="groove")

        # 三块的高度关系：号码盘钝 1 吃掉全部剩余，平特固定 240，最近记账固定 360。
        # 用 grid 的 minsize + weight 钉死：pack 里 expand 那块会把后面的请求高度吃光，
        # 结果平特被压成 0（踩过），所以这里走 grid。
        left.grid_columnconfigure(0, weight=1)
        # 不让 grid 子控件把 left 的请求尺寸顶大 —— 否则两个号码盘会把右边排序栏挤掉
        left.grid_propagate(False)
        left.grid_rowconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=0, minsize=186)
        left.grid_rowconfigure(2, weight=0, minsize=250)
        garea.grid(row=0, column=0, sticky="nsew")
        zbp.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        zbp.grid_propagate(False)
        bp.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        bp.grid_propagate(False)


        # 过滤条：按期看。选项是具体三个期（本期 / 前一期 / 前二期），
        # 不是「最近 N 期」这种含糊说法 —— 选中哪一期就只显示那一期。
        bar = tk.Frame(bp, bg=CLR_BG)
        bar.pack(fill="x", padx=6, pady=(4, 0))
        tk.Label(bar, text="账期", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED
                 ).pack(side="left")
        self.rec_scope = ttk.Combobox(bar, state="readonly", width=18, font=FONT_S)
        self.rec_scope.pack(side="left", padx=(4, 8))
        self.rec_scope.bind("<<ComboboxSelected>>", self._on_rec_scope)
        self.lb_rec_hint = tk.Label(bar, text="", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED)
        self.lb_rec_hint.pack(side="left")

        # 撤销做成表格最后一列的「点一下」单元格：Tk 的 Treeview 放不了行内按钮。
        cols = ("ts", "issue", "chat", "who", "text", "nums", "amt", "op")
        self._rec_op_col = "#%d" % len(cols)
        box = tk.Frame(bp, bg=CLR_BG)
        box.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        rsb = ttk.Scrollbar(box, orient="vertical")
        self.tv_rec = ttk.Treeview(box, columns=cols, show="headings",
                                   yscrollcommand=rsb.set)
        rsb.configure(command=self.tv_rec.yview)
        rsb.pack(side="right", fill="y")
        for c, t, w, a in (("ts", "时间", 128, "w"), ("issue", "期号", 100, "w"),
                           ("chat", "会话", 118, "w"),
                           ("who", "发送人", 86, "w"), ("text", "原文", 300, "w"),
                           ("nums", "号码", 56, "e"), ("amt", "本单", 74, "e"),
                           ("op", "操作", 60, "center")):
            self.tv_rec.heading(c, text=t)
            self.tv_rec.column(c, width=w, anchor=a)
        self.tv_rec.pack(side="left", fill="both", expand=True)
        self.tv_rec.tag_configure("who", foreground=CLR_BLUE)
        self.tv_rec.tag_configure("rev", foreground="#9aa0a6")
        self.tv_rec.bind("<Button-1>", self._on_rec_click)

        # 右侧：总额排序，澳门在上、香港在下；有金额的号全部列出（面板可滚动）
        rwrap = tk.Frame(body, bg=CLR_BG, width=430)
        rwrap.pack(side="left", fill="y")
        rwrap.pack_propagate(False)
        for mk, title in (("MO", " 澳门 总额排序 "), ("HK", " 香港 总额排序 ")):
            rp = tk.LabelFrame(rwrap, text=title, font=FONT_B, bg=CLR_BG,
                               fg="#111827", bd=1, relief="groove")
            rp.pack(fill="both", expand=True, pady=(0, 6))
            box = tk.Frame(rp, bg=CLR_BG)
            box.pack(fill="both", expand=True, padx=5, pady=5)
            sb = ttk.Scrollbar(box, orient="vertical")
            tv = ttk.Treeview(box, columns=("rank", "num", "zod", "amt", "share"),
                              show="headings", yscrollcommand=sb.set)
            sb.configure(command=tv.yview)
            sb.pack(side="right", fill="y")
            tv.pack(side="left", fill="both", expand=True)
            for c, t, w, a in (("rank", "序", 40, "center"), ("num", "号码", 66, "center"),
                               ("zod", "生肖", 62, "center"), ("amt", "金额", 96, "e"),
                               ("share", "占比", 74, "e")):
                tv.heading(c, text=t)
                tv.column(c, width=w, anchor=a)
            tv.tag_configure("hot", foreground=CLR_RED)
            self.tv_risk[mk] = tv

    def _render_grid(self, market, rows):
        """12 个生肖各一行：左边生肖名，右边一格格「号码 + 金额」。

        整盘是销毁重建的（Tk 做不了局部刷新），所以先比数值指纹：一模一样的
        数据直接返回，否则每 5 秒刷一次就是满屏闪烁。
        """
        # 本盘合计只加盘面上这些号的钱。整盘总额里还有平特 / 连肖 / 组合注
        # （那些不进 49 号盘），并进来的话盘面明明全空、合计却有钱，对不上。
        total = round(sum(float(c["amount"] or 0) for r in rows for c in r["cells"]), 2)
        sig = (tuple((r["zodiac"],
                      tuple((c["num"], c["amount"], c["color"]) for c in r["cells"]))
                     for r in rows),
               total)
        if sig == self._grid_sig.get(market):
            return
        self._grid_sig[market] = sig
        holder = self.grid_holder.get(market)
        if holder is None:
            return
        if os.environ.get("MSB_DEBUG_REDRAW"):
            try:
                import tempfile
                with open(os.path.join(tempfile.gettempdir(), "msb_redraw.txt"),
                          "a", encoding="utf-8") as _f:
                    _f.write("%s grid %s redraw\n" % (time.strftime("%H:%M:%S"), market))
            except Exception:
                pass
        for w in holder.winfo_children():
            w.destroy()
        cells_out = self._grid_cells.setdefault(market, [])
        cells_out.clear()
        if not rows:
            tk.Label(holder, text="暂无号码表", font=FONT_S,
                     bg=CLR_BG, fg=CLR_MUTED).pack(pady=20)
            return
        # 用 grid 均分：生肖列固定宽，号码列按「行里最多的那一行」算（马 5 个号），
        # 4 个号的行第 5 格留空。这样整数列等宽、上下对齐，还能把盘面铺满容器，
        # 不会像固定格宽那样右边空一大片。
        ncol = max(len(r["cells"]) for r in rows)
        self._grid_rows[market] = len(rows)
        # 最后一列（49 号那一格所在列）下半截要放「本盘合计」，那几格就不能再填空白框
        total_col = ncol >= 3
        holder.grid_columnconfigure(0, weight=0, minsize=52)
        for c in range(ncol):
            holder.grid_columnconfigure(c + 1, weight=1, uniform="cell")
        for r in range(len(rows)):
            holder.grid_rowconfigure(r, weight=1, uniform="row")
        for r, row in enumerate(rows):
            tk.Label(holder, text=row["zodiac"], font=FONT_CELL, width=2,
                     bg="#eaeef2", fg="#4a5463", relief="solid", bd=1,
                     ).grid(row=r, column=0, sticky="nsew", padx=(0, 1), pady=1)
            for c in range(ncol):
                cell = row["cells"][c] if c < len(row["cells"]) else None
                if not cell:
                    # 最后一列（49 号所在那一列）从第二行往下整列空着，
                    # 留给「本盘合计」，别用空框占位，否则跟它叠在一起
                    if total_col and c + 1 == ncol:
                        continue
                    tk.Frame(holder, bg=CLR_BG).grid(row=r, column=c + 1, sticky="nsew")
                    continue
                color = cell["color"]
                bg = {"红波": CLR_RED, "蓝波": CLR_BLUE2, "绿波": CLR_GREEN}.get(color, CLR_GREY)
                f = tk.Frame(holder, bg="#fafbfc", highlightbackground="#e3e7ec",
                             highlightthickness=1)
                f.grid(row=r, column=c + 1, sticky="nsew", padx=1, pady=1)
                num = tk.Label(f, text=cell["num"], font=FONT_NUM, bg=bg, fg="white",
                               relief="flat")
                num.pack(side="left", padx=(5, 6))
                # 0 元留空而不是写个「0」：一屏 12 行 × 4 格全是 0，数字会把真正
                # 有注的号码埋掉。
                amt = tk.Label(f, text=fmt_money(cell["amount"]) if cell["amount"] else "",
                               font=FONT_AMT, bg="#fafbfc", fg="#111827", anchor="w")
                amt.pack(side="left")
                cells_out.append((num, amt))
        # 本盘合计放进「49 号」那一列：只有马那一行用得到最后这一格，下面十行
        # 整列空着，正好竖着写本盘金额，宽度跟 49 格一样，看着是同一列
        if ncol >= 3:
            box = tk.Frame(holder, bg=CLR_BG)
            box.grid(row=1, column=ncol, rowspan=max(len(rows) - 1, 1),
                     sticky="nsew", padx=1, pady=1)
            # 两行贴在一起，整体在这块空白里居中 —— 各自 expand 会把中间撑开
            inner = tk.Frame(box, bg=CLR_BG)
            inner.pack(expand=True)
            tk.Label(inner, text="本盘合计", font=FONT_S, bg=CLR_BG,
                     fg=CLR_MUTED).pack()
            tk.Label(inner, text=zx_amt(total) or "0",
                     font=("Microsoft YaHei UI", 15, "bold"),
                     bg=CLR_BG, fg=CLR_RED).pack(pady=(1, 0))
        # 画完立刻按当前尺寸定一次字号，再把缩放事件挂上
        holder.bind("<Configure>", lambda _e, mk=market: self._rescale_grid(mk))
        self._rescale_grid(market)

    def _render_zxbar(self, boards):
        """生肖金额（平特 + 连肖合并一列）+ 组合注。

        12 个格子 × 2 本盘每 5 秒重画一次就是满屏闪，先比数值指纹。
        """
        sig = []
        for mk in ("MO", "HK"):
            b = boards.get(mk) or {}
            sig.append(tuple((r["zodiac"], r["amount"])
                             for r in (b.get("rows") or [])))
            sig.append(tuple((str(c.get("text") or ""), c.get("amount"))
                             for c in (b.get("combo") or [])))
        sig = tuple(sig)
        if sig == getattr(self, "_zx_sig", None):
            return
        self._zx_sig = sig

        for mk in ("MO", "HK"):
            b = boards.get(mk) or {}
            rows = b.get("rows") or []
            for i, al in enumerate(self._zx_cells.get(mk) or []):
                amt = float(rows[i].get("amount") or 0) if i < len(rows) else 0.0
                al.configure(text=zx_amt(amt),
                             fg=CLR_RED if amt else CLR_GREY)
            t = self._zx_total.get(mk)
            if t is not None:
                # 这里只算生肖表（平特 + 连肖）；组合注在下面单独一行，不并进来
                board_tot = float((b.get("totals") or {}).get("board") or 0)
                t.configure(text="合计 %s" % (zx_amt(board_tot) or "0"))

        # 组合注：一行一种玩法（同类同生肖合并），港澳合并，有注才出现
        self._render_combo(boards)

    def _render_combo(self, boards):
        """组合注：一行一种玩法，同类同生肖合并，澳门 + 香港合并成一份。"""
        for w in self.combo_box.winfo_children():
            w.destroy()
        agg = {}
        for mk in ("MO", "HK"):
            for g in (boards.get(mk) or {}).get("combo_groups") or []:
                lb = str(g.get("label") or "二中二")
                zs = [z for z in (g.get("zodiacs") or []) if z]
                key = (lb, "".join(zs))
                a = agg.get(key)
                if a is None:
                    agg[key] = a = {"label": lb, "zodiacs": zs, "amount": 0.0}
                a["amount"] = round(a["amount"] + float(g.get("amount") or 0), 2)
        if not agg:
            self.lb_combo_sum.configure(text="")
            tk.Label(self.combo_box, text="组合注：本期没有", bg=CLR_BG,
                     fg=CLR_MUTED, font=("Microsoft YaHei UI", 10)).grid(
                         row=0, column=0, sticky="w")
            return
        rank = {"二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
        rows = sorted(agg.values(), key=lambda g: (
            rank.get(g["label"][0], 9), g["label"],
            [msb_view.ZODIAC_ANIMALS.index(z)
             if z in msb_view.ZODIAC_ANIMALS else 99 for z in g["zodiacs"]]))
        total = 0.0
        self.combo_box.grid_columnconfigure(1, weight=1)
        for i, g in enumerate(rows):
            tk.Label(self.combo_box, text=g["label"], bg=CLR_BG, anchor="w",
                     fg=zhong_color(g["label"]),
                     font=("Microsoft YaHei UI", 9, "bold")
                     ).grid(row=i, column=0, sticky="w", pady=0)
            tk.Label(self.combo_box, text="+".join(g["zodiacs"]) or "—", bg=CLR_BG,
                     fg="#111827", wraplength=190, justify="left", anchor="w",
                     font=("Microsoft YaHei UI", 9, "bold")
                     ).grid(row=i, column=1, sticky="w", padx=(6, 6), pady=0)
            tk.Label(self.combo_box, text="共%s元" % fmt_money(g["amount"]), bg=CLR_BG,
                     fg=CLR_RED, font=("Consolas", 10, "bold")
                     ).grid(row=i, column=2, sticky="e", pady=0)
            total = round(total + g["amount"], 2)
        # 合计搬到栏底那一行固定显示，不跟着内容一起滚
        self.lb_combo_sum.configure(text="合计：%s 元" % fmt_money(total))
        # 内容区只有这么高，玩法多了靠滚轮翻（canvas 里的子控件不会冒泡，得逐个绑）
        def _wheel(e):
            try:
                self.combo_cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        for w in (self.combo_cv, self.combo_box) + tuple(self.combo_box.winfo_children()):
            w.bind("<MouseWheel>", _wheel)
        self.root.after(80, self._fit_combo_sb)

    def _on_combo_box(self, _e):
        """组合注内容重新排完：更新滚动范围，顺便决定要不要露滚动条。"""
        try:
            self.combo_cv.configure(scrollregion=self.combo_cv.bbox("all"))
        except Exception:
            pass
        self._fit_combo_sb()

    def _on_combo_cv(self, e):
        """组合注内容区尺寸变了：让内容跟宽度走，再看要不要露滚动条。"""
        try:
            self.combo_cv.itemconfigure(self._combo_win, width=e.width)
        except Exception:
            pass
        self._fit_combo_sb()

    def _fit_combo_sb(self):
        """组合注多到装不下才露滚动条，装得下就把那条灰杠收起来。"""
        try:
            need = self.combo_box.winfo_reqheight()
            have = self.combo_cv.winfo_height()
        except Exception:
            return
        if have < 10:
            return
        over = need > have + 2
        try:
            shown = bool(self.combo_sb.winfo_ismapped())
        except Exception:
            return
        if over and not shown:
            # 必须插在 canvas 前面：canvas 是 expand 的那个，后 pack 的滚动条会被
            # 它挤成 0 宽（踩过），用 before 把它排到最右
            self.combo_sb.pack(side="right", fill="y", pady=(4, 0), before=self.combo_cv)
        elif not over and shown:
            self.combo_sb.pack_forget()

    def _rescale_grid(self, market):
        """按格子实际高度重算号码盘字号。

        字号太小就变成一坨看不清的方块，太大又会被格子裁掉。行高撑得住
        15 号，就还它 15 号；窗口缩小、行高掉下来，字号跟着降，最低到 8 号。
        只在字号真的变了的时候才批量改字体，避免每次 Configure 都重排。
        """
        holder = self.grid_holder.get(market)
        cells = self._grid_cells.get(market) or []
        rows_n = int(self._grid_rows.get(market) or 0)
        if holder is None or not cells or rows_n <= 0:
            return
        h = holder.winfo_height()
        w = holder.winfo_width()
        if h < 20 or w < 20:
            return                      # 还没排完版，等下一条 Configure
        cell_h = h / float(rows_n)
        cell_w = w / (max(1, len(cells) // rows_n) + 1.0)
        # 行高决定字号上限，列宽再压一道：一格要同时放「01」和「100」
        by_h = cell_h * 0.62
        by_w = cell_w * 0.30
        # 下限给到 10 号：再小就只剩一团糊，宁可让格子自己裁一点也不缩到看不清
        size = int(max(10, min(15, min(by_h, by_w))))
        if size == self._grid_font.get(market):
            return
        self._grid_font[market] = size
        num_font = ("Consolas", size, "bold")
        amt_font = ("Consolas", max(9, size - 1))
        zod_font = ("Microsoft YaHei UI", max(9, size - 2), "bold")
        for num, amt in cells:
            try:
                num.configure(font=num_font, padx=3 if size < 11 else 5)
                amt.configure(font=amt_font)
            except Exception:
                pass
        for child in holder.winfo_children():
            if isinstance(child, tk.Label):
                try:
                    child.configure(font=zod_font)
                except Exception:
                    pass

    # ---------- 监听对象 ----------
    # ---------- 配置 ----------
    def _build_tab_cfg(self, tab):
        # 125% 缩放的笔记本上窗口高度有限，配置项一多底部就被裁，
        # 所以整页放进可滚动 Canvas：宁可多滚一下，也不让字段够不着。
        holder = tk.Frame(tab, bg=CLR_BG)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=CLR_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        wrap = tk.Frame(canvas, bg=CLR_BG)
        win = canvas.create_window((0, 10), window=wrap, anchor="nw")
        wrap.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=max(1, e.width - 8)))

        def _wheel(e):
            if wrap.winfo_reqheight() <= canvas.winfo_height():
                return
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind_all("<MouseWheel>", _wheel)

        def group(title):
            g = tk.LabelFrame(wrap, text=" %s " % title, font=FONT_B, bg=CLR_BG,
                              fg="#111827", bd=1, relief="groove")
            g.pack(fill="x", pady=(0, 10))
            return g

        g1 = group("采集端与验签")
        self.v_secret = self._row(g1, "推送密钥", "wx_push_secret",
                                  "白名单为空时才用到它。名单一旦有条目，服务端只认名单里的密钥", 0,
                                  secret=True)
        self.v_port = self._row(g1, "端口", "port",
                                "默认 8899。改完要停止再启动才生效；采集端的推送地址也要跟着改", 1)
        tip = tk.Label(g1, text="接几个采集端、各自用哪把密钥，都在「白名单」页里管；"
                                "签发授权时会自动写进去，不用在这里填。",
                       font=FONT_S, bg=CLR_BG, fg=CLR_MUTED, justify="left")
        tip.pack(fill="x", padx=14, pady=(0, 10))

        g2 = group("大模型（AI 解析）")
        self.v_key = self._row(g2, "API Key", "llm_api_key",
                               "填你自己的大模型 Key（阿里云百炼 / 任意 OpenAI 兼容端点）", 0,
                               secret=True)
        self.v_base = self._row(g2, "接口地址", "llm_base_url",
                                "兼容 OpenAI 协议的 /v1 地址，一般不用改", 1)
        self.v_model = self._row(g2, "模型名", "llm_model",
                                 "默认 qwen3.8-flash；换成别的模型名也行", 2)
        row = tk.Frame(g2, bg=CLR_BG)
        row.pack(fill="x", pady=4, padx=8)
        tk.Label(row, text="思考模式", font=FONT, bg=CLR_BG, fg="#374151",
                 width=12, anchor="w").pack(side="left")
        self.v_think = tk.BooleanVar(value=bool(int(self.cfg.get("llm_thinking") or 0)))
        tk.Checkbutton(row, text="开启（慢但更啰嗦；实测关掉更快更准）", variable=self.v_think,
                       font=FONT_S, bg=CLR_BG, fg=CLR_MUTED, activebackground=CLR_BG,
                       selectcolor=CLR_BG).pack(side="left")

        tk.Label(wrap, text="改完点左下「保存配置」（本页底部按钮条）；"
                            "正在跑的服务要「停止 → 启动」才会用上新配置。",
                 font=FONT_S, bg=CLR_BG, fg=CLR_MUTED).pack(anchor="w", pady=(2, 0))
        tk.Button(wrap, text="保存配置", font=FONT_B, bg=CLR_BLUE, fg="white",
                  relief="flat", bd=0, cursor="hand2", command=self.save_cfg, width=14
                  ).pack(anchor="w", pady=(10, 0))

    def _row(self, parent, label, key, hint, idx, secret=False):
        row = tk.Frame(parent, bg=CLR_BG)
        row.pack(fill="x", pady=(4, 0), padx=8)
        tk.Label(row, text=label, font=FONT, bg=CLR_BG, fg="#374151",
                 width=12, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(self.cfg.get(key, DEFAULTS.get(key, "")) or ""))
        ent = tk.Entry(row, textvariable=var, font=FONT, relief="solid", bd=1,
                       highlightthickness=0, width=56)
        if secret:
            # 密钥类一律打码：截图、录屏、客户在旁边看着的时候不至于整串
            # 漏出去。旁边给个按钮，需要核对时才临时显示。
            ent.configure(show="*")

        def _toggle(entry=ent, button=None):
            if entry.cget("show"):
                entry.configure(show="")
                button.configure(text="隐藏")
            else:
                entry.configure(show="*")
                button.configure(text="显示")

        ent.pack(side="left", fill="x", expand=True)
        var._entry = ent
        if secret:
            btn = tk.Button(row, text="显示", font=FONT_S, relief="flat", bd=0,
                            bg=CLR_BG, fg=CLR_BLUE, cursor="hand2", width=5)
            btn.configure(command=lambda e=ent, b=btn: _toggle(e, b))
            btn.pack(side="left", padx=(6, 0))
        if hint:
            h = tk.Frame(parent, bg=CLR_BG)
            h.pack(fill="x", padx=8)
            tk.Label(h, text=hint, font=FONT_S, bg=CLR_BG, fg=CLR_MUTED,
                     anchor="w", justify="left").pack(side="left", padx=(100, 0))
        return var

    # ---------- 日志 ----------
    def _build_tab_log(self, tab):
        top = tk.Frame(tab, bg=CLR_BG)
        top.pack(fill="x", padx=12, pady=(10, 2))
        self.lb_msg_head = tk.Label(top, text="收到的消息", font=FONT_B, bg=CLR_BG,
                                    fg="#111827")
        self.lb_msg_head.pack(side="left")
        tk.Button(top, text="运行日志", font=FONT_S, bg="#eef0f3", fg="#374151",
                  relief="flat", bd=0, cursor="hand2", command=self.open_logs
                  ).pack(side="right", padx=4)
        tk.Button(top, text="刷新", font=FONT_S, bg="#eef0f3", fg="#374151",
                  relief="flat", bd=0, cursor="hand2", command=self.refresh_log
                  ).pack(side="right", padx=4)

        sub = tk.Frame(tab, bg=CLR_BG)
        sub.pack(fill="x", padx=12, pady=(0, 8))
        self.lb_msg_sub = tk.Label(sub, text="", font=FONT_S, bg=CLR_BG,
                                   fg=CLR_MUTED, justify="left")
        self.lb_msg_sub.pack(side="left")
        # 消息页也按期看，期别选项和总览页共用一份，数字才对得上
        tk.Label(sub, text="账期", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED
                 ).pack(side="left", padx=(12, 0))
        self.log_scope = ttk.Combobox(sub, state="readonly", width=18, font=FONT_S)
        self.log_scope.pack(side="left", padx=(4, 0))
        self.log_scope.bind("<<ComboboxSelected>>", self._on_log_scope)

        body = tk.Frame(tab, bg=CLR_BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        cols = (("t", "时间", 130), ("acc", "账号", 108), ("chat", "会话", 160),
                ("who", "发送人", 84), ("body", "内容", 240), ("res", "结果", 178))
        tv = ttk.Treeview(body, columns=[c[0] for c in cols], show="headings")
        for key, title, w in cols:
            tv.heading(key, text=title)
            tv.column(key, width=w, anchor="w", stretch=(key == "body"))
        vsb = ttk.Scrollbar(body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True)
        tv.tag_configure("odd", background="#fbfcfd")
        tv.tag_configure("ok", foreground="#2b8a5f")
        tv.tag_configure("bad", foreground="#c0392b")
        tv.tag_configure("skip", foreground="#8a8f98")
        tv.bind("<<TreeviewSelect>>", self._on_msg_select)
        tv.bind("<Double-1>", self._on_msg_double)
        self.tv_msg = tv
        self._msg_sig = None
        self._msg_mid = {}
        self._msg_res = {}
        self._msg_rev = {}

        bar = tk.Frame(tab, bg=CLR_BG)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_rev = tk.Button(bar, text="撤销选中的这笔账", font=FONT_S,
                                 bg="#fdecec", fg="#b23b3b", relief="flat", bd=0,
                                 cursor="hand2", command=self.reverse_selected,
                                 state="disabled")
        self.btn_rev.pack(side="left")
        self.lb_rev_hint = tk.Label(bar, text="选一笔「记账」的行，再点左边按钮",
                                    font=FONT_S, bg=CLR_BG, fg=CLR_MUTED)
        self.lb_rev_hint.pack(side="left", padx=10)

    def refresh_log(self, force=False):
        """按期显示消息流水。内容没变就不重画 —— 5 秒一次重建会闪。"""
        tv = getattr(self, "tv_msg", None)
        if tv is None:
            return
        rows, meta = [], {}
        try:
            s = msb_view.summary(self.acc)
            picks = self._issue_choices(s)
            self._issue_opts = picks
            labels = tuple(p[0] for p in picks)
            if tuple(self.log_scope["values"] or ()) != labels:
                self.log_scope["values"] = labels
                if not self.log_scope.current() >= 0:
                    self.log_scope.current(0)
            idx = self.log_scope.current()
            if idx < 0 or idx >= len(picks):
                idx = 0
                self.log_scope.current(0)
            sel = picks[idx][1] if picks else ""
        except Exception:
            sel = ""
        port = int(getattr(getattr(self, "v_port", None), "get", lambda: "8899")() or 8899)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/messages?limit=200" % port,
                headers=api_headers())
            with urllib.request.urlopen(req, timeout=3) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            rows = j.get("messages") or []
            meta = j
        except Exception:
            pass
        self._msg_rows = rows or self._msg_rows
        # 只留选中这一期的消息：和总览页的未解析数、记账数同一口径
        if sel:
            rows = [m for m in rows
                    if msb_view.stage_of_ts(m.get("ts")) == sel]
        sig = (sel, len(rows), rows[0].get("ts") if rows else "",
               rows[0].get("content") if rows else "")
        if not force and sig == self._msg_sig:
            return
        self._msg_sig = sig

        kids = tv.get_children()
        if kids:
            tv.delete(*kids)
        self._msg_mid = {}
        self._msg_res = {}
        self._msg_rev = {}
        for i, m in enumerate(rows):
            res = str(m.get("result") or "")
            reason = str(m.get("reason") or "")
            # 「解析失败，已写入 unparsed.jsonl」这种长句在列里放不下，
            # 而且客户不需要知道内部文件名，只留前半句
            if "解析失败" in reason:
                reason = "解析失败"
            elif len(reason) > 12:
                reason = reason[:12] + "…"
            amt = m.get("amount")
            if res == "记账":
                base = "记账" + (" ¥%s" % fmt_money(amt) if amt is not None else "")
                if m.get("reversed"):
                    label = "已撤销 · 原 %s" % base
                    tag = "skip"
                else:
                    label = base
                    tag = "ok"
            else:
                label = ("%s · %s" % (res, reason)) if reason else res
                tag = "bad" if res == "未解析" else "skip"
            tags = (tag,)
            if i % 2:
                tags = tags + ("odd",)
            iid = str(i)
            tv.insert("", "end", iid=iid, values=(
                str(m.get("ts") or ""),
                str(m.get("account") or m.get("wechat") or ""),
                str(m.get("chat") or ""),
                str(m.get("sender") or ""),
                str(m.get("content") or ""),
                label,
            ), tags=tags)
            self._msg_mid[iid] = str(m.get("msg_id") or "")
            self._msg_res[iid] = res
            self._msg_rev[iid] = bool(m.get("reversed"))

        if rows:
            okn = sum(1 for m in rows if str(m.get("result") or "") == "记账")
            tst = sum(1 for m in rows if str(m.get("result") or "") == "记账"
                      and str(m.get("chat") or "") in ("规则测试", "自检"))
            badn = sum(1 for m in rows if str(m.get("result") or "") == "未解析")
            skipn = len(rows) - okn - badn
            self.lb_msg_sub.configure(
                text="%s · 共 %d 条：记账 %d（测试 %d）/ 未解析 %d / 跳过 %d"
                     % (sel or "全部", len(rows), okn, tst, badn, skipn),
                fg=CLR_MUTED)
        else:
            self.lb_msg_sub.configure(
                text="%s 这一期还没有消息。采集端推送一条，这里就会立刻出现。"
                     "（只留 %s 天，过期自动删）" % (sel or "所选账期",
                                                   meta.get("keep_days") or 7),
                fg=CLR_MUTED)

    def _on_log_scope(self, _evt=None):
        """消息页切账期：强制重画一次。"""
        self._msg_sig = None
        self.refresh_log(force=True)

    def _on_msg_select(self, _evt=None):
        """选中的行决定撤销按钮能不能按 —— 只有活着的「记账」行可以。"""
        tv = getattr(self, "tv_msg", None)
        btn = getattr(self, "btn_rev", None)
        hint = getattr(self, "lb_rev_hint", None)
        if tv is None or btn is None or hint is None:
            return
        sel = tv.selection()
        if not sel:
            btn.configure(state="disabled")
            hint.configure(text="选一笔「记账」的行，再点左边按钮", fg=CLR_MUTED)
            return
        iid = sel[0]
        res = self._msg_res.get(iid) or ""
        if res != "记账":
            btn.configure(state="disabled")
            hint.configure(text="只能撤销「记账」的行", fg=CLR_MUTED)
            return
        if self._msg_rev.get(iid):
            btn.configure(state="disabled")
            hint.configure(text="这笔已经撤销过了", fg=CLR_MUTED)
            return
        btn.configure(state="normal")
        hint.configure(text="撤销只减这一笔的金额，别的账一分不动", fg="#b23b3b")

    def _on_msg_double(self, _evt=None):
        btn = getattr(self, "btn_rev", None)
        if btn is not None and str(btn["state"]) == "normal":
            self.reverse_selected()

    def reverse_selected(self):
        """撤销消息页选中的那一笔。"""
        tv = getattr(self, "tv_msg", None)
        if tv is None:
            return
        sel = tv.selection()
        if not sel:
            messagebox.showinfo("撤销这笔账", "先在列表里选一笔「记账」的行",
                                parent=self.root)
            return
        iid = sel[0]
        mid = self._msg_mid.get(iid) or ""
        if not mid:
            messagebox.showinfo("撤销这笔账", "这行没有消息号，撤不了", parent=self.root)
            return
        if self._msg_rev.get(iid):
            messagebox.showinfo("撤销这笔账", "这笔已经撤销过了，不能撤两次",
                                parent=self.root)
            return
        self._reverse_by_msg(mid)

    # ---------- 最近记账：单笔撤销 ----------
    def _on_rec_click(self, event):
        """点「操作」那一格就撤这一笔；点别的列只是普通选中，什么都不做。"""
        tv = getattr(self, "tv_rec", None)
        if tv is None:
            return
        try:
            if tv.identify_region(event.x, event.y) != "cell":
                return
            if tv.identify_column(event.x) != getattr(self, "_rec_op_col", ""):
                return
            iid = tv.identify_row(event.y)
        except Exception:
            return
        if not iid:
            return
        try:
            tv.selection_set(iid)
        except Exception:
            pass
        if getattr(self, "_rec_rev", {}).get(iid):
            messagebox.showinfo("撤销这笔账", "这笔已经撤销过了，不能撤两次",
                                parent=self.root)
            return
        mid = getattr(self, "_rec_mid", {}).get(iid) or ""
        if not mid:
            messagebox.showinfo("撤销这笔账", "这行没有消息号，撤不了", parent=self.root)
            return
        self._reverse_by_msg(mid)

    def _reverse_by_msg(self, mid):
        """拉明细给人看清楚要减什么 → 确认 → 调 /api/reverse → 刷新。

        消息页和最近记账共用这一套，免得两处算出不一样的提示。
        """
        port = int(getattr(getattr(self, "v_port", None), "get", lambda: "8899")() or 8899)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/record?msg_id=%s"
                % (port, urllib.parse.quote(mid, safe="")),
                headers=api_headers())
            with urllib.request.urlopen(req, timeout=5) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError:
            messagebox.showwarning("撤销这笔账", "找不到这笔账（可能流水已经清理）",
                                   parent=self.root)
            return False
        except Exception as e:
            messagebox.showerror("撤销这笔账", "读不到明细：%r" % (e,), parent=self.root)
            return False

        if not j.get("has_detail"):
            messagebox.showwarning(
                "撤销这笔账",
                "这笔是旧版本记的账，没存号码明细，算不出该减哪几个号。\n"
                "不拿「金额前 10」猜着减 —— 猜错比不改更糟。请手工改账本。",
                parent=self.root)
            return False

        ap = j.get("applied") or {}
        segs = j.get("segments") or []
        total = j.get("total")
        if len(segs) > 1:
            # 分盘消息：一条消息落了两笔，撤销时一起撤，得把每个盘都说清楚
            lines = []
            for s in segs:
                mkt = "香港" if s.get("market") == "HK" else "澳门"
                det = "、".join("%s ¥%s" % (k, fmt_money(v))
                                for k, v in sorted((s.get("applied") or {}).items()))
                lines.append("%s：%s%s¥%s"
                             % (mkt, det, "　" if det else "", fmt_money(s.get("total"))))
            body = ("原文：%s\n\n"
                    "这条消息落了两笔账（港、澳分开记）：\n%s\n\n"
                    "合计：¥%s\n\n"
                    "撤销会把这两笔一起撤掉，每个月各自的盘扣回，别的账一分不动。\n\n"
                    "确定撤销吗？" % (j.get("text") or "", "\n".join(lines), fmt_money(total)))
        else:
            items = "、".join("%s ¥%s" % (k, fmt_money(v)) for k, v in sorted(ap.items()))
            body = ("原文：%s\n\n"
                    "当时记账：%s\n"
                    "合计：¥%s\n\n"
                    "撤销只把这 %d 个号各减回去，别的账一分不动。\n"
                    "账本总额会减 ¥%s。\n\n确定撤销吗？"
                    % (j.get("text") or "", items or "（整笔，不进号码盘）",
                       fmt_money(total), len(ap), fmt_money(total)))
        if not messagebox.askyesno("撤销这笔账", body, parent=self.root):
            return False

        res = api_post("/api/reverse", {"msg_id": mid, "note": "控制台撤销"}, port=port)
        if not res.get("ok"):
            messagebox.showerror("撤销这笔账", res.get("error") or "撤销失败",
                                 parent=self.root)
            return False

        self._msg_sig = None
        self._rec_sig = None
        self.refresh_log(force=True)
        try:
            self.refresh_overview()
        except Exception:
            pass
        messagebox.showinfo(
            "撤销完成",
            "已撤销 ¥%s，账本总额现在 ¥%s"
            % (fmt_money(abs(res.get("total") or 0)), fmt_money(res.get("grand_total"))),
            parent=self.root)
        return True

    # ---------- 白名单 ----------
    def _build_tab_white(self, tab):
        top = tk.Frame(tab, bg=CLR_BG)
        top.pack(fill="x", padx=12, pady=(10, 2))
        self.lb_wl_mode = tk.Label(top, text="…", font=FONT_B, bg=CLR_BG, fg="#111827")
        self.lb_wl_mode.pack(side="left")
        tk.Button(top, text="打开文件", font=FONT_S, bg="#eef0f3", fg="#374151",
                  relief="flat", bd=0, cursor="hand2", command=self._white_open
                  ).pack(side="right", padx=4)
        tk.Button(top, text="刷新", font=FONT_S, bg="#eef0f3", fg="#374151",
                  relief="flat", bd=0, cursor="hand2", command=self.refresh_white
                  ).pack(side="right", padx=4)

        sub = tk.Frame(tab, bg=CLR_BG)
        sub.pack(fill="x", padx=12, pady=(0, 8))
        self.lb_wl_sub = tk.Label(sub, text="", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED)
        self.lb_wl_sub.pack(side="left")

        body = tk.Frame(tab, bg=CLR_BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        cols = ("wid", "cust", "secret", "expire", "last", "state")
        heads = {"wid": "微信号", "cust": "客户", "secret": "密钥",
                 "expire": "有效期至", "last": "最后推送", "state": "状态"}
        widths = {"wid": 150, "cust": 100, "secret": 150, "expire": 100,
                  "last": 140, "state": 80}
        self.tv_wl = ttk.Treeview(body, columns=cols, show="headings", height=12)
        for c in cols:
            self.tv_wl.heading(c, text=heads[c])
            self.tv_wl.column(c, width=widths[c],
                              anchor="w" if c in ("wid", "cust", "secret", "last") else "center")
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tv_wl.yview)
        self.tv_wl.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tv_wl.pack(side="left", fill="both", expand=True)
        self.tv_wl.tag_configure("off", foreground=CLR_GREY)
        self.tv_wl.tag_configure("on", foreground="#111827")

        foot = tk.Frame(tab, bg=CLR_BG)
        foot.pack(fill="x", padx=12, pady=(0, 10))

        def mk(text, cmd, bg, fg):
            return tk.Button(foot, text=text, font=FONT_S, bg=bg, fg=fg,
                             relief="flat", bd=0, cursor="hand2", command=cmd)

        mk("启用", lambda: self._white_set(True), "#eef7f1", CLR_GREEN
           ).pack(side="left", padx=(0, 6))
        mk("停用", lambda: self._white_set(False), "#fdf4f3", CLR_RED
           ).pack(side="left", padx=(0, 6))
        mk("删除", self._white_delete, "#fdf4f3", CLR_RED).pack(side="left")
        self.lb_wl_tip = tk.Label(foot, text="", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED)
        self.lb_wl_tip.pack(side="right")

        self.tv_wl.bind("<Double-1>", lambda e: self._white_set(None))
        self.refresh_white()

    def _read_white(self) -> dict:
        """直接读文件，不依赖服务在跑 —— 服务没启动也得能看名单。"""
        if not WHITELIST_PATH.exists():
            return {"updated_at": "", "clients": []}
        try:
            with open(WHITELIST_PATH, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {"updated_at": "", "clients": []}
        except Exception as e:
            self._toast("白名单文件读不出来：%r" % (e,))
            return {"updated_at": "", "clients": []}

    def _white_enabled_count(self) -> int:
        d = self._read_white()
        return sum(1 for x in (d.get("clients") or [])
                   if isinstance(x, dict) and x.get("enabled", True))

    def _write_white(self, doc: dict) -> bool:
        """原子写：服务端按 mtime 热加载，改完下一次请求就生效，不用重启。"""
        try:
            WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(WHITELIST_PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, str(WHITELIST_PATH))
            return True
        except Exception as e:
            self._toast("写白名单失败：%r" % (e,))
            return False

    def refresh_white(self, keep: str = ""):
        doc = self._read_white()
        rows = [x for x in (doc.get("clients") or []) if isinstance(x, dict)]
        en = sum(1 for x in rows if x.get("enabled", True))
        # 服务在跑就顺手补"最后推送"，读不到就算了
        last = {}
        port = int(getattr(getattr(self, "v_port", None), "get", lambda: "8899")() or 8899)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/clients" % port, headers=api_headers())
            with urllib.request.urlopen(req, timeout=2) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
                for c in (j.get("clients") or []):
                    last[c.get("wechat_id")] = c.get("last_push") or ""
        except Exception:
            pass

        self.tv_wl.delete(*self.tv_wl.get_children())
        row_ids = {}
        for x in rows:
            sec = str(x.get("secret") or "")
            masked = (sec[:6] + "…" + sec[-5:]) if len(sec) > 12 else "…"
            on = bool(x.get("enabled", True))
            iid = self.tv_wl.insert("", "end", values=(
                x.get("wechat_id") or "", x.get("customer_id") or "-",
                masked, x.get("expire_at") or "-",
                last.get(x.get("wechat_id")) or "-",
                "● 启用" if on else "○ 已停用",
            ), tags=("on" if on else "off",))
            row_ids[str(x.get("wechat_id") or "")] = iid
        if keep and keep in row_ids:                 # 操作完把选中还回去
            self.tv_wl.selection_set(row_ids[keep])
            self.tv_wl.focus(row_ids[keep])

        # 三态必须分开说，否则"全停用"会被误读成"兼容模式全放行"
        if en:
            self.lb_wl_mode.configure(text="严格模式：只接收名单内的采集端", fg=CLR_GREEN)
        elif rows:
            self.lb_wl_mode.configure(
                text="名单里的采集端全部已停用：一律拒收", fg=CLR_RED)
        else:
            self.lb_wl_mode.configure(
                text="兼容模式：名单是空的，还在用配置页那把密钥", fg="#b5651d")
        upd = doc.get("updated_at") or "—"
        self.lb_wl_sub.configure(
            text="白名单 %d 个 · 启用 %d 个 · 最后更新 %s" % (len(rows), en, upd))
        if en:
            tip = "签发授权后这里自动出现；停用即刻生效，不用重启"
        elif rows:
            tip = "全部停用中，采集端现在推什么都收不到"
        else:
            tip = "去厂商授权工具签发一次，就会自动写进来"
        self.lb_wl_tip.configure(text=tip)

    def _white_sel(self):
        sel = self.tv_wl.selection()
        if not sel:
            self._toast("先在表格里选一行")
            return None
        return self.tv_wl.item(sel[0], "values")[0]

    def _white_set(self, on):
        wid = self._white_sel()
        if not wid:
            return
        doc = self._read_white()
        for x in (doc.get("clients") or []):
            if str(x.get("wechat_id") or "") == str(wid):
                new = (not bool(x.get("enabled", True))) if on is None else bool(on)
                x["enabled"] = new
                tip = "已启用 %s" % wid if new else "已停用 %s（这个号的推送立刻会被拒）" % wid
                break
        else:
            return
        doc["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if self._write_white(doc):
            self.refresh_white(keep=wid)
            self._toast(tip)

    def _white_delete(self):
        wid = self._white_sel()
        if not wid:
            return
        if not messagebox.askyesno("删除", "把 %s 从白名单里删掉？\n\n"
                                       "删掉后这个号的推送会被直接拒绝，不会记进账本。" % wid):
            return
        doc = self._read_white()
        doc["clients"] = [x for x in (doc.get("clients") or [])
                          if str(x.get("wechat_id") or "") != str(wid)]
        doc["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if self._write_white(doc):
            self.refresh_white()
            self._toast("已删除 %s" % wid)

    def _white_open(self):
        if not WHITELIST_PATH.exists():
            self._toast("还没有白名单文件，签发一次授权就会生成")
            return
        os.startfile(str(WHITELIST_PATH))

    # ---- 状态刷新 ----
    def _on_tab(self, event=None):
        idx = self.nb.current()
        if idx == 0:
            self.refresh_overview()
        elif idx == 2:
            self.refresh_white()
        elif idx == 3:
            self.refresh_log()

    def _on_acc_change(self, event=None):
        v = (self.acc_var.get() or "").strip()
        self.acc = "" if v.startswith("全部账号") else v
        self.refresh_overview()

    def _sync_accounts(self):
        """账号下拉：账号变了才重建选项，避免每次刷新都把用户的选择冲掉。

        「全部账号（合计）」永远在第一位、默认选中 —— 只有一个采集端时也不隐藏，
        这样看到的默认口径就是全部数据合计，切单号只是下钻。
        """
        accs = msb_view.all_accounts()
        sig = tuple(accs)
        if sig != self._acc_sig:
            self._acc_sig = sig
            self.acc_sel.configure(values=["全部账号（合计）"] + list(accs))
            if self.acc and self.acc not in accs:
                self.acc = ""
            self.acc_var.set(self.acc or "全部账号（合计）")
        elif not self.acc_var.get():
            self.acc_var.set(self.acc or "全部账号（合计）")

    def refresh_overview(self):
        try:
            s = msb_view.summary(self.acc)
        except Exception as e:
            self.lb_grand.configure(text="—")
            self._toast("读数据出错：%r" % (e,))
            return
        self._sync_accounts()
        self.lb_grand.configure(text=fmt_money(s["grand_total"]))
        banks = s.get("markets") or {}
        self.lb_mo.configure(text=fmt_money((banks.get("MO") or {}).get("total") or 0))
        self.lb_hk.configure(text=fmt_money((banks.get("HK") or {}).get("total") or 0))
        self.lb_issue.configure(text="%s 期" % s.get("issue", "—"))
        self.lb_zyear.configure(text="%s年 · %s" % (s["zodiac_year"], s["zodiac"]))
        self.lb_kbets.configure(text=str(s.get("numbers_with_bets_all", s["numbers_with_bets"])))
        # 顶栏只剩一个「号码盘 = 澳 + 港」，两本各自的金额在盘面右下角
        self.lb_desk.configure(text=fmt_money(s.get("grand_total") or 0))
        self.lb_flat.configure(text=fmt_money(s.get("flat_total") or 0))
        self.lb_krec.configure(text=str(s.get("records_current") or 0))
        cur_issue = str(s.get("issue") or "")

        # 期别选择：本期 / 前一期 / 前二期，都是具体日期，选中哪期就看哪期
        picks = self._issue_choices(s)
        self._issue_opts = picks
        labels = tuple(p[0] for p in picks)
        if tuple(self.rec_scope["values"] or ()) != labels:
            self.rec_scope["values"] = labels
            self.rec_scope.current(0)
        idx = self.rec_scope.current()
        if idx < 0 or idx >= len(picks):
            idx = 0
            self.rec_scope.current(0)
        sel = picks[idx][1] if picks else cur_issue

        # 消息流水是「消息」页和这里共用的事实源，两边数字才对得上。
        # 拉不到（服务端没起）就退回本地文件口径，不让界面空着。
        self._msg_rows = self._fetch_messages()
        mine = [m for m in self._msg_rows
                if msb_view.stage_of_ts(m.get("ts")) == sel]
        msg_ok = sum(1 for m in mine if str(m.get("result") or "") == "记账")
        msg_rev = sum(1 for m in mine if str(m.get("result") or "") == "记账"
                      and m.get("reversed"))
        # 规则测试（selftest 打进去的假消息）不该算进业务笔数，单列出来
        msg_test = sum(1 for m in mine if str(m.get("result") or "") == "记账"
                       and str(m.get("chat") or "") in ("规则测试", "自检"))
        unp_n = sum(1 for m in mine if str(m.get("result") or "") == "未解析")
        if not self._msg_rows:
            unp_n = (int(s.get("unparsed_current") or 0) if sel == cur_issue else
                     sum(1 for x in s["unparsed"]
                         if str(x.get("issue") or "") == sel))
        self.lb_kunp.configure(text=str(unp_n))
        self.btn_unp.configure(text="未解析 %d" % unp_n)

        # 两本号码盘同时画，左澳门右香港；盘内合计由盘面金额自己算
        grids = s.get("grids") or {}
        self._render_grid("MO", grids.get("MO") or [])
        self._render_grid("HK", grids.get("HK") or [])

        # 生肖金额表 + 组合注：不落号码盘的钱，按生肖摊开看
        try:
            boards = {mk: msb_view.zodiac_board(self.acc, mk) for mk in ("MO", "HK")}
        except Exception:
            boards = {}
        self._render_zxbar(boards)

        # 两本风险榜：澳门在上、香港在下，有金额的号全部列出
        risks = s.get("risks") or {}
        for mk in ("MO", "HK"):
            rows = risks.get(mk) or []
            risk_sig = tuple((x["rank"], x["num"], x["zodiac"], x["amount"], x["share"])
                             for x in rows)
            if risk_sig == self._risk_sig.get(mk):
                continue
            self._risk_sig[mk] = risk_sig
            tv = self.tv_risk.get(mk)
            if tv is None:
                continue
            for row in tv.get_children():
                tv.delete(row)
            for x in rows:
                tv.insert("", "end",
                          values=(x["rank"], x["num"], x["zodiac"],
                                  fmt_money(x["amount"]), "%s%%" % x["share"]),
                          tags=("hot",) if x["rank"] == 1 else ())

        # 最近记账：只显示选中的那一期。手工记账不在消息流水里（消息页看不到），
        # 所以提示里把两边条数摆出来：消息记账 N 笔 / 账目 M 笔（含手工 K 笔）。
        all_rec = s["recent"]
        rec_rows = [x for x in all_rec if str(x.get("issue") or "") == sel]
        manual_n = sum(1 for x in rec_rows
                       if str(x.get("sender") or "") == "manual"
                       or str(x.get("chat") or x.get("chat_name") or "") == "手工记账")
        if getattr(self, "lb_rec_hint", None) is not None:
            self.lb_rec_hint.configure(
                text="%s · 消息记账 %d 笔（测试 %d · 已撤 %d）· 账目 %d 笔（手工 %d）"
                     % (sel or "—", msg_ok, msg_test, msg_rev, len(rec_rows), manual_n))

        rec_sig = (sel, tuple((str(x.get("ts") or ""), str(x.get("issue") or ""),
                                   str(x.get("chat") or x.get("chat_name") or ""),
                                   str(x.get("sender_name") or x.get("sender") or ""),
                                   str(x.get("text") or ""), x.get("numbers") or 0,
                                   x.get("total") or 0,
                                   bool(x.get("reversed")))
                                  for x in rec_rows))
        if rec_sig != self._rec_sig:
            self._rec_sig = rec_sig
            for row in self.tv_rec.get_children():
                self.tv_rec.delete(row)
            self._rec_mid = {}
            self._rec_rev = {}
            for x in rec_rows:
                rev = bool(x.get("reversed"))
                iid = self.tv_rec.insert("", "end", values=(
                    str(x.get("ts") or "")[:16],
                    str(x.get("issue") or ""),
                    str(x.get("chat") or x.get("chat_name") or "")[:24],
                    str(x.get("sender_name") or x.get("sender") or "")[:14],
                    str(x.get("text") or "")[:70],
                    x.get("numbers") or 0,
                    fmt_money(x.get("total")),
                    "已撤销" if rev else "撤销",
                ), tags=("who",) if not rev else ("who", "rev"))
                self._rec_mid[iid] = str(x.get("msg_id") or "")
                self._rec_rev[iid] = rev

    def _fetch_messages(self, limit=500):
        """拉最近的消息流水。总览和「消息」页共用这一份，计数才对得上。"""
        port = int(getattr(getattr(self, "v_port", None), "get", lambda: "8899")() or 8899)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/api/messages?limit=%d" % (port, limit),
                headers=api_headers())
            with urllib.request.urlopen(req, timeout=3) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return j.get("messages") or []
        except Exception:
            return []

    def _issue_choices(self, s):
        """可选账期：本期 + 已归档的前两期。返回 [(显示文本, 期号)]。

        显示文本带日期，用户要的是「本期是哪天 / 上一期是哪天」，而不是
        「最近 3 期」这种对数不方便的说法。
        """
        cur = str(s.get("issue") or "")
        opts = [("本期 %s" % cur, cur)] if cur else []
        arch = []
        try:
            # 合计视图下 self.acc 是空的，得把所有账号的归档期并起来
            accs = [self.acc] if self.acc else msb_view.all_accounts()
            for a in accs:
                arch += [p.stem for p in msb_view.issue_files(a)]
        except Exception:
            arch = []
        arch = sorted(set(arch), reverse=True)
        rest = [a for a in arch if a and a != cur][:2]
        for i, iss in enumerate(rest):
            opts.append(("%s %s" % ("前一期" if i == 0 else "前二期", iss), iss))
        return opts

    def _on_rec_scope(self, _evt=None):
        """切账期：指纹清掉，强制按新期重画一次。"""
        self._rec_sig = None
        self.refresh_overview()
        self.refresh_log(force=True)

    def _paint_run(self, running: bool):
        """主按钮跟着状态换字换色：在跑就显示「停止服务」，红字浅底。"""
        b = getattr(self, "btn_run", None)
        if b is None:
            return
        try:
            if running:
                b.configure(text="停止服务", bg="#fdf4f3", fg=CLR_RED,
                            activebackground="#fbeae8", activeforeground=CLR_RED)
            else:
                b.configure(text="启动服务", bg=CLR_BLUE, fg="white",
                            activebackground=CLR_BLUE, activeforeground="white")
        except Exception:
            pass

    def _refresh_status(self):
        try:
            port = int(self.v_port.get() or 8899)
        except ValueError:
            port = 8899
        ok, why = health(port)
        if ok:
            self.lb_state.configure(text="● 运行中", fg=CLR_OK)
            self.lb_hint.configure(text="服务端在 %d 端口等着采集端推送" % port)
            self._paint_run(True)
        else:
            pid = running_pid()
            if pid and _pid_alive(pid):
                self.lb_state.configure(text="● 启动中", fg="#b8860b")
                self.lb_hint.configure(text="进程在，但端口还没响应（前几秒正常）")
                self._paint_run(True)
            else:
                self.lb_state.configure(text="● 未运行", fg=CLR_BAD)
                self.lb_hint.configure(text='点左下「启动服务」；窗口关掉不影响后台')
                self._paint_run(False)
        self.lb_port.configure(text=str(port))
        self.lb_dash.configure(text="http://127.0.0.1:%d/" % port)
        try:
            s = msb_view.summary("")
            self.lb_sum.configure(text="%d 个 / %s 元 / 今日 %d 笔" % (
                len(s["accounts"]), fmt_money(s["grand_total"]), s["records_today"]))
        except Exception:
            self.lb_sum.configure(text="—")

    def _loop(self):
        self.root.after(5000, self._loop)
        try:
            self._refresh_status()
            # 只在当前页自动刷，别把别页的选中/滚动冲掉
            try:
                cur = self.nb.current()
                if cur == 0:
                    self.refresh_overview()
                elif cur == 3:
                    self.refresh_log()
            except Exception:
                pass
        except Exception:
            pass

    # ---- 动作 ----
    def goto_unparsed(self):
        """点顶栏「本期未解析」：跳到消息页看原始消息，未解析的那几条标红。

        顶栏不再单放一个「未解析」按钮，点数字就跳过去，省一行位置。
        """
        try:
            self.nb.select(self.tab_log)
        except Exception:
            return
        try:
            self.refresh_log(force=True)
        except Exception:
            pass

    def show_unparsed(self):
        # 和「消息」页、顶部数字同一个来源：消息流水里 result=未解析 的那些，
        # 再按当前选中的账期过滤，三处数就一致了。
        s = msb_view.summary(self.acc)
        picks = self._issue_opts or self._issue_choices(s)
        idx = self.rec_scope.current()
        sel = picks[idx][1] if (picks and 0 <= idx < len(picks)) else str(s.get("issue") or "")
        if not self._msg_rows:
            self._msg_rows = self._fetch_messages()
        rows = [m for m in self._msg_rows
                if str(m.get("result") or "") == "未解析"
                and msb_view.stage_of_ts(m.get("ts")) == sel]
        if not rows and not self._msg_rows:
            rows = [x for x in s["unparsed"]
                    if str(x.get("issue") or "") == sel]
        win = tk.Toplevel(self.root)
        win.title("未解析 %s · %d 条（需人工核对）" % (sel or "—", len(rows)))
        win.configure(bg=CLR_BG)
        win.geometry("860x440")
        tk.Label(win, text="账期 %s 没解析出来的消息，原文同时留在 unparsed.jsonl 里。"
                          % (sel or "—"),
                 font=FONT_S, bg=CLR_BG, fg=CLR_MUTED).pack(anchor="w", padx=12, pady=(10, 4))
        body = tk.Frame(win, bg=CLR_BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        cols = ("ts", "chat", "who", "text", "reason")
        tv = ttk.Treeview(body, columns=cols, show="headings")
        for c, t, w, a in (("ts", "时间", 130, "w"), ("chat", "会话", 150, "w"),
                           ("who", "发送人", 100, "w"),
                           ("text", "原文", 340, "w"), ("reason", "原因", 160, "w")):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor=a)
        vsb = ttk.Scrollbar(body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True)
        if not rows:
            tv.insert("", "end", values=("—", "—", "—", "这一期没有未解析的单", "—"))
        for x in rows:
            tv.insert("", "end", values=(str(x.get("ts") or "")[:16],
                                         str(x.get("chat") or x.get("chat_name") or "")[:20],
                                         str(x.get("sender") or "")[:12],
                                         str(x.get("content") or x.get("text") or "")[:80],
                                         str(x.get("reason") or "")[:40]))

    def save_cfg(self):
        try:
            port = int(self.v_port.get() or 8899)
            if not (1 <= port <= 65535):
                raise ValueError("端口要在 1~65535 之间")
        except ValueError as e:
            messagebox.showerror("端口填错了", str(e))
            return
        # wx_account_secrets 不在这里改：它已经被「白名单」页接管，
        # 原值原样写回，免得老配置被别人一次保存就清空。
        old = read_cfg()
        write_cfg({
            "port": port,
            "wx_push_secret": self.v_secret.get().strip(),
            "wx_account_secrets": str(old.get("wx_account_secrets") or ""),
            "llm_api_key": self.v_key.get().strip(),
            "llm_base_url": self.v_base.get().strip(),
            "llm_model": self.v_model.get().strip(),
            "llm_thinking": 1 if self.v_think.get() else 0,
        })
        self.cfg = read_cfg()
        self._toast("配置已保存（正在跑的服务要停止再启动才生效）")

    def toggle_server(self):
        """一个按钮管启动和停止：现在在跑就停，没跑就起。"""
        try:
            port = int(self.v_port.get() or 8899)
        except ValueError:
            port = 8899
        ok, _ = health(port)
        pid = running_pid()
        if ok or (pid and _pid_alive(pid)):
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        port = int(self.v_port.get() or 8899)
        if health(port)[0]:
            messagebox.showinfo("已经在跑", "服务端已经在 %d 端口运行了，不用重复启动。" % port)
            return
        pid = running_pid()
        if pid and _pid_alive(pid):
            messagebox.showinfo("正在启动", "已经有一个启动中的进程（PID %d），等几秒再看。" % pid)
            return
        missing = []
        if not self.v_key.get().strip():
            missing.append("大模型 API Key")
        # 白名单有条目就不需要全局密钥了；否则还得填一把，不然一个号都收不进来
        wl_ok = self._white_enabled_count() > 0
        if not wl_ok and not self.v_secret.get().strip():
            missing.append("推送密钥（或者先在「白名单」页加一个采集端）")
        if missing:
            messagebox.showerror("配置还没填",
                                 "缺这些必填项，服务起不来：\n\n  · " + "\n  · ".join(missing) +
                                 "\n\n先在本页填好，点「保存配置」。")
            return

        cmd = self_cmd() + ["--headless", "--quiet"]
        log_dir = msb_view.logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        out = open(log_dir / ("console_%s.log" % time.strftime("%Y%m%d")), "a",
                   encoding="utf-8")
        try:
            self._proc = subprocess.Popen(cmd, cwd=str(app_dir()),
                                          stdout=out, stderr=subprocess.STDOUT,
                                          creationflags=0x08000000)   # 不弹黑窗
        except Exception as e:
            messagebox.showerror("启动失败", "拉不起后台服务：\n\n%r" % (e,))
            return
        try:
            pid_path().parent.mkdir(parents=True, exist_ok=True)
            pid_path().write_text(str(self._proc.pid), encoding="utf-8")
        except Exception:
            pass
        self._toast("已启动，正在等端口响应…")
        self.root.after(1200, self._after_start)

    def _after_start(self):
        port = int(self.v_port.get() or 8899)
        if health(port)[0]:
            self._toast("服务端已就绪：http://127.0.0.1:%d/" % port)
        else:
            self._toast("还没响应，稍等几秒；不行就看「日志」页")
        self._refresh_status()

    def stop_server(self):
        pid = running_pid()
        killed = False
        if pid and _pid_alive(pid):
            try:
                # 用内核 API 结束，不拉 taskkill（Windows 11 会闪黑窗）
                import ctypes
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                h = k32.OpenProcess(0x0001, False, int(pid))   # PROCESS_TERMINATE
                if h:
                    k32.TerminateProcess(h, 0)
                    k32.CloseHandle(h)
                    killed = True
            except Exception:
                pass
        try:
            pid_path().unlink()
        except Exception:
            pass
        if killed:
            self._toast("已停止")
        else:
            port = int(self.v_port.get() or 8899)
            if health(port)[0]:
                messagebox.showinfo("没找到进程",
                                    "端口 %d 还有响应，但没找到本窗口启动的进程。\n"
                                    "可能是别的进程占着，用任务管理器结束 MarkSixBot.exe 即可。" % port)
            else:
                self._toast("已经没在跑了")
        self.root.after(1000, self._refresh_status)

    def do_backup(self):
        port = int(self.v_port.get() or 8899)
        if not health(port)[0]:
            messagebox.showerror("服务没在跑", "备份要通过服务端接口，请先「启动服务」。")
            return
        d = api_post("/backup", None, port)
        if d.get("ok"):
            self._toast("已备份 %s 个账号的账本" % d.get("count"))
        else:
            messagebox.showerror("备份失败", str(d.get("error") or "未知错误"))

    def _recent_chats(self, limit=40):
        """最近出现过的会话名，给「挂在谁名下」当候选 —— 光有账号不够，
        得知道这注是谁下的。"""
        chats = []
        try:
            for rec in msb_view.recent_records("", limit):
                c = str(rec.get("chat") or "").strip()
                if c and c not in chats:
                    chats.append(c)
        except Exception:
            pass
        return chats

    def open_manual(self):
        """打开手工记账窗口：口头单、电话单都从这里进，跟聊天记录无关。"""
        try:
            mapping = msb_view.zodiac_year()[2]
        except Exception as e:
            self._toast("生肖表读不出来：%r" % (e,))
            return
        if not mapping:
            self._toast("生肖表是空的，先看看日志页有没有报错")
            return
        chats = self._recent_chats()
        port = int(self.v_port.get() or 8899)

        def _submit(payload):
            """窗口把清单拼成客户原话交给服务端记账。

            不在这里写账本 —— 手记和微信推送必须同一条落账链路，
            期号归档、港澳分盘、流水、撤销才不会有第二套算法。
            """
            if not health(port)[0]:
                return {"ok": False, "error": "服务没在跑，先点右上角「启动服务」"}
            d = api_post("/api/quick_bet", payload, port)
            if d.get("ok"):
                self.refresh_overview()
            return d

        ManualBetDialog(self.root, mapping, msb_view.grid_rows(self.acc), chats,
                        account=self.acc, on_submit=_submit,
                        demo=(os.environ.get("MSB_MANUAL") or "") == "2")

    def open_quick(self):
        """粘文本记账：客户原文只错一个字时，改一下就能记，不用等对方重发。"""
        accs = msb_view.all_accounts()
        QuickBetDialog(self.root, self.acc or (accs[0] if accs else ""),
                       chats=self._recent_chats(), on_done=self.refresh_overview)

    def do_reset(self):
        """清空账本。选「全部账号（合计）」就清所有账号，选具体号就只清它。

        这里清的是整本账（流水 / 未解析 / 期号 / 归档），不是把号码盘数字抹成 0，
        所以确认框要把删什么一条条写清楚，清全部再要一次确认。
        """
        accs = msb_view.all_accounts()
        if not accs:
            messagebox.showinfo("没有账号", "现在还没有任何账号的账本", parent=self.root)
            return
        target = self.acc or ""
        one = bool(target)
        if one:
            scope = "账号「%s」" % target
        else:
            scope = "全部 %d 个账号（%s）" % (len(accs), "、".join(accs))

        body = ("即将清空 %s。\n\n"
                "会删掉的：\n"
                "  · 累计账本 —— 号码盘 49 个号全部归零\n"
                "  · 记账流水 records.jsonl（撤销和 recalc 都靠它）\n"
                "  · 未解析队列 unparsed.jsonl\n"
                "  · 当前期号 current_issue.txt 和历史期归档 issues\\\n\n"
                "清理前会自动把整份账打包到 backups\\ 留底，但清理本身不可撤销。\n"
                "会话清单、监听对象配置不受影响。\n\n"
                "确定清空 %s 吗？" % (scope, scope))
        if not messagebox.askyesno("确认清空", body, parent=self.root):
            return
        if not one:
            if not messagebox.askyesno(
                    "再确认一次",
                    "这次清的是全部 %d 个账号的所有流水，不只是一个号。\n\n真的继续吗？"
                    % len(accs), parent=self.root):
                return

        port = int(self.v_port.get() or 8899)
        if not health(port)[0]:
            messagebox.showerror("服务没在跑", "清空要通过服务端接口，请先「启动服务」。",
                                 parent=self.root)
            return
        d = api_post("/reset?account=%s" % urllib.parse.quote(target, safe=""), None, port)
        if d.get("ok"):
            self._rec_sig = None
            self._msg_sig = None
            self._toast("已清空 %d 个账号" % (d.get("count") or 0))
            self.refresh_overview()
        else:
            messagebox.showerror("清空失败", str(d.get("error") or "未知错误"),
                                 parent=self.root)

    def open_dashboard(self):
        port = int(self.v_port.get() or 8899)
        os.startfile("http://127.0.0.1:%d/" % port)

    def open_logs(self):
        d = msb_view.logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    def open_data(self):
        d = data_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))

    # ---- 开机自启 ----
    def _autorun_on(self) -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, "MarkSixBot")
                return True
        except Exception:
            return False

    def toggle_autorun(self):
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                if self.v_auto.get():
                    # 用完整命令行，不能只取 [0]：源码模式下 self_cmd() 是
                    # [python.exe, main.py]，只取第一个会把 main.py 丢掉。
                    parts = self_cmd() + ["--headless", "--quiet"]
                    reg = " ".join('"%s"' % p if (" " in p or not p) else p for p in parts)
                    winreg.SetValueEx(k, "MarkSixBot", 0, winreg.REG_SZ, reg)
                    self._toast("已设置开机自动启动（后台静默，无窗口）")
                else:
                    try:
                        winreg.DeleteValue(k, "MarkSixBot")
                    except Exception:
                        pass
                    self._toast("已取消开机自动启动")
        except Exception as e:
            messagebox.showerror("设置失败", "改注册表失败：\n\n%r" % (e,))
            self.v_auto.set(self._autorun_on())

    # ---- 小工具 ----
    def _toast(self, msg):
        self.lb_hint.configure(text=msg)


def run():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    # 表格默认字体太小，行也矮；放大一档并加高行，右侧排序表读起来清楚
    try:
        st = ttk.Style()
        st.configure("Treeview", font=("Microsoft YaHei UI", 11), rowheight=28)
        st.configure("Treeview.Heading", font=("Microsoft YaHei UI", 11, "bold"))
    except Exception:
        pass
    MainWindow(root)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    # 总览要并排摆两个号码盘，默认最大化
    try:
        root.state("zoomed")
    except Exception:
        w, h = min(1180, int(sw * 0.94)), min(880, int(sh * 0.94))
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 4)))
    try:
        root.attributes("-topmost", True)
        root.after(2000, lambda: root.attributes("-topmost", False))
    except Exception:
        pass
    root.mainloop()
    return 0


class QuickBetDialog(tk.Toplevel):
    """粘一段客户原文，先看清规则认成什么，对得上再记账。

    存在的理由：客户经常只发错一个字（把"鼠 5 5尾各10"写成"鼠5尾各10"），
    让对重发一轮要等半天；这里把原文改一下就能记。走的是和微信推送
    完全同一条解析链 —— 规则优先、模型兜底、12/13/14 直接判死。
    """

    def __init__(self, master, account: str, chats=None, on_done=None):
        super().__init__(master)
        self.title("文本记账")
        self.account = account or ""
        self.chats = list(chats or [])
        self.on_done = on_done
        self.configure(bg=CLR_BG)
        self.resizable(False, False)

        wrap = tk.Frame(self, bg=CLR_BG)
        wrap.pack(fill="both", expand=True, padx=16, pady=(14, 12))

        tk.Label(wrap, text="文本记账", font=FONT_T, bg=CLR_BG, fg="#111827"
                 ).pack(anchor="w")
        tk.Label(wrap, font=FONT_S, bg=CLR_BG, fg=CLR_MUTED, justify="left",
                 text="粘贴客户发来的原文。先点「解析」看规则认成什么，对得上再点「记账」。"
                 ).pack(anchor="w", pady=(2, 8))

        row = tk.Frame(wrap, bg=CLR_BG)
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="记入账号", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED).pack(side="left")
        # 账必须落到具体账号上：主界面停在「全部账号（合计）」时没有账号可记，
        # 所以这里给下拉，默认取主界面选中的那个，没选就取唯一/第一个账号。
        self._accounts = self._load_accounts()
        if self.account not in self._accounts:
            self.account = self._accounts[0] if self._accounts else ""
        self.v_acc = tk.StringVar(value=self.account)
        ttk.Combobox(row, textvariable=self.v_acc, state="readonly", font=FONT_S,
                     values=self._accounts, width=18).pack(side="left", padx=(6, 16))

        # 账号是「哪个微信号的账」，对象是「谁下的注」——两个都得有，
        # 只记账号的话，同一个号下面几个人下单就分不出谁是谁了。
        tk.Label(row, text="挂在谁名下", font=FONT_S, bg=CLR_BG, fg=CLR_MUTED
                 ).pack(side="left")
        self.v_chat = tk.StringVar(value="")
        self.cb_chat = ttk.Combobox(row, textvariable=self.v_chat, font=FONT_S,
                                    state="normal", values=self.chats, width=16)
        self.cb_chat.pack(side="left", padx=6)
        self.cb_chat.bind("<Return>", lambda e: self.do_record())

        self.txt = tk.Text(wrap, height=5, width=56, font=FONT, relief="solid", bd=1,
                           bg=CLR_CARD, fg="#111827", wrap="word", undo=True)
        self.txt.pack(fill="x")
        self.txt.focus_set()

        btns = tk.Frame(wrap, bg=CLR_BG)
        btns.pack(fill="x", pady=8)

        def mk(text, cmd, bg, fg, w=8):
            return tk.Button(btns, text=text, font=FONT, bg=bg, fg=fg, relief="flat",
                             bd=0, cursor="hand2", command=cmd, width=w)

        # 按钮靠右，左边只留「连续记账」勾选；默认记完一笔就关窗。
        # 勾选后记完不关，清空输入接着录下一笔。
        self.v_loop = tk.BooleanVar(value=False)
        tk.Checkbutton(btns, text="连续记账", variable=self.v_loop, font=FONT_S,
                       bg=CLR_BG, fg=CLR_MUTED, activebackground=CLR_BG,
                       selectcolor=CLR_BG, bd=0, highlightthickness=0,
                       cursor="hand2").pack(side="left")
        mk("记账", self.do_record, CLR_BLUE, "white", 9).pack(side="right")
        mk("解析", self.do_parse, "#eef7ff", CLR_BLUE2, 7).pack(side="right", padx=(0, 8))

        self.lb_result = tk.Label(wrap, text="", font=FONT, bg=CLR_BG, fg=CLR_MUTED,
                                  justify="left", anchor="w", wraplength=540)
        self.lb_result.pack(fill="x", pady=(2, 0))

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self.do_record())
        # 输入框吃掉 Ctrl+P 默认是插入控制字符，改成直接解析，省一次鼠标
        self.bind("<Control-p>", lambda e: self.do_parse())

        # 固定在父窗口左上方一点，别让 Tk 随便丢到屏幕角落
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + 40
            y = master.winfo_rooty() + 70
            self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        except Exception:
            pass

    def _load_accounts(self):
        d = api_get("/api/accounts")
        out = [str(a.get("account") or "") for a in (d.get("accounts") or [])]
        out = [a for a in out if a]
        if out:
            return out
        try:
            out = msb_view.all_accounts()
        except Exception:
            out = []
        return out or ([self.account] if self.account else [])

    def _text(self) -> str:
        return (self.txt.get("1.0", "end") or "").strip()

    def _show(self, text, color):
        self.lb_result.configure(text=text, fg=color)

    def do_parse(self):
        t = self._text()
        if not t:
            self._show("先粘一段文本", CLR_MUTED)
            return
        d = api_post("/api/parse", {"text": t})
        if d.get("ok"):
            self._show("%s\n规则识别 · %s 个号 · 合计 %s 元"
                       % (d.get("detail") or "", d.get("numbers") or 0,
                          fmt_money(d.get("total"))), CLR_OK)
            return
        if d.get("error"):
            self._show("接口出错：%s" % d.get("error"), CLR_BAD)
        elif d.get("can_llm"):
            self._show("%s\n（点「记账」会交给模型再试一次）"
                       % (d.get("reason") or "规则没认出来"), "#b45309")
        else:
            self._show("认不了：%s\n（这类也不交给模型，改一下写法再试）"
                       % (d.get("reason") or ""), CLR_BAD)

    def do_record(self):
        t = self._text()
        if not t:
            self._show("先粘一段文本", CLR_MUTED)
            return
        acc = (self.v_acc.get() or "").strip() or self.account
        if not acc:
            self._show("没有可记的账号，先在控制台里选一个账号", CLR_BAD)
            return
        chat = (self.v_chat.get() or "").strip()
        d = api_post("/api/quick_bet", {"text": t, "account": acc, "chat": chat})
        if not d.get("ok"):
            self._show("没记上：%s" % (d.get("reason") or d.get("error") or "未知原因"), CLR_BAD)
            return
        mkname = "香港" if d.get("market") == "HK" else "澳门"
        msg = ("已记账（%s）：%s\n对象 %s · 账号 %s · %s 个号 · 合计 %s 元"
               % (mkname, d.get("detail") or "", chat or "手工记账",
                  d.get("account") or self.account,
                  d.get("numbers") or 0, fmt_money(d.get("total"))))
        if d.get("archived_issue"):
            # 换期会把上一期清零，不说明白就跟丢账一样
            msg += ("\n已换期：上一期 %s 归档，金额 %s 元（现在的数从本期重新算）"
                    % (d.get("archived_issue"), fmt_money(d.get("archived_total"))))
        self._show(msg, CLR_OK)
        if self.on_done:
            try:
                self.on_done()
            except Exception:
                pass
        if self.v_loop.get():
            # 连续记账：留在窗口里，清空输入，焦点回到文本框
            self.txt.delete("1.0", "end")
            self.txt.focus_set()
        else:
            # 记完一笔就关 —— 给个短延时，先让人看见记成了什么
            self.after(1200, self._close)

    def _close(self):
        """延时关窗时窗口可能已经被 Esc 关掉了，别在这儿抛异常。"""
        try:
            self.destroy()
        except Exception:
            pass


class ManualBetDialog(tk.Toplevel):
    """手工补录：点号码 / 生肖 / 玩法，填一个金额，回车成注。

    口径只有一条：**每个被选中的号码各记这个金额**，不摊开、不折算。
    生肖点一下＝选它名下那几个号；「大」「单」这类点一下＝选该集合的号。
    多个生肖取并集（牛马＝9 号），多个玩法取交集（大+单＝13 号），
    生肖与玩法之间也取交集（牛马再点大＝23/25/37/49 里的 5 个）。
    点具体号码时，生肖/玩法条件让位给号码 —— 否则"这注到底算哪个"说不清。

    记账不另起一套：把清单拼成客户原话（"牛各100；大单各50"）交给
    /api/quick_bet，和微信推送走同一条落账链路 —— 期号归档、港澳分盘、
    流水、撤销都自动继承，手记和推送不会算出两个数。
    拼完先用规则解析器自检，号码集合与每号金额都对得上才提交；
    对不上就退回「号码 + 金额」的写法，绝不把一笔记成别的。
    """

    COLORS = {"红波": "#c0392b", "蓝波": "#1f6feb", "绿波": "#1a7f37"}
    PICK_BG = "#111827"
    PICK_EDGE = "#ffffff"
    LIMIT_CONFIRM = 1000.0          # 合计超过这个数，记账前再问一次

    QUICK_AMTS = ["10", "20", "50", "100", "200", "500"]

    # (组名, [(按钮文字, 拼进原文的写法)])
    # 玩法按钮表跟网页版共用一份（manual_plays），避免两边按钮不一样
    PLAY_GROUPS = manual_plays.GROUPS

    def __init__(self, master, mapping, grid, chats, account="", on_submit=None,
                 demo=False):
        super().__init__(master)
        self.title("手工记账")
        self.configure(bg=CLR_BG)
        self.transient(master)
        self.mapping = mapping or {}
        self.grid = list(grid or [])
        self.chats = list(chats or [])
        self.on_submit = on_submit
        self.account = account or ""

        self.notes = []                 # 已加入的注：text/label/nums/each/total
        self.sel_nums = set()           # 直接点的号
        self.sel_z = set()              # 生肖（并集）
        self.sel_p = set()              # 玩法（交集）
        self.zx_mode = "pt"             # 生肖注：pt 平特 / lx 连肖 / zh2 二中二 / zh3 三中三
        self.zx_split = "share"         # 生肖注：share 共（分摊） / each 各
        self.zx_len = 2                 # 连肖位数
        self.sel_zx = set()             # 生肖注选中的生肖（跟号码盘的生肖分开）
        self._pn = {}                   # 玩法写法 → 号码集合，缓存
        self._num_lb = {}
        self._num_bg = {}
        self._z_lb = {}
        self._p_lb = {}
        self._p_disp = {}
        self._mk_lb = {}
        self._fw = []                   # [(widget, 字号档)]，缩放时统一改
        self._busy = False
        self._event = None
        self._scale = 1.0

        self.v_market = tk.StringVar(value="MO")     # 默认澳门
        self.v_chat = tk.StringVar()
        self.v_amt = tk.StringVar()
        self.v_loop = tk.BooleanVar(value=False)

        self._accounts = self._load_accounts()
        if self.account not in self._accounts:
            self.account = self._accounts[0] if self._accounts else ""
        self.v_acc = tk.StringVar(value=self.account)

        self._build()
        self.update_idletasks()
        # 内容多（43 个玩法 + 49 个号），窗口给宽一点高一点
        w, h = 1280, 1010
        try:
            x = master.winfo_rootx() + max(0, (master.winfo_width() - w) // 2)
            y = master.winfo_rooty() + max(0, (master.winfo_height() - h) // 2)
        except Exception:
            x = y = 0
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(w, sw - 60)
        h = min(h, sh - 90)
        x = max(0, min(x, sw - w - 20))
        y = max(0, min(y, sh - h - 60))
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.minsize(1120, 860)
        self.update()
        self._repaint()
        try:
            self.grab_set()
        except Exception:
            pass
        if demo:
            self.after(300, self._demo)

    def _load_accounts(self):
        """账号下拉：只列真接过账的号，免得选到一个不存在的账号。

        主窗口停在「全部账号（合计）」时传进来是空字符串；这时不能拿
        "default" 兜底 —— 那是个测试号，一记就又冒出来了。改成选真有账的那个。
        """
        d = api_get("/api/accounts")
        items = d.get("accounts") or []
        out = [str(a.get("account") or "") for a in items if a.get("account")]
        if not out:
            try:
                out = msb_view.all_accounts()
            except Exception:
                out = []
        if not out and self.account:
            out = [self.account]
        if not self.account and out:
            tops = {str(a.get("account")): float(a.get("grand_total") or 0)
                    for a in items}
            out = sorted(out, key=lambda a: -tops.get(a, 0.0))
        return out

    # ---------- 字号 ----------
    def _font(self, kind="body"):
        size, weight = {
            "title": (16, "bold"), "body": (11, ""),
            "small": (10, ""), "tiny": (9, ""),
        }.get(kind, (11, ""))
        size = max(8, int(round(size * self._scale)))
        fam = "Microsoft YaHei UI"
        return (fam, size, weight) if weight else (fam, size)

    def _num_size(self):
        h = 0
        try:
            h = self.board.winfo_height()
        except Exception:
            pass
        n = max(1, len(self.grid))
        if h < 40:
            return 14
        return int(max(10, min(20, (h / float(n)) * 0.52)))

    def _num_font(self):
        return ("Consolas", self._num_size(), "bold")

    def _lbl(self, parent, text="", kind="body", **kw):
        lb = tk.Label(parent, text=text, font=self._font(kind), **kw)
        self._fw.append((lb, kind))
        return lb

    def _on_conf(self, e=None):
        if e is not None and e.widget is not self:
            return
        w = max(1, self.winfo_width())
        s = max(0.90, min(1.45, w / 1060.0))
        if abs(s - self._scale) < 0.02:
            return
        self._scale = s
        self._restyle()

    def _restyle(self):
        for w, kind in self._fw:
            try:
                w.configure(font=self._font(kind))
            except Exception:
                pass
        nf = self._num_font()
        for lb in self._num_lb.values():
            try:
                lb.configure(font=nf)
            except Exception:
                pass
        try:
            self.style.configure("Man.Treeview", font=self._font("small"),
                                 rowheight=int(22 * self._scale))
            self.style.configure("Man.Treeview.Heading", font=self._font("small"))
        except Exception:
            pass

    # ---------- 界面 ----------
    def _build(self):
        wrap = tk.Frame(self, bg=CLR_BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(12, 8))
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(2, weight=3)
        wrap.grid_rowconfigure(3, weight=2)

        # ---- 顶部：标题 / 盘口 / 账号 / 对象 ----
        top = tk.Frame(wrap, bg=CLR_CARD, highlightbackground=CLR_LINE,
                       highlightthickness=1)
        top.grid(row=0, column=0, sticky="ew")
        t = tk.Frame(top, bg=CLR_CARD)
        t.pack(fill="x", padx=12, pady=9)

        self._lbl(t, "手工记账", "title", bg=CLR_CARD, fg="#111827").pack(side="left")

        seg = tk.Frame(t, bg="#eef0f3")
        seg.pack(side="left", padx=(20, 0))
        for mk, name in (("MO", "澳门"), ("HK", "香港")):
            b = tk.Label(seg, text=name, font=self._font("small"), bg="#eef0f3",
                         fg="#374151", padx=12, pady=3, cursor="hand2")
            b.pack(side="left", padx=1, pady=1)
            b.bind("<Button-1>", lambda e, m=mk: self._pick_market(m))
            self._mk_lb[mk] = b
            self._fw.append((b, "small"))

        self._lbl(t, "挂在谁名下", "small", bg=CLR_CARD, fg=CLR_MUTED
                  ).pack(side="right")
        self.cb_chat = ttk.Combobox(t, textvariable=self.v_chat,
                                    font=self._font("small"), values=self.chats,
                                    width=16)
        self.cb_chat.pack(side="right", padx=(6, 14))
        self.cb_chat.bind("<KeyRelease>", lambda e: self._refresh_foot())
        self.cb_chat.bind("<<ComboboxSelected>>", lambda e: self._refresh_foot())
        self._fw.append((self.cb_chat, "small"))

        self._lbl(t, "记入账号", "small", bg=CLR_CARD, fg=CLR_MUTED
                  ).pack(side="right")
        cb_acc = ttk.Combobox(t, textvariable=self.v_acc, state="readonly",
                              font=self._font("small"), values=self._accounts,
                              width=16)
        cb_acc.pack(side="right", padx=6)
        self._fw.append((cb_acc, "small"))

        # ---- 条件回显 ----
        self.lb_hint = self._lbl(wrap, "", "small", bg=CLR_BG, fg=CLR_MUTED,
                                 anchor="w", justify="left")
        self.lb_hint.grid(row=1, column=0, sticky="ew", pady=(8, 6))

        # ---- 主体：左号码盘 / 右操作区 ----
        body = tk.Frame(wrap, bg=CLR_BG)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        bcard = tk.Frame(body, bg=CLR_CARD, highlightbackground=CLR_LINE,
                         highlightthickness=1)
        bcard.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.board = tk.Frame(bcard, bg=CLR_CARD)
        self.board.pack(fill="both", expand=True, padx=12, pady=10)
        for c in range(6):
            self.board.grid_columnconfigure(c, weight=(1 if c else 0))
        for r, rowd in enumerate(self.grid):
            self.board.grid_rowconfigure(r, weight=1)
            zlb = self._lbl(self.board, rowd["zodiac"], "tiny", bg=CLR_CARD,
                            fg="#374151", width=2, anchor="w")
            zlb.grid(row=r, column=0, sticky="w")
            for c, cell in enumerate(rowd["cells"], 1):
                n = cell["num"]
                color = self.COLORS.get(cell.get("color") or "", "#6b7280")
                self._num_bg[n] = color
                lb = tk.Label(self.board, text=n, bg=color, fg="white",
                              font=self._num_font(), cursor="hand2",
                              highlightthickness=3,
                              highlightbackground=color, padx=1, pady=1)
                lb.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
                lb.bind("<Button-1>", lambda e, x=n: self._pick_num(x))
                self._num_lb[n] = lb

        # 右栏：选法说明固定在上，条件区竖滚，金额行固定在底部
        rcard = tk.Frame(body, bg=CLR_CARD, highlightbackground=CLR_LINE,
                         highlightthickness=1)
        rcard.grid(row=0, column=1, sticky="ns", padx=(0, 0))
        rcard.grid_rowconfigure(1, weight=1)
        rcard.grid_columnconfigure(0, weight=1)

        # 说明行右边挂一个跳转：右栏内容比可视区高，生肖注在下面 500px 处，
        # 不点这一下很多人以为没有这块（实际有，要滚下去）。
        rulebar = tk.Frame(rcard, bg=CLR_CARD)
        rulebar.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))
        self.lb_rule = tk.Label(
            rulebar,
            text="生肖多选＝并集（牛+马 共 9 个号）｜玩法多选＝交集（大+单 共 13 个号）"
                 "｜生肖和玩法一起选，两边再取交集",
            font=self._font("tiny"), bg=CLR_CARD, fg="#6b7280",
            justify="left", anchor="w", wraplength=352)
        self.lb_rule.pack(side="left", fill="x", expand=True)
        self._fw.append((self.lb_rule, "tiny"))
        zjump = tk.Label(rulebar, text="生肖注 ↓", font=self._font("tiny"),
                         bg="#dbeafe", fg="#1e40af", padx=6, pady=2,
                         cursor="hand2")
        zjump.pack(side="right", padx=(6, 0), anchor="n")
        zjump.bind("<Button-1>", lambda e: self._jump_zx())
        self._fw.append((zjump, "tiny"))

        mid = tk.Frame(rcard, bg=CLR_CARD)
        mid.grid(row=1, column=0, sticky="nsew")
        self.canvas = tk.Canvas(mid, bg=CLR_CARD, highlightthickness=0, width=452)
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.ops = tk.Frame(self.canvas, bg=CLR_CARD)
        self._cwin = self.canvas.create_window((0, 0), window=self.ops, anchor="nw")
        self.ops.bind("<Configure>",
                      lambda e: self.canvas.configure(
                          scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._cwin, width=e.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.ops.bind("<MouseWheel>", self._wheel)

        # 金额：快捷额度、输入框、加入 —— 三个一行，钉在右栏底部不跟着滚
        amtbar = tk.Frame(rcard, bg=CLR_CARD)
        amtbar.grid(row=2, column=0, sticky="ew")
        tk.Frame(amtbar, bg=CLR_LINE, height=1).pack(fill="x")
        row = tk.Frame(amtbar, bg=CLR_CARD)
        row.pack(fill="x", padx=10, pady=8)
        self._lbl(row, "每号", "small", bg=CLR_CARD, fg=CLR_MUTED
                  ).pack(side="left")
        for a in self.QUICK_AMTS:
            b = tk.Label(row, text=a, font=self._font("small"), bg="#eef0f3",
                         fg="#374151", padx=5, pady=3, cursor="hand2")
            b.pack(side="left", padx=(4, 0))
            b.bind("<Button-1>", lambda e, v=a: self._quick_amt(v))
            self._fw.append((b, "small"))

        self.btn_add = tk.Button(row, text="加入", font=self._font("body"),
                                 bg=CLR_BLUE2, fg="white", relief="flat", bd=0,
                                 width=5, cursor="hand2", command=self._add)
        self.btn_add.pack(side="right", padx=(6, 0))
        self.en_amt = tk.Entry(row, textvariable=self.v_amt, font=self._font("body"),
                               width=7, relief="flat", bg="#fbfcfd", fg="#111827",
                               highlightbackground=CLR_LINE, highlightthickness=1,
                               justify="right", insertbackground="#111827")
        self.en_amt.pack(side="right", padx=(6, 0), ipady=3)
        self.en_amt.bind("<Return>", lambda e: self._add())
        self._fw.append((self.en_amt, "body"))
        self._fw.append((self.btn_add, "body"))

        # 生肖
        self._lbl(self.ops, "生肖（可多选，取并集）", "small", bg=CLR_CARD,
                  fg=CLR_MUTED).pack(anchor="w", padx=12, pady=(10, 2))
        zg = tk.Frame(self.ops, bg=CLR_CARD)
        zg.pack(fill="x", padx=12)
        for i, rowd in enumerate(self.grid):
            z = rowd["zodiac"]
            r, c = divmod(i, 6)
            b = tk.Label(zg, text=z, font=self._font("small"), bg="#eef0f3",
                         fg="#374151", padx=6, pady=3, cursor="hand2")
            b.grid(row=r, column=c, padx=2, pady=2, sticky="ew")
            b.bind("<Button-1>", lambda e, x=z: self._pick_z(x))
            self._z_lb[z] = b
            self._fw.append((b, "small"))
        for c in range(6):
            zg.grid_columnconfigure(c, weight=1)

        # 玩法：单双 / 大小 / 波色 … 一组一行
        for gname, items in self.PLAY_GROUPS:
            self._lbl(self.ops, gname + "（可多选，取交集）", "tiny", bg=CLR_CARD,
                      fg="#9aa3af").pack(anchor="w", padx=12, pady=(9, 1))
            gf = tk.Frame(self.ops, bg=CLR_CARD)
            gf.pack(fill="x", padx=12)
            cols = 7 if len(items) <= 7 else 6
            for i, (disp, code) in enumerate(items):
                r, c = divmod(i, cols)
                b = tk.Label(gf, text=disp, font=self._font("small"), bg="#f7f8fa",
                             fg="#374151", padx=3, pady=3, cursor="hand2")
                b.grid(row=r, column=c, padx=1, pady=1, sticky="ew")
                b.bind("<Button-1>", lambda e, k=code: self._pick_p(k))
                self._p_lb[code] = b
                self._p_disp[code] = disp
                self._fw.append((b, "small"))
            for c in range(cols):
                gf.grid_columnconfigure(c, weight=1)

        # ---- 生肖注：平特 / 连肖 / 组合注 ----
        # 先做界面。记账功能等口径确认后再接，现在加入清单只做核对。
        tk.Frame(self.ops, bg=CLR_LINE, height=1).pack(fill="x", padx=12, pady=(12, 0))
        self._lbl(self.ops, "生肖注（平特 / 连肖 / 组合注）", "small",
                  bg=CLR_CARD, fg=CLR_MUTED
                  ).pack(anchor="w", padx=12, pady=(8, 0))
        self._lbl(self.ops, "整肖的钱不摊进上面的号码盘", "tiny",
                  bg=CLR_CARD, fg="#9aa3af").pack(anchor="w", padx=12, pady=(0, 4))

        zxrow = tk.Frame(self.ops, bg=CLR_CARD)
        zxrow.pack(fill="x", padx=12)
        self._zx_mode_lb = {}
        for code, disp in (("pt", "平特"), ("lx", "连肖"),
                           ("zh2", "二中二"), ("zh3", "三中三"),
                           ("zh4", "四中四"), ("zh5", "五中五"),
                           ("zh6", "六中六")):
            b = tk.Label(zxrow, text=disp, font=self._font("small"), bg="#eef0f3",
                         fg="#374151", padx=6, pady=3, cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda e, k=code: self._pick_zx_mode(k))
            self._zx_mode_lb[code] = b
            self._fw.append((b, "small"))

        self.zx_len_row = tk.Frame(self.ops, bg=CLR_CARD)
        self._zx_len_lb = {}
        self._lbl(self.zx_len_row, "连肖位数", "tiny", bg=CLR_CARD, fg="#9aa3af").pack(side="left")
        for n in (2, 3, 4, 5):
            b = tk.Label(self.zx_len_row, text="%d连" % n, font=self._font("tiny"),
                         bg="#eef0f3", fg="#374151", padx=5, pady=2, cursor="hand2")
            b.pack(side="left", padx=(4, 0))
            b.bind("<Button-1>", lambda e, x=n: self._pick_zx_len(x))
            self._zx_len_lb[n] = b
            self._fw.append((b, "tiny"))

        sprow = tk.Frame(self.ops, bg=CLR_CARD)
        sprow.pack(fill="x", padx=12, pady=(6, 0))
        self._zx_sp_lb = {}
        for code, disp in (("share", "共（分摊到每个肖）"), ("each", "各（每个肖各这么多）")):
            b = tk.Label(sprow, text=disp, font=self._font("tiny"), bg="#eef0f3",
                         fg="#374151", padx=6, pady=2, cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda e, k=code: self._pick_zx_split(k))
            self._zx_sp_lb[code] = b
            self._fw.append((b, "tiny"))

        zxg = tk.Frame(self.ops, bg=CLR_CARD)
        zxg.pack(fill="x", padx=12, pady=(6, 0))
        self._zxz_lb = {}
        for i, rowd in enumerate(self.grid):
            z = rowd["zodiac"]
            r, c = divmod(i, 6)
            b = tk.Label(zxg, text=z, font=self._font("small"), bg="#eef0f3",
                         fg="#374151", padx=6, pady=3, cursor="hand2")
            b.grid(row=r, column=c, padx=2, pady=2, sticky="ew")
            b.bind("<Button-1>", lambda e, x=z: self._pick_zx(x))
            self._zxz_lb[z] = b
            self._fw.append((b, "small"))
        for c in range(6):
            zxg.grid_columnconfigure(c, weight=1)

        self.lb_zx = self._lbl(self.ops, "", "tiny", bg=CLR_CARD, fg="#6b7280",
                               anchor="w", justify="left", wraplength=430)
        self.lb_zx.pack(fill="x", padx=12, pady=(6, 0))

        zxbar = tk.Frame(self.ops, bg=CLR_CARD)
        zxbar.pack(fill="x", padx=12, pady=(4, 8))
        self.btn_zx = tk.Button(zxbar, text="加入生肖注", font=self._font("small"),
                                bg=CLR_BLUE2, fg="white", relief="flat", bd=0,
                                width=9, cursor="hand2", command=self._zx_add)
        self.btn_zx.pack(side="left")
        self._fw.append((self.btn_zx, "small"))

        tk.Frame(self.ops, bg=CLR_CARD, height=10).pack(fill="x")

        # ---- 清单 ----
        ncard = tk.Frame(wrap, bg=CLR_CARD, highlightbackground=CLR_LINE,
                         highlightthickness=1)
        ncard.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        head = tk.Frame(ncard, bg=CLR_CARD)
        head.pack(fill="x", padx=12, pady=(8, 2))
        self._lbl(head, "本次要记的", "small", bg=CLR_CARD, fg=CLR_MUTED
                  ).pack(side="left")
        btn_drop = tk.Button(head, text="删掉选中", font=self._font("small"),
                             bg="#eef0f3", fg="#374151", relief="flat", bd=0,
                             width=8, cursor="hand2", command=self._drop)
        btn_drop.pack(side="right")
        btn_clear = tk.Button(head, text="清空", font=self._font("small"),
                              bg="#eef0f3", fg="#374151", relief="flat", bd=0,
                              width=6, cursor="hand2", command=self._clear)
        btn_clear.pack(side="right", padx=(0, 6))
        self._fw.append((btn_drop, "small"))
        self._fw.append((btn_clear, "small"))

        tv_wrap = tk.Frame(ncard, bg=CLR_CARD)
        tv_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.style = ttk.Style(self)
        try:
            self.style.configure("Man.Treeview", font=self._font("small"),
                                 rowheight=int(22 * self._scale))
            self.style.configure("Man.Treeview.Heading", font=self._font("small"))
        except Exception:
            pass
        self.tv = ttk.Treeview(tv_wrap, columns=("desc", "each", "sub"),
                               show="headings", style="Man.Treeview", height=6)
        self.tv.heading("desc", text="记什么")
        self.tv.heading("each", text="每号")
        self.tv.heading("sub", text="小计")
        self.tv.column("desc", width=560, anchor="w")
        self.tv.column("each", width=90, anchor="e")
        self.tv.column("sub", width=110, anchor="e")
        tsb = ttk.Scrollbar(tv_wrap, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.tv.bind("<Delete>", lambda e: self._drop())

        # ---- 底栏 ----
        foot = tk.Frame(wrap, bg=CLR_BG)
        foot.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.lb_total = self._lbl(foot, "合计 0 元", "title", bg=CLR_BG, fg=CLR_RED)
        self.lb_total.pack(side="left")

        self.btn_ok = tk.Button(foot, text="记账", font=self._font("body"),
                                bg=CLR_BLUE, fg="white", relief="flat", bd=0,
                                width=9, cursor="hand2", command=self._submit)
        self.btn_ok.pack(side="right")
        btn_cancel = tk.Button(foot, text="取消", font=self._font("body"),
                               bg="#eef0f3", fg="#374151", relief="flat", bd=0,
                               width=7, cursor="hand2", command=self._close)
        btn_cancel.pack(side="right", padx=(0, 8))
        chk = tk.Checkbutton(foot, text="连续记账", variable=self.v_loop,
                             font=self._font("small"), bg=CLR_BG, fg=CLR_MUTED,
                             activebackground=CLR_BG, selectcolor=CLR_BG,
                             bd=0, highlightthickness=0, cursor="hand2")
        chk.pack(side="right", padx=(0, 20))
        self._fw.append((self.btn_ok, "body"))
        self._fw.append((btn_cancel, "body"))
        self._fw.append((chk, "small"))

        self.lb_note = self._lbl(wrap, "", "small", bg=CLR_BG, fg=CLR_BAD, anchor="w")
        self.lb_note.grid(row=5, column=0, sticky="ew", pady=(4, 0))

        self.bind("<Escape>", lambda e: self._close())
        self.bind("<Control-Return>", lambda e: self._submit())
        self.bind("<Configure>", self._on_conf)
        self._bind_wheel(self.ops)
        # 金额一改，生肖注的预览跟着走
        try:
            self.v_amt.trace_add("write", lambda *a: self._zx_prev())
        except Exception:
            pass
        self._zx_prev()

    def _bind_wheel(self, w):
        """滚轮事件只管给 Canvas，鼠标在生肖/玩法按钮上也要能滚。"""
        try:
            w.bind("<MouseWheel>", self._wheel)
            for c in w.winfo_children():
                self._bind_wheel(c)
        except Exception:
            pass

    # ---------- 点选 ----------
    def _wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-1 * (e.delta / 120.0)), "units")
        except Exception:
            pass

    def _pick_market(self, mk):
        self.v_market.set(mk)
        self._repaint()

    def _pick_num(self, n):
        self.sel_z = set()
        self.sel_p = set()
        if n in self.sel_nums:
            self.sel_nums.discard(n)
        else:
            self.sel_nums.add(n)
        self._repaint()

    def _pick_z(self, z):
        self.sel_nums = set()
        if z in self.sel_z:
            self.sel_z.discard(z)
        else:
            self.sel_z.add(z)
        self._repaint()

    def _pick_p(self, k):
        self.sel_nums = set()
        if k in self.sel_p:
            self.sel_p.discard(k)
        else:
            self.sel_p.add(k)
        self._repaint()

    # ---------- 生肖注（平特 / 连肖 / 二中二 / 三中三） ----------
    def _jump_zx(self):
        """滚到生肖注面板。右栏内容比可视区高，它埋在下面 500px 处。"""
        try:
            self.update_idletasks()
            sr = str(self.canvas.cget("scrollregion") or "").split()
            total = float(sr[3]) if len(sr) == 4 else 0.0
            if total <= 1:
                return
            y = self._zx_mode_lb["pt"].winfo_rooty() - self.ops.winfo_rooty() - 14
            self.canvas.yview_moveto(max(0.0, min(total, float(y))) / total)
        except Exception:
            pass

    def _pick_zx_mode(self, k):
        self.zx_mode = k
        if k == "lx":
            self.zx_len_row.pack(fill="x", padx=12, pady=(6, 0))
        else:
            self.zx_len_row.pack_forget()
        self._zx_prev()

    def _pick_zx_len(self, n):
        self.zx_len = int(n)
        self._zx_prev()

    def _pick_zx_split(self, k):
        self.zx_split = k
        self._zx_prev()

    def _pick_zx(self, z):
        if z in self.sel_zx:
            self.sel_zx.discard(z)
        else:
            self.sel_zx.add(z)
        self._zx_prev()

    def _zx_row_order(self):
        """生肖按钮的排列顺序（跟号码盘的行序一致，方便对着看）。"""
        return [rowd["zodiac"] for rowd in self.grid] if self.grid else []

    def _zx_name(self):
        if self.zx_mode in ZH_NAME:
            return ZH_NAME[self.zx_mode]
        return "平特" if self.zx_mode == "pt" else "%d连肖" % int(self.zx_len or 2)

    def _zx_set(self):
        """这一注涉及哪几个肖。连肖只点一个肖时，按传统生肖序往后补。

        排列用传统序（鼠牛虎兔龙蛇马羊猴鸡狗猪），跟客户写 "鼠牛" 的习惯一致，
        不跟号码盘的行序（马蛇龙…）走，否则会显示成 "牛+鼠"。
        """
        trad = self._order() or self._zx_row_order()
        picked = [z for z in trad if z in self.sel_zx]
        for z in self._zx_row_order():
            if z in self.sel_zx and z not in picked:
                picked.append(z)
        if self.zx_mode == "lx" and picked:
            start = picked[0]
            n = max(2, int(self.zx_len or 2))
            out = []
            if start in trad:
                i = trad.index(start)
                out = [trad[(i + k) % len(trad)] for k in range(n)]
            return out or picked[:n]
        return picked

    def _zx_calc(self):
        """返回 (生肖列表, 每肖金额, 总额)。组合注每肖金额为空，只给整注。"""
        zs = self._zx_set()
        amt = self._read_amt()
        if not zs or amt is None:
            return None, {}, 0.0
        if self.zx_mode in ZH_NEED:
            need = ZH_NEED[self.zx_mode]
            if len(zs) < need:
                return None, {}, 0.0
            zs = zs[:need]
            # 组合注客户说「一般不会有各」，原文一律写整笔（鼠牛虎三中三300）。
            # 界面上若点了「各」，就折算成整笔金额，别写「各」。
            total = round(amt * len(zs), 2) if self.zx_split == "each" else round(amt, 2)
            return zs, {}, total
        n = len(zs)
        if self.zx_split == "each":
            return zs, {z: amt for z in zs}, round(amt * n, 2)
        # 共：抹到分，余数给第一个肖
        each = round(float(int(amt / n * 100)) / 100.0, 2)
        per = {z: each for z in zs}
        per[zs[0]] = round(amt - each * (n - 1), 2)
        return zs, per, round(sum(per.values()), 2)

    def _zx_prev(self):
        try:
            for k, b in self._zx_mode_lb.items():
                on = (k == self.zx_mode)
                b.configure(bg="#dbeafe" if on else "#eef0f3",
                            fg="#1e40af" if on else "#374151")
            for n2, b in self._zx_len_lb.items():
                on = (n2 == self.zx_len)
                b.configure(bg="#dbeafe" if on else "#eef0f3",
                            fg="#1e40af" if on else "#374151")
            for k, b in self._zx_sp_lb.items():
                on = (k == self.zx_split)
                b.configure(bg="#dbeafe" if on else "#eef0f3",
                            fg="#1e40af" if on else "#374151")
            for z, b in self._zxz_lb.items():
                on = (z in self.sel_zx)
                b.configure(bg="#fde68a" if on else "#eef0f3",
                            fg="#92400e" if on else "#374151")
        except Exception:
            pass
        if not hasattr(self, "lb_zx"):
            return
        zs, per, total = self._zx_calc()
        if not zs:
            if self.zx_mode in ZH_NEED:
                msg = "%s 要选 %d 个肖" % (self._zx_name(), ZH_NEED[self.zx_mode])
            elif self.zx_mode == "lx":
                msg = "点一个生肖，按传统生肖序往后连 %d 个" % int(self.zx_len or 2)
            else:
                msg = "点生肖；平特可多选（共＝分摊，各＝每肖各这么多）"
            try:
                self.lb_zx.configure(text=msg)
            except Exception:
                pass
            return
        txt = "%s %s" % (self._zx_name(), "+".join(zs))
        if total <= 0:
            txt += " → 填金额"
        elif per:
            txt += " → 每肖 %s，共 %s" % (self._amt_str(per[zs[0]]), self._amt_str(total))
        elif self.zx_split == "each" and self.zx_mode not in ZH_NEED:
            txt += " → 各 %s × %d 肖 = %s（6 个平特号同时有 %s）" % (
                self._amt_str(self._read_amt()), len(zs), self._amt_str(total),
                "、".join(zs))
        elif self.zx_mode in ZH_NEED:
            txt += " → 整注 %s（%s，%d 个肖）" % (
                self._amt_str(total), self._zx_name(), len(zs))
        else:
            txt += " → 整注 %s（6 个平特号同时有 %s）" % (
                self._amt_str(total), "、".join(zs))
        try:
            self.lb_zx.configure(text=txt)
        except Exception:
            pass

    def _zx_text(self, zs, total, per):
        """把生肖注写成客户本来会写的那句原文。

        服务端按同一套 bet_rules 再解析一遍，所以这里不能自创语法：
          平特鼠牛各50 / 平特鼠牛50 / 鼠牛2连肖100 / 鼠牛二中二100 / 鼠牛虎三中三200
        """
        all_sx = "".join(zs)
        total_txt = self._amt_str(total)
        if self.zx_mode in ZH_NAME:
            return "%s%s%s" % (all_sx, ZH_NAME[self.zx_mode], total_txt)
        if self.zx_mode == "lx":
            return "%s%d连肖%s" % (all_sx, max(2, len(zs)), total_txt)
        if self.zx_split == "each":
            return "平特%s各%s" % (all_sx, self._amt_str(per[zs[0]]))
        return "平特%s%s" % (all_sx, total_txt)

    def _zx_add(self):
        if self._busy:
            return
        zs, per, total = self._zx_calc()
        if not zs:
            return self._say("生肖注：先在下面选生肖、填金额")
        if total <= 0:
            return self._say("金额写个数字，比如 50")
        if per:
            vals = [round(per[z], 2) for z in zs]
            each_txt = ("每肖 %s" % self._amt_str(vals[0]) if len(set(vals)) == 1
                        else "分摊 " + "/".join(self._amt_str(per[z]) for z in zs))
        elif self.zx_split == "each" and self.zx_mode in ZH_NEED:
            # 组合注（二中二…六中六）客户明确「一般不会有各」，原文一律按整笔写，
            # 这里也只是提示它是整笔，不要再显示成"各 ×N"（会跟原文对不上）
            each_txt = "整注"
        else:
            each_txt = "整注"
        self.notes.append({
            "zx": True, "flat": True, "text": self._zx_text(zs, total, per),
            "label": "%s %s" % (self._zx_name(), "+".join(zs)),
            "nums": [], "each": (per[zs[0]] if per else total),
            "each_txt": each_txt, "total": round(total, 2),
            "zx_mode": self.zx_mode, "zodiacs": list(zs), "per": dict(per),
        })
        self.sel_zx = set()
        self.v_amt.set("")
        self._zx_prev()
        self._refresh_list()
        self._say("生肖注已进清单，记账时单独落一笔（不进号码盘）", CLR_OK)
        self.en_amt.focus_set()

    def _quick_amt(self, v):
        self.v_amt.set(v)
        self.en_amt.focus_set()
        self.en_amt.icursor("end")
        self._repaint()

    # ---------- 集合 ----------
    def _nums_of_play(self, code):
        """玩法写法 → 号码集合。借规则解析器算，界面和账本永远同一套。"""
        return manual_plays.nums_of_play(code, self.mapping, self._pn)

    def _order(self):
        """拼原文时生肖的书写顺序（鼠牛虎兔…），不是盘面上的行序。"""
        return manual_plays.zodiac_order(self.mapping)

    def _target(self):
        return manual_plays.target(self.mapping, self.sel_nums, self.sel_z,
                                   self.sel_p, self._pn)

    # ---------- 文案 ----------
    def _read_amt(self):
        raw = (self.v_amt.get() or "").strip().replace("，", "").replace(",", "")
        try:
            v = float(raw)
        except Exception:
            return None
        if v <= 0 or v > 1e7:
            return None
        return round(v, 2)

    @staticmethod
    def _amt_str(v):
        return ("%.2f" % float(v)).rstrip("0").rstrip(".")

    def _combo_name(self):
        """多玩法 → 一个能写进原文的组合名（大单 / 红单 / 红小）。"""
        return manual_plays.combo_name(self.sel_p)

    def _compose_text(self, tgt):
        """清单里的这一注，拼成原文用的话。拼不出来就退回号码。"""
        return manual_plays.compose_text(self.mapping, self.sel_nums, self.sel_z,
                                         self.sel_p, self._order(), tgt)

    def _verify(self, text, nums, amt):
        """拼出来的话，规则解析器得认成同一组号、同一个单价。"""
        s = "%s各%s" % (text, self._amt_str(amt))
        r = bet_rules.parse_local(s, self.mapping)
        if not r.get("ok"):
            return False
        bets = r.get("bets") or {}
        if set(bets.keys()) != set(nums):
            return False
        return all(abs(float(bets[n]) - amt) < 1e-6 for n in nums)

    def _label(self, tgt):
        return manual_plays.label_of(self.sel_nums, self.sel_z, self.sel_p,
                                     self._order(), tgt, self.mapping)

    def _hint_text(self, tgt):
        parts = []
        if self.sel_nums:
            parts.append("号码 " + " ".join(sorted(self.sel_nums)))
        else:
            if self.sel_z:
                order = [rowd["zodiac"] for rowd in self.grid]
                parts.append("+".join(z for z in order if z in self.sel_z))
            if self.sel_p:
                parts.append("∩".join(self._p_disp.get(k, k) for k in sorted(self.sel_p)))
        if not parts:
            return "点号码 / 生肖 / 玩法；填的金额＝每个号各多少"
        head = "条件：" + " · ".join(parts)
        if not tgt:
            return head + " → 没有共同的号，换个选法"
        nums = sorted(tgt)
        show = " ".join(nums) if len(nums) <= 14 else " ".join(nums[:14]) + " …"
        amt = self._read_amt()
        tail = ("每号 %s · 合计 %s" % (self._amt_str(amt),
                                     fmt_money(amt * len(nums)))) if amt else "每号 —"
        return "%s → %d 个号：%s · %s" % (head, len(nums), show, tail)

    # ---------- 重绘 ----------
    def _repaint(self):
        tgt = self._target()
        for n, lb in self._num_lb.items():
            on = n in tgt
            try:
                lb.configure(bg=(self.PICK_BG if on else self._num_bg.get(n, "#6b7280")),
                             fg="white",
                             highlightbackground=(self.PICK_EDGE if on
                                                 else self._num_bg.get(n, "#6b7280")))
            except Exception:
                pass
        for z, b in self._z_lb.items():
            on = z in self.sel_z
            try:
                b.configure(bg="#fde68a" if on else "#eef0f3",
                            fg="#92400e" if on else "#374151")
            except Exception:
                pass
        for k, b in self._p_lb.items():
            on = k in self.sel_p
            try:
                b.configure(bg="#dbeafe" if on else "#f7f8fa",
                            fg="#1e40af" if on else "#374151")
            except Exception:
                pass
        for mk, b in self._mk_lb.items():
            on = (self.v_market.get() == mk)
            try:
                b.configure(bg=CLR_BLUE if on else "#eef0f3",
                            fg="white" if on else "#374151")
            except Exception:
                pass
        try:
            self.lb_hint.configure(text=self._hint_text(tgt),
                                   fg=(CLR_MUTED if tgt else "#b45309"))
        except Exception:
            pass
        self._refresh_foot()

    def _refresh_list(self):
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        for i, n in enumerate(self.notes):
            each = n.get("each_txt") or self._amt_str(n["each"])
            self.tv.insert("", "end", iid=str(i), values=(
                "%d. %s" % (i + 1, n["label"]), each, fmt_money(n["total"])))
        total = sum(n["total"] for n in self.notes)
        cnt = sum(len(n["nums"]) for n in self.notes)
        zx_n = sum(1 for n in self.notes if n.get("zx"))
        txt = "合计 %s 元 / %d 个号 / %d 注" % (
            fmt_money(total), cnt, len(self.notes))
        if zx_n:
            txt += "（含生肖注 %d 注）" % zx_n
        self.lb_total.configure(text=txt)
        self._refresh_foot()

    def _refresh_foot(self):
        chat = (self.v_chat.get() or "").strip()
        # 只按「有没有清单 / 忙不忙」决定能不能点。缺对象时不灰按钮，
        # 让用户点了能看到「填一下挂在谁名下」，否则按钮点不动像是坏了。
        ok = bool(self.notes) and not self._busy
        try:
            self.btn_ok.configure(state="normal" if ok else "disabled",
                                  bg=CLR_BLUE if ok else "#9bb4dd")
        except Exception:
            pass
        if self.notes and not chat:
            self._chat_warn = True
            try:
                if self._event:
                    self.after_cancel(self._event)
                    self._event = None
                self.lb_note.configure(text="还差一步：填「挂在谁名下」再点记账", fg=CLR_BAD)
            except Exception:
                pass
        elif getattr(self, "_chat_warn", False):
            self._chat_warn = False
            try:
                # 只清掉自己那句提醒，别把「已记账…」之类的消息抹掉
                if "挂在谁名下" in (self.lb_note.cget("text") or ""):
                    self.lb_note.configure(text="")
            except Exception:
                pass

    def _say(self, msg, color=CLR_BAD):
        try:
            self.lb_note.configure(text=msg, fg=color)
        except Exception:
            return
        if self._event:
            try:
                self.after_cancel(self._event)
            except Exception:
                pass
        if msg and color != CLR_OK:
            self._event = self.after(3500, lambda: self.lb_note.configure(text=""))
        else:
            self._event = None

    # ---------- 加入 / 删除 ----------
    def _add(self):
        if self._busy:
            return
        amt = self._read_amt()
        if amt is None:
            return self._say("金额写个数字，比如 50")
        tgt = self._target()
        if not tgt:
            return self._say("这组条件是空的，先选号码或换个玩法")
        text = self._compose_text(tgt)
        if not self._verify(text, tgt, amt):
            text = " ".join(sorted(tgt))
            if not self._verify(text, tgt, amt):
                return self._say("这组号拼不出规则能懂的话，换个选法再试")
        self.notes.append({
            "text": text, "label": self._label(tgt), "nums": sorted(tgt),
            "each": amt, "total": round(amt * len(tgt), 2),
        })
        self.sel_nums = set()
        self.sel_z = set()
        self.sel_p = set()
        self.v_amt.set("")
        self._say("")
        self._refresh_list()
        self._repaint()
        self.en_amt.focus_set()

    def _drop(self):
        sel = self.tv.selection()
        if not sel:
            return self._say("先在清单里点一行")
        try:
            idx = int(sel[0])
        except Exception:
            return
        if 0 <= idx < len(self.notes):
            self.notes.pop(idx)
        self._refresh_list()

    def _clear(self):
        self.notes = []
        self._refresh_list()

    # ---------- 记账 ----------
    def _verify_join(self, joined):
        """整句拼一起后，解析结果得等于清单的合并结果。"""
        return manual_plays.verify_join(self.mapping, self.notes, joined)

    def _submit(self):
        if self._busy or not self.notes:
            return
        chat = (self.v_chat.get() or "").strip()
        if not chat:
            self._say("填一下挂在谁名下")
            self.cb_chat.focus_set()
            return
        total = sum(n["total"] for n in self.notes)
        if total > self.LIMIT_CONFIRM:
            if not messagebox.askyesno(
                    "确认金额",
                    "这次要记 %s 元（%d 注，%d 个号），\n确认记吗？"
                    % (fmt_money(total), len(self.notes),
                       sum(len(n["nums"]) for n in self.notes)),
                    parent=self):
                return
        mk = self.v_market.get()
        acc = (self.v_acc.get() or "").strip() or self.account
        if not acc:
            self._say("没有可记的账号，先在控制台里选一个账号", CLR_BAD)
            return
        # 生肖注不进号码盘，每注是一条完整原文，单独落一笔；
        # 号码注还能拼成一句交给服务端，拼不通才逐注记。
        flat_notes = [n for n in self.notes if n.get("zx")]
        normal = [n for n in self.notes if not n.get("zx")]

        self._busy = True
        res = None
        fails = []
        done_numbers = 0
        done_total = 0.0
        detail = []
        try:
            for n in flat_notes:
                r = self.on_submit({"text": n["text"], "chat": chat,
                                    "account": acc, "market": mk}) \
                    if self.on_submit else {"ok": False, "error": "没有提交入口"}
                if not r.get("ok"):
                    fails.append("%s：%s" % (n["label"],
                                            r.get("error") or r.get("reason") or "失败"))
                    continue
                done_numbers += int(r.get("numbers") or 0)
                done_total += float(r.get("total") or 0)
                if r.get("detail"):
                    detail.append(str(r.get("detail")))
                res = res or r

            if normal:
                joined = manual_plays.join_text(normal)
                if self._verify_join(joined):
                    r = self.on_submit({"text": joined, "chat": chat,
                                        "account": acc, "market": mk}) \
                        if self.on_submit else {"ok": False, "error": "没有提交入口"}
                    if not r.get("ok"):
                        fails.append("%s" % (r.get("error") or r.get("reason") or "失败"))
                    else:
                        done_numbers += int(r.get("numbers") or 0)
                        done_total += float(r.get("total") or 0)
                        if r.get("detail"):
                            detail.append(str(r.get("detail")))
                        res = res or r
                else:
                    # 整句拼不通就逐注记：宁可多几条流水，也不把账记错
                    for i, n in enumerate(normal):
                        r = self.on_submit(
                            {"text": "%s各%s" % (n["text"], self._amt_str(n["each"])),
                             "chat": chat, "account": acc, "market": mk}) \
                            if self.on_submit else {"ok": False, "error": "没有提交入口"}
                        if not r.get("ok"):
                            fails.append("第%d注：%s"
                                         % (i + 1, r.get("error")
                                            or r.get("reason") or "失败"))
                        else:
                            done_numbers += int(r.get("numbers") or 0)
                            done_total += float(r.get("total") or 0)
                            if r.get("detail"):
                                detail.append(str(r.get("detail")))
                            res = res or r
        finally:
            self._busy = False

        if fails:
            self._say("没记全：%s\n已记上的部分是 %s 元；清单还在，改完可以再点一次记账"
                      % ("；".join(fails), fmt_money(done_total)), CLR_BAD)
            return
        if res is None:
            self._say("没有提交入口", CLR_BAD)
            return

        mkname = "香港" if (res.get("market") or mk) == "HK" else "澳门"
        msg = "已记账（%s）：%d 注 · %s 个号 · 合计 %s 元" % (
            mkname, len(self.notes), done_numbers, fmt_money(done_total))
        if res.get("archived_issue"):
            msg += "；已换期，上一期 %s 归档" % res.get("archived_issue")
        if detail:
            msg += "\n" + "；".join(detail)
        self._say(msg, CLR_OK)
        self.notes = []
        self._refresh_list()
        if self.v_loop.get():
            self._repaint()
            self.en_amt.focus_set()
        else:
            self.after(1400, self._close)

    def _close(self):
        try:
            self.destroy()
        except Exception:
            pass

    # ---------- 调试 ----------
    def _demo(self):
        """只在 MSB_MANUAL=2 时用：预填几注，方便截图看效果。"""
        if self.chats:
            self.v_chat.set(self.chats[0])
        self.v_amt.set("100")
        self.sel_z = {"牛"}
        self._repaint()
        self._add()
        self.v_amt.set("50")
        self.sel_p = {"大", "单"}
        self._repaint()
        self._add()
        self.zx_mode = "pt"
        self.zx_split = "share"
        self.sel_zx = {"鼠", "牛"}
        self.v_amt.set("100")
        self._zx_prev()
        self._zx_add()
        self._say("")


if __name__ == "__main__":
    sys.exit(run())
