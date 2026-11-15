# -*- coding: utf-8 -*-
"""
香港/澳门六合彩自动化算账引擎 —— 尾数全量修正与完美多段切片版
"""

import openpyxl
import os
import re

SESSION_RECORDS = []

# =========================================================
# 📊 标准生肖映射（马5个号，其余4个号）
# =========================================================
DEFAULT_SX_NUMS = {
    "马": [1, 13, 25, 37, 49],
    "蛇": [2, 14, 26, 38],
    "龙": [3, 15, 27, 39],
    "兔": [4, 16, 28, 40],
    "虎": [5, 17, 29, 41],
    "牛": [6, 18, 30, 42],
    "鼠": [7, 19, 31, 43],
    "猪": [8, 20, 32, 44],
    "狗": [9, 21, 33, 45],
    "鸡": [10, 22, 34, 46],
    "猴": [11, 23, 35, 47],
    "羊": [12, 24, 36, 48]
}

# 严格修正：每个尾数在 1~49 中固定为 5 个号码
TAIL_NUMS = {
    "0尾": [10, 20, 30, 40],                          # 0尾只有 4 个号 (10,20,30,40)
    "1尾": [1, 11, 21, 31, 41],
    "2尾": [2, 12, 22, 32, 42],
    "3尾": [3, 13, 23, 33, 43],
    "4尾": [4, 14, 24, 34, 44],
    "5尾": [5, 15, 25, 35, 45],
    "6尾": [6, 16, 26, 36, 46],
    "7尾": [7, 17, 27, 37, 47],
    "8尾": [8, 18, 28, 38, 48],
    "9尾": [9, 19, 29, 39, 49],
}

TOU_NUMS = {
    "0头": [i for i in range(1, 10)],
    "1头": [i for i in range(10, 20)],
    "2头": [i for i in range(20, 30)],
    "3头": [i for i in range(30, 40)],
    "4头": [i for i in range(40, 50)],
}

COLOR_MAP = {
    "红波": [1, 2, 7, 8, 12, 13, 18, 19, 23, 24, 29, 30, 34, 35, 40, 45, 46],
    "蓝波": [3, 4, 9, 10, 14, 15, 20, 25, 26, 31, 36, 37, 41, 42, 47, 48],
    "绿波": [5, 6, 11, 16, 17, 21, 22, 27, 28, 32, 33, 38, 39, 43, 44, 49]
}

JIA_QIN_SHOU = {
    "家禽": ["牛", "马", "羊", "鸡", "狗", "猪"],
    "野兽": ["鼠", "虎", "兔", "龙", "蛇", "猴"]
}

WUXING_MAP = {
    "金": [4, 5, 12, 13, 26, 27, 34, 35, 42, 43],
    "木": [8, 9, 16, 17, 24, 25, 38, 39, 46, 47],
    "水": [1, 14, 15, 22, 23, 30, 31, 44, 45],
    "火": [2, 3, 10, 11, 18, 19, 32, 33, 40, 41, 48, 49],
    "土": [6, 7, 20, 21, 28, 29, 36, 37]
}

HS_DAN = [1, 3, 5, 7, 9, 10, 12, 14, 16, 18, 21, 23, 25, 27, 29, 30, 32, 34, 36, 38, 41, 43, 45, 47, 49]
HS_SHUANG = [2, 4, 6, 8, 11, 13, 15, 17, 19, 20, 22, 24, 26, 28, 31, 33, 35, 37, 39, 40, 42, 44, 46, 48]

TYPO_CORRECTIONS = {
    "洪波": "红波", "红坡": "红波", "宏波": "红波", "鸿波": "红波",
    "兰波": "蓝波", "蓝坡": "蓝波", "岚波": "蓝波", "澜波": "蓝波",
    "绿坡": "绿波", "绿博": "绿波", "绿播": "绿波",
    "野首": "野兽", "夜兽": "野兽", "耶兽": "野兽",
    "家琴": "家禽", "加禽": "家禽", "佳禽": "家禽",
}

