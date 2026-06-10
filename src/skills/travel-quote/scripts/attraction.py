#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
景点门票 + 游玩项目费用计算

核心设计：
1. 按 doc_id 去重，同一景点门票只收一次
2. 门票和项目分别用独立 LLM prompt 提取
3. 价格取同一票种/项目的最大值（挂牌价）
4. 人数分配由代码决定，不让 LLM 参与
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from llm_client import call_llm


@dataclass
class UniqueAttraction:
    """去重后的景点信息"""
    name: str
    doc_id: int
    activities: List[str] = field(default_factory=list)
    days: List[int] = field(default_factory=list)


def calculate_attraction_cost(
    items: list,
    tenant_id: str,
    attraction_matches: List[Dict],
    adults: int,
    children_half: int,
    students: int,
    elders: int,
    total_people: int,
    teacher_count: int = 0,
    attraction_ids: Optional[List[int]] = None,
) -> list:
    """
    计算景点门票 + 游玩项目费用（统一入口）。

    attraction_matches: resolve_resources() 的输出，每项含 name, doc_id, activities
    attraction_ids: 旧模式数据库景点 ID（兼容）
    """
    # 旧模式：通过 attraction_ids 查数据库
    if attraction_ids and not attraction_matches:
        return _calculate_attraction_cost_db(
            items, tenant_id, attraction_ids,
            adults, children_half, students, elders,
            total_people, teacher_count
        )

    if not attraction_matches:
        return items

    # 新模式：知识库 + LLM 提取
    unique_attractions = _merge_attractions(attraction_matches)

    for ua in unique_attractions:
        try:
            items = _process_single_attraction(
                items, ua,
                adults, children_half, students, elders,
                total_people, teacher_count
            )
        except Exception as e:
            logger.error(f"[travel-quote] 景点 {ua.name} 处理失败: {e}")

    return items


def _merge_attractions(attraction_matches: List[Dict]) -> List[UniqueAttraction]:
    """按 doc_id 去重，合并同一景点在不同天的 activities"""
    doc_map: Dict[int, UniqueAttraction] = {}

    for match in attraction_matches:
        doc_id = match["doc_id"]
        name = match["name"]
        activities = match.get("activities", [])

        if doc_id in doc_map:
            ua = doc_map[doc_id]
            existing = set(ua.activities)
            for act in activities:
                if act and act not in existing:
                    ua.activities.append(act)
                    existing.add(act)
        else:
            doc_map[doc_id] = UniqueAttraction(
                name=name,
                doc_id=doc_id,
                activities=list(activities),
            )

    result = list(doc_map.values())
    logger.info(f"[travel-quote] 景点去重: {len(attraction_matches)} 条匹配 → {len(result)} 个唯一景点")
    for ua in result:
        logger.info(f"  - {ua.name} (doc_id={ua.doc_id}), activities={ua.activities}")

    return result


def _process_single_attraction(
    items: list,
    ua: UniqueAttraction,
    adults: int, children_half: int, students: int, elders: int,
    total_people: int, teacher_count: int,
) -> list:
    """处理单个景点：获取知识库数据 → LLM 提取 → 转 items"""
    from attraction_retriever import AttractionRetriever
    retriever = AttractionRetriever()

    attraction_info = retriever.get_attraction_info(ua.doc_id) or ""
    ticket_table = retriever.get_ticket_table(ua.doc_id)
    project_table = retriever.get_project_table(ua.doc_id)

    attraction_name = ua.name
    # 尝试从 info 中提取正式名称
    if attraction_info:
        for line in attraction_info.split('\n'):
            if '景点名称' in line or '名称' in line:
                parts = line.split('：', 1)
                if len(parts) > 1:
                    attraction_name = parts[-1].strip()
                break

    # Step 1: 提取门票
    tickets = None
    if ticket_table:
        tickets = _llm_extract_tickets(
            ua.name, attraction_info, ticket_table,
            adults, children_half, students, elders
        )

    if tickets is None and ticket_table:
        # LLM 失败，fallback
        tickets = _fallback_parse_tickets(ticket_table, adults, children_half, students, elders)

    if tickets:
        items = _build_ticket_items(
            items, attraction_name, tickets,
            adults, children_half, students, elders,
            total_people, teacher_count
        )

    # Step 2: 提取游玩项目（仅当行程中有 activities 时）
    if ua.activities and project_table:
        projects = _llm_extract_projects(
            ua.name, attraction_info, project_table,
            ua.activities, total_people
        )

        if projects is None:
            projects = _fallback_parse_projects(project_table, ua.activities)

        if projects:
            items = _build_project_items(
                items, attraction_name, projects,
                total_people, teacher_count
            )

    return items


