"""语义标签生成器

为 DOM 元素生成语义化的标签描述，便于 LLM 理解元素含义。
"""

import re
from typing import Optional, Dict, Any, List


class SemanticTagger:
    """生成元素的语义标签"""

    # 按钮/链接常用文本模式
    BUTTON_KEYWORDS = {
        "确认", "确定", "提交", "保存", "取消", "关闭", "删除", "编辑",
        "修改", "新建", "创建", "添加", "查询", "搜索", "登录", "注册",
        "下一步", "上一步", "返回", "退出", "下载", "上传", "刷新",
        "重置", "跳过", "继续", "完成", "发送", "接收", "展开", "收起",
        "展开更多", "查看更多", "加载更多", "了解更多", "更多",
        "确定", "应用", "同意", "拒绝", "接受", "忽略",
        "复制", "粘贴", "剪切", "撤销", "重做",
        "点赞", "收藏", "分享", "评论", "转发", "关注",
        "换一换", "清除", "筛选", "排序", "导出", "导入", "打印",
        "submit", "confirm", "cancel", "save", "delete", "edit", "create",
        "add", "search", "login", "register", "next", "prev", "back",
        "reset", "skip", "continue", "finish", "send", "receive",
        "ok", "yes", "no", "close", "open", "get", "post", "put",
        "expand", "collapse", "more", "load", "filter", "sort",
        "copy", "paste", "cut", "undo", "redo", "apply",
        "like", "share", "follow", "subscribe", "download", "upload",
    }

    # 输入框标签模式（全面覆盖）
    INPUT_LABELS = {
        "用户名": ["用户名", "账号", "user", "username", "loginname", "account", "userid"],
        "密码": ["密码", "口令", "password", "passwd", "pwd"],
        "邮箱": ["邮箱", "邮件", "email", "mail", "e-mail"],
        "手机": ["手机", "电话", "phone", "tel", "mobile", "cellphone", "cell"],
        "金额": ["金额", "钱", "数额", "amount", "money", "sum", "price", "fee", "cost"],
        "日期": ["日期", "时间", "date", "time", "datetime", "birthday", "startdate", "enddate", "begindate", "enddate"],
        "描述": ["描述", "备注", "说明", "原因", "description", "remark", "note", "memo", "comment"],
        "搜索": ["搜索", "关键字", "关键词", "keyword", "keywords", "search", "query", "wd", "kw", "q", "s"],
        "地址": ["地址", "address", "addr", "location", "street"],
        "邮编": ["邮编", "邮政编码", "zipcode", "postcode", "zip", "postal"],
        "身份证": ["身份证", "证件号", "idcard", "id_card", "idnumber", "identity"],
        "验证码": ["验证码", "校验码", "captcha", "verifycode", "vcode", "checkcode"],
        "文件上传": ["文件", "file", "upload", "attachment", "附件"],
        "公司": ["公司", "企业", "company", "corp", "organization", "org", "enterprise"],
        "姓名": ["姓名", "名字", "真实姓名", "fullname", "name", "realname", "truename"],
        "职位": ["职位", "岗位", "title", "position", "job", "role"],
        "部门": ["部门", "department", "dept", "division"],
        "标题": ["标题", "题目", "subject", "title", "heading"],
        "内容": ["内容", "正文", "content", "body"],
        "网址": ["网址", "链接", "url", "website", "homepage", "link"],
        "数量": ["数量", "个数", "count", "quantity", "qty", "num", "number"],
        "百分比": ["百分比", "比例", "percent", "percentage", "rate", "ratio"],
        "颜色": ["颜色", "colour", "color"],
        "国家": ["国家", "地区", "country", "region", "nation"],
        "城市": ["城市", "省份", "city", "province", "state"],
        "传真": ["传真", "fax"],
        "年龄": ["年龄", "age"],
        "性别": ["性别", "gender", "sex"],
        "学号": ["学号", "工号", "员工号", "studentid", "staffid", "empid", "emp_no"],
    }

    def __init__(self):
        self._aria_cache: Dict[str, str] = {}

    def generate_label(
        self,
        tag: str,
        attributes: Dict[str, Any],
        text_content: str = "",
    ) -> str:
        """生成元素语义标签

        优先级：aria-label > aria-labelledby > text content > placeholder > title > value > 自动生成

        Args:
            tag: 元素标签名
            attributes: 元素属性字典
            text_content: 元素的文本内容

        Returns:
            str: 语义标签
        """
        # 1. 尝试 aria-label
        aria_label = attributes.get("aria-label")
        if aria_label:
            label = aria_label.strip()
            if label:
                return label

        # 2. 尝试 aria-labelledby（需要页面上下文，这里简化处理）
        aria_labelledby = attributes.get("aria-labelledby")
        if aria_labelledby:
            label = self._resolve_aria_labelledby(aria_labelledby, attributes)
            if label:
                return label

        # 3. 尝试有意义的文本内容（放宽长度限制到 100）
        text = self._extract_meaningful_text(text_content)
        if text and len(text) <= 100:
            return text
        # 超长文本截取
        if text and len(text) > 100:
            return text[:100] + "..."

        # 4. 尝试 placeholder（input 的关键信息来源）
        placeholder = attributes.get("placeholder")
        if placeholder:
            return placeholder.strip()

        # 5. 尝试 title 属性
        title = attributes.get("title")
        if title:
            return title.strip()

        # 6. 尝试 value 属性（按钮类）
        value = attributes.get("value")
        if value and tag.lower() in ("button", "submit", "reset"):
            return value.strip()

        # 7. 尝试 alt 属性（图片、area 链接等）
        alt = attributes.get("alt")
        if alt and tag.lower() in ("img", "area", "input"):
            return alt.strip()

        # 8. 根据标签和属性自动生成
        return self._auto_generate_label(tag, attributes)

    def _resolve_aria_labelledby(
        self,
        aria_labelledby: str,
        attributes: Dict[str, Any],
    ) -> Optional[str]:
        """解析 aria-labelledby 引用

        注意：由于没有页面上下文，这里返回简化结果
        """
        element_id = attributes.get("id", "")
        if aria_labelledby == element_id:
            return attributes.get("aria-label") or ""

        self._aria_cache[aria_labelledby] = ""
        return None

    def _extract_meaningful_text(self, text_content: str) -> str:
        """提取有意义的文本内容

        Args:
            text_content: 原始文本内容

        Returns:
            str: 清理后的文本
        """
        if not text_content:
            return ""

        # 移除多余空白
        text = re.sub(r"\s+", " ", text_content)
        text = text.strip()

        # 移除纯空白/控制字符，保留中文、英文、数字、常见标点
        # 注意：不删除 emoji 和特殊符号，它们可能是按钮文本的一部分
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        return text.strip()

    def _auto_generate_label(self, tag: str, attributes: Dict[str, Any]) -> str:
        """根据标签和属性自动生成标签

        Args:
            tag: 标签名
            attributes: 属性字典

        Returns:
            str: 自动生成的标签
        """
        tag_lower = tag.lower()

        # input 特殊处理
        if tag_lower == "input":
            input_type = attributes.get("type", "text").lower()
            name = attributes.get("name", "")
            id_attr = attributes.get("id", "")
            class_attr = attributes.get("class", "").lower()

            # submit/reset/button/image 按钮类 input，优先用 value 属性作为标签
            if input_type in ("submit", "reset", "button", "image"):
                value = attributes.get("value", "")
                if value and value.strip():
                    return value.strip()
                if input_type == "submit":
                    return "提交"
                elif input_type == "reset":
                    return "重置"
                return "按钮"

            # 检查常用字段名（name、id、class）
            for label, keywords in self.INPUT_LABELS.items():
                for kw in keywords:
                    if kw.lower() in name.lower() or kw.lower() in id_attr.lower() or kw.lower() in class_attr:
                        return label

            # 根据 type 生成
            type_labels = {
                "email": "邮箱",
                "password": "密码",
                "number": "数字",
                "tel": "电话",
                "url": "网址",
                "search": "搜索",
                "date": "日期",
                "time": "时间",
                "datetime-local": "日期时间",
                "month": "月份",
                "week": "周",
                "file": "文件上传",
                "range": "滑动选择",
                "color": "颜色选择",
                "checkbox": "复选框",
                "radio": "单选框",
            }
            if input_type in type_labels:
                return type_labels[input_type]

            return "输入框"

        # button/submit/reset
        if tag_lower in ("button", "submit", "reset"):
            value = attributes.get("value", "")
            if value:
                return value.strip()

            text = attributes.get("text", "")
            if text:
                return text.strip()

            if tag_lower == "submit":
                return "提交"
            elif tag_lower == "reset":
                return "重置"
            return "按钮"

        # select
        if tag_lower == "select":
            name = attributes.get("name", "")
            id_attr = attributes.get("id", "")
            class_attr = attributes.get("class", "").lower()
            for label, keywords in self.INPUT_LABELS.items():
                for kw in keywords:
                    if kw.lower() in name.lower() or kw.lower() in id_attr.lower() or kw.lower() in class_attr:
                        return label
            return "下拉选择"

        # textarea
        if tag_lower == "textarea":
            name = attributes.get("name", "")
            id_attr = attributes.get("id", "")
            class_attr = attributes.get("class", "").lower()
            placeholder = attributes.get("placeholder", "")
            # 先用关键词匹配
            for label, keywords in self.INPUT_LABELS.items():
                for kw in keywords:
                    if kw.lower() in name.lower() or kw.lower() in id_attr.lower() or kw.lower() in class_attr:
                        return label
            # 再用 placeholder
            if placeholder:
                return placeholder.strip()
            return "文本框"

        # a (链接)
        if tag_lower == "a":
            # 如果前面步骤已经有文本但被截断，attributes.text 可能存在
            text = attributes.get("text", "")
            if text and len(text.strip()) <= 100:
                return text.strip()

            href = attributes.get("href", "")
            # 常见链接模式（扩展）
            href_patterns = [
                ("logout", "退出"), ("signout", "退出"), ("退出", "退出"),
                ("login", "登录"), ("signin", "登录"), ("登录", "登录"),
                ("register", "注册"), ("signup", "注册"), ("注册", "注册"),
                ("home", "首页"), ("index", "首页"),
                ("about", "关于"), ("contact", "联系我们"),
                ("help", "帮助"), ("faq", "常见问题"),
                ("cart", "购物车"), ("checkout", "结算"),
                ("profile", "个人中心"), ("settings", "设置"),
                ("admin", "管理后台"), ("dashboard", "仪表盘"),
                ("download", "下载"), ("export", "导出"),
            ]
            href_lower = href.lower()
            for pattern, label in href_patterns:
                if pattern in href_lower:
                    return label

            return "链接"

        # details/summary
        if tag_lower == "summary":
            text = attributes.get("text", "")
            return text.strip() if text else "详情"

        # option
        if tag_lower == "option":
            text = attributes.get("text", "")
            value = attributes.get("value", "")
            return text.strip() if text else (value if value else "选项")

        # area (图片映射链接)
        if tag_lower == "area":
            alt = attributes.get("alt", "")
            return alt.strip() if alt else "图片链接"

        # dialog
        if tag_lower == "dialog":
            return "对话框"

        return f"<{tag}>"

    def generate_role(self, tag: str, attributes: Dict[str, Any]) -> Optional[str]:
        """生成 ARIA role

        Args:
            tag: 标签名
            attributes: 属性字典

        Returns:
            str: ARIA role 或 None
        """
        # 优先使用已有的 role
        role = attributes.get("role")
        if role:
            return role

        tag_lower = tag.lower()

        # 根据标签推断 role（更完整）
        role_map = {
            "a": "link",
            "button": "button",
            "input": "textbox",
            "select": "listbox",
            "textarea": "textbox",
            "nav": "navigation",
            "article": "article",
            "main": "main",
            "aside": "complementary",
            "header": "banner",
            "footer": "contentinfo",
            "form": "form",
            "section": "region",
            "dialog": "dialog",
            "option": "option",
            "summary": "button",
            "area": "link",
        }

        # input 特殊处理：根据 type 推断更精确的 role
        if tag_lower == "input":
            input_type = attributes.get("type", "text").lower()
            if input_type == "checkbox":
                return "checkbox"
            elif input_type == "radio":
                return "radio"
            elif input_type == "search":
                return "searchbox"
            elif input_type == "range":
                return "slider"
            elif input_type == "file":
                return None  # file input 没有精确的 ARIA role

        inferred_role = role_map.get(tag_lower)

        # 检查 aria-haspopup
        if tag_lower in ("button", "a") and attributes.get("aria-haspopup"):
            return "menuitem"

        return inferred_role

    def should_include(self, tag: str, attributes: Dict[str, Any]) -> bool:
        """判断元素是否应该包含在快照中

        Args:
            tag: 标签名
            attributes: 属性字典

        Returns:
            bool: 是否包含
        """
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "noscript", "link", "meta", "title", "head", "html"):
            return False

        if attributes.get("hidden") is not None:
            return False

        if attributes.get("type", "").lower() == "hidden":
            return False

        if attributes.get("aria-hidden") == "true":
            return False

        return True
