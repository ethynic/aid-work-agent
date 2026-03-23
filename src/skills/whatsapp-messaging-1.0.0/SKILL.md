---
name: whatsapp-messaging
description: >
  发送WhatsApp消息给用户，支持文本、图片、视频、文档、音频等多种消息类型。
  支持发送模板消息、交互式消息、位置消息、联系人消息等高级功能。
  使用场景：客户通知、营销推广、订单更新、预约提醒、验证码发送、客服支持等。
metadata:
  openclaw:
    requires:
      env:
        - WHATSAPP_ACCESS_TOKEN
        - WHATSAPP_PHONE_NUMBER_ID
        - WHATSAPP_BUSINESS_ACCOUNT_ID
    primaryEnv: WHATSAPP_ACCESS_TOKEN
    emoji: "💬"
    homepage: https://developers.facebook.com/docs/whatsapp
---

# WhatsApp消息发送技能

## 何时使用此技能

**使用WhatsApp消息发送用于**：
- 发送文本消息给客户或用户
- 发送图片、视频、文档等多媒体消息
- 发送模板消息（需预先审批）
- 发送交互式消息（按钮、列表）
- 发送位置和联系人信息
- 发送订单确认、发货通知等业务消息
- 发送验证码或提醒通知
- 客户服务和售后支持

**关键词触发**：
- WhatsApp、发送消息、WhatsApp消息
- 发送通知、客户通知
- 短信通知、消息推送
- WhatsApp营销、客户沟通
- 订单通知、发货提醒
- 验证码、通知消息

## 如何使用此技能

**⛔ 强制限制 - 必须遵守 ⛔**

1. **仅使用WhatsApp Business API** - 执行脚本 `python scripts/whatsapp_sender.py`
2. **不要尝试其他方法** - 不要建议使用其他消息发送方式
3. **如果API失败** - 显示错误消息并立即停止
4. **没有回退方案** - 不要尝试其他方式发送WhatsApp消息

如果脚本执行失败（API未配置、网络错误等）：
- 向用户显示错误消息
- 不要提供替代方案
- 等待用户修复配置

### 基本工作流程

#### 1. 发送文本消息

**必需参数**：
- `to`: 接收者手机号（格式：国家码+号码，如8613800138000）
- `message`: 消息内容

**示例1：发送简单文本消息**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --message "您好，您的订单已确认，订单号：12345"
```

**示例2：发送格式化文本**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --message "订单更新通知\n\n订单号：12345\n状态：已发货\n预计送达：3-5个工作日\n\n如有疑问请联系客服" \
  --preview-url
```

#### 2. 发送图片消息

**必需参数**：
- `to`: 接收者手机号
- `type`: image
- `image`: 图片URL或本地文件路径

**可选参数**：
- `caption`: 图片说明文字

**示例1：发送图片URL**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type image \
  --image "https://example.com/product.jpg" \
  --caption "新品上市，限时优惠！"
```

**示例2：发送本地图片**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type image \
  --image "./product.jpg" \
  --caption "产品图片"
```

#### 3. 发送视频消息

**必需参数**：
- `to`: 接收者手机号
- `type`: video
- `video`: 视频URL或本地文件路径

**可选参数**：
- `caption`: 视频说明文字

**示例：**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type video \
  --video "https://example.com/demo.mp4" \
  --caption "产品演示视频"
```

#### 4. 发送文档消息

**必需参数**：
- `to`: 接收者手机号
- `type`: document
- `document`: 文档URL或本地文件路径

**可选参数**：
- `filename`: 文件名
- `caption`: 文档说明

**示例1：发送PDF文档**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type document \
  --document "https://example.com/contract.pdf" \
  --filename "合同.pdf" \
  --caption "您的合同文件"
```

**示例2：发送本地文档**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type document \
  --document "./invoice.pdf" \
  --filename "发票.pdf"
```

#### 5. 发送音频消息

**必需参数**：
- `to`: 接收者手机号
- `type`: audio
- `audio`: 音频URL或本地文件路径

**示例：**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type audio \
  --audio "https://example.com/voice.ogg"
```

#### 6. 发送模板消息

**必需参数**：
- `to`: 接收者手机号
- `type`: template
- `template-name`: 模板名称
- `template-language`: 模板语言代码（如zh_CN, en_US）

**可选参数**：
- `template-components`: 模板参数（JSON格式）

**示例1：发送简单模板**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type template \
  --template-name "order_confirmation" \
  --template-language "zh_CN"
```

**示例2：发送带参数的模板**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type template \
  --template-name "delivery_update" \
  --template-language "zh_CN" \
  --template-components '{
    "type": "body",
    "parameters": [
      {"type": "text", "text": "12345"},
      {"type": "text", "text": "已发货"},
      {"type": "text", "text": "3-5个工作日"}
    ]
  }'
```

#### 7. 发送交互式按钮消息

**必需参数**：
- `to`: 接收者手机号
- `type`: interactive
- `interactive-type`: button
- `interactive-body`: 消息正文
- `interactive-buttons`: 按钮配置（JSON格式）

