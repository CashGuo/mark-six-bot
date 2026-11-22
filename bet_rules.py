# -*- coding: utf-8 -*-
"""下注文本的规则解析器 —— 能算的先用规则算，算不了的再交给大模型。

核心不变式：**金额永远是「单价 × 号码个数」**，号码集合一律由本地号码表给出，
模型永远不参与算钱。这样即便模型看错玩法，也只错在玩法判断这一层，
不会把金额算飞。

扩展方式：新增玩法往 PLAYS 里加一条 Play 就行，主流程不动。
12/13/14（平特/平码/平、连肖、二中二/三中二/三中三）暂不支持，
命中关键词直接判失败，交给上层标"暂不支持" —— 也不送模型，
免得它瞎猜给客户记错账。
"""

import re
from typing import Dict, List, Optional, Set, Tuple

# ======================== 号码表 ========================
#
# 号码统一用两位字符串（"01"…"49"），和账本里的 key 保持一致。


def _two(n) -> str:
    return "%02d" % int(n)


TAIL: Dict[str, List[str]] = {
    str(d): [_two(n) for n in range(1, 50) if n % 10 == d] for d in range(1, 10)
}
TAIL["0"] = ["10", "20", "30", "40"]        # 0 尾只有 4 个号

HEAD: Dict[str, List[str]] = {
    "0": [_two(n) for n in range(1, 10)],
    "1": [_two(n) for n in range(10, 20)],
    "2": [_two(n) for n in range(20, 30)],
    "3": [_two(n) for n in range(30, 40)],
    "4": [_two(n) for n in range(40, 50)],   # 49 是四字头
}

COLOR: Dict[str, List[str]] = {
    "红": ["01", "02", "07", "08", "12", "13", "18", "19", "23", "24",
           "29", "30", "34", "35", "40", "45", "46"],
    "蓝": ["03", "04", "09", "10", "14", "15", "20", "25", "26",
           "31", "36", "37", "41", "42", "47", "48"],
    "绿": ["05", "06", "11", "16", "17", "21", "22", "27", "28",
           "32", "33", "38", "39", "43", "44", "49"],
}

COLOR_PARITY: Dict[str, List[str]] = {
    "红单": ["01", "07", "13", "19", "23", "29", "35", "45"],
    "红双": ["02", "08", "12", "18", "24", "30", "34", "40", "46"],
    "蓝单": ["03", "09", "15", "25", "31", "37", "41", "47"],
    "蓝双": ["04", "10", "14", "20", "26", "36", "42", "48"],
    "绿单": ["05", "11", "17", "21", "27", "33", "39", "43", "49"],
    "绿双": ["06", "16", "22", "28", "32", "38", "44"],
}

_ODD = [_two(n) for n in range(1, 50) if n % 2]
_EVEN = [_two(n) for n in range(1, 50) if n % 2 == 0]

SIZE: Dict[str, List[str]] = {
    "小": [_two(n) for n in range(1, 25)],      # 1–24
    "大": [_two(n) for n in range(25, 50)],     # 25–49
}

PARITY: Dict[str, List[str]] = {"单": _ODD, "双": _EVEN}

# 大小 ∩ 单双：小单 12、小双 12、大单 13、大双 12
SIZE_PARITY: Dict[str, List[str]] = {
    "小单": [n for n in SIZE["小"] if int(n) % 2],
    "小双": [n for n in SIZE["小"] if int(n) % 2 == 0],
    "大单": [n for n in SIZE["大"] if int(n) % 2],
    "大双": [n for n in SIZE["大"] if int(n) % 2 == 0],
}

# 波色 ∩ 大小：红小 红大 蓝小 蓝大 绿小 绿大（"红小100" = 红波里的小号，每个 100）
COLOR_SIZE: Dict[str, List[str]] = {
    c + s: [n for n in COLOR[c] if n in SIZE[s]]
    for c in ("红", "蓝", "绿") for s in ("小", "大")
}