def chinese_to_number(chinese_str):
    if not chinese_str:
        return 10.0
    if str(chinese_str).isdigit() or ('.' in str(chinese_str) and str(chinese_str).replace('.', '', 1).isdigit()):
        return float(chinese_str)
    simple_map = {
        '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '十五': 15, '二十': 20,
        '三十': 30, '五十': 50, '百': 100, '两百': 200, '二百': 200, '三百': 300,
        '五百': 500, '一千': 1000, '两千': 2000
    }
    return float(simple_map.get(str(chinese_str), 10.0))

def parse_single_segment(seg_text):
    seg_text = seg_text.strip()
    if not seg_text:
        return 0.0

    # 1. 提取专属单价
    unit_amt = 10.0
    match_each = re.search(r'(?:各|个|买|叫|=)\s*([零一二两三四五六七八九十百千万\d]+)', seg_text)
    if match_each:
        unit_amt = chinese_to_number(match_each.group(1))

    # 2. 复合波色
    for c_prefix in ["红", "蓝", "绿"]:
        if f"{c_prefix}单" in seg_text:
            nums = [n for n in COLOR_MAP[f"{c_prefix}波"] if n % 2 != 0]
            return len(nums) * unit_amt
        if f"{c_prefix}双" in seg_text:
            nums = [n for n in COLOR_MAP[f"{c_prefix}波"] if n % 2 == 0]
            return len(nums) * unit_amt

    # 3. 合数单双
    if "合数单" in seg_text or "和数单" in seg_text or "合单" in seg_text:
        return len(HS_DAN) * unit_amt
    if "合数双" in seg_text or "和数双" in seg_text or "合双" in seg_text:
        return len(HS_SHUANG) * unit_amt

    # 4. 五行
    for wx_key, wx_nums in WUXING_MAP.items():
        if wx_key in seg_text:
            return len(wx_nums) * unit_amt

    # 5. 家禽与野兽
    for qin_shous, sxs in JIA_QIN_SHOU.items():
        if qin_shous in seg_text:
            total_nums = []
            for sx in sxs:
                total_nums.extend(DEFAULT_SX_NUMS.get(sx, []))
            return len(total_nums) * unit_amt

    # 6. 波色
    for color_key, nums in COLOR_MAP.items():
        if color_key in seg_text or (color_key[0] in seg_text and "波" in seg_text):
            return len(nums) * unit_amt

    # 7. 头数
    for tou_key, nums in TOU_NUMS.items():
        if tou_key in seg_text or (tou_key[0] in seg_text and "头" in seg_text):
            count = seg_text.count(tou_key) or 1
            return len(nums) * unit_amt * count

    # 8. 尾数（精确匹配 0~9 尾，每个尾数 5 个号，0尾 4 个号）
    for tail_key, nums in TAIL_NUMS.items():
        if tail_key in seg_text or (tail_key[0] in seg_text and "尾" in seg_text):
            count = seg_text.count(tail_key) or seg_text.count(tail_key[0] + "尾") or 1
            return len(nums) * unit_amt * count

    # 9. 生肖检查
    unique_nums = set()
    sx_found = False
    for sx_key, nums in DEFAULT_SX_NUMS.items():
        if sx_key in seg_text:
            c = seg_text.count(sx_key)
            for _ in range(c):
                for n in nums:
                    unique_nums.add(n)
            sx_found = True

    if sx_found:
        num_part = re.split(r'各|个|买|叫|=', seg_text)[0]
        raw_matches = re.findall(r'\d+', num_part)
        for m in raw_matches:
            val = int(m)
            if 1 <= val <= 49 and float(val) != unit_amt:
                unique_nums.add(val)
        return len(unique_nums) * unit_amt

    # 10. 纯直选号码
    num_part = re.split(r'各|个|买|叫|=', seg_text)[0]
    raw_matches = re.findall(r'\d+', num_part)
    valid_numbers = []
    for m in raw_matches:
        val = int(m)
        if 1 <= val <= 49 and float(val) != unit_amt:
            valid_numbers.append(val)
    
    if valid_numbers:
        return len(set(valid_numbers)) * unit_amt

    return 0.0

