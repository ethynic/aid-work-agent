#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资源解析 - 景点/酒店向量匹配"""

from loguru import logger


def resolve_resources(parsed: dict, tenant_id: str) -> dict:
    """将 LLM 解析出的名称/偏好转换为知识库 doc_id"""
    logger.info(f"[travel-quote] resolve_resources tenant_id={tenant_id}")
    result = {"attraction_doc_ids": [], "attraction_matches": [], "hotel_doc_id": None, "hotel_stays": []}

    # 景点匹配
    attraction_names = []
    attraction_activities_map = {}

    daily_attractions = parsed.get("daily_attractions", [])
    if daily_attractions:
        for day_info in daily_attractions:
            for attr in day_info.get("attractions", []):
                name = attr.get("name", "").strip()
                if not name:
                    continue
                if name not in attraction_activities_map:
                    attraction_activities_map[name] = []
                    attraction_names.append(name)
                existing = set(attraction_activities_map[name])
                for act in attr.get("activities", []):
                    act = act.strip()
                    if act and act not in existing:
                        attraction_activities_map[name].append(act)
                        existing.add(act)
    elif parsed.get("attraction_names"):
        attraction_names = parsed["attraction_names"]

    if attraction_names:
        try:
            from attraction_retriever import AttractionRetriever
            retriever = AttractionRetriever()
            seen_doc_ids = set()
            for name in attraction_names:
                matches = retriever.search(tenant_id, name, top_k=1)
                if matches:
                    doc_id = matches[0]["doc_id"]
                    match_info = {
                        "name": name,
                        "doc_id": doc_id,
                        "info": matches[0].get("info", ""),
                        "activities": attraction_activities_map.get(name, []),
                    }
                    if doc_id in seen_doc_ids:
                        for existing in result["attraction_matches"]:
                            if existing["doc_id"] == doc_id:
                                existing_activities = set(existing["activities"])
                                for act in match_info["activities"]:
                                    if act not in existing_activities:
                                        existing["activities"].append(act)
                                        existing_activities.add(act)
                        logger.info(f"[travel-quote] 景点合并: '{name}' → doc_id={doc_id} (已有，合并activities)")
                        continue
                    seen_doc_ids.add(doc_id)
                    result["attraction_doc_ids"].append(doc_id)
                    result["attraction_matches"].append(match_info)
                    logger.info(f"[travel-quote] 景点匹配: '{name}' → doc_id={doc_id}, "
                                f"activities={match_info['activities']}")
                else:
                    logger.warning(f"[travel-quote] 景点未匹配: '{name}'")
        except Exception as e:
            logger.warning(f"[travel-quote] 景点检索失败: {e}")

    # 酒店匹配
    hotel_stays = parsed.get("hotel_stays", [])
    hotel_pref = parsed.get("hotel_preference", "")

    if hotel_stays:
        try:
            from hotel_retriever import HotelRetriever
            retriever = HotelRetriever()
            for stay in hotel_stays:
                city = stay.get("city", "")
                area = stay.get("area", "")
                query = f"{city} {area} 酒店 {hotel_pref}".strip()
                matches = retriever.search(tenant_id, query, top_k=1)
                stay["hotel_doc_id"] = matches[0]["doc_id"] if matches else None
                if matches:
                    logger.info(f"[travel-quote] 酒店匹配: '{city} {area}' → doc_id={matches[0]['doc_id']}")
                else:
                    logger.warning(f"[travel-quote] 酒店未匹配: '{city} {area}'")
            result["hotel_stays"] = hotel_stays
        except Exception as e:
            logger.warning(f"[travel-quote] 酒店检索失败: {e}")
    elif hotel_pref:
        try:
            from hotel_retriever import HotelRetriever
            retriever = HotelRetriever()
            matches = retriever.search(tenant_id, hotel_pref, top_k=1)
            if matches:
                result["hotel_doc_id"] = matches[0]["doc_id"]
                logger.info(f"[travel-quote] 酒店匹配: '{hotel_pref}' → doc_id={matches[0]['doc_id']}")
        except Exception as e:
            logger.warning(f"[travel-quote] 酒店检索失败: {e}")

    return result
