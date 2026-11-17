"""lyjy_employment renderer 单测：职位/文章双模板结构、PII 拦截与联系方式行豁免、
分类路由、到期换算、HTML 双重编码纯文本化、hash 稳定性。
"""

from datetime import datetime

import pytest

import src.tenant_custom.lyjy_employment.renderer as renderer
from src.tenant_custom.lyjy_employment.renderer import (
    CATEGORY_ROUTES,
    PiiRejectedError,
    build_article_context,
    build_chunks,
    compute_content_hash,
    html_to_text,
    render_article,
    render_job,
)

DICT_CACHE = {
    "qs_category_jobs": {
        "12": {"id": 12, "categoryname": "焊工"},
        "34": {"id": 34, "categoryname": "叉车工"},
    },
    "qs_category_district": {
        "5": {"id": 5, "categoryname": "山东"},
        "54": {"id": 54, "categoryname": "聊城"},
    },
    "qs_article_category": {
        "1": {"id": 1, "parentid": 0, "categoryname": "资讯中心"},
        "2": {"id": 2, "parentid": 1, "categoryname": "专题活动"},
        "17": {"id": 17, "parentid": 0, "categoryname": "彭水专区"},
        "19": {"id": 19, "parentid": 17, "categoryname": "彭水求职"},
    },
    "qs_guide_category": {
        "2": {"id": 2, "categoryname": "生涯指南"},
    },
}


def _job(**overrides):
    row = {
        "id": 2116,
        "jobs_name": "叉车司机",
        "companyname": "某某物流有限公司",
        "company_id": 77,
        "trade_cn": "物流/仓储",
        "nature_cn": "全职",
        "district_cn": "山东/聊城/东昌府区",
        "district": "5,54",
        "tag_cn": "环境好,年终奖",
        "amount": 3,
        "category_cn": "运输类",
        "subclass": 34,
        "education_cn": "不限",
        "experience_cn": "1年",
        "minwage": 5000,
        "maxwage": 7000,
        "negotiable": 0,
        "contents": "<p>负责仓库货物搬运</p><p>&nbsp; 安全驾驶</p>",
        "addtime": 1700000000,
        "deadline": 1893456000,
        "refreshtime": 1791879066,
        "focus_group": 1,
        "is_ex_serviceman": 0,
        "is_disabled": 0,
        "disabilityType": "",
        "disability": "",
        "sex_cn": "不限",
        "age": "25-45",
        "language": "",
        "jobAsk": "持证上岗",
        "cert": "叉车证",
        "scale_cn": "50-99人",
    }
    row.update(overrides)
    return row


def _contact(**overrides):
    row = {
        "pid": 2116, "contact": "王经理", "telephone": "13800001111",
        "landline_tel": "", "contact_show": 1, "telephone_show": 1,
        "landline_tel_show": 0,
    }
    row.update(overrides)
    return row


# ------------------------------- 职位模板 -------------------------------


def test_render_job_template_structure():
    doc = render_job(_job(), _contact(), {"id": 77, "companyname": "某某物流",
                                          "contents": "一家物流公司"}, DICT_CACHE)
    c = doc.content_md
    assert c.startswith("# 叉车司机（某某物流有限公司）\n\n## 职位信息\n")
    assert "- 职位类别：运输类 / 叉车工" in c
    assert "- 工作地区：山东/聊城/东昌府区" in c
    assert "- 薪资：5000-7000 元/月" in c
    assert "- 学历/经验要求：不限 / 1年" in c
    assert "- 招聘人数：3" in c
    assert "- 福利待遇：环境好,年终奖" in c
    assert "- 岗位职责：负责仓库货物搬运\n安全驾驶" in c  # 去标签保留段落
    assert "- 其他要求：不限、25-45、持证上岗、叉车证、50-99人" in c
    assert "- 特殊群体：优先招用重点群体" in c
    assert "- 联系方式：王经理，13800001111" in c
    assert "## 企业信息\n\n一家物流公司" in c
    assert c.endswith("分类：运输类；数据来源：聊易就业平台。\n")
    assert doc.category_display_name == "在招职位"
    assert doc.external_id == "job:2116"


def test_render_job_negotiable_and_empty_sections():
    """negotiable=1 写面议；空字段行省略；无联系/简介时整行整节省略。"""
    doc = render_job(
        _job(negotiable=1, tag_cn="", amount=0, focus_group=0,
             is_disabled=1, disabilityType="肢体"),
        None, None, DICT_CACHE,
    )
    c = doc.content_md
    assert "- 薪资：面议" in c
    assert "福利待遇" not in c and "招聘人数" not in c and "联系方式" not in c
    assert "## 企业信息" not in c
    assert "- 特殊群体：残疾人" in c


