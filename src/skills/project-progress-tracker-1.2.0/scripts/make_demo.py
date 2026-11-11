#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成演示数据与多格式样例源文件（仅首次体验用）

演示主体为虚构的「华瓷建材集团」——主营建筑陶瓷（瓷砖 / 岩板）的制造企业。
8 个项目覆盖三态：窑炉技改、智能工厂 MES、精装房集采供货、大规格薄板量产验收、
绿色建材认证、防伪溯源、出口订单、烟气环保整改。
同时生成 md/txt/docx/xlsx/pdf/eml/png/html/zip 共 9 类样例资料，用于验证抽取脚本与看板。
真实资料接入时整体替换 data/projects.json 即可。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

AS_OF = "2026-11-10"
COMPANY = "华瓷建材集团"


def P(**kw):
    base = {
        "client": "", "owner": "", "confidence": 0.9, "start_date": "", "due_date": "",
        "closed": False, "demo": True, "evidence_gaps": [], "highlights": [], "risks": [],
        "next_actions": [], "milestones": [], "sources": [], "communications": [],
        "status_history": [], "updated_at": f"{AS_OF}T19:46:00+08:00",
    }
    base.update(kw)
    return base


PROJECTS = [
    P(
        id="p001", name="佛山基地3号宽体窑炉技改", client="制造中心 · 佛山基地", owner="梁国锋",
        status="overdue", status_reason="计划 2026-10-31 完成点火投产验收，截至 2026-11-10 仍未获批复点火，已逾期 10 天。",
        confidence=0.94, progress=82, planned_progress=100, start_date="2026-07-01", due_date="2026-10-31",
        risk_level="high",
        summary="窑体改造与冷调全部完成，卡在燃气调压站特种设备登记与环保在线监测联网批复，已逾期 10 天。",
        highlights=[
            "10 月 22 日完成 3# 宽体窑炉耐火材料更换与低氮燃烧器调试",
            "空窑冷调连续 72 小时通过，温差曲线控制在 ±3℃ 以内",
            "10 月 28 日已提交点火投产申请及全套特种设备资料",
        ],
        risks=[
            "燃气调压站特种设备使用登记未办结，属外部审批不可控",
            "环保排放在线监测联网验收排期未定，3# 线持续停窑，日均影响产能约 1.2 万㎡",
        ],
        next_actions=[
            "升级至园区管委会与市监局，锁定特种设备登记与联网验收时间",
            "同步出具停窑损失测算与延期责任边界说明",
            "预约 11 月 14 日备用点火窗口，避免继续空等",
        ],
        milestones=[
            {"name": "技改方案评审", "planned": "2026-07-20", "actual": "2026-07-18", "state": "done"},
            {"name": "窑体改造完工", "planned": "2026-09-30", "actual": "2026-10-22", "state": "done_late"},
            {"name": "空窑冷调联试", "planned": "2026-10-15", "actual": "2026-10-22", "state": "done_late"},
            {"name": "点火投产验收", "planned": "2026-10-31", "actual": None, "state": "overdue"},
        ],
        sources=[
            {"file": "佛山基地3号宽体窑炉技改_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "环保在线监测联网批复仍未下发，点火排期存在风险，已第三次发函催办。"},
            {"file": "佛山基地3号宽体窑炉技改_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "特种设备登记还在走流程，下周再看看。"},
            {"file": "佛山基地3号宽体窑炉技改_点火投产申请_20261028.docx", "type": "acceptance", "date": "2026-10-28", "excerpt": "窑体改造与空窑冷调全部完成，提交点火投产及特种设备验收申请。"},
        ],
        communications=[
            {"date": "2026-11-06", "channel": "企业微信", "from": "园区安监 陈工", "excerpt": "特种设备登记还在走流程，下周再看看。", "signal": "blocked"},
            {"date": "2026-11-08", "channel": "电话", "from": "梁国锋 → 基地总经理", "excerpt": "已升级至园区管委会，等待批复时间。", "signal": "blocked"},
            {"date": "2026-10-22", "channel": "项目群", "from": "窑炉车间", "excerpt": "冷调 72 小时通过，可以报点火了。", "signal": "resolved"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "delayed", "progress": 70},
            {"as_of": "2026-11-03", "status": "delayed", "progress": 78},
            {"as_of": "2026-11-10", "status": "overdue", "progress": 82},
        ],
    ),
    P(
        id="p002", name="岩板智能工厂MES一期", client="数字化部 · 岩板分厂", owner="徐雅琴",
        status="delayed", status_reason="截至 2026-11-10 实际完成 58%，线性计划应达 78%，落后 20 个百分点，且设备数采依赖压机/喷墨机厂家开放协议。",
        confidence=0.86, progress=58, planned_progress=78, start_date="2026-08-15", due_date="2026-12-20",
        risk_level="medium",
        summary="坯体与磨边工序追溯已上线，压机、喷墨机数采协议未开放且实施人力被抽调，整体落后计划 20 个百分点。",
        highlights=[
            "11 月 3 日坯体工序批次追溯进入试运行",
            "磨边、抛光 2 条产线完成质量数据采集点改造",
        ],
        risks=[
            "压机与喷墨机厂家数采协议迟迟未开放，联调窗口无法确认",
            "2 名实施工程师被临时抽调至肇庆新厂项目，11 月底才回归",
        ],
        next_actions=[
            "本周内锁定设备厂家联调窗口并写入双方计划",
            "申请临时补充 1 名实施工程师，或把非关键报表后移",
            "重排 12 月里程碑并同步岩板分厂生产计划",
        ],
        milestones=[
            {"name": "蓝图评审", "planned": "2026-09-05", "actual": "2026-09-05", "state": "done"},
            {"name": "坯体工序试运行", "planned": "2026-11-01", "actual": "2026-11-03", "state": "done_late"},
            {"name": "设备数采联调", "planned": "2026-11-08", "actual": None, "state": "overdue"},
            {"name": "全厂切换上线", "planned": "2026-12-20", "actual": None, "state": "pending"},
        ],
        sources=[
            {"file": "岩板智能工厂MES一期_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "压机与喷墨机数采协议仍在等设备厂家开放，本周无进展。"},
            {"file": "岩板智能工厂MES一期_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "两名实施工程师被肇庆新厂借调，月底才能回来。"},
            {"file": "岩板智能工厂MES一期_补充资料_20261109.zip", "type": "milestone", "date": "2026-11-09", "excerpt": "设备数采联调 计划 2026-11-08 状态 阻塞"},
        ],
        communications=[
            {"date": "2026-11-05", "channel": "邮件", "from": "压机设备厂商", "excerpt": "数采协议需等本月固件版本发布后统一开放。", "signal": "blocked"},
            {"date": "2026-11-08", "channel": "企业微信", "from": "实施主管", "excerpt": "两名实施工程师被肇庆新厂借调，月底才能回来。", "signal": "blocked"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "on_track", "progress": 45},
            {"as_of": "2026-11-03", "status": "delayed", "progress": 53},
            {"as_of": "2026-11-10", "status": "delayed", "progress": 58},
        ],
    ),
    P(
        id="p003", name="华东精装房集采瓷砖供货", client="战略客户部 · 华东区域", owner="陈昊",
        status="on_track", status_reason="截至 2026-11-10 已完成 24/32 个楼盘首批供货验收（76%），高于线性计划 74%，无未解决阻塞。",
        confidence=0.93, progress=76, planned_progress=74, start_date="2026-09-01", due_date="2026-12-15",
        risk_level="low",
        summary="24 个楼盘已完成首批供货并一次验收，按每周 3 盘的节奏推进，进度略优于计划。",
        highlights=[
            "第 24 个楼盘 11 月 8 日完成首批供货，色号与平整度抽检一次通过",
            "标准化托盘与包装方案把单盘交付周期从 3 天压到 1.5 天",
        ],
        risks=["剩余 8 个楼盘集中在 12 月，需避开地产方年终盘点与冬季施工停工令"],
        next_actions=[
            "提前锁定 12 月各楼盘收货窗口与叉车/卸货条件",
            "沉淀色号分批与留样手册，为华北集采复制做准备",
        ],
        milestones=[
            {"name": "样板房封样 3 盘", "planned": "2026-09-20", "actual": "2026-09-18", "state": "done"},
            {"name": "批量供货 20 盘", "planned": "2026-11-05", "actual": "2026-11-02", "state": "done"},
            {"name": "全部 32 盘交付", "planned": "2026-12-15", "actual": None, "state": "pending"},
        ],
        sources=[
            {"file": "华东精装房集采瓷砖供货_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "本周新增 3 个楼盘首批供货，累计 24 盘，色号抽检全部一次通过。"},
            {"file": "华东精装房集采瓷砖供货_里程碑台账_20261108.xlsx", "type": "milestone", "date": "2026-11-08", "excerpt": "批量供货 20 盘 2026-11-05 2026-11-02 已完成"},
            {"file": "华东精装房集采瓷砖供货_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "托盘方案很顺，下周按计划继续 3 盘。"},
        ],
        communications=[
            {"date": "2026-11-08", "channel": "项目群", "from": "物流调度", "excerpt": "托盘方案很顺，下周按计划继续 3 盘。", "signal": "progress"},
            {"date": "2026-11-09", "channel": "企业微信", "from": "地产甲方成本部", "excerpt": "12 月 20 日后工地陆续停工，尽量提前收尾。", "signal": "neutral"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "on_track", "progress": 50},
            {"as_of": "2026-11-03", "status": "on_track", "progress": 66},
            {"as_of": "2026-11-10", "status": "on_track", "progress": 76},
        ],
    ),
    P(
        id="p004", name="750x1500薄板量产验收", client="技术中心 · 薄板事业部", owner="王敏",
        status="overdue", status_reason="计划 2026-11-03 完成量产验收，因 10 月 29 日平整度抽检未达标需返工，截至 2026-11-10 已逾期 7 天。",
        confidence=0.91, progress=91, planned_progress=100, start_date="2026-06-10", due_date="2026-11-03",
        risk_level="high",
        summary="大规格薄板平整度合格率未达内控门槛导致量产验收未通过，正在调整窑炉温差曲线，已逾期 7 天。",
        highlights=[
            "10 月 29 日首次量产验收，抽检平整度合格率 86.4%，低于内控 92% 门槛",
            "11 月 6 日完成抛光模具更换与窑炉温差曲线二次标定",
        ],
        risks=[
            "复验日期未定，甲方对抽检是否剔除边角料的口径仍有异议",
            "集采合同含逾期供货违约条款，需同步商务评估",
        ],
        next_actions=[
            "与甲方书面确认平整度抽检口径并留样封存",
            "预约 11 月 17 日量产复验",
            "同步商务评估逾期供货条款影响与备选产能",
        ],
        milestones=[
            {"name": "中试试产", "planned": "2026-09-30", "actual": "2026-10-08", "state": "done_late"},
            {"name": "首次量产验收", "planned": "2026-10-29", "actual": "2026-10-29", "state": "done_late"},
            {"name": "量产验收通过", "planned": "2026-11-03", "actual": None, "state": "overdue"},
        ],
        sources=[
            {"file": "750x1500薄板量产验收_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "抽检平整度合格率 86.4%，低于内控 92%，量产验收未通过，需返工。"},
            {"file": "750x1500薄板量产验收_验收沟通_20261106.html", "type": "communication", "date": "2026-11-06", "excerpt": "甲方认为抽检口径应剔除切割边角料，双方尚未达成一致。"},
            {"file": "750x1500薄板量产验收_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "复验时间还没定，等甲方回复。"},
        ],
        communications=[
            {"date": "2026-11-06", "channel": "邮件", "from": "集采甲方质检组", "excerpt": "甲方认为抽检口径应剔除切割边角料，双方尚未达成一致。", "signal": "blocked"},
            {"date": "2026-11-08", "channel": "企业微信", "from": "王敏", "excerpt": "复验时间还没定，等甲方回复。", "signal": "blocked"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "delayed", "progress": 84},
            {"as_of": "2026-11-03", "status": "overdue", "progress": 88},
            {"as_of": "2026-11-10", "status": "overdue", "progress": 91},
        ],
    ),
    P(
        id="p005", name="绿色建材认证及低碳改造", client="安环部 · 集团双碳办", owner="刘洋",
        status="delayed", status_reason="截至 2026-11-10 完成 41%，线性计划应达 62%，落后 21 个百分点；叠加 10 月碳足迹核算边界变更与能源管理负责人离职，是组合内最可能转超时的项目。",
        confidence=0.72, progress=41, planned_progress=62, start_date="2026-05-06", due_date="2027-01-31",
        risk_level="high",
        summary="碳足迹核算边界反复变更与关键人员流失叠加，缺口 21 个百分点，是当前最需要先干预的项目。",
        highlights=[
            "10 月 12 日认证机构追加 3 类核算边界变更，未走内部变更评审",
            "能源管理负责人 10 月 31 日离职，分厂能耗台账交接不完整",
        ],
        risks=[
            "核算边界未冻结，认证范围持续膨胀",
            "能耗数据口径断档，新方案无人拍板",
            "按当前速度外推，2027-01-31 认证审核窗口难以守住",
        ],
        next_actions=[
            "冻结核算边界并补齐变更评审记录",
            "一周内指定能源数据负责人并完成台账交接复核",
            "重做剩余工作量估算，评估分厂分期认证可行性",
        ],
        evidence_gaps=["缺少 11 月最新能耗台账，41% 取自 10 月 31 日月报的定性描述，属估算"],
        milestones=[
            {"name": "核算边界评审", "planned": "2026-07-15", "actual": "2026-07-15", "state": "done"},
            {"name": "能效数据平台搭建", "planned": "2026-10-31", "actual": None, "state": "overdue"},
            {"name": "全厂认证审核", "planned": "2027-01-31", "actual": None, "state": "pending"},
        ],
        sources=[
            {"file": "绿色建材认证及低碳改造_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "能源负责人离职后核算方案评审暂停，核算边界三次变更尚未冻结。"},
            {"file": "绿色建材认证及低碳改造_月报_20261031.pdf", "type": "report", "date": "2026-10-31", "excerpt": "整体进度约四成，核算边界变更与人员变动导致后续排期需重新评估。"},
            {"file": "绿色建材认证及低碳改造_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "新能源数据负责人还没定，方案先搁着。"},
        ],
        communications=[
            {"date": "2026-11-04", "channel": "企业微信", "from": "认证机构", "excerpt": "还有两类排放因子想纳进来，能不能这版一起做？", "signal": "blocked"},
            {"date": "2026-11-08", "channel": "项目群", "from": "刘洋", "excerpt": "新能源数据负责人还没定，方案先搁着。", "signal": "blocked"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "delayed", "progress": 35},
            {"as_of": "2026-11-03", "status": "delayed", "progress": 38},
            {"as_of": "2026-11-10", "status": "delayed", "progress": 41},
        ],
    ),
    P(
        id="p006", name="瓷砖防伪溯源二维码系统", client="品牌与市场部", owner="赵磊",
        status="on_track", status_reason="截至 2026-11-10 完成 88%，高于计划 85%，关键里程碑全部按期完成。",
        confidence=0.90, progress=88, planned_progress=85, start_date="2026-09-10", due_date="2026-11-28",
        risk_level="low",
        summary="赋码产线改造与主体功能完成，进入经销商试运行与培训阶段，进度略优于计划。",
        highlights=[
            "11 月 5 日开放 20 家试点经销商扫码验真",
            "一物一码与 ERP 批次接口联调一次通过",
        ],
        risks=["经销商侧账号开通率仅 60%，门店培训覆盖不足可能影响上线效果"],
        next_actions=[
            "完成剩余经销商账号批量开通",
            "11 月 20 日前交付扫码操作手册并完成两场区域培训",
        ],
        milestones=[
            {"name": "赋码方案确认", "planned": "2026-09-25", "actual": "2026-09-24", "state": "done"},
            {"name": "产线赋码改造完成", "planned": "2026-10-31", "actual": "2026-10-30", "state": "done"},
            {"name": "全国正式上线", "planned": "2026-11-28", "actual": None, "state": "pending"},
        ],
        sources=[
            {"file": "瓷砖防伪溯源二维码系统_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "20 家试点经销商已开始扫码验真，与 ERP 批次接口联调通过。"},
            {"file": "瓷砖防伪溯源二维码系统_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "账号开通还差 8 家，本周统一批量处理。"},
        ],
        communications=[
            {"date": "2026-11-08", "channel": "企业微信", "from": "渠道运营", "excerpt": "账号开通还差 8 家，本周统一批量处理。", "signal": "progress"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "on_track", "progress": 62},
            {"as_of": "2026-11-03", "status": "on_track", "progress": 80},
            {"as_of": "2026-11-10", "status": "on_track", "progress": 88},
        ],
    ),
    P(
        id="p007", name="迪拜项目防滑砖出口订单", client="海外事业部 · 中东区", owner="孙悦",
        status="delayed", status_reason="截至 2026-11-10 完成 63%，线性计划应达 80%，落后 17 个百分点；花色样板三轮未定稿且等待甲方书面封样确认。",
        confidence=0.82, progress=63, planned_progress=80, start_date="2026-08-01", due_date="2026-12-05",
        risk_level="medium",
        summary="花色封样反复与甲方确认链路慢，造成排产等待，整体落后计划 17 个百分点。",
        highlights=[
            "R11 防滑坯体配方与釉线工艺已确认，柜装体积优化 22%",
            "第三轮花色样板 11 月 5 日寄出，甲方尚未反馈",
        ],
        risks=[
            "甲方花色封样确认已等待 5 天，无明确回复时间",
            "12 月 5 日装船窗口与中东旺季舱位紧张冲突",
        ],
        next_actions=[
            "设定封样确认截止日，超时按第三轮样板排产并书面留痕",
            "把不依赖花色的包装、唛头与单证前置准备",
            "与货代对齐装船窗口，预留备选船期",
        ],
        milestones=[
            {"name": "技术协议评审", "planned": "2026-08-20", "actual": "2026-08-20", "state": "done"},
            {"name": "花色样板封样", "planned": "2026-10-20", "actual": None, "state": "overdue"},
            {"name": "批量生产完成", "planned": "2026-11-25", "actual": None, "state": "pending"},
            {"name": "装船发运", "planned": "2026-12-05", "actual": None, "state": "pending"},
        ],
        sources=[
            {"file": "迪拜项目防滑砖出口订单_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "第三轮花色样板 11 月 5 日寄出后甲方未反馈，批量排产处于等待状态。"},
            {"file": "迪拜项目防滑砖出口订单_花色封样确认邮件_20261105.eml", "type": "communication", "date": "2026-11-05", "excerpt": "烦请于 11 月 8 日前书面确认花色封样，逾期将按第三轮样板排产。"},
            {"file": "迪拜项目防滑砖出口订单_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "甲方那边还没回，先做包装和单证。"},
        ],
        communications=[
            {"date": "2026-11-05", "channel": "邮件", "from": "孙悦 → 甲方项目部", "excerpt": "烦请于 11 月 8 日前书面确认花色封样，逾期将按第三轮样板排产。", "signal": "neutral"},
            {"date": "2026-11-08", "channel": "企业微信", "from": "海外单证组", "excerpt": "甲方那边还没回，先做包装和单证。", "signal": "blocked"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "on_track", "progress": 48},
            {"as_of": "2026-11-03", "status": "delayed", "progress": 57},
            {"as_of": "2026-11-10", "status": "delayed", "progress": 63},
        ],
    ),
    P(
        id="p008", name="窑炉烟气环保排放整改", client="安环部 · 佛山基地", owner="周涛",
        status="on_track", status_reason="截至 2026-11-10 完成 95%，高于计划 92%，仅剩 2 项低危整改项待停窑窗口期复核。",
        confidence=0.95, progress=95, planned_progress=92, start_date="2026-09-15", due_date="2026-11-20",
        risk_level="low",
        summary="脱硝与在线监测高危整改项全部闭环，进入第三方检测复核阶段，预计按期完成。",
        highlights=[
            "23 项整改（脱硝喷枪、在线监测标定、料场封闭等）11 月 7 日全部闭环",
            "第三方检测机构预检已通过，颗粒物与氮氧化物排放均低于限值",
        ],
        risks=["剩余 2 项低危依赖停窑检修窗口，需与 3# 窑炉技改错峰预约"],
        next_actions=[
            "11 月 14 日停窑窗口期完成收尾并留存影像",
            "11 月 18 日提交正式环保验收材料",
        ],
        milestones=[
            {"name": "排放差距自查", "planned": "2026-09-30", "actual": "2026-09-28", "state": "done"},
            {"name": "高危项整改闭环", "planned": "2026-11-07", "actual": "2026-11-07", "state": "done"},
            {"name": "正式环保验收", "planned": "2026-11-20", "actual": None, "state": "pending"},
        ],
        sources=[
            {"file": "窑炉烟气环保排放整改_项目周报_20261107.md", "type": "report", "date": "2026-11-07", "excerpt": "23 项整改已全部闭环，第三方检测机构预检通过。"},
            {"file": "窑炉烟气环保排放整改_整改看板截图_20261107.png", "type": "other", "date": "2026-11-07", "excerpt": "截图：整改看板显示 23/25 项完成（图片资料，需视觉读取）"},
            {"file": "窑炉烟气环保排放整改_沟通记录_20261108.txt", "type": "communication", "date": "2026-11-08", "excerpt": "停窑窗口约在 11 月 14 日，够用。"},
        ],
        communications=[
            {"date": "2026-11-08", "channel": "企业微信", "from": "设备动力部", "excerpt": "停窑窗口约在 11 月 14 日，够用。", "signal": "resolved"},
        ],
        status_history=[
            {"as_of": "2026-10-20", "status": "on_track", "progress": 70},
            {"as_of": "2026-11-03", "status": "on_track", "progress": 88},
            {"as_of": "2026-11-10", "status": "on_track", "progress": 95},
        ],
    ),
]

META = {
    "generated_at": f"{AS_OF}T19:46:00+08:00",
    "as_of": AS_OF,
    "title": f"{COMPANY} · 项目进度看板",
    "subtitle": "瓷砖与岩板业务 · 多项目进度健康度巡检",
    "source_mode": "demo",
    "demo": True,
    "overall_status": "需重点关注",
    "executive_summary": (
        f"本期跟踪{COMPANY} 8 个在建项目：3 个正常、3 个延误、2 个超时，平均完成率约 74%，整体判断为「需重点关注」。"
        "两个超时项目都卡在外部审批与验收链路而非产能本身——佛山 3# 宽体窑炉技改等燃气调压站特种设备登记与环保在线监测联网批复，"
        "750x1500 薄板量产验收因平整度合格率未达内控门槛、且抽检口径与甲方未书面统一而返工，建议合并升级到园区管委会与集采甲方一次性协调。"
        "三个延误项目里，绿色建材认证及低碳改造缺口最大（41% 对计划 62%），同时叠加碳足迹核算边界反复变更与能源管理负责人离职，是最可能转超时的项目，需要优先干预。"
        "跨项目共性风险有两条：外部审批/第三方确认链路慢（影响 4 个项目），以及停窑窗口、旺季舱位与产能排期冲突（影响 3 个项目）；"
        "前者靠设确认截止日与书面留痕机制收敛，后者需要一次统一的产能与交付窗口重排。"
    ),
    "top_risks": [
        "外部审批与甲方/第三方确认链路普遍偏慢，已直接导致 2 个项目超时、2 个项目延误",
        "绿色建材认证核算边界未冻结 + 能源数据负责人缺位，21 个百分点缺口有继续扩大趋势",
        "岩板智能工厂 MES 一期依赖压机/喷墨机厂家开放数采协议，且实施人力被新厂项目借调",
        "750x1500 薄板平整度抽检口径未书面固化，复验可能二次不通过并触发供货违约条款",
        "迪拜防滑砖出口订单花色封样未回，12 月装船窗口与中东旺季舱位紧张叠加，缓冲不足",
    ],
    "recommendations": [
        "本周内组织一次「审批与验收」联合协调会，把 3# 窑炉的燃气/环保验收与薄板复验口径、签字人一次敲定并记录责任边界",
        "对绿色建材认证立即冻结核算边界、补齐变更评审、指定能源数据负责人，一周内重做剩余工作量估算",
        "对所有外部依赖统一设置「确认截止日 + 超时默认推进 + 书面留痕」机制（含花色封样、数采协议、检测排期）",
        "做一次跨项目产能与交付窗口盘点，把停窑改造、量产复验、出口装船排到同一张日历上，优先保障逾期与高缺口项目",
    ],
}


# ---------- 样例源文件 ----------

def write_common_sources(root: Path) -> None:
    """按 sources[] 声明的文件名落盘，保证看板里展示的出处都真实存在。"""
    special_ext = {".docx", ".xlsx", ".pdf", ".eml", ".html", ".png", ".zip"}
    for p in PROJECTS:
        d = root / "data" / "sources" / p["name"]
        d.mkdir(parents=True, exist_ok=True)
        short = p["name"]
        md = [
            f"# {short} 项目周报（2026-11-07）", "",
            f"- 归属单位：{COMPANY}",
            f"- 项目负责人：{p['owner']}", f"- 委托/对口部门：{p['client']}",
            f"- 计划周期：{p['start_date']} ~ {p['due_date']}",
            f"- 实际完成率：{p['progress']}%；计划完成率：{p['planned_progress']}%",
            f"- 当前状态：{p['status']}", "",
            "## 本周进展", "", *[f"- {x}" for x in p["highlights"]], "",
            "## 风险与问题", "", *[f"- {x}" for x in p["risks"]], "",
            "## 原文摘录", "", *[f"> {s['excerpt']}" for s in p["sources"] if s["type"] == "report"], "",
        ]
        chat = [f"# {short} 沟通记录（截至 2026-11-08）", ""]
        for c in p["communications"]:
            chat.append(f"[{c['date']}] {c['channel']} · {c['from']}：{c['excerpt']}（信号：{c['signal']}）")
        chat += ["", "## 补充说明", "", f"判定理由参考：{p['status_reason']}", ""]
        for s in p["sources"]:
            ext = Path(s["file"]).suffix.lower()
            if ext in special_ext:
                continue  # 由 write_special_sources 生成真实格式文件
            if ext in {".md", ".markdown"}:
                (d / s["file"]).write_text("\n".join(md), encoding="utf-8")
            elif ext in {".txt", ".csv", ".log"}:
                (d / s["file"]).write_text("\n".join(chat), encoding="utf-8")


def write_special_sources(root: Path) -> None:
    # docx · 点火投产申请
    d = root / "data" / "sources" / "佛山基地3号宽体窑炉技改"
    try:
        import docx
        doc = docx.Document()
        doc.add_heading("佛山基地 3# 宽体窑炉技改 · 点火投产申请", 0)
        doc.add_paragraph("日期：2026-10-28")
        doc.add_paragraph("窑体改造与空窑冷调全部完成，温差曲线控制在 ±3℃ 以内，提交点火投产及特种设备验收申请。")
        tb = doc.add_table(rows=1, cols=3)
        tb.rows[0].cells[0].text, tb.rows[0].cells[1].text, tb.rows[0].cells[2].text = "阶段", "计划", "实际"
        for row in [("技改方案评审", "2026-07-20", "2026-07-18"), ("窑体改造完工", "2026-09-30", "2026-10-22"), ("点火投产验收", "2026-10-31", "待批复")]:
            cells = tb.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = v
        doc.save(str(d / "佛山基地3号宽体窑炉技改_点火投产申请_20261028.docx"))
    except Exception:
        (d / "佛山基地3号宽体窑炉技改_点火投产申请_20261028.txt").write_text(
            "窑体改造与空窑冷调全部完成，提交点火投产及特种设备验收申请。", encoding="utf-8")

    # xlsx · 集采供货里程碑台账
    d = root / "data" / "sources" / "华东精装房集采瓷砖供货"
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "供货里程碑台账"
        ws.append(["里程碑", "计划日期", "实际日期", "状态", "备注"])
        for m in PROJECTS[2]["milestones"]:
            ws.append([m["name"], m["planned"], m["actual"] or "", m["state"], "累计完成 24/32 盘首批供货"])
        ws.append(["进度汇总", "2026-11-08", "", "76%", "批量供货 20 盘 2026-11-05 2026-11-02 已完成"])
        wb.save(str(d / "华东精装房集采瓷砖供货_里程碑台账_20261108.xlsx"))
    except Exception:
        (d / "华东精装房集采瓷砖供货_里程碑台账_20261108.csv").write_text(
            "里程碑,计划日期,实际日期,状态\n批量供货 20 盘,2026-11-05,2026-11-02,已完成\n", encoding="utf-8")

    # pdf（最小可见文本层，验证标准库兜底抽取）
    d = root / "data" / "sources" / "绿色建材认证及低碳改造"
    lines = [
        "绿色建材认证及低碳改造 月报 2026-10-31",
        "整体进度约四成，核算边界变更与人员变动导致后续排期需重新评估。",
        "能效数据平台搭建 计划 2026-10-31 未完成",
    ]
    content = "BT /F1 12 Tf 50 760 Td 16 TL\n" + "\n".join(f"({s}) Tj T*" for s in lines) + "\nET\n"
    cbytes = content.encode("utf-8")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(cbytes)).encode() + b" >>\nstream\n" + cbytes + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, o in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets[1:]:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    (d / "绿色建材认证及低碳改造_月报_20261031.pdf").write_bytes(bytes(pdf))

    # eml · 花色封样确认邮件
    d = root / "data" / "sources" / "迪拜项目防滑砖出口订单"
    (d / "迪拜项目防滑砖出口订单_花色封样确认邮件_20261105.eml").write_text(
        "From: 孙悦 <sunyue@huaci.example.com>\nTo: 甲方项目部 <project@client.example.com>\n"
        "Subject: 迪拜项目防滑砖 · 第三轮花色样板封样确认\nDate: Thu, 5 Nov 2026 10:20:00 +0800\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        "烦请于 11 月 8 日前书面确认花色封样，逾期将按第三轮样板排产。\n"
        "第三轮花色样板已随 DHL 寄出，批量排产目前处于等待状态。\n", encoding="utf-8")

    # html · 量产验收沟通纪要
    d = root / "data" / "sources" / "750x1500薄板量产验收"
    (d / "750x1500薄板量产验收_验收沟通_20261106.html").write_text(
        "<html><head><meta charset='utf-8'><title>量产验收沟通纪要</title></head><body>"
        "<h1>750x1500 薄板 · 量产验收沟通纪要（2026-11-06）</h1>"
        "<p>甲方认为抽检口径应剔除切割边角料，双方尚未达成一致。</p>"
        "<p>抽检平整度合格率 86.4%，低于内控 92% 门槛。</p></body></html>", encoding="utf-8")

    # png · 整改看板截图
    d = root / "data" / "sources" / "窑炉烟气环保排放整改"
    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (640, 240), "#f4f6fa")
        dr = ImageDraw.Draw(im)
        dr.rectangle([20, 20, 620, 60], fill="#22304a")
        dr.text((36, 34), "Kiln Emission Fix Board  23/25 done", fill="white")
        for i in range(25):
            x = 24 + (i % 13) * 46
            y = 90 + (i // 13) * 60
            dr.rectangle([x, y, x + 36, y + 36], fill="#16a34a" if i < 23 else "#f59e0b")
        im.save(str(d / "窑炉烟气环保排放整改_整改看板截图_20261107.png"))
    except Exception:
        (d / "窑炉烟气环保排放整改_整改看板截图_20261107.txt").write_text(
            "截图：整改看板显示 23/25 项完成（图片资料，需视觉读取）", encoding="utf-8")

    # zip 内含 csv · 数采联调排期
    d = root / "data" / "sources" / "岩板智能工厂MES一期"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("数采联调排期.csv", "任务,计划日期,状态,备注\n设备数采联调,2026-11-08,阻塞,等压机/喷墨机厂家开放协议\n坯体工序试运行,2026-11-01,已完成,延后2天\n")
        z.writestr("周补充.txt", "两名实施工程师被肇庆新厂借调，月底回归。\n")
    (d / "岩板智能工厂MES一期_补充资料_20261109.zip").write_bytes(buf.getvalue())


def main() -> int:
    ap = argparse.ArgumentParser(description="生成建材行业演示项目数据与多格式样例源文件")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--as-of", default=AS_OF)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)

    META["as_of"] = args.as_of
    META["generated_at"] = f"{args.as_of}T19:46:00+08:00"
    for p in PROJECTS:
        p["updated_at"] = f"{args.as_of}T19:46:00+08:00"
    write_common_sources(root)
    write_special_sources(root)
    (root / "data" / "projects.json").write_text(
        json.dumps({"meta": META, "projects": PROJECTS}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files = sum(1 for _ in (root / "data" / "sources").rglob("*") if _.is_file())
    print(f"[demo] {COMPANY} · 生成 {len(PROJECTS)} 个演示项目、{files} 份多格式样例资料 -> data/projects.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
