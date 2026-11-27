#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聊易服务器 nginx conf 定向插入签名 demo API location（sudo python3 执行，幂等）"""
import sys

PATH = '/etc/nginx/sites-available/agent.0635easy.com.conf'
ANCHOR = '''    location @web_c_demo_missing {
        default_type text/plain;
        return 404 "demo page missing\\n";
    }
'''
BLOCK = '''
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
open(PATH, 'w', encoding='utf-8').write(s.replace(ANCHOR, ANCHOR + BLOCK, 1))
print('patched')
