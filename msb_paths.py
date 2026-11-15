# -*- coding: utf-8 -*-
"""路径与老数据迁移 —— main.py 和 server_gui.py 共用这一份。

为什么要单独抽出来：两个入口都要算"程序目录 / 数据目录 / 配置文件"，
各写一份必然漂移（第一版就漂了：GUI 里 sys.executable 指向 venv\\Scripts，
把配置文件算到了 venv 下面）。
"""

import os
import shutil
import sys
from pathlib import Path


def is_compiled() -> bool:
    """Nuitka / PyInstaller 打包后的运行态。"""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """EXE 所在目录（只读：配置、模板、文档）。

    源码模式下 __file__ 就在项目根；打包后 __file__ 指向每次启动新建的
    临时解包目录，必须改用 EXE 自身路径，否则 config 找不到、数据写进临时目录退出即丢。
    """
    if is_compiled():
        for cand in (os.getenv("NUITKA_ONEFILE_BINARY"), sys.argv[0], sys.executable):
            if not cand:
                continue
            try:
                p = Path(cand)
                if p.is_file() and p.suffix.lower() == ".exe":
                    return p.resolve().parent
            except Exception:
                pass
    return Path(__file__).resolve().parent


def home_dir() -> Path:
    """用户目录根：配置、日志、账本全挂在这下面，跟采集端一个套路。

    EXE 可能被放到 Program Files 这种只读位置，任何要写的东西都不能落在
    程序目录，所以统一放 %LOCALAPPDATA%\\MarkSixBot。
    """
    for i, a in enumerate(sys.argv):
        if a == "--workdir" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).resolve()
    env = (os.getenv("MSB_HOME") or "").strip()
    if env:
        return Path(env)
    base = os.getenv("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "MarkSixBot"


def data_dir() -> Path:
    """可写数据目录：账本 / 清单 / 日志 / 去重表。"""
    env = (os.getenv("MSB_DATA_DIR") or "").strip()
    if env:
        return Path(env)
    return home_dir() / "data"


def config_path() -> Path:
    """配置文件：用户目录下的 config\\server.json。"""
    env = (os.getenv("MSB_CONFIG") or "").strip()
    if env:
        return Path(env)
    return home_dir() / "config" / "server.json"


APP_DIR = app_dir()
HOME_DIR = home_dir()
DATA_DIR = data_dir()
CONFIG_PATH = config_path()


_DEFAULT_CONFIG = """{
  "port": 8899,
  "host": "0.0.0.0",
  "wx_push_secret": "",
  "wx_account_secrets": "",
  "wx_skew_sec": 300,
  "llm_api_key": "",
  "llm_base_url": "https://llm-o8ou6he0iyxcz0ns.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
  "llm_model": "qwen3.8-27b",
  "llm_thinking": 0,
  "llm_timeout": 60
}
"""


def adopt_config() -> str:
    """老部署把 server.json 放在程序目录 config\\ 下，现在统一挪到用户目录。

    只在用户目录还没有配置时复制一次（连白名单一起），以后都以用户目录为准。
    两处都没有就写一份出厂空壳，客户打开配置页直接填就行 —— 单 EXE 交付时
    什么都不用带，双击就有配置可填。
    """
    moved = []
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            src = APP_DIR / "config"
            dst = CONFIG_PATH.parent
            if src.resolve() != dst.resolve():
                for name in ("server.json", "wx_clients.json"):
                    s = src / name
                    if not s.is_file():
                        continue
                    d = CONFIG_PATH if name == "server.json" else (dst / name)
                    if d.exists():
                        continue
                    shutil.copyfile(s, d)
                    moved.append(name)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(_DEFAULT_CONFIG, encoding="utf-8")
            moved.append("出厂配置")
    except Exception as e:
        print(f"[config] 初始化配置失败：{e}")
    return "、".join(moved)


_CONFIG_ADOPTED = adopt_config()


def adopt_old_layout() -> str:
    """把"源码时代"放在程序目录里的数据复制进数据目录。

    用复制不用移动：万一路径算错，原数据还在，客户回退不丢账。
    数据目录里已经有账本就不覆盖，避免把旧快照盖回去。
    """
    try:
        if APP_DIR.resolve() == DATA_DIR.resolve():
            return ""
        if any(d.exists() and any(d.iterdir()) for d in
               (DATA_DIR / "state", DATA_DIR / "contacts", DATA_DIR / "targets")):
            return ""
        moved = []
        for name in ("state", "contacts", "targets", "backups", "logs"):
            src = APP_DIR / name
            if src.is_dir():
                shutil.copytree(src, DATA_DIR / name, dirs_exist_ok=True)
                moved.append(name + "/")
        for name in ("processed_ids.json", "seen_fingerprints.json"):
            src = APP_DIR / name
            if src.is_file() and not (DATA_DIR / name).exists():
                shutil.copyfile(src, DATA_DIR / name)
                moved.append(name)
        return "、".join(moved)
    except Exception as e:
        print(f"[data] 迁移老数据失败：{e}")
        return ""


if __name__ == "__main__":
    print("APP_DIR  :", APP_DIR)
    print("HOME_DIR :", HOME_DIR)
    print("DATA_DIR :", DATA_DIR)
    print("CONFIG   :", CONFIG_PATH, "exists=", CONFIG_PATH.exists())
    print("迁移配置 :", _CONFIG_ADOPTED or "（无需迁移）")
