#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recap 任务适配器注册表

新任务接入步骤：
1. 在本目录创建 {task_name}_xxx.py，实现 name 类属性 + async execute(payload) 静态方法
2. 在 RECAP_TASK_ADAPTERS 中注册 {task_name: AdapterClass}
3. 在 runner._system_switch_enabled 中映射系统级总闸（如需 config.yaml 开关）
"""

from typing import Any, Dict, Type

from src.services.recap.tasks.external_push import ExternalPushAdapter
from src.services.recap.tasks.lead_refresh import LeadRefreshAdapter

# recap 任务名 -> 适配器类。列表顺序无关，执行顺序由 SUBAGENT.md recap.tasks 声明顺序决定
RECAP_TASK_ADAPTERS: Dict[str, Type[Any]] = {
    "external_push": ExternalPushAdapter,
    "lead_refresh": LeadRefreshAdapter,
}