# ============================================================
# LLM 门票提取
# ============================================================

def _llm_extract_tickets(
    search_name: str,
    attraction_info: str,
    ticket_table: str,
    adults: int, children_half: int, students: int, elders: int,
) -> Optional[Dict]:
    """LLM 提取门票价格（独立 prompt，专注于门票）"""
    prompt = f"""你是一个旅游报价数据提取助手。请从以下门票价格表中提取报价用的价格。

## 景点信息
{attraction_info}

## 搜索关键词
"{search_name}"

## 门票价格表
{ticket_table}

## 团队构成
- 成人: {adults} 人
- 儿童（半票）: {children_half} 人
- 学生: {students} 人
- 老人: {elders} 人

## 任务

1. 判断搜索关键词"{search_name}"与知识库中的景点是否是同一个（考虑别名、简称）
2. 从门票价格表中提取门票价格
3. 价格表是用 | 分隔的文本，每行格式为：票种名 | 客户类型 | 挂牌价 | 团队价 | ...。**取第一个出现的数字价格列**（通常是挂牌价，即较高的那个价格，用于对外报价）。例如 "140 | 90"，取 140

请返回 JSON：
{{
    "confirmed": true,
    "name": "景点正式名称",
    "tickets": [
        {{"name": "景点名(成人票)", "unit_price": 180, "ticket_type": "adult", "remark": "挂牌价"}},
        {{"name": "景点名(学生票)", "unit_price": 90, "ticket_type": "student", "remark": "挂牌价"}},
        {{"name": "景点名(儿童票)", "unit_price": 90, "ticket_type": "child_half", "remark": ""}},
        {{"name": "景点名(老人票)", "unit_price": 0, "ticket_type": "elder", "remark": "免票"}}
    ]
}}

## 关键规则

1. **取第一个数字价格（挂牌价/原价）**：价格表中每行有多个数字时，第一个数字是挂牌价（用于报价），后面的数字是渠道价/团队价（不用于报价）。例如 "联票 | 全体游客 | 140 | 90" 应取 140
2. **按票种去重，每种票型只返回一条**：如果价格表中有旺季/淡季多行同票种（如"成人票 | 普通游客 | 120 | 旺季"和"成人票 | 普通游客 | 100 | 淡季"），只取第一个出现的（即旺季价格）
3. **团队票 vs 成人票**：如果价格表中同时有"成人票"和"团队票"，只保留"团队票"（因为本报价是团队出行）
4. **只提取团队中有人数的票种**：团队构成中人数为0的票种不要提取。例如学生=0则不要提取学生票
5. **ticket_type 取值**：adult / student / child_half / elder。如果价格表不区分票种（统一票价），ticket_type 用 "adult"
6. 景点不匹配时 confirmed=false，但仍提取价格
7. 门票价格表为空时，tickets 返回空数组
8. 只返回 JSON，不要其他文字"""

    try:
        raw = call_llm(prompt)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)
        result = json.loads(raw.strip())
        ticket_count = len(result.get('tickets') or [])
        logger.info(f"[travel-quote] LLM门票提取: 搜索='{search_name}', "
                     f"确认={result.get('confirmed')}, {ticket_count}项门票")
        return result
    except Exception as e:
        logger.warning(f"[travel-quote] LLM门票提取失败: {e}")
        return None


# ============================================================
# LLM 游玩项目匹配
# ============================================================

