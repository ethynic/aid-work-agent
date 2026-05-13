## 1. 后端API开发

- [x] 1.1 创建平台Token消耗报表API (`/api/admin/token-usage`)
  - 在 `src/api/` 下创建 `admin_reports.py` 文件
  - 实现 `GET /api/admin/token-usage?month=YYYY-MM` 端点
  - 认证：仅平台管理员 (`is_platform_admin`)
  - 查询逻辑：按月分组统计所有租户的token消耗，排除测试数据
  - 响应结构：包含汇总信息和租户列表

- [x] 1.2 创建租户Token消耗明细API (`/api/saas/reports/token-details`)
  - 在 `src/saas/api/usage_reports.py` 中添加新端点
  - 实现 `GET /api/saas/reports/token-details?month=YYYY-MM&page=1&page_size=100`
  - 认证：租户管理员 (`require_admin`)
  - 查询逻辑：按月分页查询租户对话明细，排除测试数据
  - 响应结构：包含汇总信息和分页的对话明细列表

- [x] 1.3 注册API路由
  - 在 `src/main.py` 中注册新的API路由
  - 平台API注册到admin路由组
  - 租户API注册到saas路由组

- [x] 1.4 实现数据查询工具函数
  - 在 `ChatRecordDB` 类中添加平台报表查询方法
  - 在 `ChatRecordDB` 类中添加租户明细分页查询方法
  - 实现月份边界转换函数
  - 实现测试数据过滤逻辑

## 2. 前端组件开发

+ [x] 2.1 创建平台Token消耗报表组件 (`PlatformTokenUsage.vue`)
  - 在 `frontend/src/components/saas/` 下创建组件文件
  - 实现月份选择器（使用现有UI组件）
  - 实现租户汇总表格，包含表头：月份、租户代码、租户名称、输入Token数、输出Token数、对话次数
  - 实现汇总行显示月度总计
  - 集成API调用，加载和显示数据

+ [x] 2.2 创建租户Token消耗明细组件 (`TenantTokenUsage.vue`)
  - 在 `frontend/src/components/saas/` 下创建组件文件
  - 实现月份选择器（使用现有UI组件）
  - 实现对话明细表格，包含表头：用户消息（前10字）、输入Token数、输出Token数、创建时间
  - 实现分页组件（每页100条）
  - 实现汇总行显示月度总计
  - 集成API调用，加载和显示数据

+ [x] 2.3 创建Token格式化工具函数
  - 在 `frontend/src/utils/` 下创建 `formatTokens.ts` 文件
  - 实现 `formatTokensToMillions()` 函数，将原始token数转换为百万单位并保留2位小数
  - 实现 `formatMessagePreview()` 函数，截取用户消息前10个字

+ [x] 2.4 创建API调用模块
  - 在 `frontend/src/api/` 下创建 `adminReports.ts` 文件
  - 实现平台报表API调用函数
  - 在 `frontend/src/api/saasTenant.ts` 中添加租户明细API调用函数

## 3. 路由和菜单集成

+ [x] 3.1 添加前端路由配置
  - 在 `frontend/src/main.ts` 中添加平台报表路由：`/portal/token-usage`
  - 在 `frontend/src/main.ts` 中添加租户明细路由：`/t/:tenant_id/token-usage`
  - 确保路由指向正确的组件

+ [x] 3.2 添加平台管理后台菜单项
  - 修改 `frontend/src/components/saas/PortalLayout.vue`
  - 在 `portalMenuItems` 数组中添加平台Token消耗报表菜单项
  - 菜单项：路径 `/portal/token-usage`，标签 `平台Token消耗`，图标 `📊`

+ [x] 3.3 添加租户管理菜单项
  - 修改 `frontend/src/components/MenuSidebar.vue`
  - 在 `adminSubMenuItems` 计算属性中添加租户Token消耗明细菜单项
  - 菜单项：路径 `${base}/token-usage`，标签 `站点Token用量报表`，图标 `📊`
  - 确保仅租户管理员可见

## 4. 测试和验证

+ [x] 4.1 后端API测试
  - 测试平台报表API：验证权限控制、数据过滤、月份边界
  - 测试租户明细API：验证权限控制、分页功能、数据过滤
  - 测试测试数据排除逻辑

+ [x] 4.2 前端组件测试
  - 测试平台报表组件：验证月份选择、表格渲染、汇总行计算
  - 测试租户明细组件：验证月份选择、表格渲染、分页功能、汇总行计算
  - 测试Token格式化函数：验证单位转换和数字格式化

+ [x] 4.3 集成测试
  - 测试平台管理员完整流程：登录平台后台 → 访问Token消耗报表 → 查看数据
  - 测试租户管理员完整流程：登录租户前台 → 访问Token消耗明细 → 查看数据
  - 测试权限控制：非管理员无法访问，租户管理员无法访问其他租户数据

+ [x] 4.4 性能验证
  - 验证平台报表查询性能（租户数量较多时）
  - 验证租户明细分页查询性能（数据量较大时）
  - 检查 `chat_records` 表索引，确保 `(tenant_id, created_at)` 复合索引存在

## 5. 文档和清理

+ [x] 5.1 更新API文档
  - 在相关文档中记录新增的API端点
  - 说明请求参数、响应结构和权限要求

+ [x] 5.2 代码审查和清理
  - 检查代码风格一致性
  - 移除调试代码和注释
  - 确保错误处理完整

+ [x] 5.3 用户文档更新
  - 在用户手册中添加报表功能说明
  - 说明如何使用平台Token消耗报表
  - 说明如何使用租户Token消耗明细报表