**示例：**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type interactive \
  --interactive-type button \
  --interactive-body "您的订单已准备好，请选择配送方式：" \
  --interactive-buttons '[
    {"type": "reply", "reply": {"id": "express", "title": "快递配送"}},
    {"type": "reply", "reply": {"id": "pickup", "title": "到店自提"}}
  ]'
```

#### 8. 发送交互式列表消息

**必需参数**：
- `to`: 接收者手机号
- `type`: interactive
- `interactive-type`: list
- `interactive-body`: 消息正文
- `interactive-list-button`: 列表按钮文字
- `interactive-list-sections`: 列表选项（JSON格式）

**示例：**
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
        {"id": "furniture", "title": "家具", "description": "优质家具"},
        {"id": "decor", "title": "装饰品", "description": "精美装饰"}
      ]
    }
  ]'
```

#### 9. 发送位置消息

**必需参数**：
- `to`: 接收者手机号
- `type`: location
- `location-latitude`: 纬度
- `location-longitude`: 经度

**可选参数**：
- `location-name`: 位置名称
- `location-address`: 地址详情

**示例：**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type location \
  --location-latitude "39.9042" \
  --location-longitude "116.4074" \
  --location-name "北京天安门" \
  --location-address "北京市东城区东长安街"
```

#### 10. 发送联系人消息

**必需参数**：
- `to`: 接收者手机号
- `type`: contacts
- `contacts`: 联系人信息（JSON格式）

**示例：**
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

#### 11. 发送贴纸消息

**必需参数**：
- `to`: 接收者手机号
- `type`: sticker
- `sticker`: 贴纸URL或本地文件路径（WebP格式）

**示例：**
```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type sticker \
  --sticker "https://example.com/sticker.webp"
```

### 参数详细说明

#### 基本参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| to | string | 接收者手机号（国家码+号码） | 8613800138000 |
| type | string | 消息类型 | text, image, video, document, audio, template, interactive, location, contacts, sticker |
| message | string | 文本消息内容 | 您好，您的订单已确认 |
| preview-url | boolean | 是否预览URL | true/false |

#### 媒体消息参数

| 参数 | 类型 | 说明 |
|------|------|------|
| image | string | 图片URL或路径 |
| video | string | 视频URL或路径 |
| document | string | 文档URL或路径 |
| audio | string | 音频URL或路径 |
| sticker | string | 贴纸URL或路径（WebP格式） |
| caption | string | 媒体说明文字 |
| filename | string | 文档文件名 |

#### 模板消息参数

| 参数 | 类型 | 说明 |
|------|------|------|
| template-name | string | 模板名称 |
| template-language | string | 模板语言代码（zh_CN, en_US等） |
| template-components | JSON | 模板参数组件 |

#### 交互式消息参数

| 参数 | 类型 | 说明 |
|------|------|------|
| interactive-type | string | 交互类型（button/list） |
| interactive-body | string | 消息正文 |
| interactive-buttons | JSON | 按钮配置 |
| interactive-list-button | string | 列表按钮文字 |
| interactive-list-sections | JSON | 列表选项 |

#### 位置消息参数

| 参数 | 类型 | 说明 |
|------|------|------|
| location-latitude | string | 纬度 |
| location-longitude | string | 经度 |
| location-name | string | 位置名称 |
| location-address | string | 地址详情 |

#### 联系人消息参数

| 参数 | 类型 | 说明 |
|------|------|------|
| contacts | JSON | 联系人信息数组 |

### 理解返回结果

脚本返回JSON格式数据：

**成功响应**：
```json
{
  "success": true,
  "message_id": "wamid.HBgMODYxMzgwMDEzODAwMBUCABEYEjk0QTlCOEVGMEE1MUQzQTk1AA==",
  "to": "8613800138000",
  "type": "text",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**错误响应**：
```json
{
  "success": false,
  "error": {
    "code": 100,
    "message": "Invalid parameter",
    "details": "Recipient phone number is invalid"
  }
}
```

**关键字段**：
- `success`: 是否成功
- `message_id`: 消息ID（用于跟踪和回执）
- `to`: 接收者号码
- `type`: 消息类型
- `timestamp`: 发送时间戳

### 高级用法

#### 1. 批量发送消息

通过脚本多次调用来批量发送：

```bash
# 批量发送示例（从文件读取号码列表）
python scripts/whatsapp_sender.py --to "8613800138001" --message "消息1"
python scripts/whatsapp_sender.py --to "8613800138002" --message "消息2"
python scripts/whatsapp_sender.py --to "8613800138003" --message "消息3"
```

#### 2. 带变量的模板消息

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type template \
  --template-name "appointment_reminder" \
  --template-language "zh_CN" \
  --template-components '{
    "type": "body",
    "parameters": [
      {"type": "text", "text": "张三"},
      {"type": "text", "text": "2024-01-20"},
      {"type": "text", "text": "14:00"},
      {"type": "text", "text": "李医生"}
    ]
  }'
