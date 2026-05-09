"""旅游报价定价数据管理 API

提供区域、车辆、景点、酒店、餐标、导游、费用、淡旺季等定价数据的 CRUD 接口。
"""

import re
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.db.database import get_db_connection


def sanitize_error_info(error_msg: str) -> str:
    if not error_msg:
        return error_msg
    for pattern in [r'password["\s:=]+\S+', r'token["\s:=]+\S+', r'api[_-]?key["\s:=]+\S+']:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg


router = APIRouter(prefix="/api/v1/travel-quote", tags=["旅游报价管理"])


def _get_tenant_id(request: Request) -> str:
    tenant_id = getattr(request.state, 'tenant_id', None)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")
    return tenant_id


class ApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


# ============================================================
# 通用 CRUD 辅助
# ============================================================

def _crud_list(table: str, tenant_id: str, filters: Dict[str, Any] = None,
               order_by: str = "sort_order, id") -> List[Dict]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        where = "tenant_id = %s AND is_active = true"
        params = [tenant_id]
        if filters:
            for k, v in filters.items():
                if v is not None:
                    where += f" AND {k} = %s"
                    params.append(v)
        cursor.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order_by}", tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def _crud_get(table: str, record_id: int, tenant_id: str) -> Optional[Dict]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE id = %s AND tenant_id = %s", (record_id, tenant_id))
        row = cursor.fetchone()
        return dict(row) if row else None


def _crud_create(table: str, tenant_id: str, data: Dict) -> Dict:
    data['tenant_id'] = tenant_id
    cols = [k for k in data.keys() if k != 'id']
    vals = [data[k] for k in cols]
    placeholders = ', '.join(['%s'] * len(cols))
    col_names = ', '.join(cols)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) RETURNING *",
            tuple(vals)
        )
        conn.commit()
        return dict(cursor.fetchone())


def _crud_update(table: str, record_id: int, tenant_id: str, data: Dict) -> Optional[Dict]:
    cols = [k for k in data.keys() if k not in ('id', 'tenant_id', 'created_at')]
    if not cols:
        return _crud_get(table, record_id, tenant_id)
    set_clause = ', '.join(f"{k} = %s" for k in cols)
    vals = [data[k] for k in cols]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = %s AND tenant_id = %s RETURNING *",
            tuple(vals) + (record_id, tenant_id)
        )
        conn.commit()
        row = cursor.fetchone()
        return dict(row) if row else None


