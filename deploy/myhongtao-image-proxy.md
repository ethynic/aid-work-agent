# 宏陶 OSS 图片反向代理（myhongtao.aidingyi.cn）

> **状态**：✅ 已完成并验证（2026-09-22，测试服务器生效，真实图片 URL 微信内可访问）

## 背景

为 hongtao（宏陶）客户抓取的数据中包含 OSS 图片链接（形如
`https://myhongtao.oss-cn-shen-zhen.aliyuncs.com/up-load/1/20260907/xxx.jpg`），
该 OSS 域名在微信环境内无法显示（被微信屏蔽）。

方案：在测试服务器（124.222.3.254）nginx 上配置反向代理，用自有域名
`myhongtao.aidingyi.cn` 转发到宏陶 OSS，数据中的图片链接替换为自有域名后即可在微信内正常显示。

## 转发关系

```
https://myhongtao.aidingyi.cn/*  ->  https://myhongtao.oss-cn-shenzhen.aliyuncs.com/*
```

- 路径 1:1 原样透传，仅替换域名
- HTTP 80 强制 301 跳转 HTTPS
- 证书复用泛域名证书 `STAR_aidingyi_cn`（有效期至 2027-02-15）

## 重要发现：上游域名必须是 oss-cn-shenzhen

数据源中出现的 `oss-cn-shen-zhen`（`shen` 与 `zhen` 之间多一个连字符）**不是有效的
阿里云 OSS region 域名**，DNS 解析 NXDOMAIN，任何环境都无法访问。正确的深圳 region
写法是 `oss-cn-shenzhen`（已实测可解析、bucket 公共读可访问）。

如果抓取数据中确实存的是 `oss-cn-shen-zhen` 链接，说明数据源头就是坏的，仅靠反代救不回来，
需要把链接中的域名统一替换为 `myhongtao.aidingyi.cn`（顺带修正）。

## 配置位置

| 位置 | 文件 |
|------|------|
| 测试服务器（124.222.3.254） | `/etc/nginx/conf.d/myhongtao.aidingyi.cn.conf` |
| 仓库副本 | [myhongtao.aidingyi.cn.conf](./myhongtao.aidingyi.cn.conf) |

关键配置点：

- `proxy_set_header Host myhongtao.oss-cn-shenzhen.aliyuncs.com` -- OSS 依赖 Host 头命中 bucket，必须改写
- `proxy_ssl_server_name on` -- 上游 HTTPS 需要 SNI
- `limit_rate 128k` + `limit_rate_after 1m` -- 限速保护 5Mbps 出带宽：前 1MB 全速（小图无感，实测 350KB 图 0.25s 加载完），超过后单连接约 1Mbps
- **不加 `limit_conn`** -- 微信消息内多图并发加载，并发限制会导致第 3 张图起 503（同 inline 预览例外，见 backend_dev.md 下载限速规范）
- `expires 7d` -- 静态图片允许浏览器缓存

## 部署与验证（2026-09-22 已完成）

```bash
# 修改后检查并重载
ssh ubuntu@124.222.3.254 "sudo -n nginx -t && sudo -n systemctl reload nginx"

# 验证（代理链路通，OSS 返回原样响应）
curl -sk -o /dev/null -w '%{http_code}\n' https://myhongtao.aidingyi.cn/up-load/1/test.jpg
```

当日验证结果：

- HTTPS 证书正常（`*.aidingyi.cn`）
- 80 端口 301 跳转 HTTPS 正常
- 代理请求已转发到 OSS，链路通
- ✅ 用户已用真实图片 URL 验证通过：`https://myhongtao.aidingyi.cn/upload/1/1e090d10_1752718922_9637.jpg` 可正常访问

## 后续

- 若生产也需要（图片出现在生产租户数据中），需在生产服务器（129.211.65.243）同步该 conf
  并确认 DNS `myhongtao.aidingyi.cn` 指向（当前指向测试机 254）