def test_render_job_contact_show_flags():
    """源站展示位关闭（*_show=0）的联系方式不出现在正文。"""
    doc = render_job(_job(), _contact(contact_show=0, telephone_show=0),
                     None, DICT_CACHE)
    assert "联系方式" not in doc.content_md
    # 电话隐藏但联系人可见
    doc2 = render_job(_job(), _contact(telephone_show=0), None, DICT_CACHE)
    assert "- 联系方式：王经理" in doc2.content_md
    # 电话隐藏回落固话
    doc3 = render_job(_job(), _contact(telephone_show=0, landline_tel="0635-8000000",
                                       landline_tel_show=1), None, DICT_CACHE)
    assert "- 职位类别" in doc3.content_md
    assert "- 联系方式：王经理，0635-8000000" in doc3.content_md


def test_render_job_deadline_to_expires_at():
    """deadline>0 → Asia/Shanghai 挂钟 naive datetime；deadline=0/空 → None（长期）。"""
    doc = render_job(_job(), None, None, DICT_CACHE)
    # 1893456000 = 2030-01-01T00:00:00Z → 上海挂钟 08:00（检索会话时区解释）
    assert doc.expires_at == datetime(2030, 1, 1, 8, 0)
    assert render_job(_job(deadline=0), None, None, DICT_CACHE).expires_at is None
    assert render_job(_job(deadline=None), None, None, DICT_CACHE).expires_at is None


def test_deadline_to_expires_at_timezone_wall_clock():
    """时区回归（CR P2 登记项）：expires_at 必须是上海挂钟时间，不能是 UTC 挂钟。

    documents.expires_at 为无时区列，检索按会话时区（PG 固定 Asia/Shanghai）
    解释；写 UTC 挂钟会让岗位提前约 8h 从检索消失。
    """
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from src.tenant_custom.lyjy_employment.renderer import deadline_to_expires_at

    seconds = 1893456000  # 2030-01-01T00:00:00Z
    expected_utc = datetime.fromtimestamp(seconds, tz=timezone.utc)
    expected_sh = expected_utc.astimezone(ZoneInfo("Asia/Shanghai"))
    got = deadline_to_expires_at(seconds)
    assert got == expected_sh.replace(tzinfo=None)
    assert got.hour == expected_sh.hour  # 上海挂钟 8 点（非 UTC 0 点）
    assert deadline_to_expires_at(-1) is None


def test_render_job_metadata_whitelist_and_hash_stability():
    """metadata.raw_payload 只含稳定业务字段（无 refreshtime 等每轮变化值）；
    hash = pipeline_version + 正文 sha256，渲染幂等。"""
    doc = render_job(_job(), _contact(), None, DICT_CACHE)
    raw = doc.metadata["raw_payload"]
    assert raw["source_table"] == "qs_jobs" and raw["native_id"] == "2116"
    assert raw["wage_min"] == 5000 and raw["deadline"] == 1893456000
    assert "refreshtime" not in raw
    assert doc.metadata["trace"]["content_hash"] == doc.content_hash
    assert doc.content_hash == compute_content_hash(doc.content_md)
    again = render_job(_job(), _contact(), None, DICT_CACHE)
    assert again.content_hash == doc.content_hash


# ------------------------------- PII 扫描 -------------------------------


def test_pii_rejected_and_contact_line_exempt():
    """正文手机号/身份证命中即拒渲染；「联系方式：」行内号码豁免（决议 8）。"""
    with pytest.raises(PiiRejectedError) as exc:
        render_job(_job(contents="<p>联系人老李 13912345678 请勿来电</p>"),
                   None, None, DICT_CACHE)
    assert "phone" in str(exc.value)

    # 联系方式行放行：电话只出现在该行
    doc = render_job(_job(), _contact(telephone="13800001111"), None, DICT_CACHE)
    assert "- 联系方式：王经理，13800001111" in doc.content_md

    with pytest.raises(PiiRejectedError):
        render_job(_job(contents="<p>证件号 37090219900112003X 请核实</p>"),
                   None, None, DICT_CACHE)

    # 身份证 15 位同样拦截
    with pytest.raises(PiiRejectedError):
        render_article("qs_notice",
                       {"id": 1, "title": "t", "content": "号码 370902900112003"},
                       "", "", id_column="id")


def test_pii_scan_shapes():
    assert renderer.scan_pii("正常文本 无号码") == []
    assert renderer.scan_pii("手机 13912345678") == ["phone"]
    assert renderer.scan_pii("18 位 37090219900112003X") == ["idcard"]
    # 长数字串（如订单号 16 位）不算手机号/身份证
    assert renderer.scan_pii("1234567812345678") == []


# ------------------------------- 文章模板 -------------------------------


def _article(table, **overrides):
    rows = {
        "qs_guide": {"id": 9, "type_id": 2, "title": "面试技巧", "content": "<p>准备</p>",
                     "author": "就业中心", "source": "", "addtime": 1700000100,
                     "subsite_id": 0},
        "qs_article": {"id": 10, "type_id": 19, "parentid": 17, "title": "政策速递",
                       "content": "<p>内容</p>", "author": "", "source": "人社局",
                       "addtime": 1700000200, "subsite_id": 3},
        "qs_notice": {"id": 11, "type_id": 1, "title": "通知",
                      "content": "&lt;p&gt;&amp;nbsp; 正文&lt;/p&gt;",
                      "addtime": 1700000300, "subsite_id": 0},
        "qs_explain": {"id": 12, "type_id": 1, "title": "指南",
                       "content": "<p>流程</p>", "addtime": 1700000400, "subsite_id": 0},
    }
    row = dict(rows[table])
    row.update(overrides)
    return row


