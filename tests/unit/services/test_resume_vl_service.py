"""简历云端 VL 识别服务单元测试（mock gateway，无真实 LLM/DB）

覆盖（设计 §4.1 v2 / 开发计划 Phase 2）：
- resolve_model_spec：配置默认 / 白名单内显式指定 / 白名单外拒绝 / 格式非法拒绝
- slice_stitched_image：矮图整图单发 / 高图切带带重叠且像素对齐 / 超限加大带高重切 ≤10 带 /
  base64 与图像解码失败抛 ResumeVLError
- evaluate_resume：成功返回 name_seen/resume_summary/score/match_summary/key_info +
  指定通道直连（provider/model 透传）+ 提示词含期望姓名 / JSON 不合法或 name_seen 缺失按失败
  重试 / 重试耗尽抛 ResumeVLError / 无职位 score=null
- resume_name_matches：精确包含 / 错 1 字 / 漏 1 字 / 噪声尾 / 头部窗口外不命中 / 空输入 false
"""

import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from src.services import resume_vl_service
from src.services.resume_vl_service import ResumeVLModelError, ResumeVLError

pytestmark = pytest.mark.unit


def _png_base64(width: int, height: int, seed: int = 7) -> str:
    """生成确定性的噪声 PNG（每像素颜色随坐标变化，重叠区像素比对才有区分度）"""
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            px[x, y] = ((x * 13 + y * 29 + seed) % 256, (x * 7 + y * 3 + seed) % 256, (x + y + seed) % 256)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _decode_band(b64: str) -> Image.Image:
    img = Image.open(BytesIO(base64.b64decode(b64)))
    img.load()
    return img


def _make_gateway(content: str | dict | None, error: Exception | None = None):
    """构造 mock gateway：chat_direct 抛 error（非 None）或返回 content（dict 直接作响应，str 包装成 content）"""
    gw = MagicMock()
    if error is not None:
        gw.chat_direct = AsyncMock(side_effect=error)
    else:
        resp = content if isinstance(content, dict) else {"content": content}
        gw.chat_direct = AsyncMock(return_value=resp)
    gw.get_model_name = MagicMock(return_value="GLM-5.3-Flash")
    return gw


