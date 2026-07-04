"""
规划调度引擎

根据意图生成执行计划，分解复杂任务
"""

import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from src.models.plan import ExecutionPlan, Task, TaskStatus
from src.core.intent_engine import IntentResult, intent_engine


class Planner:
    """
    规划调度引擎
    
    负责：
    - 根据意图生成执行计划
    - 分解复杂任务为子任务
    - 管理任务执行顺序
    """
    
    # 意图到工具的映射
    INTENT_TOOL_MAPPING = {
        "email_send": "email_send",
        "email_read": "email_read",
        "email_search": "email_search",
        "ocr_image": "ocr_image",
        "ocr_pdf": "ocr_pdf",
        "web_search": "web_search",
        "kb_search": "kb_search",
    }
    
    def __init__(self):
        """初始化规划器"""
        pass
    
    async def create_plan(
        self,
        intent_result: IntentResult,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExecutionPlan:
        """
        创建执行计划
        
        Args:
            intent_result: 意图识别结果
            context: 上下文信息
        
        Returns:
            执行计划
        """
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        intent = intent_result.intent
        entities = intent_result.entities
        
        # 获取意图信息
        intent_info = intent_engine.get_intent_info(intent)
        required_entities = intent_info.get("required_entities", [])
        
        # 检查必需实体
        missing_entities = [
            entity for entity in required_entities
            if entity not in entities or not entities[entity]
        ]
        
        if missing_entities:
            # 创建需要澄清的计划
            return self._create_clarification_plan(
                plan_id=plan_id,
                intent=intent,
                missing_entities=missing_entities,
            )
        
        # 根据意图类型创建计划
        if intent in self.INTENT_TOOL_MAPPING:
            return self._create_single_task_plan(
                plan_id=plan_id,
                intent=intent,
                entities=entities,
            )
        elif intent == "greeting":
            return self._create_greeting_plan(plan_id=plan_id)
        elif intent == "help":
            return self._create_help_plan(plan_id=plan_id)
        else:
            return self._create_unknown_plan(plan_id=plan_id)
    
    def _create_single_task_plan(
        self,
        plan_id: str,
        intent: str,
        entities: Dict[str, Any],
    ) -> ExecutionPlan:
        """
        创建单任务计划
        
        Args:
            plan_id: 计划ID
            intent: 意图
            entities: 实体
        
        Returns:
            执行计划
        """
        tool_name = self.INTENT_TOOL_MAPPING[intent]
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        
        # 构建任务参数
        parameters = self._build_parameters(intent, entities)
        
        task = Task(
            task_id=task_id,
            tool_name=tool_name,
            parameters=parameters,
            dependencies=[],
            status=TaskStatus.PENDING,
        )
        
        plan = ExecutionPlan(
            plan_id=plan_id,
            intent=intent,
            tasks=[task],
            execution_mode="sequential",
        )
        
        logger.info(f"创建单任务计划: {plan_id}, 工具: {tool_name}")
        return plan
    
    def _create_clarification_plan(
        self,
        plan_id: str,
        intent: str,
        missing_entities: List[str],
    ) -> ExecutionPlan:
        """
        创建澄清计划
        
        Args:
            plan_id: 计划ID
            intent: 意图
            missing_entities: 缺失的实体
        
        Returns:
            执行计划
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        
        # 生成澄清问题
        questions = self._generate_clarification_questions(missing_entities)
        
        task = Task(
            task_id=task_id,
            tool_name="clarify",
            parameters={
                "intent": intent,
                "missing_entities": missing_entities,
                "questions": questions,
            },
            dependencies=[],
            status=TaskStatus.PENDING,
        )
        
        plan = ExecutionPlan(
            plan_id=plan_id,
            intent=intent,
            tasks=[task],
            execution_mode="sequential",
        )
        
        logger.info(f"创建澄清计划: {plan_id}, 缺失实体: {missing_entities}")
        return plan
    
    def _create_greeting_plan(self, plan_id: str) -> ExecutionPlan:
        """
        创建问候计划
        
        Args:
            plan_id: 计划ID
        
        Returns:
            执行计划
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        
        task = Task(
            task_id=task_id,
            tool_name="respond",
            parameters={
                "response_type": "greeting",
                "message": "您好！我是AID助手，很高兴为您服务。请问有什么可以帮助您的？",
            },
            dependencies=[],
            status=TaskStatus.PENDING,
        )
        
        return ExecutionPlan(
            plan_id=plan_id,
            intent="greeting",
            tasks=[task],
            execution_mode="sequential",
        )
    
    def _create_help_plan(self, plan_id: str) -> ExecutionPlan:
        """
        创建帮助计划
        
        Args:
            plan_id: 计划ID
        
        Returns:
            执行计划
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        
        help_message = """我可以帮助您完成以下任务：

[邮件] 邮件处理
- 发送邮件：帮我给张三发邮件，主题是项目进度
- 读取邮件：查看我的收件箱
- 搜索邮件：搜索关于合同的邮件

[文档] 文档处理
- 文档摘要：帮我总结这份文档
- 文档翻译：把这段话翻译成英语

[搜索] 信息检索
- 网络搜索：搜索关于AI的最新资讯
- 知识库检索：在知识库中搜索报销流程

[OCR] OCR识别
- 图片识别：识别这张图片中的文字
- PDF识别：识别这个PDF中的文字

请告诉我您需要什么帮助？"""
        
        task = Task(
            task_id=task_id,
            tool_name="respond",
            parameters={
                "response_type": "help",
                "message": help_message,
            },
            dependencies=[],
            status=TaskStatus.PENDING,
        )
        
        return ExecutionPlan(
            plan_id=plan_id,
            intent="help",
            tasks=[task],
            execution_mode="sequential",
        )
    
    def _create_unknown_plan(self, plan_id: str) -> ExecutionPlan:
        """
        创建未知意图计划
        
        Args:
            plan_id: 计划ID
        
        Returns:
            执行计划
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        
        task = Task(
            task_id=task_id,
            tool_name="respond",
            parameters={
                "response_type": "unknown",
                "message": "抱歉，我不太理解您的意思。您可以尝试描述您需要完成的任务，或者回复'帮助'查看我能做什么。",
            },
            dependencies=[],
            status=TaskStatus.PENDING,
        )
        
        return ExecutionPlan(
            plan_id=plan_id,
            intent="unknown",
            tasks=[task],
            execution_mode="sequential",
        )
    
    def _build_parameters(
        self,
        intent: str,
        entities: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        构建任务参数
        
        Args:
            intent: 意图
            entities: 实体
        
        Returns:
            参数字典
        """
        parameters = {}
        
        if intent == "email_send":
            parameters = {
                "to": entities.get("收件人", ""),
                "subject": entities.get("主题", ""),
                "body": entities.get("正文", ""),
            }
        elif intent == "email_read":
            parameters = {
                "filter": entities.get("筛选条件", ""),
                "limit": entities.get("数量", 10),
            }
        elif intent == "email_search":
            parameters = {
                "keyword": entities.get("关键词", ""),
                "sender": entities.get("发件人", ""),
            }
        elif intent in ["ocr_image", "ocr_pdf"]:
            parameters = {
                "file": entities.get("图片", entities.get("PDF文件", "")),
            }
        elif intent in ["web_search", "kb_search"]:
            parameters = {
                "keyword": entities.get("关键词", ""),
                "limit": entities.get("数量", 5),
            }
        
        return parameters
    
    def _generate_clarification_questions(
        self,
        missing_entities: List[str],
    ) -> str:
        """
        生成澄清问题
        
        Args:
            missing_entities: 缺失的实体列表
        
        Returns:
            澄清问题
        """
        entity_questions = {
            "收件人": "请问您要发送给谁？",
            "主题": "请问邮件的主题是什么？",
            "正文": "请问邮件的内容是什么？",
            "文档": "请问您要处理哪个文档？",
            "目标语言": "请问您要翻译成什么语言？",
            "关键词": "请问您要搜索什么内容？",
            "图片": "请问您要识别哪张图片？",
            "PDF文件": "请问您要识别哪个PDF文件？",
        }
        
        questions = []
        for entity in missing_entities:
            if entity in entity_questions:
                questions.append(entity_questions[entity])
        
        if questions:
            return " ".join(questions)
        
        return "请提供更多信息以便我帮您完成任务。"
    
    def get_next_task(self, plan: ExecutionPlan) -> Optional[Task]:
        """
        获取下一个可执行的任务
        
        Args:
            plan: 执行计划
        
        Returns:
            任务或None
        """
        return plan.get_next_task()
    
    def update_task_status(
        self,
        plan: ExecutionPlan,
        task_id: str,
        status: TaskStatus,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        更新任务状态
        
        Args:
            plan: 执行计划
            task_id: 任务ID
            status: 新状态
            result: 执行结果
            error: 错误信息
        """
        task = plan.get_task(task_id)
        if task:
            task.status = status
            if result:
                task.result = result
            if error:
                task.error = error


# 全局规划器实例
planner = Planner()
