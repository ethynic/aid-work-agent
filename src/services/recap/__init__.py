#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recap 运行时：轮后异步沉淀任务（docs/subagent/recap-mechanism-design.md）

每轮问答回复送达（send_ok=True）后由代码触发，执行 SUBAGENT.md recap.tasks 声明的
沉淀类任务（如推送外部系统）。决策者是代码不是模型——LLM 只在适配器内部被调用做摘要等。
"""

from src.services.recap.runner import RecapPayload, trigger_recap

__all__ = ["RecapPayload", "trigger_recap"]
