# 租户到期管理功能实施计划

## 需求概述
1. 租户表新增 `expire_at` 字段（到期日期，时分秒为 23:59:59）
2. 平台管理员可以在租户管理页面手动设置/修改到期日期
3. 租户前台登录时：
   - 已过期（当前日期 > expire_at）→ 不允许登录
   - 剩余不足 15 天 → 允许登录，toast 提示续费
   - expire_at 为空 → 不限制登录

---

## 实施步骤

### 步骤 1: 数据库变更

**文件**: `deploy/init-postgres.sql`
- 在 tenants 表定义中添加 `expire_at TIMESTAMP` 字段

**文件**: `deploy/db_update.sql`
- 添加增量更新 SQL 语句：
```sql
-- 2026-04-29: 租户到期管理，添加到期日期字段
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS expire_at TIMESTAMP;
COMMENT ON COLUMN tenants.expire_at IS '到期日期（时分秒为 23:59:59，当天仍可登录）';
```

---

### 步骤 2: 后端数据模型更新

**文件**: `src/saas/models/tenant.py`
- `TenantCreate` 添加 `expire_at` 字段（可选，datetime 类型）
- `TenantUpdate` 添加 `expire_at` 字段（可选，datetime 类型）
- `TenantResponse` 添加 `expire_at` 字段（可选，str 类型）

---

### 步骤 3: 后端登录检查逻辑

**文件**: `src/saas/api/tenant_auth.py`

修改 `/login/password` 接口（约 450 行之后），在获取租户信息后增加检查逻辑：

```python
# 8. 获取租户信息后，检查租户到期状态
tenant = None
if target_tenant_id:
    tenant = TenantDB.get_by_id(target_tenant_id)
    
    if tenant:
        expire_check_result = _check_tenant_expiration(tenant)
        if not expire_check_result["can_login"]:
            return AdminLoginResponse(
                success=False,
                message=f"该租户已过期（到期日期：{expire_check_result['expire_date']}），请联系平台管理员续费"
            )
```

新增辅助函数 `_check_tenant_expiration(tenant)`:

```python
def _check_tenant_expiration(tenant: dict) -> dict:
    """
    检查租户到期状态
    
    Returns:
        {
            "can_login": bool,          # 是否允许登录
            "is_expired": bool,         # 是否已过期
            "days_remaining": int | None,  # 剩余天数（未过期时）
            "expire_date": str | None,  # 到期日期字符串
            "show_warning": bool        # 是否显示续费提示
        }
    """
    expire_at = tenant.get("expire_at")
    
    # 到期日期为空，不限制
    if not expire_at:
        return {
            "can_login": True,
            "is_expired": False,
            "days_remaining": None,
            "expire_date": None,
            "show_warning": False
        }
    
    # 处理 datetime 或字符串类型
    if isinstance(expire_at, str):
        try:
            expire_datetime = datetime.fromisoformat(expire_at)
        except ValueError:
            # 格式错误，视为不限制
            return {
                "can_login": True,
                "is_expired": False,
                "days_remaining": None,
                "expire_date": expire_at,
                "show_warning": False
            }
    else:
        expire_datetime = expire_at
    
    now = datetime.now()
    
    # 确保 expire_datetime 的时分秒是 23:59:59
    # 存储时已经是 23:59:59，这里直接比较
    is_expired = now > expire_datetime
    
    if is_expired:
        return {
            "can_login": False,
            "is_expired": True,
            "days_remaining": 0,
            "expire_date": expire_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "show_warning": False
        }
    
    # 计算剩余天数
    delta = expire_datetime - now
    days_remaining = delta.days
    
    # 剩余不足 15 天，显示提示
    show_warning = days_remaining < 15
    
    return {
        "can_login": True,
        "is_expired": False,
        "days_remaining": days_remaining,
        "expire_date": expire_datetime.strftime("%Y-%m-%d %H:%M:%S"),
        "show_warning": show_warning
    }
```

**登录响应扩展**:
- 在 `AdminLoginResponse` 中添加 `expire_warning` 字段
- 登录成功时，如果需要显示警告，返回提示信息

---

### 步骤 4: 租户管理 API 更新

**文件**: `src/saas/api/tenant_mgmt.py`
- 确保 `PUT /{tenant_id}` 接口接受并正确处理 `expire_at` 字段
- 需要将前端传入的日期（YYYY-MM-DD）转换为 YYYY-MM-DD 23:59:59 的 datetime

新增辅助函数 `_normalize_expire_date`:

```python
from datetime import datetime

def _normalize_expire_date(date_str: str | None) -> datetime | None:
    """
    将日期字符串标准化为当天 23:59:59 的 datetime
    
    输入格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
    输出: datetime 对象（时分秒为 23:59:59）
    """
    if not date_str:
        return None
    
    try:
        # 如果已经是完整的 datetime 格式
        if " " in date_str or "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("T", " "))
            # 强制设置为当天 23:59:59
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            # 只有日期部分
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
    except ValueError:
        return None
```

在 `update_tenant` 函数中处理：

```python
# 处理到期日期
if "expire_at" in updates:
    updates["expire_at"] = _normalize_expire_date(updates["expire_at"])
```

