#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊易服务器 nginx conf 定向插入 demo2（连接 agent2）API location（sudo python3 执行，幂等）

静态登录页 web_c_sig_demo2.html 无需新 location：已有正则
^/web_c_[A-Za-z0-9_-]+_demo\.html$ 天然匹配带 2 后缀的文件名。
本脚本只补 API 代理：/web_c_sig_demo2/api/ -> 127.0.0.1:8791。
"""
import sys

PATH = '/etc/nginx/sites-available/agent.0635easy.com.conf'
ANCHOR = '''    # ========== 前端路由配置 ==========
    # SPA 应用 - 所有路由返回 index.html
    location / {
        root /var/www/agent/app/frontend/dist/;
'''
BLOCK = '''    # ========== 签名链接 demo2 登录后端（聊易托管页 -> agent2 测试环境聊天页） ==========
    # 静态登录页 web_c_sig_demo2.html 由上方 web_c_*_demo.html 正则直接命中，无需新 location；
    # 本 location 代理登录签名 API 到宿主机 systemd 服务（web-c-sig-demo2.service，
    # 127.0.0.1:8791，纯 stdlib Python，账号硬编码仅 demo 用）。proxy_pass 尾斜杠剥离前缀：
    # /web_c_sig_demo2/api/login -> 后端 /login。与 demo1（/web_c_sig_demo/api/ -> 8790）并存。
    location /web_c_sig_demo2/api/ {
        proxy_pass http://127.0.0.1:8791/;
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
if '/web_c_sig_demo2/api/' in s:
    print('already patched, skip')
    sys.exit(0)
assert ANCHOR in s, 'anchor not found'
open(PATH, 'w', encoding='utf-8').write(s.replace(ANCHOR, BLOCK + ANCHOR, 1))
print('patched')