def _llm_extract_projects(
    search_name: str,
    attraction_info: str,
    project_table: str,
    activities: List[str],
    total_people: int = 0,
) -> Optional[List[Dict]]:
    """LLM 提取游玩项目价格（独立 prompt，专注于项目匹配）"""
    activities_list = "\n".join(f"  - {a}" for a in activities)

    prompt = f"""你是一个旅游项目价格匹配助手。请将行程中提到的活动与知识库价格表进行匹配。

## 景点信息
{attraction_info}

## 搜索关键词
"{search_name}"

## 项目/服务价格表
{project_table}

## 行程中提到的游玩项目
{activities_list}

## 团队人数
{total_people} 人

## 任务

逐一检查每个游玩项目，在价格表中找到匹配的项目并提取价格。

## 匹配规则

1. **语义匹配**：行程中的活动描述可能与价格表名称不完全一致，需要理解语义
   - "登台俯瞰天眼全貌" ↔ "FAST观测体验" 或 "天眼瞭望台直通车"
   - "天文小课堂" ↔ "天文小课堂"
   - "夜游望远镜观星" ↔ "夜游望远镜观星"
   - "瑶陶拉胚体验" ↔ "瑶陶制作" / "瑶陶拉胚"
   - "专业讲解导览" ↔ "讲解收费" 或 "讲解费" 类项目
   - "天文互动体验" ↔ "FAST观测体验" 或 "天象影院" 等体验项目
2. **只返回能匹配到的项目**：找不到匹配的活动不要添加
3. **不要添加行程未提到的项目**：价格表中有但行程没提到的项目不要添加
4. **计费方式判断**：
   - 价格表中有"团""组""每场"等字样 → 按团计费（per_group）
   - 否则 → 按人计费（per_person）
5. **价格取值**：价格表每行用 | 分隔，第一个出现的数字是挂牌价（用于报价），后面的数字是渠道价/团队价。取第一个数字
6. **分档价格匹配**：如果同一项目有多档价格（如按人数分档 "1-20人 120元"、"21-30人 150元"、"31-40人 200元"），**根据团队人数 {total_people} 人选择对应档位**，只返回一个档位的价格
7. **同类服务只取一项**：如果行程提到"专业讲解导览"，价格表中有多个讲解服务（如"天文体验馆讲解"、"事迹馆讲解"、"瞭望台讲解"），这些属于同一类服务，**只返回一个综合的讲解项目**，取最高价

请返回 JSON：
{{
    "projects": [
        {{"name": "FAST观测体验", "unit_price": 30, "billing_method": "per_person", "matched_activity": "登台俯瞰天眼全貌"}},
        {{"name": "天文小课堂", "unit_price": 1000, "billing_method": "per_group", "matched_activity": "天文小课堂"}}
    ]
}}

如果没有任何项目能匹配，projects 返回空数组。
只返回 JSON，不要其他文字。"""

    try:
        raw = call_llm(prompt)
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)
        result = json.loads(raw.strip())
        projects = result.get("projects") or []
        logger.info(f"[travel-quote] LLM项目匹配: 搜索='{search_name}', "
                     f"匹配{len(projects)}项, 输入活动{len(activities)}项")
        return projects
    except Exception as e:
        logger.warning(f"[travel-quote] LLM项目匹配失败: {e}")
        return None


# ============================================================
# Items 构建
# ============================================================

def _build_ticket_items(
    items: list,
    attraction_name: str,
    tickets_data: Dict,
    adults: int, children_half: int, students: int, elders: int,
    total_people: int, teacher_count: int,
) -> list:
    """将门票提取结果转为 items，根据团队构成分配人数"""
    name = tickets_data.get("name") or attraction_name
    tickets = tickets_data.get("tickets") or []

    ticket_type_to_count = {
        "adult": adults,
        "child_half": children_half,
        "student": students,
        "elder": elders,
    }

    # 按人数排序的主要客群（用于"全体游客"类门票的 fallback）
    pax_sorted = sorted(
        [("student", students), ("adult", adults), ("child_half", children_half), ("elder", elders)],
        key=lambda x: x[1], reverse=True
    )

    seen_ticket_types = set()
    for ticket in tickets:
        unit_price = float(ticket.get("unit_price") or 0)
        if unit_price == 0:
            continue

        ticket_type = ticket.get("ticket_type", "adult")
        count = ticket_type_to_count.get(ticket_type, 0)

        # 该票型对应人数为 0：fallback 到团队主要客群
        if count == 0:
            for fallback_type, fallback_count in pax_sorted:
                if fallback_count > 0:
                    ticket_type = fallback_type
                    count = fallback_count
                    break

        if count == 0:
            continue

        # 同一票型只保留第一条（去重：LLM 可能返回旺季/淡季重复票型）
        if ticket_type in seen_ticket_types:
            continue
        seen_ticket_types.add(ticket_type)

        teacher_subtotal = 0
        if teacher_count > 0 and ticket_type in ("adult", "student"):
            teacher_subtotal = round(unit_price * teacher_count, 2)

        items.append({
            "category": "门票/项目",
            "name": ticket.get("name") or f"{name}({ticket_type}票)",
            "unit_price": unit_price,
            "quantity": count,
            "unit": "人",
            "frequency": 1,
            "freq_unit": "次",
            "subtotal": unit_price,
            "teacher_subtotal": teacher_subtotal,
            "remark": ticket.get("remark") or "",
        })

    return items


