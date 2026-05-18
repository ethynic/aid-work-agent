# SMB/FTP 文件上传工具使用说明

## 功能概述

SMB/FTP 文件上传工具允许智能体将用户上传的文件或中间过程生成的文件上传到 SMB 或 FTP 服务器。

**核心特性**：
- 支持 SMB 和 FTP 两种协议
- 用户凭据加密存储，安全可靠
- 智能检测凭据，自动提示用户配置
- 前端可视化凭据管理界面

---

## 安装依赖

```bash
pip install -r requirements.txt
```

新增依赖：
- `pysmb>=1.2.9` - SMB 协议支持
- `cryptography>=41.0.0` - 密码加密

---

## 数据库初始化

首次使用需要初始化数据库（会自动创建 `remote_credentials` 表）：

```bash
python test_remote_credentials.py
```

或者直接启动服务（会自动初始化）：

```bash
python -m src.main
```

---

## 使用流程

### 1. 配置凭据

#### 方式一：通过前端页面配置

1. 启动前端服务：
   ```bash
   cd frontend
   npm run dev
   ```

2. 启动后端服务：
   ```bash
   python -m src.main
   ```

3. 在前端页面点击右上角的"凭据管理"按钮

4. 点击"+ 添加凭据"，填写以下信息：
   - **连接类型**：SMB 或 FTP
   - **凭据名称**：可选，用于标识该凭据
   - **服务器地址**：例如 `192.168.1.100` 或 `ftp.example.com`
   - **服务器端口**：SMB 默认 445，FTP 默认 21
   - **域**：仅 SMB 可选，例如 `WORKGROUP`
   - **用户名**：服务器登录用户名
   - **密码**：服务器登录密码
   - **远程路径**：目标目录路径，例如 `/share/folder` 或 `/var/www/uploads`
   - **描述**：可选，添加备注信息

5. 点击"创建"保存凭据

#### 方式二：通过 API 配置

```bash
curl -X POST http://localhost:8000/api/credentials \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "connection_type": "smb",
    "server_host": "192.168.1.100",
    "server_port": 445,
    "username": "admin",
    "password": "your_password",
    "remote_path": "/share/folder",
    "name": "我的SMB服务器",
    "domain": "WORKGROUP"
  }'
```

### 2. 使用智能体上传文件

在对话中，您可以告诉智能体将文件上传到指定路径：

**示例 1**：上传已存在的文件
```
用户：请把 uploads/report.pdf 上传到我的 SMB 服务器 /share/folder 目录
```

**示例 2**：上传用户上传的文件
```
用户：把刚才上传的那个 Excel 文件传到 FTP 服务器 /var/www/uploads
```

**示例 3**：指定文件名
```
用户：把 data.xlsx 上传到 /share/reports，命名为 月度报告.xlsx
```

### 3. 智能体自动处理流程

1. **检测凭据**：智能体自动查找该路径的凭据
2. **上传文件**：使用凭据进行文件上传
3. **返回结果**：告知用户上传成功或失败原因

**如果凭据未配置**：
```
智能体：检测到路径 /share/folder 的 SMB 凭据未配置，
请点击下方链接配置凭据后继续：
http://localhost:3000/credentials/add?path=/share/folder&type=smb

（用户配置完成后）
智能体：已收到凭据配置完成通知，正在继续上传...
✅ 文件已成功上传到 SMB 服务器: //192.168.1.100/share/folder/report.pdf
```

---

## API 接口

### 1. 创建凭据

```bash
POST /api/credentials
```

**请求体**：
```json
{
  "connection_type": "smb",  // "smb" 或 "ftp"
  "server_host": "192.168.1.100",
  "server_port": 445,
  "username": "admin",
  "password": "your_password",
  "remote_path": "/share/folder",
  "name": "我的SMB服务器",
  "domain": "WORKGROUP",  // 仅 SMB 可选
  "description": "用于测试"
}
```