```

#### 3. 发送带图片的交互消息

```bash
python scripts/whatsapp_sender.py \
  --to "8613800138000" \
  --type interactive \
  --interactive-type button \
  --interactive-body "查看我们的新产品！" \
  --interactive-header-type image \
  --interactive-header-image "https://example.com/product.jpg" \
  --interactive-buttons '[
    {"type": "reply", "reply": {"id": "buy", "title": "立即购买"}},
    {"type": "reply", "reply": {"id": "info", "title": "了解更多"}}
  ]'
```

### 首次配置

**当API未配置时**：

错误将显示：
```
WHATSAPP_ACCESS_TOKEN not configured.
WHATSAPP_PHONE_NUMBER_ID not configured.

Please configure your WhatsApp Business API credentials:
1. Create a WhatsApp Business Account at https://business.facebook.com
2. Get your credentials from https://developers.facebook.com/apps
3. Add credentials to .env file
```

**配置工作流程**：

1. **显示确切的错误消息**给用户（包括URL）

2. **引导用户安全配置**：
   - 推荐通过应用程序的标准方法配置
   - 列出必需的环境变量：
     ```
     - WHATSAPP_ACCESS_TOKEN
     - WHATSAPP_PHONE_NUMBER_ID
     - WHATSAPP_BUSINESS_ACCOUNT_ID (可选)
     ```
   - 在skill目录下创建 `.env` 文件并添加：
     ```
     WHATSAPP_ACCESS_TOKEN=your_access_token_here
     WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id_here
     WHATSAPP_BUSINESS_ACCOUNT_ID=your_business_account_id_here
     ```

3. **如果用户在对话中提供凭证**（接受任何合理格式）：
   - `WHATSAPP_ACCESS_TOKEN=EAAxxxxx`
   - `这是我的Access Token：EAAxxxxx`
   - 复制粘贴的代码格式
   - **安全提示**：警告用户在对话中共享的凭证可能存储在对话历史中

4. **解析并验证值**：
   - 提取所有必需的凭证
   - 确认格式正确
   - 告诉用户需要在 `.env` 文件中设置哪些环境变量

5. **请用户确认环境已配置**：
   - 等待用户确认已在适当的位置设置了值

6. **确认后重试**：
   - 用户确认环境变量可用后，重试原始消息发送任务

**重要**：错误消息格式是严格的，必须完全按照脚本提供的显示。不要修改或改写。

### 错误处理

**认证失败（401）**：
```
error: Authentication failed
```
→ Access Token无效，使用正确的凭证重新配置

**权限不足（403）**：
```
error: Permission denied
```
→ 检查应用权限配置，确保有发送消息权限

**号码格式错误（400）**：
```
error: Invalid phone number
```
→ 检查号码格式，确保使用国家码+号码格式

**消息类型错误（400）**：
```
error: Invalid message type
```
→ 检查消息参数格式和类型

**模板未审批（400）**：
```
error: Template not approved
```
→ 模板消息需先在WhatsApp Business Manager中审批

**API配额超限（429）**：
```
error: Rate limit exceeded
```
→ 超过发送频率限制，等待后重试

**接收者不在WhatsApp（404）**：
```
error: Recipient not on WhatsApp
```
→ 该号码未注册WhatsApp

### 支持的媒体格式

#### 图片格式
- JPEG, PNG
- 最大大小：5MB
- 推荐尺寸：400x400 到 800x800

#### 视频格式
- MP4, 3GPP
- 最大大小：16MB
- H.264视频编码，AAC音频编码

#### 音频格式
- AAC, AMR, MP3, OGG
- 最大大小：16MB

#### 文档格式
- PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX
- 最大大小：100MB

#### 贴纸格式
- WebP
- 最大大小：100KB
- 推荐：512x512

### 最佳实践

1. **遵守WhatsApp政策**
   - 不要发送垃圾消息
   - 获得用户同意后再发送营销消息
   - 提供退订选项

2. **合理使用模板消息**
   - 重要通知使用模板消息
   - 确保模板已审批
   - 模板参数要准确

3. **消息发送频率**
   - 避免短时间内大量发送
   - 遵守API速率限制

4. **处理回执**
   - 使用message_id跟踪消息状态
   - 处理已发送、已送达、已读状态

5. **用户隐私**
   - 保护用户手机号等隐私信息
   - 遵守GDPR等隐私法规

## 重要说明

- **脚本从不修改内容** - 它总是发送用户指定的准确内容
- **AI代理决定消息内容** - 基于用户的具体请求
- **支持所有消息类型** - 文本、媒体、模板、交互等
- **完整的功能覆盖** - 支持WhatsApp Business API所有主要功能

## 参考文档

详细的API文档请参考：
- WhatsApp Business API官方文档：https://developers.facebook.com/docs/whatsapp
- 消息类型参考：https://developers.facebook.com/docs/whatsapp/api/messages
- 模板消息指南：https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates

## 测试技能

要验证技能是否正常工作：
```bash
python scripts/test_api.py
```

这将测试配置和API连接。
