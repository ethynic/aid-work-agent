# 数字员工管理功能代码审查报告

**审查日期**: 2026/04/13
**审查范围**: 数字员工（子智能体）管理功能 - 后端 API + 前端页面

**修复状态**: 已修复问题 1、4、7、8

---

## 发现的问题

### 问题 1: 创建时未验证 agent_id 格式

**文件**: `src/api/admin_subagent.py:270`

**问题描述**:
创建数字员工时，只验证了唯一性，但未校验 `agent_id` 格式。目录名应只包含安全字符（字母、数字、连字符、下划线）。

**当前代码**:
```python
if not registry.validate_id_uniqueness(body.agent_id):
    return _error_response(f"ID 已存在: {body.agent_id}", f"agent_id '{body.agent_id}' already exists", 400)
```

**建议修复**:
```python
import re
if not re.match(r'^[a-zA-Z0-9_-]+$', body.agent_id):
    return _error_response("ID 只能包含字母、数字、下划线和连字符", f"invalid agent_id: {body.agent_id}", 400)
if not registry.validate_id_uniqueness(body.agent_id):
    return _error_response(f"ID 已存在: {body.agent_id}", f"agent_id '{body.agent_id}' already exists", 400)
```

---

### 问题 2: 更新时未验证 agent_id 唯一性（自定义目录名冲突）

**状态**: 不适用（当前设计不允许修改目录名）

**问题描述**:
更新数字员工时，只验证了 `name` 唯一性，但没有验证 `agent_id`（目录名）的唯一性。如果修改了 `agent_id`，可能会与其他现有 ID 冲突。

**当前设计**: 更新时 `agent_id` 从 URL 路径获取，不可修改。

---

### 问题 3: `serialize_to_subagent_md` 缺少 version 字段默认值

**文件**: `src/subagents/loader.py:245-264`

**状态**: 已修复

**问题描述**:
`serialize_to_subagent_md` 方法在序列化时未设置 `version` 字段的默认值，导致新创建的数字员工没有版本号。

**当前代码**:
```python
frontmatter = {
    "name": config.name,
    "description": config.description,
    "version": config.version or "1.0.0",
    "author": config.author or "admin",
}
```

**实际测试**: 代码已正确处理。

---

### 问题 4: 前端 agent_id 输入未做格式校验

**状态**: 已修复

**文件**: `frontend/src/components/DigitalEmployeeManager.vue:155`

**问题描述**:
前端 `agent_id` 输入框没有实时格式校验，用户可能输入非法字符（如空格、中文、特殊符号），导致后端创建失败。

**建议修复**:
```javascript
// 在 createNew 或 saveAgent 前添加校验
const validAgentId = editForm.value.agent_id.trim().replace(/[^a-zA-Z0-9_-]/g, '')
if (editForm.value.agent_id !== validAgentId) {
  showToast('ID 只能包含字母、数字、下划线和连字符', 'error')
  return
}
```

---

### 问题 5: 删除操作未验证目录是否存在就加载

**状态**: 设计如此，无需修复

**文件**: `src/subagents/registry.py:71-102`

**说明**:
`_load_custom` 方法在目录不存在时自动创建，这是合理行为（首次启动时需要创建目录）。

---

### 问题 6: 管理员权限校验默认允许访问

**状态**: 设计确认，无需修改

**文件**: `src/api/admin_subagent.py:53-79`

**说明**:
未配置 `admin.phones` 时拒绝访问是正确行为，生产环境必须配置管理员。

---

### 问题 7: 前端保存后未正确切换到查看模式

**状态**: 已修复

**文件**: `frontend/src/components/DigitalEmployeeManager.vue:546-573`

**问题描述**:
创建新数字员工并保存成功后，页面仍停留在编辑模式，`isNewMode` 未被重置为 `false`。

**当前代码**:
```javascript
if (res.success) {
  showToast('保存成功', 'success')
  await loadList()
  // Select the saved agent
  const agentId = isNewMode.value ? editForm.value.agent_id : selectedAgent.value?.agent_id
  if (agentId) {
    const found = allList.value.find(i => i.agent_id === agentId)
    if (found) await selectAgent(found)
  }
}
```

**问题**: `selectAgent` 会设置 `isEditMode = false`，但 `isNewMode` 仍为 `true`，导致再次点击保存时会走创建逻辑而非更新逻辑。

**建议修复**:
```javascript
if (isNewMode.value) {
  isNewMode.value = false
}
```

---

### 问题 8: 列表获取后未按类型排序，内置和定制混排

**状态**: 已修复

**文件**: `frontend/src/components/DigitalEmployeeManager.vue:402-404`

**问题描述**:
`builtinList` 和 `customList` 是通过计算属性过滤的，列表按原始顺序显示。

**修复内容**: 添加按 name 字母排序。

---

## 总结

| 严重程度 | 问题数量 | 状态 |
|---------|---------|------|
| 高 | 0 | 已修复 |
| 中 | 0 | 已修复 |
| 低 | 3 | 问题 5、6 设计确认，问题 8 已修复 |

**已修复**: 问题 1、4、7、8