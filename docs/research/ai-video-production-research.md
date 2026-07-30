# AI 批量电商视频生成深度调研：从 API 调用到可用视频的工艺链

> 状态：调研完成  
> 日期：2026-07-29  
> 关联：[AI 批量视频内容生产系统 PRD](../system/content-production/ai-video-production-prd.md) #47  
> 调研目标：搞清楚"一个能过审、能带货、不千篇一律的 AI 电商视频，到底是怎么生产出来的"，为 PRD 从"调 API"升级为"场景化预设 + 精准 prompt + 用户微调"提供工艺依据。

---

## 核心结论（TL;DR）

1. **"从 API 到可用视频"是一条 8-10 环节的流水线，而非单次 API 调用。** 抠图→参考图注入→AI 补全完整 prompt→运镜设计→I2V 生成→多段拼接→超分补帧→转场→字幕→配音/BGM。任何只做"调一次生成 API"的产品都会倒在产品变形、画面闪烁、时长不够这些坑上。

2. **社区已沉淀大量可复用资产**：ComfyUI 工作流 JSON（= 可序列化的"出片配方"）、运镜 LoRA、可灵 8 层提示词框架、Seedance 结构化公式。这些应作为产品"场景预设"的技术蓝本。

3. **所有成功竞品的共性范式 = "场景模板 + AI 补全 prompt + 用户微调"**（Creatify URL 转视频、Runway 多图参考+标签 prompt、万相营造场景模板一键匹配）。产品不是让用户写 prompt，而是让用户选场景。

4. **四个对原 PRD 的硬冲击**（必须修正）：
   - ⚠️ 带货视频黄金时长是 **15-30s**，60s 完播率 <70%，原 PRD 的 30-60s 偏长。
   - ⚠️ **AI 内容标识 2025.9.1 起是硬性法规**（抖音/视频号/快手/B站全跟进），产品必须内置合规标识能力。
   - ⚠️ **平台明确惩罚"同构图换皮连发"**，原"多SKU×单模板批量"正是此模式，需引入差异化机制（多脚本角度 A/B + 运镜/光影变化）。
   - ⚠️ **美妆品类行业共识：不能纯 AI**，需"实拍商品图 + AI 场景扩展 + AI 口播"混合，纯数字人对美妆是减分项。

5. **纯 AI 视频带货的规模化 GMV 闭环，公开可查的真实案例很少**。大量"月入 XX 万"案例本质是卖 AI 课/矩阵号流量主。这说明 AI 视频当前的真实定位是**辅助投放素材生产 + 快速试错**，而非"一键生成直接带货"。

---

## 一、开源图生视频 SOTA 盘点（2024-2026）

### 1.1 核心规格对比

| 模型 | 出品 | 分辨率 | 单段时长 | I2V | 显存 | 运镜控制 | 许可证 | 电商适配 |
|------|------|--------|---------|-----|------|---------|--------|---------|
| **Wan2.2-I2V-A14B** | 阿里万相 | 480-720p | 5s（可扩展）| ✅ | 16GB | ✅ Fun-Control + 运镜LoRA | **Apache 2.0** | ★★★★★ 首选 |
| Wan2.1 | 阿里万相 | 1280×720 | 5s | ✅ | 16GB | ❌（靠prompt） | Apache 2.0 | ★★★★ |
| CogVideoX1.5-5B-I2V | 智谱 | 任意分辨率 | 10s(161帧) | ✅ | **5GB** | ✅ ControlNet | Apache 2.0 | ★★★★ 低显存王 |
| HunyuanVideo | 腾讯 | 1280×720 | 5s | ✅ | 24GB | 部分 | ⚠️ Tencent许可（限商用，EU/UK/韩禁） | ★★★ |
| LTX-Video | Lightricks | 1216×704 | 最长2分钟 | ✅ | **8GB** | ❌ | Apache 2.0 | ★★★ |
| Mochi 1 | Genmo | 848×480 | 5.4s | ❌仅T2V | 24GB | ❌ | Apache 2.0 | ★★ |
| SVD | Stability | 1024×576 | ~4s | ✅ | ~10GB | ❌ | SAI社区许可 | ★★ 老旧 |
| DynamiCrafter | 腾讯ARC | 1024×1024 | 短 | ✅ | 中 | ❌ | Apache 2.0 | ★★★ |

