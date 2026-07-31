"""场景预设硬编码常量（MVP 2 个场景，不建表）。

设计依据：docs/system/content-production/mvp-design.md §6。
提示词引擎极简版：expanded_prompt = scene.prompt_template.format(copywriting=copywriting)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenePreset:
    """场景预设。"""
    scene_id: str
    name: str                   # 展示名
    description: str
    prompt_template: str        # 含 {copywriting} 变量槽
    negative_prompt: str        # 负面词（传给万相 parameters.negative_prompt）
    default_duration: int       # 默认时长（秒）


SCENES: dict[str, ScenePreset] = {
    "product_showcase": ScenePreset(
        scene_id="product_showcase",
        name="产品展示",
        description="产品主体居中，缓慢运镜展示全貌与细节，纯净背景突出产品",
        prompt_template=(
            "写实产品广告风格，{copywriting}。"
            "产品主体居中稳定展示，纯净柔和的摄影棚背景，"
            "缓慢的推近镜头（slow dolly in）突出产品细节与质感，"
            "专业打光，高细节，8k画质，商业广告质感。"
        ),
        negative_prompt="变形，扭曲，模糊，morphing，手指畸形，低质量，水印，文字，多余物体",
        default_duration=5,
    ),
    "atmosphere": ScenePreset(
        scene_id="atmosphere",
        name="场景氛围",
        description="产品置于使用场景中，氛围光感呈现，营造使用联想",
        prompt_template=(
            "写实氛围场景风格，{copywriting}。"
            "产品自然置于使用场景中，温暖自然的光线，"
            "缓慢的环绕镜头（slow orbit）展示产品与场景的融合，"
            "浅景深，电影感氛围，高细节，8k画质。"
        ),
        negative_prompt="变形，扭曲，模糊，morphing，手指畸形，低质量，水印，文字，杂乱背景",
        default_duration=5,
    ),
}


def get_scene(scene_id: str) -> ScenePreset | None:
    return SCENES.get(scene_id)


def list_scenes() -> list[ScenePreset]:
    return list(SCENES.values())
