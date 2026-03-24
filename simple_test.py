import sys
sys.path.insert(0, 'c:/repos/aid-work-agent')

import requests
import json

BASE_URL = "http://127.0.0.1:8000"

try:
    # 登录
    print("1. 登录...")
    login_data = {"phone": "18117370416", "code": "888888"}

    resp = requests.post(f"{BASE_URL}/api/auth/phone/code-login", json=login_data, timeout=10)
    print(f"状态码: {resp.status_code}")
    result = resp.json()
    print(f"响应: {json.dumps(result, ensure_ascii=False)}")

    if result.get('success'):
        token = result['token']
        user_id = result['user']['user_id']
        print(f"\n[OK] 登录成功! user_id={user_id}")

        headers = {'Authorization': f'Bearer {token}'}

        # 获取会话列表
        print("\n2. 获取会话列表...")
        resp = requests.get(f"{BASE_URL}/api/sessions", headers=headers, timeout=10)
        sessions = resp.json().get('sessions', [])
        print(f"会话数量: {len(sessions)}")

        # 发送聊天
        print("\n3. 发送聊天消息...")
        chat_data = {"message": "你好"}
        resp = requests.post(
            f"{BASE_URL}/api/chat/stream",
            headers={**headers, 'Content-Type': 'application/json'},
            json=chat_data,
            stream=True,
            timeout=60
        )
        print(f"状态码: {resp.status_code}")

        # 读取响应
        for line in resp.iter_lines():
            if line:
                try:
                    data = json.loads(line.decode('utf-8')[6:])
                    if data.get('type') == 'response':
                        print(f"AI: {data.get('data', '')[:100]}")
                    elif data.get('type') == 'error':
                        print(f"错误: {data.get('data')}")
                    elif data.get('type') == 'complete':
                        break
                except:
                    pass

        # 再次获取会话列表
        print("\n4. 再次获取会话列表...")
        resp = requests.get(f"{BASE_URL}/api/sessions", headers=headers, timeout=10)
        sessions = resp.json().get('sessions', [])
        print(f"会话数量: {len(sessions)}")
        if sessions:
            s = sessions[0]
            print(f"最新会话: session_id={s['session_id']}, user_id={s['user_id']}, title={s['title']}")

            # 获取会话记录
            print("\n5. 获取会话记录...")
            resp = requests.get(f"{BASE_URL}/api/sessions/{s['session_id']}/records", headers=headers, timeout=10)
            records = resp.json().get('records', [])
            print(f"记录数量: {len(records)}")
            for r in records:
                print(f"  - record_id={r['record_id']}, user_id={r['user_id']}")
    else:
        print(f"[FAIL] 登录失败: {result}")
except Exception as e:
    print(f"异常: {e}")
    import traceback
    traceback.print_exc()