def handle_incoming_message(text):
    text = text.strip()
    
    if text in ["统计", "汇总", "结余", "查看"]:
        if not SESSION_RECORDS:
            return "目前暂无任何记账记录。"
        total_sum = float(sum(r['cost'] for r in SESSION_RECORDS))
        hk_sum = float(sum(r['cost'] for r in SESSION_RECORDS if r['market'] == 'HK'))
        mo_sum = float(sum(r['cost'] for r in SESSION_RECORDS if r['market'] == 'MO'))
        
        amounts = [f"{float(r['cost']):.0f}" if float(r['cost']).is_integer() else f"{float(r['cost']):.2f}" for r in SESSION_RECORDS]
        total_str = f"{total_sum:.0f}" if total_sum.is_integer() else f"{total_sum:.2f}"
        hk_str = f"{hk_sum:.0f}" if hk_sum.is_integer() else f"{hk_sum:.2f}"
        mo_str = f"{mo_sum:.0f}" if mo_sum.is_integer() else f"{mo_sum:.2f}"
        expression = " + ".join(amounts)
        return (
            f"📊 账单统计明细：\n"
            f"{expression} = {total_str} 元\n"
            f"--------------------\n"
            f"🇭🇰 香港盘总计: {hk_str} 元\n"
            f"🇲🇴 澳门盘总计: {mo_str} 元\n"
            f"(共 {len(SESSION_RECORDS)} 笔记录)"
        )

    if text in ["清空", "重置"]:
        SESSION_RECORDS.clear()
        return "🧹 记账已清空，可重新开始记录！"

    text = text.replace('\n', '').replace('\r', '')
    for wrong, right in TYPO_CORRECTIONS.items():
        text = text.replace(wrong, right)

    text = text.replace("斤", "").replace("元", "").replace("块", "").replace("米", "").replace("两", "")
    text = text.replace("。", ",").replace("；", ";").replace("·", ".")

    market = "HK" if ("香" in text or "港" in text or text.startswith("奥") or text.startswith("新奥")) else "MO"

    # =========================================================
    # 🎯 终极多段切片：兼顾逗号、分号、以及空格隔开的独立下注项
    # =========================================================
    cleaned_text = re.sub(r'^(新澳特|奥特|奥)\s*', '', text)
    
    # 先按逗号或分号拆大块
    raw_sub_tasks = re.split(r'[,，;；]+', cleaned_text)
    
    sub_tasks = []
    for raw_t in raw_sub_tasks:
        raw_t = raw_t.strip()
        if not raw_t:
            continue
        
        # 如果大块里面包含空格（例如 "5尾各20 猴各25"），按空格进一步拆成独立的子项
        space_splits = re.split(r'\s+', raw_t)
        if len(space_splits) > 1:
            curr = ""
            for s in space_splits:
                if "各" in s or "=" in s:
                    if curr:
                        sub_tasks.append(curr + " " + s)
                        curr = ""
                    else:
                        sub_tasks.append(s)
                else:
                    if curr:
                        curr += " " + s
                    else:
                        curr = s
            if curr:
                sub_tasks.append(curr)
        else:
            sub_tasks.append(raw_t)

    # 智能合并没有带“各”字的短前缀（如“虎5，17各20”）
    final_tasks = []
    temp_task = ""
    for task in sub_tasks:
        task = task.strip()
        if not task:
            continue
        if not any(w in task for w in ["各", "买", "叫", "="]):
            if temp_task:
                temp_task += "，" + task
            else:
                temp_task = task
        else:
            if temp_task:
                temp_task += "，" + task
                final_tasks.append(temp_task)
                temp_task = ""
            else:
                final_tasks.append(task)
    if temp_task:
        final_tasks.append(temp_task)

    total_cost = 0.0
    for task in final_tasks:
        task = task.strip()
        if not task:
            continue
        total_cost += parse_single_segment(task)

    if total_cost == 0:
        total_cost = 40.0

    SESSION_RECORDS.append({"raw": text, "cost": float(total_cost), "market": market})
    
    cost_val = float(total_cost)
    cost_str = f"{cost_val:.0f}" if cost_val.is_integer() else f"{cost_val:.2f}"
    return cost_str