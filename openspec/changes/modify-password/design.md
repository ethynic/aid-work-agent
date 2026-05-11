## Context

当前系统的密码重置功能仅面向未登录用户：
1. 未登录用户在登录页点击"忘记密码？" → 跳转 `/portal/reset-password`（可选带 `tenant_id` 参数）
2. 在 `ResetPassword.vue` 页面输入手机号 → 图形验证码校验 → 短信验证码 → 新密码 → 重置成功 → 跳转登录页

已登录的租户用户（在 `/t/:tenant_id/*` 路由下）目前没有任何修改密码的入口。`MenuSidebar.vue` 底部仅有"主题切换"和"退出登录"两个操作。

## Goals / Non-Goals

**Goals:**
1. 为已登录租户用户提供"修改密码"入口
2. "修改密码"和"忘记密码"复用同一页面和同一套验证流程（图形验证码 + 短信验证码）
3. 已登录用户进入重置页面时，预填充当前账号的手机号
4. 已登录用户修改密码成功后，清除登录状态并要求重新登录
5. 未登录用户的"忘记密码"流程完全不受影响

**Non-Goals:**
1. 不提供旧密码直接修改密码的快捷方式（安全考虑，统一走验证码流程）
2. 不修改后端密码重置 API 的逻辑和接口
3. 不修改短信验证码和图形验证码的生成/校验逻辑
4. 平台管理员（`/portal/*` 路由）的修改密码不在本次范围内（平台管理员登录后走 PortalLayout，入口方式不同）
5. 不新增独立的数据库存储或配置项

## Decisions

### 1. 复用 ResetPassword.vue 页面

**方案选择**: 复用现有的 `ResetPassword.vue`，而非新建独立页面

**Rationale**:
- **新建页面方案**: 需要复制一套几乎完全相同的 UI 和交互逻辑，增加维护成本，且容易因两处修改不一致而产生 bug
- **复用方案**: 通过检测登录状态切换页面标题和后续行为，UI 和验证逻辑完全复用，维护成本低

**实现细节**:
- 页面标题根据模式切换：
  - 未登录（忘记密码）：`重置密码` / `通过手机号重置登录密码`
  - 已登录（修改密码）：`修改密码` / `验证身份后重置登录密码`
- 手机号输入框：
  - 未登录：用户手动输入
  - 已登录：从 `useTenantAuth().admin.phone` 预填充，且设为只读（disabled），避免用户误改他人手机号
- 重置成功后的行为：
  - 未登录：3秒后跳转登录页（现有行为）
  - 已登录：调用 `logout()` 清除登录态，再跳转登录页

### 2. 入口位置设计

**方案选择**: 在 `MenuSidebar.vue` 底部操作区（"退出登录"上方）新增"修改密码"按钮

**Rationale**:
- 侧边栏底部是用户操作（账号相关）的集中区域，与"退出登录"放在一起符合用户心智模型
- 租户前台的所有页面都使用 `MenuSidebar`，因此用户在任何页面都能访问到该入口
- 仅对租户模式（`/t/:tenant_id/*`）且已登录用户显示

**实现细节**:
```vue
<!-- 在 MenuSidebar.vue 底部区域，退出登录按钮上方 -->
<div v-if="isTenantMode && tenantIsLoggedIn" class="flex-shrink-0 px-3 pb-2">
  <button
    @click="handleModifyPassword"
    class="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:text-cyan-600 hover:bg-cyan-50 rounded-lg transition-colors"
  >
    <!-- 钥匙图标 -->
    <svg ...>...</svg>
    <span>修改密码</span>
  </button>
</div>
```

### 3. 路由设计

**方案选择**: 为租户前台新增 `/t/:tenant_id/reset-password` 路由，映射到同一 `ResetPassword.vue` 组件

**Rationale**:
- 已登录用户在 `/t/:tenant_id` 下操作，路由需要体现租户上下文
- 与现有的 `/portal/reset-password` 保持平行结构
- `ResetPassword.vue` 中通过 `useRoute()` 获取 `tenant_id`，决定返回的登录页 URL

**路由配置**（`main.ts`）:
```typescript
{
  path: '/t/:tenant_id/reset-password',
  name: 'tenant-reset-password',
  component: () => import('./components/saas/ResetPassword.vue')
}
```

### 4. 修改成功后的登录态处理

**方案选择**: 修改密码成功后，前端主动调用 `logout()` 清除 token 并跳转登录页

**Rationale**:
- 密码修改后要求重新登录是安全最佳实践，避免旧 token 继续有效带来的风险
- 后端不强制踢掉已有 token（现有设计），由前端主动清理
- 跳转目标根据当前 tenant_id 动态计算：`/t/${tenant_id}/login`

**实现细节**:
- 在 `ResetPassword.vue` 的 `handleResetPassword` 成功分支中：
  ```typescript
  if (isLoggedIn.value) {
    await logout()
  }
  setTimeout(() => {
    window.location.href = loginUrl.value
  }, 3000)
  ```

## Risks / Trade-offs

### 风险与缓解措施

1. **已登录用户预填充手机号后被篡改**
   - **风险**: 如果手机号输入框不禁用，用户可能修改成其他手机号并重置他人密码
   - **缓解**: 已登录模式下，手机号输入框设为 `disabled`，用户无法修改

2. **修改密码过程中用户刷新页面导致状态丢失**
   - **风险**: 用户在短信验证码步骤刷新页面，预填充的手机号丢失
   - **缓解**: 将当前登录用户的手机号存入组件状态，刷新后通过 `useTenantAuth` 重新获取

3. **与现有"忘记密码"流程产生冲突**
   - **风险**: 修改 `ResetPassword.vue` 时可能破坏未登录用户的忘记密码体验
   - **缓解**: 修改时保持原有逻辑不变，仅增加模式判断分支；充分测试未登录流程

### 权衡

- **用户体验 vs 安全性**: 选择让用户重新登录（而非保持登录态），安全性优先
- **复用 vs 独立页面**: 选择复用页面，降低维护成本，但增加组件内部复杂度（模式判断）
- **前端清理 vs 后端强制失效**: 选择前端清理 token，不修改后端 token 机制，影响范围最小