### 1.2 自建技术栈选型结论

- **第一梯队首选**：**Wan2.2-I2V-A14B**——2025-2026 开源 SOTA，MoE 架构、Apache 2.0 完全可商用、官方 Fun-Control 运镜控制 + 丰富运镜 LoRA 生态。仓库 [Wan-Video](https://github.com/Wan-Video)、[Wan2.2-Fun-A14B-Control](https://huggingface.co/alibaba-pai/Wan2.2-Fun-A14B-Control)、加速 [Wan2.2-Lightning](https://github.com/ModelTC/Wan2.2-Lightning)（4步生成）。
- **轻量/边缘/试水**：**CogVideoX1.5-5B-I2V**——5GB 显存可跑、任意分辨率、10s、Apache 2.0。
- **避坑**：HunyuanVideo 许可证限商用（EU/UK/韩禁），商业化不押注；Mochi 仅 T2V 不适合图生视频。
- **数字人/S2V**：[Wan2.2-S2V-14B](https://modelscope.cn/models/Wan-AI/Wan2.2-S2V-14B)，480-720p、16s、24fps（音频驱动数字人，远期）。

---

## 二、电商视频专项技术（产品一致性的核心壁垒）

### 2.1 主体一致性（Subject Consistency）——让产品不变形

电商视频第一痛点。技术栈分层：

| 层 | 技术 | 说明 |
|----|------|------|
| **A. 参考图注入（最重要）** | [IP-Adapter](https://github.com/tencent-ailab/IP-Adapter) | 把产品参考图作为图像 prompt 注入扩散模型，保持主体一致的事实标准。FaceID 变体用于人脸/模特。 |
| **A+ 多图参考 + 标签 prompt** | Runway Gen-4 References 范式 | 上传 1-3 张参考图，prompt 用 `image_1/image_2` 标签引用，保持主体/风格/场景一致性。**闭源最成熟范式，值得借鉴到自建产品。** |
| B. 产品 LoRA | 对特定 SKU 训练小 LoRA | 生成时锁定该产品外观，社区已大量服饰/鞋包 LoRA |
| C. ControlNet 约束 | 深度/边缘图 | 约束产品轮廓不漂移 |
| D. 多参考帧 | few-shot reference | 单张参考帧一致性差，**建议支持上传 2-4 张多角度产品图** |

> **关键启示**：产品应支持"上传 2-4 张多角度产品图"而非单图，多图参考 + 标签化 prompt 是保证产品不变形的核心机制。

### 2.2 运镜控制（Camera Control）

**工程实用方案（推荐）**：
- **Wan2.2-Fun-Control**（阿里 PAI 官方，[ComfyUI 教程](https://docs.comfy.org/zh/tutorials/video/wan/wan2-2-fun-control)）：Control Codes + 多模态条件输入，ComfyUI 原生支持。自建运镜控制首选。
- **首尾帧模式（Start-End Frame）**：给起始帧（产品正面）+ 结束帧（产品侧面/使用场景），模型自动插值运镜——**最可控、最适合电商**的范式，可灵/即梦均主打。
- **运镜 LoRA**：社区训练好的 orbital/dolly/pan 专用 LoRA，加载即用。

**学术方法**：CameraCtrl / CameraCtrl II（ICCV2025）、MotionCtrl（SIGGRAPH2024，腾讯ARC，独立控制相机+物体运动）、CamI2V / RealCAM-I2V（ICCV2025，3D 拖拽画轨迹）。

### 2.2.1 通义万相云 API 防变形能力查证（2026-07-29，重要区分）

> **关键区分**：IP-Adapter / 产品 LoRA / 多图参考 / ControlNet 都是**开源模型本地部署**才能用的技术。**通义万相云 API 是黑盒服务，挂不了这些插件**。若 MVP 选通义万相 API，防变形只能靠 API 自身能力。

查证万相 2.7（最新）+ 首尾帧官方文档结论：

| 手段 | 机制 | 效果 | API 支持 |
|------|------|------|---------|
| **首尾帧模式**（`first_frame`+`last_frame`）| 起止两帧都是产品图，模型平滑插值，发挥空间被锚定 | ⭐⭐⭐⭐ 最强 | ✅ |
| 首帧模式 + 精准 prompt | 产品图当起点，文字约束主体+单一运镜 | ⭐⭐⭐ 中等 | ✅ |
| `negative_prompt`（2.7 支持）| 排除"变形、扭曲、morphing" | ⭐⭐ 辅助 | ✅ |
| 1080P + 短时长（2.7 支持 2-15s）| 高分辨率更稳，短时长变形少 | ⭐⭐ 辅助 | ✅ |

**限制**：
- 万相 2.7 `media` 数组每种类型仅 1 张（不支持多图参考）。
- 无独立运镜参数，运镜靠 prompt 文字隐式引导。
- 首尾帧模式需运营提供尾帧图；单图退化为首帧模式，防变形降至中等。

**结论**：通义万相 API 防变形主力 = 首尾帧模式。需前置 spike 量化「首尾帧 vs 首帧」的变形率，验证纯 API 能否满足"变形率<10%"。若不足，备选为提前引入 Phase 1 自建 Wan2.2 + IP-Adapter。参考：[万相2.7 API](https://www.alibabacloud.com/help/zh/model-studio/image-to-video-general-api-reference)、[首尾帧指南](https://help.aliyun.com/zh/model-studio/image-to-video-first-and-last-frames-guide)。

### 2.3 背景生成与主体分离（批量出片的核心机制）

```
抠图 → 生成新背景 → 合成
```
- 抠图/分割：[SAM3](https://github.com/facebookresearch/sam2)、BiRefNet、InSPyReNet、[Rembg](https://github.com/danielgatis/rembg)（最易用，支持 SAM）。
- 视频级精细抠像：MatAnyone 2（人体）、BRIA 模型。
- **电商关键模式**：抠图 → 生成新背景 → 合成，让同一产品出现在"沙滩/厨房/摄影棚"等多场景——**这是批量出片、避免同质化的核心**。

---

## 三、创作者社区提示词/skill 生态

### 3.1 ComfyUI 工作流 = 可序列化的"出片配方"

现成电商/产品视频工作流模板（可直接下载复用）：
- [comfy.org - Photo to Product Video](https://comfy.org/workflows/templates-photo_to_product_vid-02c118b50bc2)：商品照→产品视频，官方模板。
- [comfy.org - Product Scene Transformation](https://comfy.org/workflows/templates_product_scene_transformation-d686f64879fb)：产品场景动态变换。
- [Civitai - WAN2.1 IMG to VIDEO v4.0](https://civitai.com/models/1309369)（UmeAiRT 一体化工具箱）。
- OpenArt：托管式，无需搭工作流即可跑产品视频。

> **关键启示**：ComfyUI 工作流 `.json` 本质是可序列化的"出片工艺"，可直接作为产品"场景预设"的技术蓝本。产品应内建工作流引擎或对接 ComfyUI。

### 3.2 提示词工程公式（社区共识）

**高质量图生视频 prompt 公式**（[Reddit 复用模板](https://www.reddit.com/r/PromptEngineering/comments/1qf3ge1/)）：
```
Camera: [运镜] + [景别]. Motion: [主体运动]. Lighting: [主光]. Mood: [氛围]. Style: cinematic, realistic, film grain.
```

**电商铁律**：主体运动只给**一个明确动作** + 运镜只给**一个方向**。
- 反模式：堆砌运镜词（zoom pan orbit tilt 全上 → 模型混乱）；描述太抽象（"展示高级感"→ 出片不可控）。

**权威指南**：[Runway I2V Prompting Guide](https://help.runwayml.com/hc/en-us/articles/48324313115155)、[Kling Prompt Guide](https://kling.ai/blog/kling-ai-prompt-guide)。

### 3.3 可灵 8 层提示词框架（最系统化的资产，[来源](https://xiangyugongzuoliu.com/kling-video-prompt-guide/)）

核心原则"从左到右权重递减"，8 层结构：
1. 元素参考声明 → 2. 镜头标签 → 3. 景别与主体（2-3 个材质特征，禁用"漂亮的"模糊词）→ 4. 动作（细化到身体部位、量化幅度、情绪外化）→ 5. 运镜（**每镜头只写一个，与主体动作分开**）→ 6. 场景与光影（**命名真实光源收益极高**，如"苹果主题演讲光"）→ 7. 音频（每镜头独立）→ 8. 全局收尾（风格锚点 + 质量后缀 + 负面提示词）。

**已验证的电商三镜头开箱展示模板**：镜头1 特写包装+正上方光缓缓亮起+缓慢推进；镜头2 俯拍开箱+主光正上方+固定机位；镜头3 细节旋转+双重环绕。收尾：照片级产品摄影、85mm、浅景深 + 负面词"变形扭曲、形态渐变、模糊纹理、伪影"。

### 3.4 Seedance 2.0 结构化电商公式（[GitHub](https://github.com/liangdabiao/make-prompt-seedance2)）

模板八（电商商品展示）：
```
【风格】___风格，___秒，___比例，___氛围
【时间轴】0-X秒：[镜头]+[画面]+[动作]+[特效]
【声音】___配乐+___音效+___对白
【参考】@图片1 ___，@视频1 ___
```
技巧：**写意图而非微观细节**；支持最多 9 图+3 视频多模态参考。

### 3.5 提示词资产沉淀位置

- [Vlog Prompt](https://vlogprompt.com/)：按场景/镜头/风格/模型检索，支持 Sora/Veo/Kling/即梦/海螺。
- [即梦 4.0 提示词 100 条](https://zhuanlan.zhihu.com/p/1952769077547341542)。
- [阿里云文生/图生视频 Prompt 指南](https://help.aliyun.com/zh/model-studio/text-to-video-prompt)。

---

## 四、"从 API 到可用视频"的工艺链拆解

一条高质量电商视频绝不是"调一次 API"，而是 8-10 环节流水线：

| # | 环节 | 开源工具/方法 | 失败模式与解法 |
|---|------|--------------|---------------|
| 1 | 主体检测/抠图 | SAM3、BiRefNet、Rembg | 抠图不干净→换背景有毛边 |
| 2 | 参考图注入/产品锁定 | IP-Adapter、产品 LoRA、多图参考 | **产品变形morphing**→加产品 LoRA + 深度 ControlNet |
| 3 | 背景生成与主体分离 | 抠图 + 文生图背景 + 合成 | — |
| 4 | 运镜设计 | Wan2.2-Fun-Control、首尾帧、运镜LoRA | **运镜突兀**→单一方向 + 首尾帧 |
| 5 | 核心 I2V 生成 | Wan2.2 / CogVideoX1.5 | **画面闪烁flicker**→补帧RIFE + 限制运动幅度 |
| 6 | 多段生成+时长拼接 | 首尾帧接力、视频续写 | **多段拼接畸变**→chunk间强一致性约束；**主体漂移**→锁定参考帧+减小单段时长 |
| 7 | 超分+补帧（流畅度）| Topaz节点、RIFE、4x超分 | **模糊低细节**→标准模式+后期超分 |
| 8 | 转场 | FFmpeg / 剪映 | — |
| 9 | 字幕 | [SmartSub](https://github.com/buxuku/smartsub)（Whisper/FunASR 一站式） | — |
| 10 | 配音+BGM | SmartSub声音克隆 / Voice-Pro TTS | — |

---

## 五、场景化预设实践

### 5.1 电商视频典型场景分类与 prompt 模板

| 场景 | prompt 模板 | 运镜 | 品类适配 |
|------|------------|------|---------|
| 开箱展示 | Close-up of hands opening box, slow reveal, packaging details, soft studio light, premium mood | slow push in | 通用 |
| 使用教程/功能演示 | Product on table, hand pressing button, lights turning on, overhead view, clean bg | overhead pan | 工具/美妆 |
| 场景氛围 | Product on marble counter, morning sunlight, water droplets, dewy fresh | slow orbit | 家居/美妆 |
| 对比测评 | Side by side comparison on white pedestal, rotating display | static+slow pan | 通用 |
| 模特试戴 | Model wearing product, turning slowly, soft beauty light, fashion editorial | orbit+medium | 服饰/珠宝 |
| 产品旋转展示 | Product on rotating pedestal, 360 turn, studio key, white seamless bg | static主体转 | 通用 |
| 细节特写 | Macro shot of texture/engraving, shallow DoF, cinematic | macro dolly in | 通用 |

### 5.2 运镜词汇库（社区实测效果对照）

| 词汇 | 效果 | 电商适用 |
|------|------|---------|
| dolly in / push in | 推近突出主体 | ★★★★★ 最常用 |
| orbit / 360 | 环绕展全貌 | ★★★★★ |
| pan left/right | 水平摇展场景 | ★★★★ |
| tracking shot | 跟随主体 | ★★★★ |
| macro | 微距特写 | ★★★★ 细节 |
| tilt up/down | 垂直仰俯 | ★★★ |
| zoom in/out | 光学变焦 | ★★★ 易生硬 |

参考：[12 Essential Camera Movements](https://letsenhance.io/blog/all/ai-video-camera-movements/amp/)、[Wan 2.6 运镜指南](https://www.instasd.com/post/mastering-wan-2-6-for-cinematic-video-generation)。

---

## 六、竞品实践与共性做法

### 6.1 对手如何解决"用户描述模糊"——核心洞察

所有成功产品的共同范式 = **"场景模板 + AI 补全 prompt + 用户微调"**：
1. **接受极简输入**（一个 URL / 一句话 / 一张图）。
2. **自动抽取信息**：Creatify 抓取商品页所有信息（名称/描述/图片/卖点/价格）自动填 brief。
3. **场景模板作脚手架**：定义"完整视频应包含什么"（hook 黄金3秒→产品展示→利益点→CTA）。
4. **AI 推理补全缺口**：自动生成脚本、选配音、选场景、生成运镜词。
5. **给出完整草稿，用户只做微调**。

### 6.2 专门做"商品→视频"的 SaaS

| 产品 | 形态 | 特点 |
|------|------|------|
| **Creatify** | URL→广告视频，自动抓商品详情填模板 | [creatify.ai](https://creatify.ai/) |
| **万相营造**（阿里）| 专做电商商品视觉，一键智能匹配商品场景模板 | [wanxiang.art](https://www.wanxiang.art/) 最接近垂类 |
| **HeyGen** | Video Agent + 模板 + AI Product Placement | [heygen.com](https://www.heygen.com/) |
| **Arcads** | AI 演员 UGC 口播广告（1000+演员） | [arcads.ai](https://www.arcads.ai/) |
| **Higgsfield** | 单图→工作室级产品视频广告 | [higgsfield.ai](https://higgsfield.ai/ai-product-video-generator) |
| **极睿 iClip** | RPA抓取+批量混剪，日产出200+ | 淘系/京东大卖家 |
| **PicCopilot**（阿里）| 商品图→模特图/视频/海报 | 跨境，号称150万卖家 |
| **麦斯创意 MaxCreative** | 工作流编辑器（混剪/翻配/图生/爆款复刻）| TikTok/抖音 |

### 6.3 国内外形态差异

- **海外主流**：URL-to-Video（贴链接一键生成）+ AI UGC 头像。
- **国内主流**：图生视频 + 剪映精修 的工作流模式。
- 国产视频模型 API 约 **0.2-1 元/秒**。

---

## 七、抖音/视频号平台规则与质量标准（⚠️ 对 PRD 的硬冲击）

### 7.1 AI 内容标识是硬性法规（2025.9.1 起）

国家《人工智能生成合成内容标识办法》2025 年 9 月 1 日实施，抖音/视频号/快手/B站全跟进（[网信办通知](https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm)）：
- **强制显式标识**：AI 内容必须在画面醒目位置**长期放置**"AI 生成内容"标识，不能小字边角、不能一闪而过。
- **平台主动核验**：抖音通过元数据隐式标识自动检测未声明的 AI 内容。
- **实名/实人认证**：电商商家和虚拟主播必须先完成认证。
- 违规：限制推荐、截断流量、作品下架。

> **PRD 必须内置"醒目长期可见的 AI 标识"功能，否则用户发出去就被限流。**

### 7.2 被判"低质/搬运"的六条红线（[腾讯云整理](https://cloud.tencent.com/developer/article/2667206)）

1. 视觉低质：模糊、畸变、肢体崩坏、闪烁卡顿。
2. **搬运判定：直接套用他人 Prompt、照搬成片且无原创脚本**。
3. 内容空洞：仅堆砌 AI 画面，无剧情和情绪价值。
4. **灌水同质化：相同模板批量混剪、"同构图换皮连发"**。
5. 题材违规。
6. 诱导互动。
- 处罚：限流→扣健康分→禁言→封号。

> **PRD 的"多SKU×单模板批量"正是红线4"同构图换皮连发"。必须引入差异化机制。**

### 7.3 时长与完播率（⚠️ 原PRD 30-60s 偏长）

| 时长 | 完播率 | 适用 |
|------|--------|------|
| **15-30 秒** | 90%+ | **带货视频黄金区间** |
| 30-45 秒 | 80-85% | 产品深度展示 |
| 超过 60 秒 | <70% | 故事/情感类 |

- 快手数据：20-25s 完播率比 30-35s **高 23%**。
- 带购物车视频完播率本就低于不带购物车的，需更精简内容弥补。
- 抖音按停留时长分赛道（7-15s/15-30s/30-60s+），15-30s 是带货主赛道。

> **建议主力时长下调到 15-30s，60s 仅用于品牌向叙事。**

### 7.4 高完播率/高转化 AI 视频共性

- **前 3 秒黄金钩子**：72% 用户 3 秒内划走。公式：直接提痛点 / 结果先行。
- 全程无冗余，每一秒都有价值输出。
- 结构化叙事：痛点→产品→对比→CTA 四段式。
- 垂直深耕 + 固定风格，固化账号标签。

### 7.5 品类适配（⚠️ 美妆不能纯AI）

| 适合 AI 视频 | 不适合/高风险 |
|-------------|--------------|
| 工具/课程/软件/B2B（数字人讲解）| 高客单体验型（美妆质地、服饰面料）|
| 标品、可视觉化卖点强 | 食品/生鲜（色香味无法呈现）|
| 跨境多语言 | 需精准尺寸的（家具、鞋服）|

> **行业共识：美妆品类"数字人不建议完全替代真实商品素材"，需"实拍商品图/视频 + AI 场景扩展 + AI 口播"混合模式。**

---

## 八、失败教训与商业化现实

### 8.1 创作者公开吐槽的四大坑

1. **"AI 照骗"翻车**：AI 修饰效果与实物差异大→退货率高、店铺评分下滑，已被[新华网](https://www.news.cn/comments/20260115/813a1be1c2764ec882868cb49d08af68/c.htm)点名。
2. **同质化撞款**：低门槛→大量商家同模板，起量越来越难，易被判搬运限流。
3. **转化反而下降**：把 AI 等同于内容生成→同质化→流量成本飙升→有效线索转化率不升反降。
4. **变形/失真**：需要精准尺寸的商品（家具、鞋服）尤其严重。

### 8.2 商业化现实（重要信号）

- 市场大量"AI 带货月入 XX 万"案例，本质是**卖 AI 课 / 矩阵号流量主**，而非纯 AI 视频带货 GMV。
- 纯 AI 生成视频的**直接带货转化数据，公开可查的真实案例很少**——这本身是个信号：规模化 GMV 闭环还没跑通。
- **AI 视频的真实定位 = 辅助投放素材生产 + 快速试错**，而非"一键生成直接带货"。
- 核心教训："**稳定的制作流程比追求一键生成更重要**"——AI 最适合快速改脚本、换画面、迭代版本，匹配短视频投放快速试错的运营逻辑。

---

## 九、对 PRD 的修订建议汇总

| 维度 | 原 PRD | 修订建议 | 依据 |
|------|--------|---------|------|
| 主力时长 | 30-60s | **15-30s 为主，60s 仅品牌向** | §7.3 完播率数据 |
| 合规 | 未提 | **新增"AI内容显式标识"为 P0 硬需求** | §7.1 法规 |
| 批量模式 | 多SKU×单模板 | **引入差异化：多脚本角度A/B + 运镜/光影变化** | §7.2 红线4 |
| 素材模式 | 纯图生视频 | **支持混合：实拍商品图 + AI场景扩展 + 口播** | §7.5 美妆品类 |
| 生成方式 | 单次API | **新增"工艺链"模块（8-10环节流水线）** | §四 |
| 产品形态 | 用户选模板生成 | **新增"场景预设 + AI补全prompt + 用户微调"引擎** | §六.1 对手共性 |
| 提示词资产 | 无 | **新增"场景预设库 + 提示词引擎"（对标可灵8层/Seedance公式）** | §三 |
| 技术架构 | 仅API provider | **补充 ComfyUI工作流引擎 / IP-Adapter主体一致性 / 运镜控制** | §一/二 |
| 数据模型 | product/creative/gen | **扩展 scene_presets / prompt_templates / workflow_json / asset_role** | §三/五 |
| 价值定位 | 一键批量出片 | **辅助投放素材生产 + 快速试错**，诚实定位 | §八.2 |

---

## 参考来源

**技术/开源**：[Wan-Video](https://github.com/Wan-Video)、[Wan2.2-Fun-Control](https://huggingface.co/alibaba-pai/Wan2.2-Fun-A14B-Control)、[IP-Adapter](https://github.com/tencent-ailab/IP-Adapter)、[Rembg](https://github.com/danielgatis/rembg)、[SmartSub](https://github.com/buxuku/smartsub)、[ComfyUI 工作流](https://comfy.org/)、[MotionCtrl](https://github.com/TencentARC/MotionCtrl)、[CameraCtrl](https://github.com/hehao13/CameraCtrl)

**提示词资产**：[Runway I2V Prompting Guide](https://help.runwayml.com/hc/en-us/articles/48324313115155)、[Kling Prompt Guide](https://kling.ai/blog/kling-ai-prompt-guide)、[可灵8层框架](https://xiangyugongzuoliu.com/kling-video-prompt-guide/)、[Seedance公式](https://github.com/liangdabiao/make-prompt-seedance2)、[Vlog Prompt](https://vlogprompt.com/)、[Reddit复用模板](https://www.reddit.com/r/PromptEngineering/comments/1qf3ge1/)

**竞品/SaaS**：[Creatify](https://creatify.ai/)、[万相营造](https://www.wanxiang.art/)、[HeyGen](https://www.heygen.com/)、[Arcads](https://www.arcads.ai/)、[Higgsfield](https://higgsfield.ai/ai-product-video-generator)、[PicCopilot](https://piccopilot.ai/)、[ClipClap假睫毛模板](https://clipclap.ai/template/6pldD?lang=zh)

**平台规则**：[网信办AI标识办法](https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm)、[抖音AIGC限流雷区](https://cloud.tencent.com/developer/article/2667206)、[电商短视频最优时长研究](https://pdf.hanspub.org/ecl_2316574.pdf)、[新华网AI照骗评论](https://www.news.cn/comments/20260115/813a1be1c2764ec882868cb49d08af68/c.htm)

**实战教程**：[人人都是产品经理-百万播放AI视频](https://www.woshipm.com/ai/6311714.html)、[SegmentFault-快速生成爆款带货视频](https://segmentfault.com/a/1190000048008749)、[DataFocus-电商脚本3模板](https://www.datafocus.ai/infos/ecommerce-short-video-sales-script-3-templates-boost-douyin-conversion-rate)