def _eval_json(name_seen="王宣广", resume_summary="全栈工程师，5 年经验", score=82,
               match_summary="技术栈匹配", key_info=None, **extra) -> str:
    """构造合法的 VL 评估 JSON 文本"""
    data = {
        "name_seen": name_seen,
        "resume_summary": resume_summary,
        "score": score,
        "match_summary": match_summary,
        "key_info": key_info or {
            "years_of_experience": 5, "education": "本科", "current_company": "联合汽车电子",
            "core_skills": ["Java", "Vue"], "highlights": [], "ai_tool_usage": None,
            "salary_expectation": "15-20K", "concerns": [],
        },
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


# ==================== resolve_model_spec ====================


class TestResolveModelSpec:
    def _patch_settings(self, monkeypatch, model="zhipu/GLM-5.3-Flash", allowed=("zhipu/GLM-5.3-Flash",)):
        cfg = SimpleNamespace(model=model, allowed_models=list(allowed))
        monkeypatch.setattr(resume_vl_service, "settings", SimpleNamespace(resume_vl=cfg))

    def test_default_from_settings(self, monkeypatch):
        self._patch_settings(monkeypatch)
        assert resume_vl_service.resolve_model_spec(None) == ("zhipu", "GLM-5.3-Flash")
        assert resume_vl_service.resolve_model_spec("") == ("zhipu", "GLM-5.3-Flash")

    def test_explicit_allowed_model(self, monkeypatch):
        self._patch_settings(monkeypatch, allowed=("zhipu/GLM-5.3-Flash", "zhipu/GLM-5.3-Flash-V2"))
        assert resume_vl_service.resolve_model_spec("zhipu/GLM-5.3-Flash-V2") == ("zhipu", "GLM-5.3-Flash-V2")

    def test_explicit_not_in_allowlist_rejected(self, monkeypatch):
        """白名单外模型拒绝（识别费按份固定积分，放行任意模型会计费错配）"""
        self._patch_settings(monkeypatch)
        with pytest.raises(ResumeVLModelError, match="不在允许列表"):
            resume_vl_service.resolve_model_spec("openai/gpt-9")

    def test_invalid_format_rejected(self, monkeypatch):
        self._patch_settings(monkeypatch, allowed=())  # 空白名单=只看格式
        with pytest.raises(ResumeVLModelError, match="provider/model"):
            resume_vl_service.resolve_model_spec("GLM-5.3-Flash")  # 缺 provider 前缀


# ==================== slice_stitched_image ====================


class TestSliceStitched_image:
    def test_short_image_single_band_full_picture(self):
        """图高 ≤ VL_SINGLE_IMAGE_MAX_HEIGHT → 整图单发（1 带 = 原图，无切缝）"""
        b64 = _png_base64(20, 600)
        bands = resume_vl_service.slice_stitched_image(b64)
        assert len(bands) == 1
        img = _decode_band(bands[0])
        assert img.size == (20, 600)

    def test_tall_image_bands_with_overlap_and_pixel_alignment(self):
        """高图切带：带间重叠 = VL_BAND_OVERLAP，重叠区像素逐点一致（对齐无翻转）"""
        height = 5000  # 1800 带高 / 1400 步进 → tops [0,1400,2800,4200]
        bands = resume_vl_service.slice_stitched_image(_png_base64(16, height))
        assert len(bands) == 4
        imgs = [_decode_band(b) for b in bands]
        assert [i.size[1] for i in imgs] == [1800, 1800, 1800, 5000 - 4200]
        ov = resume_vl_service.VL_BAND_OVERLAP
        for prev, cur in zip(imgs, imgs[1:]):
            # 前带底部 ov 行与后带顶部 ov 行必须是同一片像素（确定噪声图逐点比对）
            prev_tail = prev.crop((0, prev.size[1] - ov, prev.size[0], prev.size[1]))
            cur_head = cur.crop((0, 0, cur.size[0], ov))
            assert list(prev_tail.getdata()) == list(cur_head.getdata())

    def test_extremely_tall_image_rebanded_within_limit(self):
        """超高图默认切带超 10 → 加大带高重切，带数 ≤ MAX_VL_BANDS 且仍带重叠盖满全图"""
        height = 16000  # 默认带高会切出 12 带 → 触发重切
        bands = resume_vl_service.slice_stitched_image(_png_base64(8, height))
        assert len(bands) <= resume_vl_service.MAX_VL_BANDS
        ov = resume_vl_service.VL_BAND_OVERLAP
        imgs = [_decode_band(b) for b in bands]
        for prev, cur in zip(imgs, imgs[1:]):
            prev_tail = prev.crop((0, prev.size[1] - ov, prev.size[0], prev.size[1]))
            cur_head = cur.crop((0, 0, cur.size[0], ov))
            assert list(prev_tail.getdata()) == list(cur_head.getdata())
        total_span = imgs[0].size[1] + sum(
            cur.size[1] - ov for cur in imgs[1:]
        )
        assert total_span == height  # 去重叠后恰好覆盖全图

    def test_invalid_base64_raises(self):
        with pytest.raises(ResumeVLError, match="base64"):
            resume_vl_service.slice_stitched_image("!!!not-base64!!!")

    def test_garbage_bytes_raises(self):
        garbage = base64.b64encode(b"not a png at all").decode("ascii")
        with pytest.raises(ResumeVLError, match="解码失败"):
            resume_vl_service.slice_stitched_image(garbage)

    def test_empty_input_raises(self):
        with pytest.raises(ResumeVLError, match="为空"):
            resume_vl_service.slice_stitched_image("")


# ==================== evaluate_resume ====================


class TestEvaluateResume:
    def _patch_allow(self, monkeypatch):
        monkeypatch.setattr(resume_vl_service, "settings", SimpleNamespace(
            resume_vl=SimpleNamespace(model="zhipu/GLM-5.3-Flash", allowed_models=["zhipu/GLM-5.3-Flash"])))

    async def test_success_returns_full_evaluation(self, monkeypatch):
        gw = _make_gateway(_eval_json())
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        r = await resume_vl_service.evaluate_resume(["AAAA"], "王宣广")
        assert r["name_seen"] == "王宣广"
        assert r["score"] == 82
        assert r["resume_summary"] == "全栈工程师，5 年经验"
        assert r["match_summary"] == "技术栈匹配"
        assert r["key_info"]["years_of_experience"] == 5
        assert r["model"] == "GLM-5.3-Flash"
        assert gw.chat_direct.await_args.args == ("zhipu", "GLM-5.3-Flash")

    async def test_prompt_contains_expected_name_and_job_context(self, monkeypatch):
        """决策⑨：期望姓名传入提示词（模型先读姓名）；职位上下文拼入评分指令"""
        gw = _make_gateway(_eval_json())
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        job_ctx = {"job_name": "全栈工程师", "notes": "上海优先",
                   "opening_scripts": [{"title": "开场", "content": "你好"}],
                   "job_requirements": None, "match_threshold": 60}
        await resume_vl_service.evaluate_resume(["AAAA"], "王宣广", job_ctx)
        prompt = gw.chat_direct.await_args.kwargs["messages"][0]["content"][0]["text"]
        assert "王宣广" in prompt  # 期望姓名进提示词
        assert "全栈工程师" in prompt and "上海优先" in prompt and "你好" in prompt
        assert '"score": 82' in prompt  # JSON 结构示例

    async def test_no_job_ctx_instructs_null_score(self, monkeypatch):
        """无职位上下文 → 提示词要求 score=null；响应 score=null 原样透出"""
        gw = _make_gateway(_eval_json(score=None, match_summary=None))
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        r = await resume_vl_service.evaluate_resume(["AAAA"], "王宣广", job_ctx=None)
        assert r["score"] is None and r["match_summary"] is None
        prompt = gw.chat_direct.await_args.kwargs["messages"][0]["content"][0]["text"]
        assert "null" in prompt

    async def test_multimodal_message_shape(self, monkeypatch):
        """user 消息 content 为 list：首项提示词文本 + 逐带 image_url data URL（按阅读顺序）"""
        gw = _make_gateway(_eval_json())
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        await resume_vl_service.evaluate_resume(["AAA", "BBB"], "王宣广")
        messages = gw.chat_direct.await_args.kwargs["messages"]
        assert len(messages) == 1 and messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        urls = [item["image_url"]["url"] for item in content[1:]]
        assert urls == ["data:image/png;base64,AAA", "data:image/png;base64,BBB"]

    async def test_retry_once_then_success(self, monkeypatch):
        """第 1 次异常 → 重试 1 次后成功（共调 2 次）"""
        gw = MagicMock()
        gw.chat_direct = AsyncMock(side_effect=[RuntimeError("timeout"), {"content": _eval_json()}])
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        r = await resume_vl_service.evaluate_resume(["AAAA"], "王宣广")
        assert r["name_seen"] == "王宣广"
        assert gw.chat_direct.await_count == 2

    async def test_invalid_json_retried_then_raises(self, monkeypatch):
        """JSON 不合法视为失败走重试；重试仍败 → ResumeVLError（共调 2 次）"""
        gw = _make_gateway("我不是 JSON")
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        with pytest.raises(ResumeVLError, match="重试后仍失败"):
            await resume_vl_service.evaluate_resume(["AAAA"], "王宣广")
        assert gw.chat_direct.await_count == 2

    async def test_missing_name_seen_retried_then_raises(self, monkeypatch):
        """name_seen 缺失/空 = 解析失败（姓名是防张冠李戴的判定依据，缺失绝不放行）"""
        gw = _make_gateway(_eval_json(name_seen="  "))
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        with pytest.raises(ResumeVLError, match="重试后仍失败"):
            await resume_vl_service.evaluate_resume(["AAAA"], "王宣广")
        assert gw.chat_direct.await_count == 2

    async def test_persistent_exception_raises_after_retry(self, monkeypatch):
        gw = _make_gateway(None, error=RuntimeError("api down"))
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        with pytest.raises(ResumeVLError, match="重试后仍失败"):
            await resume_vl_service.evaluate_resume(["AAAA"], "王宣广")
        assert gw.chat_direct.await_count == 2

    async def test_empty_bands_input_raises_without_llm(self, monkeypatch):
        gw = _make_gateway(_eval_json())
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        with pytest.raises(ResumeVLError, match="入参为空"):
            await resume_vl_service.evaluate_resume([], "王宣广")
        gw.chat_direct.assert_not_awaited()

    async def test_markdown_fenced_json_still_parses(self, monkeypatch):
        """模型偶发包 ``` 代码块 → _parse_json 剥壳后照常解析"""
        fenced = "```json\n" + _eval_json() + "\n```"
        gw = _make_gateway(fenced)
        monkeypatch.setattr(resume_vl_service, "llm_gateway", gw)
        r = await resume_vl_service.evaluate_resume(["AAAA"], "王宣广")
        assert r["score"] == 82


# ==================== resume_name_matches ====================


class TestResumeNameMatches:
    def test_exact_and_space_scattered_hit(self):
        """精确包含 + 空格打散命中（真机首行样本形态）"""
        line = "最 近 关 注 工 作 经 历 0 0 康 嘉 润 飓 飓 活 跃 严 24 《 大 亏 4 年 离 一 随 时 到 岗"
        assert resume_vl_service.resume_name_matches("康嘉润", f"{line}\n后续内容") is True
        assert resume_vl_service.resume_name_matches("张三", "张三 男 26岁 本科\nPHP 开发 5 年") is True

    def test_one_char_tolerances(self):
        """1 字容差：错 1 字（替换）/ 漏 1 字 / 多 1 字噪声尾——绝不超过 1 字"""
        assert resume_vl_service.resume_name_matches("康嘉润", "庭嘉润 活跃") is True  # 错 1 字
        assert resume_vl_service.resume_name_matches("康嘉润", "康嘉 活跃") is True  # 漏 1 字
        assert resume_vl_service.resume_name_matches("康嘉润", "康嘉润飓 活跃") is True  # 噪声尾
        assert resume_vl_service.resume_name_matches("康嘉润", "康波波 活跃") is False  # 错 2 字
        assert resume_vl_service.resume_name_matches("康嘉润", "康 活跃") is False  # 漏 2 字

    def test_outside_head_window_not_matched(self):
        """姓名出现在 400 字窗口之外 → 不命中（避免长文正文随机相似串误放行）"""
        filler = "无" * 500
        assert resume_vl_service.resume_name_matches("康嘉润", filler + "康嘉润") is False

    def test_empty_inputs_false(self):
        assert resume_vl_service.resume_name_matches("", "张三") is False
        assert resume_vl_service.resume_name_matches("张三", "") is False
        assert resume_vl_service.resume_name_matches("张三", "   ") is False