def test_render_article_templates_and_region_fallback():
    """文章模板：标题/分类/地区/来源/正文/尾行；专区根名兜底地区行。"""
    # guide：单级分类名；来源取 author
    doc = render_article("qs_guide", _article("qs_guide"), *build_article_context(
        "qs_guide", _article("qs_guide"), DICT_CACHE))
    c = doc.content_md
    assert c.startswith("# 面试技巧\n\n分类：生涯指南\n")
    assert "地区：" not in c  # subsite_id=0 无地区行
    assert "来源：就业中心" in c
    assert c.endswith("---\n\n数据来源：聊易就业平台。\n")
    assert doc.category_display_name == "就业指导"
    assert doc.external_id == "guide:9"

    # article：树路径 + 专区根名兜底地区（无 subsite 映射时）
    row = _article("qs_article")
    doc = render_article("qs_article", row, *build_article_context(
        "qs_article", row, DICT_CACHE))
    c = doc.content_md
    assert "分类：彭水专区 / 彭水求职" in c
    assert "地区：彭水专区" in c
    assert "来源：人社局" in c
    assert doc.category_display_name == "政策资讯"

    # 非专区根（资讯中心）不写地区行
    row2 = _article("qs_article", type_id=2, parentid=1, subsite_id=0)
    doc2 = render_article("qs_article", row2, *build_article_context(
        "qs_article", row2, DICT_CACHE))
    assert "分类：资讯中心 / 专题活动" in doc2.content_md
    assert "地区：" not in doc2.content_md

    # notice/explain：无子分类；notice 双重编码正文还原
    doc3 = render_article("qs_notice", _article("qs_notice"), "", "")
    assert "分类：" not in doc3.content_md
    assert "\n正文\n" in doc3.content_md
    assert doc3.category_display_name == "通知公告"
    doc4 = render_article("qs_explain", _article("qs_explain"), "", "")
    assert "流程" in doc4.content_md
    assert doc4.category_display_name == "办事指南"


def test_render_article_sccy_source_and_desc_fallback():
    """zc_article：details 正文、desc 回退、来源标注水城创业网、article_id 主键。"""
    doc = render_article("zc_article",
                         {"article_id": 66, "cate_id": 105, "title": "创业担保贷款",
                          "desc": "摘要", "details": "<p>正文内容</p>", "dateline": 1700},
                         category_path="行业资讯 / 政策东风", region="",
                         source_name=renderer.SOURCE_NAME_SCCY, id_column="article_id")
    c = doc.content_md
    assert c.startswith("# 创业担保贷款\n\n分类：行业资讯 / 政策东风\n")
    assert "正文内容" in c
    assert c.endswith("数据来源：水城创业网。\n")
    assert doc.category_display_name == "创业政策"
    assert doc.external_id == "zcarticle:66"
    # details 空 → desc 回退
    doc2 = render_article("zc_article",
                          {"article_id": 67, "cate_id": 105, "title": "t",
                           "desc": "只有摘要", "details": "", "dateline": 1700},
                          category_path="", region="",
                          source_name=renderer.SOURCE_NAME_SCCY, id_column="article_id")
    assert "只有摘要" in doc2.content_md


# ------------------------------- 分类路由与分块 -------------------------------


def test_category_routes_six_categories():
    """六分类路由常量（§7.5 映射定版）。"""
    assert CATEGORY_ROUTES == {
        "qs_jobs": "在招职位",
        "qs_guide": "就业指导",
        "qs_article": "政策资讯",
        "qs_notice": "通知公告",
        "qs_explain": "办事指南",
        "zc_article": "创业政策",
    }


def test_build_chunks_single_whole_and_overflow_fallback():
    doc = "短正文"
    chunks = build_chunks(doc)
    assert len(chunks) == 1 and chunks[0].text == doc
    long_doc = "长" * 7000
    chunks = build_chunks(long_doc)
    assert len(chunks) > 1
    assert all(c.text for c in chunks)
    assert sum(len(c.text) for c in chunks) >= 6000  # 超护栏回退分块不丢内容


def test_html_to_text_double_encoded():
    """双重编码还原（客户库实测形态）+ 压空行。"""
    raw = "&lt;p&gt;\r\n\t&amp;nbsp; 第一段&lt;/p&gt;\r\n&lt;p&gt;第二段&lt;/p&gt;"
    assert html_to_text(raw) == "第一段\n第二段"
    assert html_to_text("") == ""
    assert html_to_text("<p>a</p><p>b</p>") == "a\nb"
