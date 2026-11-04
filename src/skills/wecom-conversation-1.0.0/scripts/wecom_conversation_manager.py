"""企微会话业务 Skill 命令入口（薄封装，全部经 src/wecom_conversation）。

唯一命令：setup-daily-report——代理会话中经用户确认后建立/扩展日报计划。
聊天记录是旁路日志：由服务器在结果受理后异步保存，本 Skill 不查询/不保存
业务聊天记录、不回写处理状态、不直连模型（设计 §3/§5/§7）。

身份来源：AID_TENANT_ID / AID_USER_ID 由 ToolExecutionContext 注入
（skill_executor，可信上下文），不信任模型自报身份。输出 JSON：
{"success": bool, "data": {...}, "error": "...", "code": "..."}。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 从脚本位置回溯项目根（与 followup_manager 同范式），保证 skill 目录 cwd 下可导入 src
script_path = Path(__file__).resolve()
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


from src.wecom_conversation.service import ConversationServiceError  # noqa: E402


def _identity():
    tenant_id = os.environ.get('AID_TENANT_ID')
    user_id = os.environ.get('AID_USER_ID')
    if not tenant_id or not user_id:
        return None
    return str(tenant_id), str(user_id)


def _emit(data=None, error=None, code=None):
    payload = {'success': error is None}
    if data is not None:
        payload['data'] = data
    if error:
        payload['error'] = error
        payload['code'] = code or 'COMMAND_FAILED'
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if error is None else 1


def _cmd_setup_daily_report(args):
    """建立/扩展日报计划（设计 §5.1）：同来源复用合适计划，一个计划多会话。"""
    from src.wecom_conversation import plans

    identity = _identity()
    if identity is None:
        return _emit(error='缺少可信身份上下文（AID_TENANT_ID/AID_USER_ID）', code='IDENTITY_MISSING')
    tenant_id, user_id = identity
    schedule = {'schedule_type': args.schedule_type}
    if args.cron:
        schedule['cron_expression'] = args.cron
    if args.interval_seconds:
        schedule['interval_seconds'] = args.interval_seconds
    if args.run_at:
        schedule['run_at'] = args.run_at
    locators = [item.strip() for item in (args.sessions or '').split('|') if item.strip()]
    result = plans.ensure_daily_plan(
        tenant_id, user_id, locators=locators, schedule=schedule,
        profile_id=args.profile, timezone_name=args.timezone, device_id=args.device,
        requirements=args.requirements or '', instance_id=args.instance)
    return _emit(data={
        'plan_task_id': (result['plan'] or {}).get('task_id'),
        'created': result['created'],
        'conversation_ids': [row['id'] for row in result['conversations']],
        'message': '日报计划已建立' if result['created'] else '已并入现有日报计划',
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description='企微会话业务 Skill 命令')
    sub = parser.add_subparsers(dest='command', required=True)

    setup = sub.add_parser('setup-daily-report', help='建立/扩展企微会话日报计划')
    setup.add_argument('--sessions', required=True,
                       help='稳定会话标识（ws1.）列表，竖线分隔；一个计划同一来源账号')
    setup.add_argument('--schedule-type', required=True, choices=['daily', 'weekly', 'interval', 'once'],
                       help='计划周期类型')
    setup.add_argument('--cron', help='cron 表达式（schedule_type=daily/weekly 时使用，如 "0 8 * * *"')
    setup.add_argument('--interval-seconds', type=int, help='间隔秒（schedule_type=interval；沿既有调度契约仅支持 ≥1 小时的整数倍）')
    setup.add_argument('--run-at', help='执行时刻（schedule_type=once，北京时间 YYYY-MM-DD HH:MM:SS，须为未来时间）')
    setup.add_argument('--timezone', help='业务时区（IANA，缺省 Asia/Shanghai）')
    setup.add_argument('--profile', help='所用智能体配置 ID（缺省 main；沿用户选择）')
    setup.add_argument('--device', help='来源设备 ID（沿当前代理设备）')
    setup.add_argument('--requirements', help='特殊关注要求（同计划多个会话适用）')
    setup.add_argument('--instance', help='智能体实例 ID（可选）')
    setup.set_defaults(func=_cmd_setup_daily_report)

    args = parser.parse_args(argv)
    # 子进程独立运行时连接池不存在；在分发命令前统一初始化（身份缺失命令亦无害）。
    try:
        from src.db.database import get_postgres_pool, init_postgres_pool
        if get_postgres_pool() is None:
            init_postgres_pool()
    except Exception as error:  # noqa: BLE001 - 可展示原因，不带连接细节
        return _emit(error=f'数据库不可用，请稍后重试（{type(error).__name__}）', code='DB_UNAVAILABLE')
    try:
        return args.func(args)
    except ConversationServiceError as error:
        return _emit(error=str(error), code=error.code)
    except Exception as error:  # noqa: BLE001 - 面向模型输出可展示原因，不回传堆栈
        return _emit(error=f'命令执行失败：{type(error).__name__}', code='COMMAND_FAILED')


if __name__ == '__main__':
    sys.exit(main())