**响应**：
```json
{
  "success": true,
  "data": {
    "credential_id": "cred_abc123"
  },
  "message": "凭据创建成功"
}
```

### 2. 列出凭据

```bash
GET /api/credentials?connection_type=smb
```

**响应**：
```json
{
  "success": true,
  "data": [
    {
      "credential_id": "cred_abc123",
      "connection_type": "smb",
      "server_host": "192.168.1.100",
      "server_port": 445,
      "username": "admin",
      "remote_path": "/share/folder",
      "domain": "WORKGROUP",
      "name": "我的SMB服务器",
      "description": "用于测试",
      "status": 1,
      "created_at": "2026-03-24 10:00:00",
      "updated_at": "2026-03-24 10:00:00"
    }
  ],
  "count": 1
}
```

### 3. 获取凭据详情（包含解密密码）

```bash
GET /api/credentials/{credential_id}
```

### 4. 更新凭据

```bash
PUT /api/credentials/{credential_id}
```

**请求体**（可选字段）：
```json
{
  "password": "new_password",
  "description": "更新后的描述"
}
```

### 5. 删除凭据（软删除）

```bash
DELETE /api/credentials/{credential_id}
```

---

## 工具参数

`upload_to_remote` 工具参数：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 要上传的本地文件路径 |
| `remote_path` | string | 是 | 远程服务器路径 |
| `connection_type` | string | 是 | 连接类型：`smb` 或 `ftp` |
| `filename` | string | 否 | 上传后的文件名（默认使用原文件名） |

---

## 安全说明

1. **密码加密存储**：所有密码使用 Fernet 对称加密存储
2. **生产环境配置**：生产环境必须配置 `ENCRYPTION_KEY` 环境变量

   ```bash
   export ENCRYPTION_KEY="your-base64-encoded-32-byte-key"
   ```

   生成密钥：
   ```python
   from cryptography.fernet import Fernet
   print(Fernet.generate_key().decode())
   ```

3. **用户隔离**：每个用户只能访问自己的凭据
4. **软删除**：删除凭据采用软删除方式，数据可恢复

---

## 错误处理

### 常见错误

| 错误信息 | 原因 | 解决方案 |
|---------|------|----------|
| `凭据不存在或无权访问` | 凭据ID错误或无权限 | 检查凭据ID和登录状态 |
| `缺少 SMB 依赖库` | 未安装 pysmb | `pip install pysmb` |
| `SMB 服务器连接失败` | 网络或认证问题 | 检查服务器地址、端口、用户名密码 |
| `FTP 文件上传失败` | 网络或权限问题 | 检查服务器配置和目录权限 |
| `文件不存在` | 本地文件路径错误 | 检查文件路径 |

---

## 测试

运行完整测试：

```bash
python test_remote_credentials.py
```

测试内容：
- ✅ 加密/解密功能
- ✅ 创建 SMB/FTP 凭据
- ✅ 列出凭据
- ✅ 获取凭据详情（解密密码）
- ✅ 更新凭据
- ✅ 删除凭据（软删除）
- ✅ 上传工具参数验证

---

## 注意事项

1. **SMB 路径格式**：
   - 格式：`/share/folder`
   - `share` 是共享名，`folder` 是子目录
   - 例如：`//192.168.1.100/share/folder` → `/share/folder`

2. **FTP 路径格式**：
   - 格式：`/var/www/uploads`
   - 使用绝对路径
   - 不存在的目录会自动创建

3. **端口配置**：
   - SMB 默认端口：445
   - FTP 默认端口：21
   - 根据服务器实际情况调整

4. **文件大小限制**：
   - 取决于服务器配置
   - 大文件上传可能超时，建议分片上传

---

## 后续优化方向

- [ ] 支持文件分片上传
- [ ] 支持上传进度显示
- [ ] 支持 SFTP 协议
- [ ] 支持多文件批量上传
- [ ] 支持上传前文件校验（MD5）
- [ ] 支持上传历史记录
