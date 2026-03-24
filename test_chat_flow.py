"""测试会话记录用户身份修复"""
import requests
import json

BASE_URL = "http://127.0.0.1:8000"

def test_flow():
    print("=" * 60)
    print("测试会话记录用户身份修复")
    print("=" * 60)

    # 1. 手机号验证码登录
    print("\n1. 手机号验证码登录...")
    login_data = {
        "phone": "18117370416",
        "code": "888888"
    }
    resp = requests.post(f"{BASE_URL}/api/auth/phone/code-login", json=login_data)
    result = resp.json()
    print(f"   登录响应: {json.dumps(result, ensure_ascii=False)}")

    if not result.get("success"):
        print("   ❌ 登录失败")
        return

    token = result.get("token")
    user_id = result.get("user", {}).get("user_id")
    print(f"   ✅ 登录成功")
    print(f"   用户ID: {user_id}")
    print(f"   Token: {token[:20]}...")

    headers = {"Authorization": f"Bearer {token}"}

    # 2. 获取会话列表
    print("\n2. 获取会话列表...")
    resp = requests.get(f"{BASE_URL}/api/sessions", headers=headers)
    sessions_result = resp.json()
    print(f"   响应: {json.dumps(sessions_result, ensure_ascii=False)}")
    print(f"   会话数量: {len(sessions_result.get('sessions', []))}")

    # 3. 调用 stream 接口发送聊天消息
    print("\n3. 调用 stream 接口发送聊天消息...")
    chat_data = {
        "message": "你好",
        "session_id": None  # 不传session_id，让后端创建新的
    }
    resp = requests.post(
        f"{BASE_URL}/api/chat/stream",
        headers={**headers, "Content-Type": "application/json"},
        json=chat_data,
        stream=True
    )
    print(f"   响应状态: {resp.status_code}")

    # 读取流式响应
    full_response = ""
    for line in resp.iter_lines():
        if line:
            line_str = line.decode('utf-8')
            if line_str.startswith("data: "):
                data = line_str[6:]
                try:
                    event = json.loads(data)
                    if event.get("type") == "response":
                        full_response += event.get("data", "")
                    elif event.get("type") == "complete":
                        break
                    elif event.get("type") == "error":
                        print(f"   ❌ 错误: {event.get('data')}")
                except:
                    pass

    print(f"   AI回复: {full_response[:100]}...")

    # 4. 再次获取会话列表
    print("\n4. 再次获取会话列表...")
    resp = requests.get(f"{BASE_URL}/api/sessions", headers=headers)
    sessions_result = resp.json()
    print(f"   响应: {json.dumps(sessions_result, ensure_ascii=False)}")
    sessions = sessions_result.get('sessions', [])
    print(f"   会话数量: {len(sessions)}")

    if sessions:
        latest_session = sessions[0]
        print(f"   最新会话ID: {latest_session.get('session_id')}")
        print(f"   最新会话用户ID: {latest_session.get('user_id')}")
        print(f"   最新会话标题: {latest_session.get('title')}")

        # 5. 检查会话记录
        print("\n5. 检查会话记录...")
        session_id = latest_session.get('session_id')
        resp = requests.get(f"{BASE_URL}/api/sessions/{session_id}/records", headers=headers)
        records_result = resp.json()
        print(f"   响应: {json.dumps(records_result, ensure_ascii=False)}")
        records = records_result.get('records', [])
        print(f"   记录数量: {len(records)}")

        if records:
            for record in records:
                print(f"   - record_id: {record.get('record_id')}")
                print(f"     user_id: {record.get('user_id')}")
                print(f"     user_message: {record.get('user_message')[:50]}...")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == "__main__":
    test_flow()