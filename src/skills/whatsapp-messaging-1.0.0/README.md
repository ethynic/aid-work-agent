# WhatsApp消息发送Skill

功能全面的WhatsApp消息发送技能，支持所有主要的WhatsApp Business API功能。

## 📋 功能特性

### 支持的消息类型

✅ **基础消息类型**
- 文本消息（支持URL预览）
- 图片消息（JPEG, PNG）
- 视频消息（MP4, 3GPP）
- 音频消息（AAC, AMR, MP3, OGG）
- 文档消息（PDF, DOC, XLS, PPT等）
- 贴纸消息（WebP格式）

✅ **高级消息类型**
- 模板消息（需预先审批）
- 交互式按钮消息
- 交互式列表消息
- 位置消息
- 联系人消息

✅ **其他功能**
- 媒体文件上传
- 消息已读回执
- 本地文件支持
- URL媒体支持

## 🚀 快速开始

### 1. 获取WhatsApp Business API凭证

#### 步骤1：创建Facebook应用
1. 访问 [Facebook开发者平台](https://developers.facebook.com/apps)
2. 点击"创建应用"
3. 选择"业务"类型
4. 填写应用名称和联系邮箱

#### 步骤2：添加WhatsApp产品
1. 在应用仪表板中，点击"添加产品"
2. 找到"WhatsApp"，点击"设置"
3. 选择业务用途（如"客户支持"）

#### 步骤3：获取凭证
1. 进入 WhatsApp > API Setup
2. 复制以下信息：
   - **Phone Number ID**: 电话号码ID
   - **Access Token**: 访问令牌（点击"Generate Token"生成）
   - **Business Account ID**: 业务账户ID（可选）

#### 步骤4：配置凭证
在skill目录下创建 `.env` 文件：

```bash
# 复制示例文件
cp .env.example .env

# 编辑.env文件，填入您的凭证
WHATSAPP_ACCESS_TOKEN=EAAxxxxxxxxxxxxx
WHATSAPP_PHONE_NUMBER_ID=10xxxxxxxxx
WHATSAPP_BUSINESS_ACCOUNT_ID=10xxxxxxxxx
```

### 2. 测试配置

```bash
# 运行测试脚本
python scripts/test_api.py
```

预期输出：
```
============================================================
WhatsApp Business API 配置测试
============================================================

1. 检查环境变量...
   WHATSAPP_ACCESS_TOKEN: 已配置 ✓
   WHATSAPP_PHONE_NUMBER_ID: 已配置 ✓
   WHATSAPP_BUSINESS_ACCOUNT_ID: 已配置 ✓

2. 测试API连接...
   API连接成功 ✓
   电话号码: +86 138 0013 8000
   验证状态: HIGH

3. 测试消息格式...
   消息格式验证:
   - 文本消息: 格式正确 ✓
   - 模板消息: 格式正确 ✓
   - 位置消息: 格式正确 ✓

✓ 消息格式测试通过

============================================================
✓ 所有测试通过！WhatsApp API配置正常
============================================================
```

## 📖 使用指南

### 基本用法

#### 发送文本消息

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --message "您好，您的订单已确认，订单号：12345"
```

#### 发送带URL预览的文本

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --message "查看我们的新产品：https://example.com/products" \
  --preview-url
```

### 媒体消息

#### 发送图片

```bash
# 从URL发送
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type image \
  --image "https://example.com/product.jpg" \
  --caption "新品上市，限时优惠！"

# 从本地文件发送
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type image \
  --image "./product.jpg" \
  --caption "产品图片"
```

#### 发送视频

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type video \
  --video "https://example.com/demo.mp4" \
  --caption "产品演示视频"
```

#### 发送文档

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type document \
  --document "https://example.com/contract.pdf" \
  --filename "合同.pdf" \
  --caption "您的合同文件"
```

#### 发送音频

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type audio \
  --audio "https://example.com/voice.ogg"
```

### 模板消息

#### 创建模板

1. 访问 [WhatsApp Business Manager](https://business.facebook.com)
2. 进入 WhatsApp > Message Templates
3. 点击"Create Template"
4. 填写模板信息并提交审批

模板示例：
```
名称：order_confirmation
语言：中文（简体）
类别： TRANSACTIONAL
正文：
您的订单已确认！
订单号：{{1}}
商品：{{2}}
预计送达：{{3}}

感谢您的购买！
```

#### 发送模板消息

```bash
# 无参数模板
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type template \
  --template-name "hello_world" \
  --template-language "zh_CN"

# 带参数模板
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type template \
  --template-name "order_confirmation" \
  --template-language "zh_CN" \
  --template-components '{
    "type": "body",
    "parameters": [
      {"type": "text", "text": "12345"},
      {"type": "text", "text": "iPhone 15 Pro"},
      {"type": "text", "text": "3-5个工作日"}
    ]
  }'
```

### 交互式消息

#### 发送按钮消息

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type interactive \
  --interactive-type button \
  --interactive-body "您的订单已准备好，请选择配送方式：" \
  --interactive-buttons '[
    {"type": "reply", "reply": {"id": "express", "title": "快递配送"}},
    {"type": "reply", "reply": {"id": "pickup", "title": "到店自提"}}
  ]' \
  --interactive-footer "请在24小时内选择"
```

