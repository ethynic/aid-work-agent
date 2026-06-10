#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行程文本解析 - LLM 提取景点/团队/路线"""

import json
import re

from loguru import logger

from llm_client import call_llm


def parse_itinerary(itinerary_text: str) -> dict:
    """调用 LLM 从行程文本中提取报价所需的参数"""
    prompt = f"""你是一个旅游行程解析助手。请从以下行程方案文本中提取报价所需的关键信息。

行程方案：
{itinerary_text}

请返回 JSON 格式，包含以下字段：
{{
    "region_name": "主要目的地（省份或城市名）",
    "total_people": 30,
    "adults": 0,
    "children_half": 0,
    "students": 30,
    "elders": 0,
    "couples": 0,
    "teacher_count": 3,
    "trip_days": 4,
    "departure_city": "出发城市",
    "destination": "主要目的地城市",
    "daily_attractions": [
        {{
            "day": 1,
            "attractions": [
                {{"name": "平塘天文小镇", "activities": ["开营仪式"]}}
            ]
        }},
        {{
            "day": 2,
            "attractions": [
                {{"name": "天眼景区", "activities": ["FAST观景台参观", "天文科普讲座"]}},
                {{"name": "南仁东纪念馆", "activities": []}}
            ]
        }},
        {{
            "day": 3,
            "attractions": [
                {{"name": "小七孔景区", "activities": ["卧龙潭", "翠谷瀑布"]}}
            ]
        }}
    ],
    "hotel_preference": "4钻酒店",
    "hotel_stays": [
        {{"city": "贵阳", "area": "南明区", "nights": 2}},
        {{"city": "安顺", "area": "西秀区", "nights": 1}}
    ],
    "daily_routes": [
        {{
            "day": 1,
            "legs": [
                {{"from": "贵阳市区", "to": "平塘天文小镇"}},
                {{"from": "平塘天文小镇", "to": "平塘酒店"}}
            ]
        }},
        {{
            "day": 2,
            "legs": [
                {{"from": "平塘酒店", "to": "天眼景区"}},
                {{"from": "天眼景区", "to": "南仁东纪念馆"}},
                {{"from": "南仁东纪念馆", "to": "平塘酒店"}}
            ]
        }}
    ],
    "meal_tier": "standard",
    "guide_type": "local"
}}

注意：
1. **人数分配规则（必须严格遵守）**：
   - total_people = adults + children_half + students + elders（不含 teacher_count）
   - adults = 普通成人游客（非学生、非儿童、非老人），按成人票计费
   - students = 学生群体（初中生、高中生、大学生等），按学生票计费
   - children_half = 需要购买儿童半票的儿童人数
   - elders = 老人人数
   - teacher_count = 随队老师/领队人数，老师**不计入** adults 和 students，是独立字段
   - **关键**：如果行程明确说"XX名学生"或"XX名初一/初三/高一学生"，这些全是 students，不是 adults。adults 应为 0
   - **关键**：students + adults + children_half + elders 必须等于 total_people（不含 teacher_count）。如果30人中全部是学生且没提其他成人，则 students=30, adults=0
   - **关键**：老师不要放入 adults 中！如果文本说"30人初三学生+3名老师"，total_people=30, students=30, adults=0, teacher_count=3
2. daily_attractions 从每天行程中提取当天要去的景点和具体游玩项目。name 是景点名称。activities 是该景点中计划体验的具体项目/活动名称（如"发报机课程"、"蜡染体验"、"讲解"等），行程文本明确提到的才填写。如果行程只提到参观景点没提具体项目，activities 填空数组[]。不要编造行程中未提到的项目。**关键规则**：
   - **同一景区的子场馆合并为一个景点**：如果行程中同一天（或不同天）出现同一景区的多个子场馆（如"天文体验馆"、"南仁东事迹馆"、"FAST观景台"都属于"天眼景区"/"中国天眼科普基地"），合并为一个景点，name 用主景区名（如"中国天眼科普基地"或"天眼景区"），子场馆的 activities 合并到一起
   - **晚间活动归入当日景区**：如果晚上在景区营地或附近做的活动（如"天文小课堂"、"夜游望远镜观星"发生在天眼研学营地），这些活动也归入该景区的 activities，不要遗漏
   - **不同景区的同一天**：如果同一天确实去了多个独立景区（距离较远），则分别列出
3. hotel_preference 是酒店偏好描述（如"4钻"、"经济型"），不是酒店名
4. hotel_stays 从每天的行程安排中提取：看每天住哪个城市，同一城市连续几晚合并为一项。nights 总和应等于 trip_days - 1。area 是酒店所在区/县（如"南明区"、"西秀区"），如果无法确定具体区县则为空字符串
5. teacher_count 是随队老师人数，如果文本没提，默认 0
6. meal_tier 和 guide_type 如果文本没提，用默认值 standard 和 local
7. daily_routes 从每天行程中提取每段用车的路线节点。legs 数组中每项表示一段行程（从哪里到哪里）。一天可能有多段：如从酒店出发到景点A、景点A到景点B、最后回酒店。from 和 to 尽量写具体地点名称（如"荔波小七孔"而非"荔波"），方便计算准确距离。如果某段行程的 from 和 to 是同一个地点则不要列出来（没有移动就没有用车）。daily_routes 的长度应等于 trip_days
8. 只返回 JSON，不要其他文字"""

    raw = call_llm(prompt)

    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1)
    return json.loads(raw.strip())