---

### 步骤 5: 前端租户管理页面更新

**文件**: `frontend/src/components/saas/TenantMgmt.vue`

1. 在表单中添加到期日期输入框（日期选择器）：
```vue
<div>
  <label class="block text-sm text-slate-600 mb-1">到期日期</label>
  <input v-model="formData.expire_at" type="date" placeholder="不设置则永久有效"
    class="w-full px-3 py-2 bg-slate-100 border border-slate-300 rounded-lg text-slate-800 focus:outline-none focus:border-cyan-400" />
  <p class="text-xs text-slate-500 mt-1">到期当天仍可登录（23:59:59 前）</p>
</div>
```

2. 在租户列表表格中添加"到期日期"列：
```vue
<th class="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">到期日期</th>
```

```vue
<td class="px-4 py-3 text-sm">
  <span v-if="tenant.expire_at" :class="getExpireStatusClass(tenant.expire_at)">
    {{ formatExpireDate(tenant.expire_at) }}
  </span>
  <span v-else class="text-slate-400">永久有效</span>
</td>
```

3. 添加辅助函数：
```typescript
function formatExpireDate(dateStr: string): string {
  if (!dateStr) return '永久有效'
  // 只显示日期部分，不显示 23:59:59
  return dateStr.split(' ')[0]
}

function getExpireStatusClass(dateStr: string): string {
  if (!dateStr) return 'text-slate-600'
  
  const expireDate = new Date(dateStr)
  const now = new Date()
  const diffDays = Math.ceil((expireDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))
  
  if (diffDays < 0) return 'text-red-600 font-medium'  // 已过期
  if (diffDays < 15) return 'text-orange-600 font-medium'  // 不足15天
  return 'text-slate-600'
}
```

4. `formData` 初始化时添加 `expire_at` 字段

5. 编辑时正确回显 `expire_at` 值（只取日期部分）

---

### 步骤 6: 前端登录页面更新

**文件**: `frontend/src/components/saas/TenantLogin.vue`

1. 导入 ElMessage 或使用现有 toast 机制
2. 登录成功后检查响应中是否有 `expire_warning`
3. 如果有，显示 toast 提示：

```typescript
// 在 handleLogin 函数中
const res = await adminPasswordLogin({
  identifier: identifier.value,
  password: password.value,
  captcha_code: captchaCode.value,
  captcha_id: captchaId.value,
  tenant_id: isPortalRoute.value ? undefined : tenantId.value,
})

if (res.success) {
  setLogin(res.token, res.user, res.tenant)
  
  // 显示到期警告提示
  if (res.expire_warning) {
    ElMessage.warning({
      message: res.expire_warning,
      duration: 5000,
      showClose: true,
    })
  }
  
  // ... 后续跳转逻辑
}
```

---

### 步骤 7: 前端 API 类型定义更新

**文件**: `frontend/src/api/saasTenant.ts`
- 在 `AdminLoginResponse` 类型中添加 `expire_warning?: string`
- 在 `Tenant` 类型中添加 `expire_at?: string`

---

### 步骤 8: 前端登录时获取租户公开信息预检查（可选优化）

**文件**: `frontend/src/components/saas/TenantLogin.vue`

在 `getTenantPublicInfo` 响应中可以预先检查到期状态（但实际限制在后端，前端只做提示）。

---

## 验证清单

### 后端测试
1. [ ] 执行 `db_update.sql`，确认 `expire_at` 字段成功添加
2. [ ] 平台管理员可以设置租户的到期日期
3. [ ] 设置的到期日期自动变为 23:59:59
4. [ ] 租户已过期时登录被拒绝，返回错误信息
5. [ ] 租户剩余不足 15 天时登录成功，返回警告信息
6. [ ] 到期日期为空时不限制登录

### 前端测试
1. [ ] 租户管理列表正确显示到期日期
2. [ ] 已过期租户显示红色
3. [ ] 即将过期（<15天）租户显示橙色
4. [ ] 编辑租户时可以设置/清除到期日期
5. [ ] 即将过期租户登录时显示 toast 警告
6. [ ] 已过期租户登录时显示错误信息

---

## 涉及文件汇总

| 类型 | 文件路径 | 修改内容 |
|------|---------|---------|
| 数据库 | `deploy/init-postgres.sql` | tenants 表添加 expire_at 字段 |
| 数据库 | `deploy/db_update.sql` | 添加 ALTER TABLE 增量语句 |
| 后端 | `src/saas/models/tenant.py` | TenantCreate/Update/Response 添加 expire_at 字段 |
| 后端 | `src/saas/api/tenant_auth.py` | 登录时检查到期状态，返回警告信息 |
| 后端 | `src/saas/api/tenant_mgmt.py` | 处理 expire_at 字段更新，标准化时间为 23:59:59 |
| 前端 | `frontend/src/components/saas/TenantMgmt.vue` | 列表和表单添加到期日期字段 |
| 前端 | `frontend/src/components/saas/TenantLogin.vue` | 登录成功后显示到期警告 toast |
| 前端 | `frontend/src/api/saasTenant.ts` | 类型定义添加 expire_warning 和 expire_at |
