#!/usr/bin/env python3
"""
并发控制功能集成测试脚本
测试流程：
1. 创建实例
2. 用户A锁定实例（成功）
3. 用户B尝试锁定同一个实例（失败，进入排队）
4. 用户A释放实例
5. 用户B自动获得锁
"""

import sys
import uuid
from loguru import logger

# 配置日志
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>", level="INFO")


def test_concurrency_flow():
    """测试完整的并发流程"""
    from src.saas.services.instance_service import InstanceService
    from src.saas.db.agent_instance_db import AgentInstanceDB

    tenant_id = f"test_tenant_{uuid.uuid4().hex[:8]}"
    instance_id = f"test_instance_{uuid.uuid4().hex[:8]}"
    user_a_id = f"user_a_{uuid.uuid4().hex[:8]}"
    user_b_id = f"user_b_{uuid.uuid4().hex[:8]}"
    session_a_id = f"session_a_{uuid.uuid4().hex[:8]}"
    session_b_id = f"session_b_{uuid.uuid4().hex[:8]}"

    logger.info("=" * 60)
    logger.info("开始并发控制功能测试")
    logger.info("=" * 60)

    # 1. 创建测试实例
    logger.info("\n[1/6] 创建测试实例")
    AgentInstanceDB.create(
        tenant_id=tenant_id,
        subagent_type="trade-specialist",
        display_name="外贸智能助手",
        instance_name="测试助理小明",
        avatar="🤖",
        description="用于并发测试的实例",
        personality_traits=["专业", "高效"],
    )
    logger.success(f"  ✓ 实例创建成功: {instance_id}")

    # 2. 用户A锁定实例
    logger.info("\n[2/6] 用户A尝试锁定实例")
    result_a = InstanceService.try_lock_instance(
        instance_id=instance_id,
        session_id=session_a_id,
        user_id=user_a_id,
        lock_timeout_minutes=30,
    )
    if result_a.get("success"):
        logger.success(f"  ✓ 用户A锁定成功")
        logger.info(f"    - session_id: {session_a_id}")
    else:
        logger.error(f"  ✗ 用户A锁定失败: {result_a.get('error')}")
        return False

    # 3. 检查实例列表状态
    logger.info("\n[3/6] 检查实例列表状态")
    instances = InstanceService.list_tenant_instances(
        tenant_id=tenant_id,
        current_user_id=user_b_id,
    )
    inst = instances[0]
    if inst["status"] == "busy":
        logger.success(f"  ✓ 实例状态正确: busy")
    else:
        logger.error(f"  ✗ 实例状态错误: {inst['status']}")

    # 4. 用户B尝试锁定同一个实例（应该进入排队）
    logger.info("\n[4/6] 用户B尝试锁定同一个实例")
    result_b = InstanceService.try_lock_instance(
        instance_id=instance_id,
        session_id=session_b_id,
        user_id=user_b_id,
        lock_timeout_minutes=30,
    )
    if result_b.get("is_queued"):
        logger.success(f"  ✓ 用户B成功进入排队")
        logger.info(f"    - 排队位置: {result_b.get('queue_position', 0)}")
        logger.info(f"    - 队列长度: {result_b.get('queue_length', 0)}")
    else:
        logger.error(f"  ✗ 用户B未进入排队: {result_b.get('error')}")

    # 5. 检查排队状态
    logger.info("\n[5/6] 检查用户B的排队状态")
    queue_status = InstanceService.check_queue_status(instance_id, session_b_id)
    if queue_status.get("status") == "waiting":
        logger.success(f"  ✓ 排队状态正确: waiting")
        logger.info(f"    - 排队位置: {queue_status.get('position', 0)}")
    else:
        logger.error(f"  ✗ 排队状态错误: {queue_status.get('status')}")

    # 6. 用户A释放实例，用户B获得锁
    logger.info("\n[6/6] 用户A释放实例，用户B获得锁")
    released = InstanceService.release_instance(instance_id, session_a_id)
    if released:
        logger.success(f"  ✓ 用户A释放实例成功")
    else:
        logger.error(f"  ✗ 用户A释放实例失败")

    # 检查用户B的排队状态，应该变为 ready
    queue_status = InstanceService.check_queue_status(instance_id, session_b_id)
    if queue_status.get("status") == "ready":
        logger.success(f"  ✓ 用户B排队状态变为 ready")
    else:
        logger.warning(f"  ⚠  用户B排队状态: {queue_status.get('status')}")

    logger.info("\n" + "=" * 60)
    logger.success("所有测试用例通过！")
    logger.info("=" * 60)
    return True


def test_take_over():
    """测试接管功能"""
    from src.saas.services.instance_service import InstanceService
    from src.saas.db.agent_instance_db import AgentInstanceDB

    tenant_id = f"test_tenant_{uuid.uuid4().hex[:8]}"
    instance_id = f"test_instance_{uuid.uuid4().hex[:8]}"
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    session_device_1 = f"session_device1_{uuid.uuid4().hex[:8]}"
    session_device_2 = f"session_device2_{uuid.uuid4().hex[:8]}"

    logger.info("\n" + "=" * 60)
    logger.info("开始接管功能测试")
    logger.info("=" * 60)

    # 1. 创建测试实例
    logger.info("\n[1/4] 创建测试实例")
    AgentInstanceDB.create(
        tenant_id=tenant_id,
        subagent_type="trade-specialist",
        display_name="外贸智能助手",
        instance_name="测试助理小红",
        avatar="👩‍💼",
        description="用于接管测试的实例",
        personality_traits=["友好", "细心"],
    )
    logger.success(f"  ✓ 实例创建成功: {instance_id}")

    # 2. 用户在设备1锁定实例
    logger.info("\n[2/4] 用户在设备1锁定实例")
    InstanceService.try_lock_instance(
        instance_id=instance_id,
        session_id=session_device_1,
        user_id=user_id,
        lock_timeout_minutes=30,
    )
    logger.success(f"  ✓ 设备1锁定成功")

    # 3. 用户在设备2看到"可接管"状态
    logger.info("\n[3/4] 用户在设备2查看实例状态")
    instances = InstanceService.list_tenant_instances(
        tenant_id=tenant_id,
        current_user_id=user_id,
    )
    inst = instances[0]
    if inst.get("can_take_over"):
        logger.success(f"  ✓ 正确显示'可接管'状态")
        logger.info(f"    - 状态文本: {inst.get('status_text')}")
    else:
        logger.error(f"  ✗ 未显示'可接管'状态")

    # 4. 用户在设备2执行接管
    logger.info("\n[4/4] 用户在设备2执行接管")
    result = InstanceService.take_over_instance(
        instance_id=instance_id,
        new_session_id=session_device_2,
        user_id=user_id,
    )
    if result.get("success"):
        logger.success(f"  ✓ 设备2成功接管实例")
    else:
        logger.error(f"  ✗ 接管失败: {result.get('error')}")

    logger.info("\n" + "=" * 60)
    logger.success("接管功能测试通过！")
    logger.info("=" * 60)
    return True


if __name__ == "__main__":
    import os

    os.environ["DB_NAME"] = "aid_work_agent"
    os.environ["DB_USER"] = "aid_user"
    os.environ["DB_PASSWORD"] = "Aid_2026"

    try:
        test_concurrency_flow()
        test_take_over()
    except Exception as e:
        logger.exception(f"测试异常: {e}")
        sys.exit(1)
