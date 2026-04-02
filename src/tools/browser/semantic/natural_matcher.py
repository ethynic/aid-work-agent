"""自然语言匹配引擎

根据自然语言描述匹配快照中的元素。
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple
from loguru import logger

from .ref_mapper import InteractiveElement


@dataclass
class MatchResult:
    """匹配结果"""

    success: bool
    ref: Optional[str] = None
    label: Optional[str] = None
    confidence: float = 0.0
    alternatives: List[Dict[str, Any]] = None
    action: str = "click"
    error: Optional[str] = None

    def __post_init__(self):
        if self.alternatives is None:
            self.alternatives = []


class NaturalMatcher:
    """自然语言描述匹配器"""

    # 中文按钮关键词
    BUTTON_KEYWORDS = {
        "确认", "确定", "提交", "保存", "取消", "关闭", "删除", "编辑",
        "修改", "新建", "创建", "添加", "查询", "搜索", "登录", "注册",
        "下一步", "上一步", "返回", "退出", "下载", "上传", "刷新",
        "重置", "跳过", "继续", "完成", "发送", "接收",
    }

    # 英文按钮关键词
    ENGLISH_BUTTON_KEYWORDS = {
        "submit", "confirm", "cancel", "save", "delete", "edit", "create",
        "add", "search", "login", "register", "next", "prev", "back",
        "reset", "skip", "continue", "finish", "send", "receive",
        "ok", "yes", "no", "close", "open", "get", "post", "put",
    }

    # 输入框标签模式
    INPUT_PATTERNS = {
        r"用户[名称]?": "用户名",
        r"账号": "用户名",
        r"密码": "密码",
        r"邮箱|邮件|e-?mail": "邮箱",
        r"手机|电话|tel": "手机",
        r"金额|钱|数额|amount": "金额",
        r"日期|时间": "日期",
        r"描述|备注|说明|原因": "描述",
        r"姓名|名字": "姓名",
        r"地址": "地址",
        r"公司": "公司",
        r"部门": "部门",
        r"职位": "职位",
        r"标题": "标题",
        r"内容": "内容",
        r"备注|注释": "备注",
    }

    # Embedding 缓存（类级别共享）
    _embedding_cache: Dict[str, List[float]] = {}
    _embedding_model = None

    def __init__(self, ref_mapper):
        """初始化匹配器

        Args:
            ref_mapper: RefMapper 实例
        """
        self.ref_mapper = ref_mapper

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """获取文本的 embedding 向量

        使用简单的词向量模拟，实际生产环境可接入 OpenAI/Tongyi embeddings

        Args:
            text: 文本

        Returns:
            Optional[List[float]]: embedding 向量或 None
        """
        if not text:
            return None

        # 检查缓存
        cache_key = text.lower().strip()
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        try:
            # 尝试使用 sentence-transformers（如果可用）
            if NaturalMatcher._embedding_model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    NaturalMatcher._embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
                except ImportError:
                    logger.debug("sentence-transformers 未安装，使用字符级相似度")
                    return None

            # 生成 embedding
            embedding = NaturalMatcher._embedding_model.encode(text).tolist()

            # 存入缓存（限制缓存大小）
            if len(self._embedding_cache) < 10000:
                self._embedding_cache[cache_key] = embedding

            return embedding

        except Exception as e:
            logger.debug(f"生成 embedding 失败: {e}")
            return None

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度

        Args:
            vec1: 向量1
            vec2: 向量2

        Returns:
            float: 相似度 0-1
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def _embedding_similarity(self, text1: str, text2: str) -> float:
        """使用 embedding 计算语义相似度

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            float: 相似度 0-1
        """
        emb1 = self._get_embedding(text1)
        emb2 = self._get_embedding(text2)

        if emb1 and emb2:
            return self._cosine_similarity(emb1, emb2)

        # 如果无法使用 embedding，回退到字符级相似度
        return self._text_similarity(text1, text2)

    def match_click(self, description: str) -> MatchResult:
        """匹配点击目标

        Args:
            description: 自然语言描述，如"登录按钮"、"报销申请"

        Returns:
            MatchResult: 匹配结果
        """
        normalized_desc = self._normalize(description)
        candidates = []

        all_elements = (
            self.ref_mapper.get_all_elements() +
            [self.ref_mapper.get_by_ref(ref) for ref in self.ref_mapper._submenu_map.keys()]
        )
        all_elements = [e for e in all_elements if e]

        for elem in all_elements:
            confidence = self._calculate_click_confidence(normalized_desc, elem)
            if confidence > 0.3:
                candidates.append((elem, confidence))

        # 按置信度排序
        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            return MatchResult(
                success=False,
                error=f"未找到匹配元素: {description}",
            )

        best_match, best_confidence = candidates[0]
        alternatives = [
            {
                "ref": elem.ref,
                "label": elem.label,
                "confidence": conf,
            }
            for elem, conf in candidates[1:5]
        ]

        return MatchResult(
            success=True,
            ref=best_match.ref,
            label=best_match.label,
            confidence=best_confidence,
            alternatives=alternatives,
            action="click",
        )

    def match_fill(self, field: str, value: str = "") -> MatchResult:
        """匹配填写目标

        Args:
            field: 字段描述，如"用户名"、"报销金额"
            value: 要填写的值（用于验证）

        Returns:
            MatchResult: 匹配结果
        """
        normalized_field = self._normalize(field)
        candidates = []

        # 查找所有可输入元素（input 已包含在 find_inputs 中，不需要重复）
        input_elements = list(self.ref_mapper.find_inputs())
        # 补充 textarea（find_inputs 已包含，确保去重）
        seen_tags = set(e.ref for e in input_elements)
        for elem in self.ref_mapper.find_by_tag("textarea"):
            if elem.ref not in seen_tags:
                input_elements.append(elem)
                seen_tags.add(elem.ref)
        # 补充 contenteditable 元素（[role="textbox"] 等已通过 role 注册）

        for elem in input_elements:
            confidence = self._calculate_fill_confidence(normalized_field, elem)
            if confidence > 0.3:
                candidates.append((elem, confidence))

        # 按置信度排序
        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            return MatchResult(
                success=False,
                error=f"未找到匹配字段: {field}",
            )

        best_match, best_confidence = candidates[0]
        alternatives = [
            {
                "ref": elem.ref,
                "label": elem.label,
                "confidence": conf,
            }
            for elem, conf in candidates[1:5]
        ]

        return MatchResult(
            success=True,
            ref=best_match.ref,
            label=best_match.label,
            confidence=best_confidence,
            alternatives=alternatives,
            action="fill",
        )

    def match_select(self, field: str, option: str) -> MatchResult:
        """匹配选择目标

        Args:
            field: 选择框描述，如"部门"、"报销类型"
            option: 选项描述，如"技术研发部"、"差旅费"

        Returns:
            MatchResult: 匹配结果
        """
        normalized_field = self._normalize(field)
        candidates = []

        # 查找所有 select 元素
        select_elements = self.ref_mapper.find_by_tag("select")

        for elem in select_elements:
            confidence = self._calculate_fill_confidence(normalized_field, elem)
            if confidence > 0.3:
                candidates.append((elem, confidence))

        # 按置信度排序
        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            return MatchResult(
                success=False,
                error=f"未找到匹配选择框: {field}",
            )

        best_match, best_confidence = candidates[0]

        return MatchResult(
            success=True,
            ref=best_match.ref,
            label=best_match.label,
            confidence=best_confidence,
            alternatives=[],
            action="select",
        )

    def _normalize(self, text: str) -> str:
        """标准化描述文本

        Args:
            text: 原始文本

        Returns:
            str: 标准化后的文本
        """
        if not text:
            return ""

        # 转小写
        text = text.lower()

        # 移除常见前缀修饰词（从长到短匹配，避免部分匹配）
        # 优先匹配长模式（"帮我点击" > "请点击" > "点击"）
        prefix_patterns = [
            r"^帮我点击\s*",
            r"^请点击\s*",
            r"^请\s*点击\s*",
            r"^帮我\s*点击\s*",
            r"^按一下\s*",
            r"^点击\s*",
            r"^click\s+",
            r"^press\s+",
            r"^请填写\s*",
            r"^请输入\s*",
            r"^请\s*",
            r"^帮我\s+",
            r"^去\s+",
            r"^来进行\s+",
            r"^执行\s+",
            r"^触发\s+",
        ]
        for pattern in prefix_patterns:
            new_text = re.sub(pattern, "", text)
            if new_text != text:
                text = new_text
                break  # 只移除最外层的前缀

        # 移除常见后缀修饰词
        # 中文没有空格分隔，直接匹配后缀
        suffix_patterns = [
            r"按钮$",
            r"元素$",
            r"控件$",
            r"输入框$",
            r"表单$",
            r"字段$",
            r"链接$",
        ]
        # 英文需要空格分隔
        en_suffix_patterns = [
            r"\s+link$",
            r"\s+button$",
            r"\s+input$",
            r"\s+field$",
            r"\s+element$",
        ]
        for pattern in suffix_patterns:
            text = re.sub(pattern, "", text)
        for pattern in en_suffix_patterns:
            text = re.sub(pattern, "", text)

        # 移除多余空白
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    def _calculate_click_confidence(
        self,
        description: str,
        element: InteractiveElement,
    ) -> float:
        """计算点击匹配的置信度

        Args:
            description: 标准化后的描述
            element: 目标元素

        Returns:
            float: 置信度 0-1
        """
        label = self._normalize(element.label)
        if not label:
            return 0.0

        # 1. 精确匹配（最高）
        if description == label:
            return 1.0

        # 2. 描述包含标签 或 标签包含描述
        if description in label or label in description:
            # 长度差异越大，置信度越低
            len_ratio = min(len(description), len(label)) / max(len(description), len(label))
            return 0.7 + 0.2 * len_ratio

        # 3. 按钮关键词匹配
        is_button = (
            element.tag.lower() in ("button", "a") or
            element.role == "button" or
            (element.tag.lower() == "input" and element.input_type in ("submit", "reset", "button", "image"))
        )
        if is_button:
            button_score = self._keyword_match(description, label, self.BUTTON_KEYWORDS)
            if button_score > 0:
                return 0.5 + button_score * 0.3

        # 4. 语义相似度（优先使用 embedding）
        semantic_sim = self._embedding_similarity(description, label)
        if semantic_sim > 0.5:
            return semantic_sim * 0.7

        # 5. 字符级相似度（备用）
        similarity = self._text_similarity(description, label)
        if similarity > 0.5:
            return similarity * 0.6

        # 6. 部分字符匹配
        common_chars = set(description) & set(label)
        if common_chars and len(common_chars) >= 2:
            char_ratio = len(common_chars) / max(len(set(description)), len(set(label)))
            return char_ratio * 0.4

        return 0.0

    def _calculate_fill_confidence(
        self,
        field: str,
        element: InteractiveElement,
    ) -> float:
        """计算填写匹配的置信度

        Args:
            field: 标准化后的字段描述
            element: 目标元素

        Returns:
            float: 置信度 0-1
        """
        label = self._normalize(element.label)
        if not label:
            return 0.0

        # 1. 精确匹配
        if field == label:
            return 1.0

        # 2. 输入框标签模式匹配
        for pattern, standard_name in self.INPUT_PATTERNS.items():
            if re.search(pattern, field):
                # 字段匹配标准名称
                if standard_name in label or label in standard_name:
                    return 0.9
                # 检查标签是否包含字段
                if field in label:
                    return 0.7

        # 3. 描述包含标签 或 标签包含描述
        if field in label or label in field:
            len_ratio = min(len(field), len(label)) / max(len(field), len(label))
            return 0.6 + 0.2 * len_ratio

        # 4. 语义相似度（优先使用 embedding）
        semantic_sim = self._embedding_similarity(field, label)
        if semantic_sim > 0.5:
            return semantic_sim * 0.6

        # 5. 字符级相似度（备用）
        similarity = self._text_similarity(field, label)
        if similarity > 0.5:
            return similarity * 0.5

        # 6. input type 匹配
        input_type = getattr(element, "input_type", None)
        if input_type:
            type_score = self._match_input_type(field, input_type)
            if type_score > 0:
                return type_score * 0.4

        return 0.0

    def _keyword_match(
        self,
        text: str,
        label: str,
        keywords: set,
    ) -> float:
        """关键词匹配得分

        Args:
            text: 文本
            label: 标签
            keywords: 关键词集合

        Returns:
            float: 得分 0-1
        """
        text_set = set(text.split())
        label_set = set(label.split())
        keyword_set = keywords

        # 检查是否有关键词
        has_keyword = any(kw in text_set or kw in label_set for kw in keyword_set)
        if not has_keyword:
            return 0.0

        # 计算交集比例
        intersection = text_set & label_set
        if intersection:
            return len(intersection) / max(len(text_set), len(label_set))

        return 0.0

    def _text_similarity(self, text1: str, text2: str) -> float:
        """文本相似度（Jaccard）

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            float: 相似度 0-1
        """
        if not text1 or not text2:
            return 0.0

        set1 = set(text1)
        set2 = set(text2)

        intersection = set1 & set2
        union = set1 | set2

        if not union:
            return 0.0

        return len(intersection) / len(union)

    def _match_input_type(self, field: str, input_type: str) -> float:
        """匹配 input type

        Args:
            field: 字段描述
            input_type: input type 值

        Returns:
            float: 匹配得分
        """
        type_map = {
            "email": ["邮箱", "邮件"],
            "password": ["密码"],
            "number": ["数字", "金额", "数量"],
            "tel": ["电话", "手机"],
            "url": ["网址", "链接"],
            "search": ["搜索", "查询"],
            "date": ["日期"],
            "time": ["时间"],
            "file": ["文件"],
        }

        type_keywords = type_map.get(input_type.lower(), [])
        for keyword in type_keywords:
            if keyword in field:
                return 1.0

        return 0.0