def _heshu() -> Dict[str, List[str]]:
    """合数单双：十位 + 个位之和的奇偶。10 → 1+0=1 单，49 → 4+9=13 单。"""
    dan, shuang = [], []
    for n in range(1, 50):
        t = (n // 10) + (n % 10)
        (dan if t % 2 else shuang).append(_two(n))
    return {"单": dan, "双": shuang}


HESHU = _heshu()

WUXING: Dict[str, List[str]] = {
    "金": ["04", "05", "12", "13", "26", "27", "34", "35", "42", "43"],
    "木": ["08", "09", "16", "17", "24", "25", "38", "39", "46", "47"],
    "水": ["01", "14", "15", "22", "23", "30", "31", "44", "45"],
    "火": ["02", "03", "10", "11", "18", "19", "32", "33", "40", "41", "48", "49"],
    "土": ["06", "07", "20", "21", "28", "29", "36", "37"],
}

JIAQIN_SX = ["牛", "马", "羊", "鸡", "狗", "猪"]
YE_SHOU_SX = ["鼠", "虎", "兔", "龙", "蛇", "猴"]

# ======================== 规范化 ========================
#
# 错字、单位词、标点都在这层洗掉，后面的扫描只认干净文本。

TYPO: Dict[str, str] = {
    "洪波": "红波", "红坡": "红波", "宏波": "红波", "鸿波": "红波",
    "兰波": "蓝波", "蓝坡": "蓝波", "岚波": "蓝波", "澜波": "蓝波",
    "绿坡": "绿波", "绿博": "绿波", "绿播": "绿波",
    "野首": "野兽", "夜兽": "野兽", "耶兽": "野兽",
    "家琴": "家禽", "加禽": "家禽", "佳禽": "家禽",
    "和数": "合数", "和单": "合单", "和双": "合双",
    "红波色": "红波", "蓝波色": "蓝波", "绿波色": "绿波",
}

# 金额单位 / 无意义符号，直接抹掉。
# "元/块" 留着不抹：它是"这数字是钱"的明确信号，抹了就分不出
# "小双20元"（每号 20）和 "小双20"（20 可能是号码）。
_STRIP_CHARS = "米斤两蚊文点包#≈"

# 被拆碎的换行符
_PUNCT = {"。": ",", "；": ";", "，": ",", "、": ",", "·": "."}

# 12/13/14 关键词：暂不支持，直接判失败
_UNSUPPORTED = re.compile(r"(中特|特肖)")

# 生肖传统序：连肖补位按这个顺序往后数（鼠3连肖 = 鼠牛虎）
SX_ORDER = "鼠牛虎兔龙蛇马羊猴鸡狗猪"

# 组合注的说法是「N 中 M」，不止二中二～六中六：三中二、四中二、五中三……
# 不枚举具体词，只要「一二两三四五六(或 1-6) 中 一二两三四五六(或 1-6)」就认。
_ZHONG_WORDS = r"(?:[一二两三四五六1-6]\s*中\s*[一二两三四五六1-6])"

# 连肖位数：一到十二（"十/十一/十二" 和阿拉伯 1-12 都认，12 生肖最长十二连）
_CN_LEN = r"(?:十[一二]?|1[0-2]|[1-9]|[一二两三四五六七八九])"

# 12/13/14 的玩法词：这些整笔玩法后面跟的数字就是钱，不可能是号码，
# 所以「平特鼠牛各100 鼠牛二中二100」能干净地拆成两注（不然会被并成一注）
_FLAT_WORD = re.compile(r"(平特|平码|平肖|连肖|%s)" % _ZHONG_WORDS)

# 港澳标记
HK_MARKS = ("香", "港", "巷")
MO_MARKS = ("奥", "澳", "门")


def _expand_num_range(m):
    """「1-10各10 / 05至12各20 / 3~7各5」展开成逐个号码。

    只展开像号码的范围（1~49 内、升序、跨度不超过 20），
    别的连字符（日期、负数之类）原样留着，交给后面的规矩判。
    """
    a, b = int(m.group(1)), int(m.group(2))
    if not (1 <= a <= 49 and 1 <= b <= 49) or b < a or b - a > 20:
        return m.group(0)
    return " ".join("%d" % i for i in range(a, b + 1))


def _join_numbers(m):
    """「30同31各100」的连接词：两边都是号码才当"和"。

    "30跟100" 里 100 是钱（号码最大 49），那种「跟」是动词，原样留给单价词表。
    """
    nxt = int(m.group(2))
    if nxt <= 49:
        return "%s %s" % (m.group(1), m.group(2))
    return m.group(0)


def normalize(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("\n", "").replace("\r", "")
    for wrong, right in TYPO.items():
        t = t.replace(wrong, right)
    # "买鼠50 / 叫鼠50 / 下注鼠50"：格式词写在对象前面，纯噪声，直接去掉。
    # 只剥**项首**的：客户也写 "30买100"、"31打200"，那里的是动词。
    # 一律删掉会把号码和金额黏成 "30100"，整条就废了（线上真实报过这个）。
    t = re.sub(r"(?:^|(?<=[\s,，、;；/|]))(?:下注|买|叫)(?=[\u4e00-\u9fff])", "", t)
    for a, b in _PUNCT.items():
        t = t.replace(a, b)
    # 「30 买 100 / 49 各 250」：号码和动词之间夹了空格，会被切成两段，
    # 这里先紧缩成 "30买100"，不然「买100」那段没有号码，整条就判废
    t = re.sub(r"(\d)\s*(各号|各码|各数|每个号|每个数|一个号|一个数|每码"
               r"|各|个|号|买|打|要|投|押|跟|落|记|报|叫|约)\s*(\d)",
               r"\1\2\3", t)
    # 「30同31各100 / 30和31各100 / 30加31各100」：连接词当"和"讲
    t = re.sub(r"(\d{1,2})\s*(?:同埋|同|和|跟|及|与|加)\s*(\d{1,2})(?!\d)",
               _join_numbers, t)
    # 「1-10各10 / 1至10各10」：号码范围展开成逐个号
    t = re.sub(r"(\d{1,2})\s*[-~至到]\s*(\d{1,2})(?!\d)", _expand_num_range, t)
    # 「30买100.31买50」：手机输入用点号续写下一注。
    # 只认"两位数以上的金额 + 点 + 号码 + 动词"，免得把 3.5 这类小数拆了
    t = re.sub(r"(\d{2,})\s*\.\s*(?=\d{1,2}\s*(?:各|个|号|买|打|要|投|押|跟|落|记|报|同))",
               r"\1 ", t)
    # 复合玩法被空格拆开（"小 单 50" → "小单 50"，"红 波 100" → "红波 100"）：
    # 拆开会被当成两种独立玩法取并集，17 个号会变成 37 个号，必须合回去。
    t = re.sub(r"(红|蓝|绿)\s*(单|双|小|大|波)", r"\1\2", t)
    t = re.sub(r"(小|大)\s*(单|双)", r"\1\2", t)
    for ch in _STRIP_CHARS:
        t = t.replace(ch, "")
    return t


def detect_market(t: str) -> str:
    """香港/澳门分盘：含 香/港/巷 算香港，其余（含没标的）都算澳门。"""
    for k in HK_MARKS:
        if k in t:
            return "HK"
    return "MO"


# 港澳标记词（长的排前面，正则按左到右优先，避免 "香港" 被拆成 "香"+"港"）
# 港澳标记词（长的排前面，正则按左到右优先，避免 "香港" 被拆成 "香"+"港"）
# 「香港盘 / 香盘 / 港盘」这些带「盘」字的写法客户很爱用：以前只剥掉"香港"，
# 剩下个"盘"字留在正文里，整条就判成解析失败（"牛马各20 香港盘"→"牛马各20 盘"）。
# 复合词必须排在单字前面，否则先匹配到"香港"、剩下"盘"照样卡住。
_MARK_WORDS = ("香港盘", "香港盤", "澳门盘", "澳門盘", "澳門盤", "奥门盘", "奥門盘",
               "新奥盘", "新奧盘", "香盘", "香盤", "港盘", "港盤", "澳盘", "澳盤",
               "奥盘", "奧盘",
               "新奥", "香港", "澳门", "奥门", "港", "澳", "香", "奥", "巷", "门")
_MARK_RE = re.compile("|".join(re.escape(w) for w in _MARK_WORDS))
_HK_WORDS = {"香港", "港", "香", "巷"}


def _word_market(w: str) -> str:
    """一个标记词属于哪个盘：含 香/港/巷 就是香港，其余（澳门/奥门/新奥…）是澳门。"""
    return "HK" if any(k in w for k in HK_MARKS) else "MO"


def _strip_marks(t: str) -> str:
    """标记词只是分盘用的，不是下注内容 —— 留着会被当成认不出的碎片。"""
    for w in _MARK_WORDS:
        t = t.replace(w, "")
    # 结尾的语气词（"牛马各20香港哦" 剥掉盘口后剩 "牛马各20哦"）：留着会被当成
    # 认不出的碎片，整条判失败。只剥结尾，中间的"哦"不动。
    t = re.sub(r"[哦啊呀吧呢嘛咯嘞喔噢]+$", "", t)
    return t


def split_markets(t: str) -> List[tuple]:
    """按港澳标记词把整条消息切成 [(market, 段文本)]，标记词本身剥掉。

    客户拍板（2026-11-15）：一条消息里分别写了「港」和「澳」的，要按词分盘，
    不能把两笔都塞进第一个盘。没写盘的第一段默认澳门（跟原来一致）。
    """
    out: List[tuple] = []
    pos = 0
    cur = None                     # None = 这一段之前还没写过盘，落账时按澳门算
    for m in _MARK_RE.finditer(t):
        body = t[pos:m.start()].strip(" ,，、。;；:：")
        if body:
            out.append((cur, body))
        cur = _word_market(m.group(0))
        pos = m.end()
    tail = t[pos:].strip(" ,，、。;；:：")
    if tail:
        out.append((cur, tail))
    return out



# ======================== 中文数字 ========================

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


# 口语省略末位单位时的补位：二百五=250、两千五=2500、一万二=12000
_CN_TAIL_FILL = {"百": "十", "千": "百", "万": "千"}


def cn2num(s: str) -> Optional[float]:
    """中文/阿拉伯数字转 float。"十五"=15，"两百"=200，"二十五"=25。"""
    s = (s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)

    # 口语省略末位：客户常写 "二百五"(250)、"两千五"(2500)、"一万二"(12000)。
    # 末尾是「百/千/万 + 单个数字」时把省掉的单位补回来；
    # "一百零五" 带「零」不动（那是 105），"二百五十" 本来就完整也不动。
    s = re.sub(r"([百千万])([一二两三四五六七八九])$",
               lambda m: m.group(1) + m.group(2) + _CN_TAIL_FILL[m.group(1)], s)

    total = section = num = 0
    seen = False
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
            seen = True
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
            seen = True
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
            seen = True
        elif ch == "千":
            total += (num or 1) * 1000
            num = 0
            seen = True
        elif ch == "万":
            total += (section + num or 1) * 10000
            section = num = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return float(total + section + num)


# ======================== 玩法注册表 ========================
#
# 每条 Play 只做两件事：认出写法（pattern）、给出覆盖的号码（resolve）。
# 加新玩法 = 往 PLAYS 追加一条，主流程一行都不用改。


class Play:
    __slots__ = ("key", "name", "pattern", "resolve", "priority", "mode")

    def __init__(self, key: str, name: str, pattern: str, resolve, priority: int,
                 mode: str = "number"):
        self.key = key
        self.name = name
        self.pattern = re.compile(pattern)
        self.resolve = resolve          # (match, zodiac) -> List[str]
        self.priority = priority
        # number = 单价 × 号码个数，累加进号码盘
        # flat   = 原文那个数就是整笔金额，不落号码盘（平特整肖、二中二/三中三）
        self.mode = mode


def _r_color_parity(m, zodiac):
    return COLOR_PARITY.get(m.group(1) + m.group(2), [])


def _r_color_size(m, zodiac):
    """红小 / 红大 / 蓝小…：波色 ∩ 大小，两个条件都要满足（交集）。"""
    return COLOR_SIZE.get(m.group(1) + m.group(2), [])


def _r_size_color(m, zodiac):
    """小红 / 大蓝…：大小在前，同样是交集。"""
    return COLOR_SIZE.get(m.group(2) + m.group(1), [])


def _r_size_parity(m, zodiac):
    return SIZE_PARITY.get(m.group(1) + m.group(2), [])


def _r_heshu(m, zodiac):
    return HESHU.get(m.group(1), [])


def _r_wuxing(m, zodiac):
    """金 / 金木 / 金木水火土：连写的几个五行取并集。"""
    out: List[str] = []
    for ch in m.group(1) or "":
        out.extend(WUXING.get(ch, []))
    return out


def _r_jiaqin(m, zodiac):
    word = m.group(1)
    sxs = JIAQIN_SX if "禽" in word or "畜" in word else YE_SHOU_SX
    out: List[str] = []
    for sx in sxs:
        out.extend(zodiac.get(sx, []))
    return out


def _sx_union(text: str, zodiac: Dict[str, List[str]]) -> List[str]:
    """一串生肖字（"鼠牛虎"）→ 它们名下号码的并集，"鼠鼠" 自动去重。"""
    out: List[str] = []
    for ch in text or "":
        out.extend(zodiac.get(ch, []))
    return out


def _r_pingte(m, zodiac):
    """12 平特/平码/平：整肖。钱按整肖一共多少算，不摊到号码上。"""
    return _sx_union(m.group(1), zodiac)


def _r_lianxiao(m, zodiac):
    """13 连肖（生肖在前）：鼠二连肖 / 鼠牛三连肖。整笔金额，不落号码盘。"""
    return _sx_union(m.group(1), zodiac)


def _r_lianxiao2(m, zodiac):
    """13 连肖（生肖在后）：二连肖鼠 / 三连肖鼠牛。"""
    return _sx_union(m.group(1), zodiac)


def _r_zhong_sx(m, zodiac):
    """14 中二/中三（生肖在前）：鼠牛二中二 / 鼠牛虎三中三。整注一笔。"""
    return _sx_union(m.group(1), zodiac)


def _r_zhong2(m, zodiac):
    """14 中二/中三（生肖在后）：二中二鼠牛。"""
    return _sx_union(m.group(1), zodiac)


def _r_zhong(m, zodiac):
    """14 二中二/三中二/三中三/四中四/五中五/六中六：号码只用来回显，钱按原文那个数算总额。"""
    out: List[str] = []
    # pattern 里玩法名是非捕获组，后面的数字才是 group(1)
    for tok in re.findall(r"\d+", m.group(1) or ""):
        v = int(tok)
        if 1 <= v <= 49:
            out.append(_two(v))
    return out


def _r_color(m, zodiac):
    return COLOR.get(m.group(1), [])


def _r_tail(m, zodiac):
    """5尾 / 尾数5 / 567尾 —— 连续尾数自动展开成 5尾+6尾+7尾 的并集。"""
    digits = (m.group(1) or "") + (m.group(2) or "")
    out: List[str] = []
    for ch in digits:
        out.extend(TAIL.get(ch, []))
    return out


def _r_head(m, zodiac):
    return HEAD.get(m.group(1), [])


def _r_size(m, zodiac):
    return SIZE.get(m.group(1), [])


def _r_parity(m, zodiac):
    return PARITY.get(m.group(1), [])


def _r_zodiac(m, zodiac):
    return zodiac.get(m.group(1), [])


def _r_age(m, zodiac):
    """岁数：1 岁 = 当年生肖，号码正好是 01、13、25、37、49。

    n 岁的号码就是 n、n+12、n+24、n+36、n+48（12 年一轮），
    所以这里不用生肖表 —— 岁数本身已经决定了号码，跨年也自动对。
    """
    def nums_for(n):
        k = (n - 1) % 12 + 1
        return [_two(x) for x in range(k, 50, 12)]

    a = int(m.group(1))
    b = m.group(2)
    if b:
        lo, hi = sorted((a, int(b)))
        out: List[str] = []
        for n in range(lo, hi + 1):
            for x in nums_for(n):
                if x not in out:
                    out.append(x)
        return out
    return nums_for(a)


PLAYS: List[Play] = sorted([
    # 优先级高的先匹配：复合玩法必须先于它的组成部分被吃掉，
    # 否则 "红单" 会被 "红" 抢走、只算成红波 17 个号。
    # 14 二中二/三中二/三中三：只记总额，号码只用来回显
    Play("zhong", "中二/中三", _ZHONG_WORDS + r"((?:\s*\d+)*)", _r_zhong, 93,
         mode="flat"),
    Play("color_parity", "波色单双", r"(红|蓝|绿)(单|双)", _r_color_parity, 90),
    Play("color_size", "波色大小", r"(红|蓝|绿)(小|大)", _r_color_size, 88),
    Play("size_color", "大小波色", r"(小|大)(红|蓝|绿)", _r_size_color, 88),
    Play("size_parity", "大小单双", r"(小|大)(单|双)", _r_size_parity, 85),
    Play("heshu", "合数单双", r"合[数码]?(单|双)", _r_heshu, 80),
    Play("wuxing", "五行", r"((?:金|木|水|火|土)+)(?![肖])", _r_wuxing, 75),
    Play("jiaqin", "家禽野兽", r"(家禽|家畜|野兽)", _r_jiaqin, 70),
    Play("color", "波色", r"(红|蓝|绿)\s*(?:波色|波)?", _r_color, 65),
    Play("tail", "尾数", r"(?:尾数\s*(\d+)|(\d+)\s*尾)", _r_tail, 60),
    Play("age", "岁数", r"(?:岁数\s*)?(\d+)\s*岁(?:\s*[-~到至]\s*(\d+)\s*岁)?",
         _r_age, 58),
    Play("head", "头数", r"(\d)\s*头", _r_head, 55),
    Play("size", "大小", r"(小|大)(?![单双])", _r_size, 50),
    Play("parity", "单双", r"(单|双)(?![单双])", _r_parity, 45),
], key=lambda p: -p.priority)


def _zodiac_plays(zodiac: Dict[str, List[str]]) -> List[Play]:
    """生肖是每年的表，得动态生成；优先级最低，别抢走别的玩法。

    12/13/14 的三种玩法都在这里，口径是「整笔金额，不落号码盘」：
      平特鼠100       整肖 100
      平特鼠牛100     鼠+牛 共 100（分摊交给展示层算）
      平特鼠牛各100   鼠 100 + 牛 100 = 200
      鼠二连肖100     鼠+牛 共 100；二连肖鼠100 同义
      鼠3连肖300      鼠牛虎 共 300
      鼠牛二中二100   整注 100，不下号码盘
    """
    sxs = "|".join(re.escape(sx) for sx in zodiac)
    out = [
        Play("pingte", "平特", r"(?:平特|平码|平肖|平)((?:%s)+)" % sxs,
             _r_pingte, 95, mode="flat"),
        # 连肖两种语序都认：鼠二连肖 / 二连肖鼠；位数可写可不写
        # 位数支持到十二连（十二生肖最长），"十/十一/十二" 也算
        Play("lianxiao", "连肖",
             r"((?:%s)+)\s*%s?\s*连肖" % (sxs, _CN_LEN),
             _r_lianxiao, 93, mode="flat"),
        Play("lianxiao", "连肖",
             r"%s?\s*连肖\s*((?:%s)+)" % (_CN_LEN, sxs),
             _r_lianxiao2, 93, mode="flat"),
        # 中二/中三/中四/中五/中六：生肖在前或在后都认，整注一笔
        Play("zhong", "中二/中三", r"((?:%s)+)\s*%s" % (sxs, _ZHONG_WORDS),
             _r_zhong_sx, 94, mode="flat"),
        Play("zhong", "中二/中三", r"%s\s*((?:%s)+)" % (_ZHONG_WORDS, sxs),
             _r_zhong2, 94, mode="flat"),
    ]
    out += [Play("zodiac:%s" % sx, "生肖", r"(%s)" % re.escape(sx), _r_zodiac, 40)
            for sx in zodiac]
    return out


# ======================== 单价 ========================

# 单注格式词。文档里的是 各/各号/各码/各数/个/每码/每个号/一个号/=；
# 客户实际还这么写（都是"号码 + 动词 + 金额"）：
#   30买100  31打200  32要50  33投30  34押100  35跟50  36落20  37记100  38报30
#   39号100  40×100   41@100  42*100
# 注意别把「中」加进来：组合注是"二中二100"，那个中要先给 _ZHONG_WORDS 认。
_AMOUNT_WORDS = (r"(?:各号|各码|各数|每个号|每个数|一个号|一个数|每码|各|个|号"
                 r"|买|打|要|投|押|跟|落|记|报|同|叫|约|=|≈|×|@|\*)")

_PRICE = re.compile(
    _AMOUNT_WORDS + r"\s*([0-9零一二两三四五六七八九十百千万]+)\s*(?:元|块)?"
)


# 非生肖玩法词末字。这些玩法本身已经圈定了号码范围，后面再跟数字，
# 那个数字只可能是钱 —— 要指定号码就直接写号码，不会先写玩法再写一个号。
_PLAY_TAIL_CHARS = "红蓝绿单双小大合数码金木水火土禽兽畜波色尾头肖家岁"

# 阿拉伯数字 + 中文数字，金额两种写法都要认（"小双50" / "小双五十"）
_NUM_CHARS = "0-9零一二两三四五六七八九十百千万"


def _tail_amount(s: str):
    """非生肖玩法词 + 结尾数字（可带"元/块"）。返回 (金额, 剩余文本) 或 None。

    小双20 / 红波100 / 5尾60 / 小双20元 / 小双五十 都走这里，不限大小。
    生肖不放进来：生肖后面跟数字另走专门分支（"虎5" 的 5 是 05 号）。
    """
    m = re.search(r"[%s]\s*([%s]+)\s*([元块])?\s*$"
                  % (_PLAY_TAIL_CHARS, _NUM_CHARS), s)
    if not m:
        return None
    v = cn2num(m.group(1))
    if not v or v <= 0:
        return None
    return float(v), s[:m.start() + 1].strip()


def _bare_tail_price(s: str):
    """只写号码时，末尾那个数字当单价。返回 (金额, 剩余文本) 或 None。

    只认两种明确情况，其余的纯号码串原样不动（"1 13 34" 是三个号）：
      · 末尾 > 49  —— 号码最大 49，不可能是号（"5 50" = 5 号各 50）
      · 末尾和前一个数字相同 —— "5 5"，客户把号码和张数各写了一遍
    """
    if not re.fullmatch(r"[\s*/\\\-._0-9]+", s):
        return None                      # 带汉字/玩法的项走前面的分支，这里只管纯数字
    toks = re.findall(r"\d+(?:\.\d+)?", s)
    if len(toks) < 2:
        return None
    last, prev = float(toks[-1]), float(toks[-2])
    if last <= 49 and last != prev:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*$", s)
    head = s[:m.start()].strip()
    if not head:
        return None
    return last, head


def _take_unit(s: str, sx_chars: str) -> Tuple[Optional[float], str]:
    """抽单价。抽不到就返回 (None, 原文) —— 由调用方决定是默认 10 还是判失败。

    三条路径：
      1. 带格式词：各50 / 个50 / 每个号50
      2. 生肖直接跟数字：鼠50 / 牛马30（生肖后面跟数字一律当金额；
         "虎5 17各20" 里虎5 的 5 由切项阶段保住，不会走到这里）
      3. 其它玩法词跟数字：小双50 / 小双20 / 红波100
    """
    m = _PRICE.search(s)
    if m:
        v = cn2num(m.group(1))
        if v and v > 0:
            rest = (s[:m.start()] + " " + s[m.end():]).strip()
            return v, rest

    # 「生肖 + 数字」结尾（可带"元/块"）
    m2 = re.search(r"([%s]+)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?\s*$" % sx_chars, s)
    if m2:
        v = float(m2.group(2))
        if v > 0:
            return v, (s[:m2.start()] + m2.group(1)).strip()

    got = _tail_amount(s)
    if got:
        return got

    # 末尾数字 > 49：号码最大 49，它不可能是号，只能是钱。
    # 专门收拾 "尾数5 100"、"尾数5 一百"、"1 13 100" 这种数字夹在玩法/号码中间的写法
    # （"尾数5" 里的 5 是尾号，100 才是金额）。
    m3 = re.search(r"([%s]+)\s*(?:元|块)?\s*$" % _NUM_CHARS, s)
    if m3:
        v = cn2num(m3.group(1))
        if v and v > 49:
            head = s[:m3.start()].strip()
            if head and re.search(r"[\u4e00-\u9fff]", head):
                return float(v), head

    got = _bare_tail_price(s)
    if got:
        return got

    # 抽不到单价：不再默认 10 —— 客户拍板「香47 这种只写号码不写金额的不记账」
    return None, s


# ======================== 切项 ========================

# 单价标记：判断"这一段是不是一个完整的注"（词表与 _PRICE 保持一致）
_HAS_PRICE = re.compile(
    _AMOUNT_WORDS + r"\s*[0-9零一二两三四五六七八九十百千万]+\s*(?:元|块)?"
)


def _looks_priced(seg: str, sx_chars: str) -> bool:
    """这一段能不能自己成一个注。

    带格式词的（各50 / 每码50）直接算，另外"玩法词 + 50 以上数字"也算：
    50 不可能是号码，只能是钱，所以 "小双50" 可以自己成一项。
    "虎5" 里的 5 仍按号码处理，不会被切出去。
    """
    if _HAS_PRICE.search(seg):
        return True
    # 12/13/14 整笔玩法：末尾那个数一定是钱（"鼠牛二中二100" 的 100 不是 100 号），
    # 所以它自己就是完整一项，别并进前一笔
    if _FLAT_WORD.search(seg) and re.search(r"\d", seg):
        return True
    return bool(_tail_amount(seg))


def _subject_amount(seg: str, sx_chars: str) -> bool:
    """整段就是"对象 + 数字"（鼠50 / 牛 30 / 红单 50），可以收尾成一项。

    只要求形状，不管数字大小 —— 号码最大 49，"鼠50" 的 50 只可能是钱，
    而 "虎5" 的 5 只有当后面还挂着号码时才是号码（那种情况 buf 里会有别的段，形状对不上）。
    """
    return bool(re.fullmatch(r"[%s%s]+\s*[%s]+"
                            % (re.escape(sx_chars), _PLAY_TAIL_CHARS, _NUM_CHARS),
                            (seg or "").strip()))


def _is_subject_head(seg: str, sx_chars: str) -> bool:
    """这一段以生肖/玩法词开头 —— 说明上一段已经下完了，这是新的一注。"""
    s = (seg or "").strip()
    if not s:
        return False
    return s[0] in sx_chars or s[0] in _PLAY_TAIL_CHARS


def _subject_money(seg: str, sx_chars: str) -> bool:
    """整段是「对象 + 金额」，且那个数 > 49 —— 只能是钱，不可能是号码。

    用来断开 "鼠50 30买100" 这种生肖注后面接着号码注的写法。
    "虎5 17各20" 的 5 还可能是号码（最大 49），不能据此切，要留给同一笔扫。
    """
    if not _subject_amount(seg, sx_chars):
        return False
    m = re.search(r"([0-9]+)\s*$", seg or "")
    return bool(m and int(m.group(1)) > 49)


def split_items(text: str, sx_chars: str) -> List[str]:
    """把一条消息切成若干个独立的注。

    逗号/分号/空格都可能只是分隔符，也可能是在拼一个完整的注
    （"虎5 17各20" 里的空格是分隔符，"5尾各20 猴各25" 里的空格是分项）
    所以按"有没有单价"来判定边界：没单价的一律并到下一个有单价的段里。

    额外处理两种连着下多注的写法（都是客户真实发过/会发的）：
      "鼠 50 牛 30"     几组「生肖+金额」连写
      "红单 50 蓝双 30" 几组「玩法+金额」连写
    判据：前面已经攒成一个"对象+数字"、下一段又以生肖/玩法开头 —— 这里就是注与注的边界。
    """
    segs = [x for x in re.split(r"[,;\s]+", text) if x.strip()]
    out: List[str] = []
    buf = ""
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        # 12/13/14 的整笔玩法自带金额（平特鼠50 / 鼠牛二中二100），出现就是新一注的开头，
        # 不能并进上一笔 —— 否则「平特鼠 50 平特牛 60」会合成一笔 50，牛那 60 直接丢
        if _FLAT_WORD.search(seg):
            if buf:
                out.append(buf)
            buf = seg
            continue
        if buf and _is_subject_head(seg, sx_chars) and (
                _subject_amount(buf, sx_chars)
                # 上一笔是 12/13/14 整笔玩法且已有金额（"平特鼠 100"），后面又起生肖 → 新一注
                or (_FLAT_WORD.search(buf) and re.search(r"\d", buf))):
            out.append(buf)
            buf = seg
            continue
        # "鼠50 30买100"：生肖那笔的 50 已经是钱（>49），后面起号码注就是新的一笔
        if buf and _subject_money(buf, sx_chars) and re.match(r"[0-9]", seg):
            out.append(buf)
            buf = seg
            continue
        if _looks_priced(seg, sx_chars):
            out.append((buf + " " + seg).strip())
            buf = ""
        else:
            buf = (buf + " " + seg).strip()
    if buf:
        if (_FLAT_WORD.search(buf) or (out and _subject_amount(buf, sx_chars))
                # 末尾这段自己就带单价（"鼠50 30买100" 的 30买100），是独立一笔
                or (out and _looks_priced(buf, sx_chars))):
            out.append(buf)            # 12/13/14 的最后一笔 / "鼠 50 牛 30" 的最后一段
        elif out:
            out[-1] += " " + buf
        else:
            out.append(buf)
    return [x for x in out if x.strip()]


# ======================== 单项解析 ========================

def _scan(body: str, zodiac: Dict[str, List[str]], sx_chars: str):
    """在一项里反复扫描玩法，直到扫不动为止。

    这个循环让 "红单绿双" / "鼠马" / "1 13 34" 混写都能吃下来 ——
    不比一个 word 全能匹配，只看每个位置能被谁认领。
    """
    nums: Set[str] = set()
    names: List[str] = []
    flat: List[str] = []
    work = body
    guard = 0
    plays = sorted(PLAYS + _zodiac_plays(zodiac), key=lambda p: -p.priority)

    while work.strip() and guard < 40:
        guard += 1
        hit = None
        for p in plays:
            m = p.pattern.search(work)
            if m:
                got = p.resolve(m, zodiac)
                if got:
                    hit = (m, got, p)
                    break
        if not hit:
            break
        m, got, p = hit
        nums |= set(got)
        names.append((m.start(), m.group(0)))
        if p.mode == "flat":
            flat.append(m.group(0))
        work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]

    # 描述按原文位置排，否则 "牛马" 会写成 "马 牛"
    return (nums, [x[1] for x in sorted(names, key=lambda y: y[0])], work,
            " ".join(flat))


def _take_numbers(s: str) -> Tuple[Set[str], str]:
    """把剩下的纯号码捡出来，捡不动的原样返回（调用方会判失败）。"""
    nums: Set[str] = set()
    left: List[str] = []
    for tok in re.split(r"[\s*/\\\-._]+", s):
        tok = tok.strip()
        if not tok:
            continue
        if re.fullmatch(r"\d+", tok):
            v = int(tok)
            if 1 <= v <= 49:
                nums.add(_two(v))
                continue
        left.append(tok)
    return nums, " ".join(left)


def _no_amount(seg: str) -> bool:
    """这一段一个像金额的数字都没有：没有「各/买/…+数字」，也没有 >49 的数。

    用来把「只写号码不写金额」（香47 / 47 49）判死 —— 客户拍板这种不记账，
    也不能送模型，否则模型会自己补一个金额出来。
    """
    if _HAS_PRICE.search(seg):
        return False
    for n in re.findall(r"\d+(?:\.\d+)?", seg):
        if float(n) > 49:
            return False
    return True


def parse_item(raw: str, zodiac: Dict[str, List[str]], sx_chars: str) -> Optional[dict]:
    """解析一项。返回 {nums, unit, desc}；吃不下返回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    if _UNSUPPORTED.search(s):
        return None

    # 粘连歧义：生肖紧贴数字再跟"尾/头"（"鼠5尾"里的 5 既可能是号码又可能是尾号）。
    # 必须"紧贴"才算 —— "鼠 5 5尾" 里的空格已经把 5 和鼠分开了，那是合法的写法。
    if re.search(r"[%s]\d+(?:\.\d+)?\s*(?:尾|头)" % sx_chars, s):
        return None

    unit, body = _take_unit(s, sx_chars)
    nums, names, rest, flat = _scan(body, zodiac, sx_chars)

    if flat:
        # 平特/连肖/中二… 不落号码盘，项里那个孤零零的数字只能是钱：
        # 鼠牛二中二20 = 20 元（不是 20 号）；鼠牛二中二100 同理
        m_tail = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", rest or "")
        if m_tail:
            unit = float(m_tail.group(1))
            rest = ""

    # 整项都没有金额（"香47" / "47 49"）：不记账，整项交不出去。
    # 客户拍板：只写号码不写金额的不记，免得按默认值乱入账。
    if unit is None:
        return None

    extra, leftover = _take_numbers(rest)
    if leftover.strip():
        return None                          # 还有认不出的碎片，整项交模型
    if extra and flat:
        # 平特/连肖/中M 里又冒出一个号码（"平特鼠 07 100"）：07 是号还是钱说不清
        return None
    if extra:
        nums |= extra
        # 不再往描述里塞 "N个号"：回复文案会把号码逐个列出来，写个数是多余的

    if not nums and not flat:
        return None

    return {"nums": nums, "unit": unit, "desc": " ".join(names), "flat": flat}


# ======================== 对外入口 ========================

def _fmt(v: float) -> str:
    return ("%.0f" % v) if float(v).is_integer() else ("%.2f" % v)


def parse_local(text: str, zodiac: Dict[str, List[str]]) -> dict:
    """规则解析。ok=True 时 bets 可以直接入账，不用再调模型。

    返回：
      ok      是否整条都被规则吃下
      bets    {号码: 每号金额}
      items   [{desc, unit, count, amount}] 供回复文案用
      detail  回复文案，如 "鼠 各50共计200"
      market  HK / MO
      reason  失败原因
    """
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "reason": "空文本", "bets": {}, "items": [],
                "detail": "", "market": "MO"}

    t = normalize(raw)

    # 12/13/14 的三个手打习惯，不先归一化整条就解析不了：
    #   "平特 鼠 100" / "平肖 鼠牛虎 各30" / "3连肖 鼠 100"  —— 玩法词与生肖之间有空格
    #   "平特各鼠牛100"                                        —— 「各」插在玩法词和生肖中间
    _sx = "".join(zodiac.keys())
    # 生肖这里要用「鼠|牛|…」的或分支，不能用连写的字符序列（否则只认整串十二肖）
    _sx_alt = "|".join(re.escape(k) for k in zodiac)
    t = re.sub(r"(平特|平码|平肖|连肖)\s+(?=[%s])" % _sx, r"\1", t)
    t = re.sub(r"(平特|平码|平肖|连肖|%s)\s*各\s*((?:%s)+)"
               % (_ZHONG_WORDS, _sx_alt), r"\1\2各", t)

    if _UNSUPPORTED.search(t):
        # 中特 / 特肖客户拍板不解析（12/13/14 里只剩这两个没做），送模型也只会瞎猜 —— 直接判死
        return {"ok": False, "fatal": True,
                "reason": "暂不支持的玩法（中特/特肖）",
                "bets": {}, "items": [], "detail": "", "market": detect_market(t)}

    # 第 0 层：一个数字都没有 —— 寒暄、表情、图片占位、指令，不可能是下注。
    # 直接毙掉，别拿去喂模型（要花额度，还慢几百毫秒）。中文数字已经在
    # normalize 里转成阿拉伯数字了，所以"鼠五十"到这一步是有数字的。
    if (not re.search(r"\d", t)
            and not re.search(r"[零一二两三四五六七八九十百千万]", t)):
        return {"ok": False, "fatal": True, "reason": "非下注内容",
                "bets": {}, "items": [], "detail": "", "market": detect_market(t)}

    # 粘连歧义：生肖紧贴数字再跟"尾/头"（"鼠5尾"里的 5 既可能是号码又可能是尾号）。
    # 模型碰到这种会凭感觉猜一个，宁可判死也不让它猜。
    if re.search(r"[%s]\d+(?:\.\d+)?\s*(?:尾|头)" % "".join(zodiac.keys()), t):
        return {"ok": False, "fatal": True,
                "reason": "写法有歧义（鼠5尾 请写成 鼠 5 5尾各10）",
                "bets": {}, "items": [], "detail": "", "market": detect_market(t)}

    # 分盘：客户拍板（2026-11-15）一条消息里同时写了港和澳的要按词分盘。
    # 绝大多数消息只有一段，走下面这个快路径，行为跟以前一模一样。
    segs = split_markets(t)
    marks = {mk for mk, _ in segs if mk}
    # 只有「港」和「澳」都写了才算分盘；单盘消息一律走原路径，免得「门/香」这类
    # 单字标记把一条消息误切成两段。
    if len(segs) <= 1 or len(marks) < 2:
        mk = detect_market(t)
        r = parse_core(_strip_marks(t), zodiac, mk)
        cop = dict(r)
        r["parcels"] = [cop]
        r["multi"] = False
        return r

    parcels = [parse_core(body, zodiac, mk or "MO") for mk, body in segs]
    bad = next((p for p in parcels if not p["ok"]), {})
    return {
        "ok": all(p["ok"] for p in parcels),
        "fatal": bool(bad.get("fatal")),
        # 跨盘不合并同号（鼠在澳门和香港是两笔账，合并就串了）；落账走 parcels
        "bets": {},
        "items": [it for p in parcels for it in (p.get("items") or [])],
        "extras": [e for p in parcels for e in (p.get("extras") or [])],
        "flat_total": round(sum(p.get("flat_total") or 0 for p in parcels), 2),
        "detail": "；".join(p.get("detail") or "" for p in parcels if p.get("detail")),
        "market": parcels[0]["market"],
        "reason": bad.get("reason") or "",
        "parcels": parcels,
        "multi": True,
    }


def parse_core(t: str, zodiac: Dict[str, List[str]], market: str) -> dict:
    """单盘解析（港澳标记词已经剥掉）。parse_local 分出多段时对每段各调一次。"""
    sx_chars = "".join(zodiac.keys())

    items_out: List[dict] = []
    bets: Dict[str, float] = {}
    extras: List[dict] = []
    parts: List[str] = []

    for seg in split_items(t, sx_chars):
        # 平特/连肖里又写号码（"平特鼠 07 100"）：07 是号还是钱说不清，判死别猜。
        # 只对"带生肖"的整笔注生效 —— "二中二 5 17 各20" 是号码盘组合注，别误伤。
        # 先把玩法本身的数字去掉（"六中六"、"3连肖"）再数剩下的数字个数。
        if _FLAT_WORD.search(seg) and re.search(r"[%s]" % sx_chars, seg):
            _body = re.sub(_ZHONG_WORDS, "", seg)
            _body = re.sub(r"%s\s*连肖" % _CN_LEN, "连肖", _body)
            if len(re.findall(r"\d+(?:\.\d+)?", _body)) >= 2:
                return {"ok": False, "fatal": True,
                        "reason": "平特/连肖里不要写号码（%s）" % seg,
                        "bets": {}, "items": [], "detail": "", "market": market}
        got = parse_item(seg, zodiac, sx_chars)
        if got is None:
            # 只写号码不写金额（"香47" / "47 49"）：客户拍板不记账，判死且不送模型
            if _no_amount(seg):
                return {"ok": False, "fatal": True,
                        "reason": "没写金额，不记账（请补金额，如「47各100」「平特鼠100」）",
                        "bets": {}, "items": [], "detail": "", "market": market}
            return {"ok": False, "reason": "规则解析不了：%s" % seg,
                    "bets": {}, "items": [], "detail": "", "market": market}

        unit = got["unit"]
        if got["flat"]:
            # 12/13/14：整笔金额，号码不落盘。金额口径在这里定：
            #   平特鼠100 / 平特鼠牛100 → 原文那个数就是整笔
            #   平特鼠牛各100           → 每肖 100，整笔 200
            #   鼠3连肖300              → 鼠牛虎共 300（不带「各」）
            #   鼠牛二中二100           → 整注 100
            txt = seg.strip()
            zs = [ch for ch in txt if ch in zodiac]
            # 同一个肖写了两遍（"平特鼠鼠100"）只能算一次，否则带「各」时金额会翻倍
            zs = [ch for i, ch in enumerate(zs) if ch not in zs[:i]]
            # 带「各」＝每肖各这么多（两肖各 10 = 20）；中文金额也算（"各一百"）
            each = bool(re.search(r"各\s*(?:\d|[零一二两三四五六七八九十百千万])", txt))
            if re.search(r"连肖", txt):
                kind = "lianxiao"
                n = 2                       # 只写「连肖」不写位数，按二连
                m_len = re.search(r"(%s)\s*连肖" % _CN_LEN, txt)
                if m_len:
                    n = int(cn2num(m_len.group(1)) or 0) or 2
                if zs and len(zs) < n:
                    i = SX_ORDER.index(zs[0]) if zs[0] in SX_ORDER else 0
                    zs = [SX_ORDER[(i + k) % 12] for k in range(n)]
            elif re.search(_ZHONG_WORDS, txt):
                kind = "zhong"
                # 组合注的写法里没有「各」（"鼠牛二中二各100" 不是正常报法），
                # 而且「四中二」必须写满 4 个肖。两种都算异常写法，判死让客户重报，
                # 别自己猜 —— 整笔玩法错账没法从号盘反查。
                if each and zs:
                    return {"ok": False, "fatal": True,
                            "reason": "组合注不写「各」（%s），请直接写整注金额" % txt,
                            "bets": {}, "items": [], "detail": "", "market": market}
                m_nm = re.search(r"([一二两三四五六1-6])\s*中\s*[一二两三四五六1-6]", txt)
                n_first = int(cn2num(m_nm.group(1)) or 0) if m_nm else 0
                if n_first and zs and len(zs) != n_first:
                    return {"ok": False, "fatal": True,
                            "reason": "组合注写的肖数对不上（%s 要 %d 个肖，只写了 %d 个）"
                                      % (txt, n_first, len(zs)),
                            "bets": {}, "items": [], "detail": "", "market": market}
            else:
                kind = "pingte"
            # 带「各」＝每肖各这么多（两肖各 10 = 20）；不带就按原文整笔
            amount = round(unit * len(zs), 2) if (each and zs) else round(unit, 2)
            play = got["desc"] or txt
            extras.append({
                "play": play, "text": txt, "amount": amount,
                "nums": ",".join(str(int(n)) for n in sorted(got["nums"])),
                "kind": kind, "zodiacs": "".join(zs), "each": each,
            })
            if each and zs:
                parts.append("%s 各%s 共%s" % (play, _fmt(unit), _fmt(amount)))
            else:
                parts.append("%s 共%s" % (play, _fmt(amount)))
            continue

        for n in got["nums"]:
            bets[n] = round(bets.get(n, 0.0) + unit, 2)
        amount = round(unit * len(got["nums"]), 2)
        nums_txt = ",".join(str(int(n)) for n in sorted(got["nums"]))
        items_out.append({"desc": got["desc"], "unit": unit,
                          "count": len(got["nums"]), "amount": amount,
                          "nums": nums_txt})
        # 回复文案形如 "马：1,13,25,37,49 各10 共计50"；纯号码项没有玩法名，直接列号
        head = "%s：" % got["desc"] if got["desc"] else ""
        parts.append("%s%s 各%s 共计%s"
                     % (head, nums_txt, _fmt(unit), _fmt(amount)))

    if not bets and not extras:
        return {"ok": False, "reason": "没认出任何号码", "bets": {},
                "items": [], "detail": "", "market": market}

    flat_total = round(sum(e["amount"] for e in extras), 2)
    return {"ok": True, "bets": bets, "items": items_out,
            "extras": extras, "flat_total": flat_total,
            "detail": "；".join(parts), "market": market, "reason": ""}