def _crud_delete(table: str, record_id: int, tenant_id: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {table} WHERE id = %s AND tenant_id = %s", (record_id, tenant_id))
        conn.commit()
        return cursor.rowcount > 0


# ============================================================
# 区域管理
# ============================================================

@router.get("/regions")
async def list_regions(request: Request, region_name: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"name": region_name} if region_name else None
    return {"success": True, "data": _crud_list("bs_travel_quote_regions", tid, filters)}


@router.post("/regions")
async def create_region(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_regions", tid, body)}


@router.put("/regions/{record_id}")
async def update_region(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_regions", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/regions/{record_id}")
async def delete_region(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_regions", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 车辆管理
# ============================================================

@router.get("/vehicles")
async def list_vehicles(request: Request, region_name: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"region_name": region_name} if region_name else None
    return {"success": True, "data": _crud_list("bs_travel_quote_vehicles", tid, filters)}


@router.post("/vehicles")
async def create_vehicle(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_vehicles", tid, body)}


@router.put("/vehicles/{record_id}")
async def update_vehicle(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_vehicles", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/vehicles/{record_id}")
async def delete_vehicle(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_vehicles", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 景点管理
# ============================================================

@router.get("/attractions")
async def list_attractions(request: Request, region_name: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"region_name": region_name} if region_name else None
    return {"success": True, "data": _crud_list("bs_travel_quote_attractions", tid, filters)}


@router.post("/attractions")
async def create_attraction(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_attractions", tid, body)}


@router.put("/attractions/{record_id}")
async def update_attraction(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_attractions", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/attractions/{record_id}")
async def delete_attraction(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_attractions", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 门票管理（嵌套在景点下）
# ============================================================

@router.get("/attractions/{attraction_id}/tickets")
async def list_tickets(attraction_id: int, request: Request):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_list("bs_travel_quote_tickets", tid, {"attraction_id": attraction_id})}


@router.post("/attractions/{attraction_id}/tickets")
async def create_tickets(attraction_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    if isinstance(body, list):
        results = [_crud_create("bs_travel_quote_tickets", tid, {**t, "attraction_id": attraction_id}) for t in body]
        return {"success": True, "data": results}
    body["attraction_id"] = attraction_id
    return {"success": True, "data": _crud_create("bs_travel_quote_tickets", tid, body)}


@router.put("/tickets/{record_id}")
async def update_ticket(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_tickets", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/tickets/{record_id}")
async def delete_ticket(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_tickets", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 酒店管理
# ============================================================

@router.get("/hotels")
async def list_hotels(request: Request, region_name: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"region_name": region_name} if region_name else None
    return {"success": True, "data": _crud_list("bs_travel_quote_hotels", tid, filters)}


@router.post("/hotels")
async def create_hotel(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_hotels", tid, body)}


@router.put("/hotels/{record_id}")
async def update_hotel(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_hotels", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/hotels/{record_id}")
async def delete_hotel(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_hotels", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 房型管理（嵌套在酒店下）
# ============================================================

@router.get("/hotels/{hotel_id}/rooms")
async def list_rooms(hotel_id: int, request: Request):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_list("bs_travel_quote_rooms", tid, {"hotel_id": hotel_id})}


@router.post("/hotels/{hotel_id}/rooms")
async def create_room(hotel_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    if isinstance(body, list):
        results = [_crud_create("bs_travel_quote_rooms", tid, {**r, "hotel_id": hotel_id}) for r in body]
        return {"success": True, "data": results}
    body["hotel_id"] = hotel_id
    return {"success": True, "data": _crud_create("bs_travel_quote_rooms", tid, body)}


@router.put("/rooms/{record_id}")
async def update_room(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_rooms", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/rooms/{record_id}")
async def delete_room(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_rooms", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 餐标管理
# ============================================================

@router.get("/meals")
async def list_meals(request: Request, region_name: str = Query(None), meal_tier: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {}
    if region_name:
        filters["region_name"] = region_name
    if meal_tier:
        filters["meal_tier"] = meal_tier
    return {"success": True, "data": _crud_list("bs_travel_quote_meals", tid, filters or None)}


@router.post("/meals")
async def create_meal(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_meals", tid, body)}


@router.put("/meals/{record_id}")
async def update_meal(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_meals", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/meals/{record_id}")
async def delete_meal(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_meals", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 导游管理
# ============================================================

@router.get("/guides")
async def list_guides(request: Request, region_name: str = Query(None), guide_type: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {}
    if region_name:
        filters["region_name"] = region_name
    if guide_type:
        filters["guide_type"] = guide_type
    return {"success": True, "data": _crud_list("bs_travel_quote_guides", tid, filters or None)}


@router.post("/guides")
async def create_guide(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_guides", tid, body)}


@router.put("/guides/{record_id}")
async def update_guide(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_guides", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/guides/{record_id}")
async def delete_guide(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_guides", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 其他费用管理
# ============================================================

@router.get("/fees")
async def list_fees(request: Request, fee_category: str = Query(None)):
    tid = _get_tenant_id(request)
    filters = {"fee_category": fee_category} if fee_category else None
    return {"success": True, "data": _crud_list("bs_travel_quote_fees", tid, filters)}


@router.post("/fees")
async def create_fee(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_fees", tid, body)}


@router.put("/fees/{record_id}")
async def update_fee(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_fees", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/fees/{record_id}")
async def delete_fee(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_fees", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}


# ============================================================
# 淡旺季管理
# ============================================================

@router.get("/seasons")
async def list_seasons(request: Request):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_list("bs_travel_quote_seasons", tid, order_by="start_date")}


@router.post("/seasons")
async def create_season(request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    return {"success": True, "data": _crud_create("bs_travel_quote_seasons", tid, body)}


@router.put("/seasons/{record_id}")
async def update_season(record_id: int, request: Request, body: Dict[str, Any]):
    tid = _get_tenant_id(request)
    result = _crud_update("bs_travel_quote_seasons", record_id, tid, body)
    if not result:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": result}


@router.delete("/seasons/{record_id}")
async def delete_season(record_id: int, request: Request):
    tid = _get_tenant_id(request)
    if not _crud_delete("bs_travel_quote_seasons", record_id, tid):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}