#### 发送列表消息

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type interactive \
  --interactive-type list \
  --interactive-body "请选择您感兴趣的产品类别：" \
  --interactive-list-button "查看产品" \
  --interactive-list-sections '[
    {
      "title": "电子产品",
      "rows": [
        {"id": "phone", "title": "手机", "description": "最新款智能手机"},
        {"id": "laptop", "title": "笔记本电脑", "description": "高性能笔记本"}
      ]
    },
    {
      "title": "家居用品",
      "rows": [
        {"id": "furniture", "title": "家具", "description": "优质家具"}
      ]
    }
  ]'
```

#### 发送带图片的按钮消息

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type interactive \
  --interactive-type button \
  --interactive-body "新品上市，限时优惠！" \
  --interactive-header-type image \
  --interactive-header-image "https://example.com/product.jpg" \
  --interactive-buttons '[
    {"type": "reply", "reply": {"id": "buy", "title": "立即购买"}},
    {"type": "reply", "reply": {"id": "info", "title": "了解更多"}}
  ]'
```

### 其他消息类型

#### 发送位置

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type location \
  --location-latitude "39.9042" \
  --location-longitude "116.4074" \
  --location-name "北京天安门" \
  --location-address "北京市东城区东长安街"
```

#### 发送联系人

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type contacts \
  --contacts '[
    {
      "name": {
        "formatted_name": "张三",
        "first_name": "三",
        "last_name": "张"
      },
      "phones": [
        {"phone": "+8613800138000", "type": "MOBILE"}
      ],
      "emails": [
        {"email": "zhangsan@example.com", "type": "WORK"}
      ]
    }
  ]'
```

#### 发送贴纸

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type sticker \
  --sticker "https://example.com/sticker.webp"
```

## 🔧 高级功能

### 媒体文件大小限制

| 类型 | 格式 | 最大大小 |
|------|------|----------|
| 图片 | JPEG, PNG | 5MB |
| 视频 | MP4, 3GPP | 16MB |
| 音频 | AAC, AMR, MP3, OGG | 16MB |
| 文档 | PDF, DOC, XLS等 | 100MB |
| 贴纸 | WebP | 100KB |

### 手机号格式

使用 **国家码 + 号码** 格式，不要加 "+" 号：

```
✓ 正确：8613800138000 (中国)
✓ 正确：14155552671 (美国)
✗ 错误：+8613800138000
✗ 错误：13800138000
```

### API速率限制

WhatsApp API有以下速率限制：

- **文本消息**: 80条/秒
- **媒体消息**: 20条/秒
- **模板消息**: 80条/秒

超过限制会收到 429 错误，需要等待后重试。

### 错误代码

| 代码 | 说明 | 解决方案 |
|------|------|----------|
| 401 | 认证失败 | 检查Access Token是否正确 |
| 403 | 权限不足 | 检查应用权限配置 |
| 404 | 接收者不在WhatsApp | 确认号码已注册WhatsApp |
| 429 | 速率限制超限 | 等待后重试 |
| 100 | 参数错误 | 检查参数格式和必需参数 |

## 📚 最佳实践

### 1. 遵守WhatsApp政策

- ✅ 获得用户同意后再发送营销消息
- ✅ 提供清晰的退订方式
- ✅ 发送相关、有价值的内容
- ❌ 不要发送垃圾消息
- ❌ 不要发送非法或有害内容

### 2. 模板消息使用建议

- 重要通知（订单确认、发货通知等）使用模板消息
- 确保模板已获得批准
- 模板参数要准确无误
- 提供清晰的变量内容

### 3. 消息发送策略

- 合理控制发送频率
- 避免在非工作时间发送
- 个性化消息内容
- 及时响应用户回复

### 4. 错误处理

- 妥善处理API错误
- 记录失败的消息
- 实现重试机制
- 监控发送成功率

## 🔒 安全注意事项

1. **保护凭证**
   - 不要在代码中硬编码凭证
   - 使用环境变量存储敏感信息
   - 定期更换Access Token

2. **用户隐私**
   - 保护用户手机号等个人信息
   - 遵守GDPR等隐私法规
   - 不要泄露用户数据

3. **消息内容**
   - 不要发送敏感信息（如密码、银行卡号）
   - 重要信息使用加密传输
   - 提供安全的通知方式

## 🆘 故障排查

### 问题1：Access Token无效

**症状**: 401认证失败错误

**解决方案**:
1. 检查Token是否正确复制
2. 确认Token未过期
3. 重新生成Token

### 问题2：Phone Number ID错误

**症状**: 404错误

**解决方案**:
1. 确认ID是否正确
2. 检查号码是否已验证
3. 确认号码状态正常

### 问题3：模板消息失败

**症状**: "Template not approved"错误

**解决方案**:
1. 在Business Manager中检查模板状态
2. 确保模板已获批准
3. 检查模板名称和语言代码

### 问题4：媒体文件上传失败

**症状**: 上传超时或失败

**解决方案**:
1. 检查文件大小是否超限
2. 确认文件格式正确
3. 检查网络连接

## 📖 参考文档

- [WhatsApp Business API官方文档](https://developers.facebook.com/docs/whatsapp)
- [消息类型参考](https://developers.facebook.com/docs/whatsapp/api/messages)
- [模板消息指南](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates)
- [Webhook设置](https://developers.facebook.com/docs/whatsapp/webhook)

## 📝 更新日志

### v1.0.0 (2024-01-15)
- ✨ 初始版本发布
- ✨ 支持所有主要消息类型
- ✨ 支持媒体文件上传
- ✨ 支持交互式消息
- ✨ 完整的错误处理

## 📄 许可证

MIT License

---

**注意**: 使用本skill需要有效的WhatsApp Business API凭证。请确保遵守WhatsApp的使用政策和当地法律法规。