def _build_project_items(
    items: list,
    attraction_name: str,
    projects: List[Dict],
    total_people: int,
    teacher_count: int,
) -> list:
    """将项目提取结果转为 items，处理按人/按团计费"""
    for proj in projects:
        unit_price = float(proj.get("unit_price") or 0)
        if unit_price == 0:
            continue

        billing = proj.get("billing_method", "per_person")

        if billing == "per_group":
            quantity = 1
            unit = "团"
            subtotal = round(unit_price / total_people, 2) if total_people > 0 else 0
            teacher_subtotal = 0
        else:
            quantity = total_people
            unit = "人"
            subtotal = unit_price
            teacher_subtotal = round(unit_price * teacher_count, 2) if teacher_count > 0 else 0

        items.append({
            "category": "门票/项目",
            "name": proj.get("name") or f"{attraction_name}(项目)",
            "unit_price": unit_price,
            "quantity": quantity,
            "unit": unit,
            "frequency": 1,
            "freq_unit": "次",
            "subtotal": subtotal,
            "teacher_subtotal": teacher_subtotal,
            "remark": proj.get("remark") or "",
        })

    return items


# ============================================================
# Fallback 规则解析
# ============================================================

def _fallback_parse_tickets(
    ticket_table: str,
    adults: int, children_half: int, students: int, elders: int,
) -> Optional[Dict]:
    """规则 fallback：解析管道符分隔的门票价格表"""
    lines = [l.strip() for l in ticket_table.split('\n') if l.strip() and '|' in l]
    if not lines:
        return None

    # {ticket_type: first_price} 取第一个数字列（挂牌价）
    price_map: Dict[str, float] = {}

    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 3:
            continue

        ticket_keyword = parts[0]
        # 取第一个数字列（挂牌价/原价，用于报价）
        price = 0
        for p in parts[1:]:
            try:
                price = float(p.strip())
                break
            except ValueError:
                continue

        if price == 0:
            continue

        ticket_type = None
        if '成人' in ticket_keyword or '全价' in ticket_keyword:
            ticket_type = 'adult'
        elif '儿童' in ticket_keyword:
            ticket_type = 'child_half'
        elif '学生' in ticket_keyword:
            ticket_type = 'student'
        elif '老人' in ticket_keyword or '老年' in ticket_keyword:
            ticket_type = 'elder'

        if ticket_type:
            # 同一票种只保留第一次提取的价格（挂牌价）
            if ticket_type not in price_map:
                price_map[ticket_type] = price

    if not price_map:
        return None

    ticket_type_labels = {
        "adult": "成人票",
        "student": "学生票",
        "child_half": "儿童票",
        "elder": "老人票",
    }

    tickets = []
    for ticket_type, price in price_map.items():
        tickets.append({
            "name": f"({ticket_type_labels.get(ticket_type, ticket_type)})",
            "unit_price": price,
            "ticket_type": ticket_type,
            "remark": "",
        })

    logger.info(f"[travel-quote] fallback门票解析: {len(tickets)}项, price_map={price_map}")
    return {"confirmed": True, "name": "", "tickets": tickets}


