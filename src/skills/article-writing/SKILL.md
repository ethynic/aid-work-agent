---
name: article-writing
description: 按照专业流程撰写高质量文章、博客或报告。当用户要求写文章、写方案、撰写报告、写博客时使用。
metadata:
  version: "1.0"
  author: aid-work-agent
---

# 文章撰写工作流

## 适用场景
当用户要求撰写文章、博客、PR 文稿、报告等长文本内容时使用此技能。

## 工作流程

### 步骤1：理解需求
- 分析用户要求的主题、目标读者、文章长度、风格要求
- 如果信息不足，使用 `clarify` 工具向用户询问

### 步骤2：生成大纲
使用 `content_generate` 工具生成文章大纲：
```
content_generate(
    prompt="根据以下主题生成结构清晰的文章大纲：{用户主题}。要求：3-5个主要章节，每个章节2-3个子要点",
    content_type="outline"
)
```

### 步骤3：逐节撰写
对大纲中的每个章节，依次使用 `content_generate` 工具撰写内容：
```
content_generate(
    prompt="根据以下大纲撰写第N章「章节名」的完整内容：{大纲}。要求：专业严谨、逻辑清晰、约500字",
    content_type="article"
)
```

### 步骤4：整合与润色
将所有章节内容整合后，使用 `content_generate` 工具进行最终润色：
```
content_generate(
    prompt="请对以下完整文章进行润色优化，确保逻辑连贯、语言流畅：{完整文章}",
    content_type="polish"
)
```

## 质量标准
- 结构清晰，逻辑递进
- 语言专业但不晦涩
- 有数据或案例分析优先
