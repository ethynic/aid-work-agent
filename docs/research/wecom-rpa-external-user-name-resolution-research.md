# 企业微信个人 RPA 外部联系人 ID 姓名解析调研

> 日期：2026-07-15
> 范围：仅企业微信个人 RPA 渠道，不依赖 `wecom_kf` 或其他渠道数据。

> 2026-07-16 实现状态：已接入独立 `external_contact_secret`、客户详情解析缓存、首次绑定姓名注入和管理页有界历史回填。解析失败不阻塞归档游标；缺少可靠搜索名时出站失败关闭。

## 结论

会话内容存档负责返回消息及参与者 ID；外部联系人的可读资料需要通过客户联系接口查询。对于归档中的 `wm...` / `wo...` 外部联系人 ID，使用具备“客户联系—客户基础信息”权限的 access token 调用：

```http
GET https://qyapi.weixin.qq.com/cgi-bin/externalcontact/get
    ?access_token=ACCESS_TOKEN
    &external_userid=EXTERNAL_USERID
```

成功响应的 `external_contact.name` 是基础姓名；`follow_user[].remark` 是企业成员对客户设置的备注。当前实现安全使用 `external_contact.name`：账号映射无法证明与某个 follow_user 一一对应时，不能误取其他跟进人的备注。

现有 agent2 RPA 配置使用会话存档 Secret。2026-07-15 在 `aid-agent-api2` 以该 Secret 实测 `externalcontact/get`，返回 `errcode=48002, api forbidden`。这说明 CorpID、Secret 和 access token 链路有效，但该 Secret 没有客户联系接口权限。

## 官方能力与权限

### 单个客户详情

- 接口：`GET /cgi-bin/externalcontact/get`
- 入参：`external_userid`
- 返回：`external_contact.name/type/avatar/corp_name`，以及 `follow_user` 跟进关系和备注。
- 权限：使用“客户联系”Secret，或把自建应用加入客户联系的“可调用应用”并授予客户基础信息权限。
- 官方文档：<https://open.work.weixin.qq.com/api/doc/90000/90135/92114>

### 批量客户详情

- 接口：`POST /cgi-bin/externalcontact/batch/get_by_user`
- 入参是企业成员 `userid_list`，返回这些成员跟进的客户详情；适合定期同步，不适合仅拿到一个 external_userid 时直接查询。
- 自建应用调用时，成员必须处于应用可见范围。
- 官方文档：<https://open.work.weixin.qq.com/api/doc/90000/90135/92994>

### 存档同意事件

客户同意会话存档事件提供 `UserID` 与 `ExternalUserID`，官方说明可根据 `ExternalUserID` 调用“获取客户详情”。因此正确架构是“归档/事件取得 ID，客户联系接口补充资料”，不是期待消息体直接携带姓名。

## 管理后台配置

推荐使用独立客户联系凭证，避免误用会话存档 Secret：

1. 企业微信管理后台进入“客户联系”相关 API 配置。
2. 获取客户联系 Secret；或将专用自建应用加入“可调用应用”。
3. 授予客户基础信息读取权限，并确保应用可见范围覆盖实际跟进这些外部联系人的企业成员。
4. 将服务器出口 IP `124.222.3.254` 加入可信 IP（若该应用要求）。
5. 在 RPA 渠道配置新增加密字段 `external_contact_secret`，不得复用或明文展示。

## 推荐实现

1. `ServerArchiveFetcher` 判断入站对端是外部联系人后，取得 `external_userid`。
2. `ExternalContactResolver` 使用 `corp_id + external_contact_secret` 获取并缓存 access token。
3. 调用 `externalcontact/get`，校验响应 ID 与请求 ID 一致。
4. 选择显示名并写入 RPA 独立映射缓存；缓存键必须包含 `tenant_id + corp_id + external_userid`。
5. 将真实名称传给现有 `get_or_create_binding()`；其占位保护逻辑只替换空值、`unknown`、stable_id/search_key，不覆盖人工名称。
6. 对历史绑定做租户范围批量回填；失败保留 ID，按错误码退避，不阻塞消息与 Agent 主链路。

当前历史回填在租户管理员打开绑定列表时触发，每次最多处理 20 条占位记录；姓名成功缓存 24 小时，`48002/60020` 退避 1 小时，其他接口错误退避 5 分钟，网络错误退避 1 分钟。数据库更新条件再次校验 tenant、stable_id 和占位状态，因此幂等且不会覆盖人工名称。

## 安全与边界

- 严格租户隔离，禁止从 `wecom_kf`、其他租户或其他 CorpID 反查。
- Secret 使用现有 Fernet 机制加密，日志不得记录 Secret/access token。
- `48002` 属权限配置错误，不应高频重试；`40014/42001` 等 token 错误可刷新一次 token 后重试。
- 客户被删除、无跟进关系或不可见时可能查询失败，继续显示 stable_id 并记录可诊断错误。
- 内部成员 `userid` 不进入外部联系人绑定列表；若运维诊断需要姓名，使用通讯录成员接口，但不得改变前端只展示外部联系人的产品规则。

## 验收

- 使用新 Secret 查询 `wmS6oOTAAAT3USzBQrexUOgGBSlyi03w`，接口返回 `errcode=0` 且有非空 `external_contact.name`。
- 新外部会话首次进入时直接显示姓名或备注。
- 发布后历史 ID 占位绑定可批量回填，无需客户再次发消息。
- 内部员工会话不创建绑定；跨租户、跨 CorpID 不串名。
- 权限错误不阻塞归档游标和消息处理。