def _fallback_parse_projects(
    project_table: str,
    activities: List[str],
) -> Optional[List[Dict]]:
    """规则 fallback：解析项目价格表，用关键词字符重叠匹配"""
    if not activities:
        return None

    lines = [l.strip() for l in project_table.split('\n') if l.strip() and '|' in l]
    if not lines:
        return None

    projects = []
    for activity in activities:
        best_match = None
        best_score = 0

        for line in lines:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 3:
                continue

            project_name = parts[0]
            # 字符重叠得分
            act_chars = set(activity)
            name_chars = set(project_name)
            overlap = len(act_chars & name_chars)
            if overlap > best_score:
                best_score = overlap
                best_match = parts

        if not best_match or best_score < 3:
            continue

        try:
            # 价格取第一个数字列（挂牌价）
            unit_price = 0
            for p in best_match[1:]:
                try:
                    unit_price = float(p.strip())
                    break
                except ValueError:
                    continue
        except (ValueError, IndexError):
            continue

        if unit_price == 0:
            continue

        project_name = best_match[0].strip()
        billing_hint = "|".join(best_match[1:]).lower()
        billing_method = "per_group" if ("团" in billing_hint or "组" in billing_hint) else "per_person"

        projects.append({
            "name": project_name,
            "unit_price": unit_price,
            "billing_method": billing_method,
            "matched_activity": activity,
            "remark": "",
        })
        logger.info(f"[travel-quote] fallback项目匹配: 活动='{activity}' → 匹配='{project_name}', "
                     f"单价={unit_price}, 计费={'按团' if billing_method == 'per_group' else '按人'}")

    return projects if projects else None


# ============================================================
# 旧模式：数据库门票计算
# ============================================================

def _calculate_attraction_cost_db(
    items: list,
    tenant_id: str,
    attraction_ids: List[int],
    adults: int, children_half: int, students: int, elders: int,
    total_people: int, teacher_count: int,
) -> list:
    """旧模式：通过 attraction_ids 查数据库计算门票"""
    from db import get_db

    with get_db() as conn:
        for attr_id in attraction_ids:
            conn.execute(
                "SELECT * FROM bs_travel_quote_attractions WHERE id=%s AND is_active=true",
                (attr_id,)
            )
            attr = conn.fetchone()
            if not attr:
                continue

            if attr.get('internal_transport_price') and attr['internal_transport_price'] > 0:
                transport_price = float(attr['internal_transport_price'])
                items.append({
                    "category": "门票/项目",
                    "name": f"{attr['name']}({attr.get('internal_transport_name', '景区交通')})",
                    "unit_price": transport_price,
                    "quantity": total_people,
                    "unit": "人",
                    "frequency": 1,
                    "freq_unit": "次",
                    "subtotal": transport_price,
                    "teacher_subtotal": round(transport_price * teacher_count, 2),
                    "remark": "",
                })

            conn.execute(
                "SELECT * FROM bs_travel_quote_tickets WHERE attraction_id=%s AND is_active=true "
                "AND (season_type='default' OR season_type IS NULL)",
                (attr_id,)
            )
            tickets = conn.fetchall()
            if not tickets:
                continue

            ticket_map = {t['ticket_type']: t for t in tickets}

            if adults > 0 and 'adult' in ticket_map:
                t = ticket_map['adult']
                price = float(t.get('retail_price') or 0)
                if price == 0:
                    price = float(t.get('agency_price') or 0)
                if price > 0:
                    items.append({
                        "category": "门票/项目",
                        "name": f"{attr['name']}({t['ticket_type_label']})",
                        "unit_price": price,
                        "quantity": adults,
                        "unit": "人",
                        "frequency": 1,
                        "freq_unit": "次",
                        "subtotal": price,
                        "teacher_subtotal": round(price * teacher_count, 2) if teacher_count > 0 else 0,
                        "remark": "挂牌价" if t.get('retail_price') else "渠道价",
                    })

            if children_half > 0 and 'child_half' in ticket_map:
                t = ticket_map['child_half']
                price = float(t.get('retail_price') or 0)
                if price == 0:
                    price = float(t.get('agency_price') or 0)
                if price > 0:
                    items.append({
                        "category": "门票/项目",
                        "name": f"{attr['name']}({t['ticket_type_label']})",
                        "unit_price": price,
                        "quantity": children_half,
                        "unit": "人",
                        "frequency": 1,
                        "freq_unit": "次",
                        "subtotal": price,
                        "teacher_subtotal": 0,
                        "remark": t['ticket_type_label'],
                    })

            if students > 0 and 'student' in ticket_map:
                t = ticket_map['student']
                price = float(t.get('retail_price') or 0)
                if price == 0:
                    price = float(t.get('agency_price') or 0)
                if price > 0:
                    items.append({
                        "category": "门票/项目",
                        "name": f"{attr['name']}({t['ticket_type_label']})",
                        "unit_price": price,
                        "quantity": students,
                        "unit": "人",
                        "frequency": 1,
                        "freq_unit": "次",
                        "subtotal": price,
                        "teacher_subtotal": 0,
                        "remark": t['ticket_type_label'],
                    })

    return items
