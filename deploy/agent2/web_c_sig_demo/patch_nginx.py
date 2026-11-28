#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent2 服务器 nginx conf 定向插入签名 demo API location（sudo python3 执行，幂等）"""
import sys

PATH = '/etc/nginx/sites-available/agent2.aidingyi.cn.conf'
ANCHOR = '''    # ========== 前端路由配置 ==========
    # SPA 应用 - 所有路由返回 index.html
    location / {
        root /var/www/agent2/frontend/dist/;
'''
BLOCK = '''    # ========== 演示页（dist 外独立目录，前端发布不覆盖） ==========
    # /var/www/agent2/www_static/ 在仓库与 dist 之外：agent2_update.sh 只替换
    # frontend/dist，放这里无需在发布脚本里加保留白名单。文件含渠道 ID，不入 git，
    # 后续修改直接在服务器上覆盖该文件即可（无需 reload，nginx 每次请求现读）。
    # 命名规则 web_c_*_demo.html：新增同类演示页按此命名放入目录即生效。
    location ~ ^/web_c_[A-Za-z0-9_-]+_demo\\.html$ {
        root /var/www/agent2/www_static/;
        default_type text/html;
        charset utf-8;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        error_page 404 =404 @web_c_demo_missing;
        access_log off;
    }
    # 覆盖 server 级 error_page 404 /index.html，缺文件时返回真 404 而不是 SPA 首页
    location @web_c_demo_missing {
        default_type text/plain;
        return 404 "demo page missing\\n";
    }

    # ========== 签名链接 demo 登录后端（web_c ext 模式演示） ==========
    # 静态登录页 web_c_sig_demo.html 走上面的 www_static 正则；本 location 代理
    # 登录签名 API 到宿主机 systemd 服务（web-c-sig-demo.service，127.0.0.1:8790，
    # 纯 stdlib Python，账号硬编码仅 demo 用）。proxy_pass 尾斜杠剥离前缀：
    # /web_c_sig_demo/api/login -> 后端 /login。
    location /web_c_sig_demo/api/ {
        proxy_pass http://127.0.0.1:8790/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

'''

s = open(PATH, encoding='utf-8').read()
if '/web_c_sig_demo/api/' in s:
    print('already patched, skip')
    sys.exit(0)
assert ANCHOR in s, 'anchor not found'
open(PATH, 'w', encoding='utf-8').write(s.replace(ANCHOR, BLOCK + ANCHOR, 1))
print('patched')
