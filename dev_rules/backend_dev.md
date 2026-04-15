# 后端开发规范

## 日志规范
**本项目后端统一使用 `loguru` 作为日志库，禁止使用标准库 `logging`。**

```python
from loguru import logger

# 后端日志：复杂业务逻辑长期保留
logger.info('后端日志：开始处理用户请求')

# 后端日志：异常捕获
logger.error(f'后端日志：数据库连接失败: {e}', exc_info=True)

# 临时调试日志（bug 修复后删除）
logger.debug(f'临时调试：请求参数 {params}')
```

## 错误处理规范
所有 API 错误响应必须包含 `debug` 字段，且必须过滤敏感信息：

```python
import re

SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
]

def sanitize_error_info(error_msg: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg

# 错误响应格式
return {
    "success": False,
    "error": "操作失败，请稍后重试",
    "debug": sanitize_error_info(str(e))
}
```

## 异步/同步开发规范
**避免 `await` 调用同步方法导致的 `TypeError`**：

| 类型 | 定义 | 调用 |
|------|------|------|
| 同步 | `def method()` | `obj.method()` |
| 异步 | `async def method()` | `await obj.method()` |

异步路由调用同步服务时，使用 `asyncio.to_thread()`：
```python
import asyncio

@router.get("/data")
async def get_data():
    service = MyService()
    result = await asyncio.to_thread(service.sync_method)  # ✅ 正确
    return result
```

## Gunicorn 多 Worker 进程内存隔离
**核心问题**：Gunicorn 启动多个 worker 进程时，每个 worker 拥有独立的 Python 内存空间。

| 方案 | 适用场景 |
|------|---------|
| 磁盘刷新 | 低频读操作（如管理后台配置读取） |
| Redis 共享缓存 | 高频读操作 |
| 数据库 | 持久化数据 |
| 单 worker | 开发/调试（`gunicorn --workers 1`） |

> **磁盘/数据库是共享的，内存是隔离的。** 任何依赖内存状态且跨请求的读写操作，都必须考虑多 worker 一致性。

## API 接口命名规范
接口名称应与 Python 方法名保持一致，使用具体、有明确指向性的命名：

```python
# ✅ 正确
@router.post("/search_documents")
async def search_documents(request: SearchRequest):

# ❌ 错误
@router.post("/search")
async def search_documents(request: SearchRequest):
```