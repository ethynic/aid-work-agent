import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "llm_judge.py"
SPEC = importlib.util.spec_from_file_location("wechat_llm_judge", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class MockGateway:
    def __init__(self, content):
        self.content = content
        self.kwargs = None

    async def chat(self, **kwargs):
        self.kwargs = kwargs
        return {"content": self.content}


class FailingGateway:
    async def chat(self, **kwargs):
        raise RuntimeError("provider unavailable with secret evidence")


class SequentialGateway:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"content": outcome}


class JudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_gateway_at_zero_temperature_and_parses_fence(self):
        expected = {
            "matched": True,
            "person_name": "王承展",
            "mobile": "18511597486",
            "evidence_quote": "王承展 18511597486",
            "confidence": 0.99,
            "reason": "同一联系人片段",
        }
        gateway = MockGateway("```json\n" + json.dumps(expected, ensure_ascii=False) + "\n```")
        result = await MODULE.run_judge(
            {"association_name": "协会", "person_name": "王承展", "text": "王承展 18511597486"},
            gateway,
        )
        self.assertEqual(result, expected)
        self.assertEqual(gateway.kwargs["temperature"], 0)
        self.assertEqual(gateway.kwargs["max_tokens"], 2500)

    async def test_rejects_incomplete_schema(self):
        gateway = MockGateway('{"matched":false}')
        with self.assertRaises(ValueError):
            await MODULE.run_judge(
                {"association_name": "协会", "person_name": "姓名", "text": "证据"},
                gateway,
            )

    async def test_provider_exception_propagates_to_safe_main_boundary(self):
        with self.assertRaises(RuntimeError):
            await MODULE.run_judge(
                {"association_name": "协会", "person_name": "姓名", "text": "敏感证据"},
                FailingGateway(),
            )

    async def test_retries_once_after_invalid_json_without_echoing_raw_response(self):
        expected = {
            "matched": True,
            "person_name": "陈戟",
            "mobile": "13912345678",
            "evidence_quote": "陈戟 联系电话 13912345678",
            "confidence": 0.9,
            "reason": "重复交叉证据支持目标归属",
        }
        invalid_raw = "不是JSON，包含不应进入重试提示的原始内容"
        gateway = SequentialGateway([
            invalid_raw,
            json.dumps(expected, ensure_ascii=False),
        ])

        result = await MODULE.run_judge(
            {
                "association_name": "测试协会",
                "person_name": "陈戟",
                "text": "陈戟 联系电话 13912345678",
            },
            gateway,
        )

        self.assertEqual(result, expected)
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(
            [call["max_tokens"] for call in gateway.calls],
            [2500, 2500],
        )
        retry_messages = gateway.calls[1]["messages"]
        self.assertIn("上一次输出不符合协议", retry_messages[-1]["content"])
        self.assertNotIn(invalid_raw, json.dumps(retry_messages, ensure_ascii=False))

    async def test_retries_once_after_schema_error(self):
        valid = {
            "matched": False,
            "person_name": "",
            "mobile": "",
            "evidence_quote": "",
            "confidence": 0.2,
            "reason": "归属存在歧义",
        }
        gateway = SequentialGateway([
            '{"matched":false}',
            json.dumps(valid, ensure_ascii=False),
        ])

        result = await MODULE.run_judge(
            {"association_name": "协会", "person_name": "陈戟", "text": "证据"},
            gateway,
        )

        self.assertEqual(result, valid)
        self.assertEqual(len(gateway.calls), 2)

    async def test_provider_value_error_is_not_retried(self):
        gateway = SequentialGateway([ValueError("authentication rejected")])

        with self.assertRaisesRegex(ValueError, "authentication"):
            await MODULE.run_judge(
                {"association_name": "协会", "person_name": "陈戟", "text": "证据"},
                gateway,
            )

        self.assertEqual(len(gateway.calls), 1)

    async def test_second_invalid_response_fails_after_exactly_two_calls(self):
        gateway = SequentialGateway(["not json", '{"matched":"wrong type"}'])

        with self.assertRaises(ValueError):
            await MODULE.run_judge(
                {"association_name": "协会", "person_name": "陈戟", "text": "证据"},
                gateway,
            )

        self.assertEqual(len(gateway.calls), 2)

    async def test_final_invalid_response_carries_both_billable_calls(self):
        class UsageGateway:
            def __init__(self):
                self.calls = 0

            async def chat(self, **_kwargs):
                self.calls += 1
                return {
                    "content": "not-json",
                    "usage": {
                        "prompt_tokens": 10 * self.calls,
                        "cached_tokens": self.calls,
                        "completion_tokens": 2,
                        "total_tokens": 999,
                    },
                }

        gateway = UsageGateway()
        with self.assertRaises(MODULE.JudgeUsageError) as raised:
            await MODULE.run_judge(
                {"association_name": "协会", "person_name": "姓名", "text": "证据"},
                gateway,
            )
        self.assertEqual(
            raised.exception.token_usage,
            {
                "prompt_tokens": 30,
                "cached_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 34,
                "call_count": 2,
            },
        )

    async def test_prompt_supports_repeated_independent_target_binding(self):
        mobile = "13912345678"
        evidence = (
            f"结果一：刘甲、陈戟 联系电话 {mobile}\n"
            f"结果二：联系人陈乙、陈戟，电话 {mobile}\n"
            f"结果三：刘丙 陈戟 联系电话：{mobile}"
        )
        quote = f"刘丙 陈戟 联系电话：{mobile}"
        expected = {
            "matched": True,
            "person_name": "陈戟",
            "mobile": mobile,
            "evidence_quote": quote,
            "confidence": 0.91,
            "reason": "同一号码在多条独立结果中反复与目标姓名和联系电话关系紧邻",
        }
        gateway = MockGateway(json.dumps(expected, ensure_ascii=False))

        result = await MODULE.run_judge(
            {
                "association_name": "测试协会",
                "person_name": "陈戟",
                "text": evidence,
            },
            gateway,
        )

        self.assertEqual(result, expected)
        prompt = gateway.kwargs["messages"][1]["content"]
        self.assertIn("旧信息同样可以采用", prompt)
        self.assertIn("多条相互独立的搜索结果", prompt)
        self.assertIn("重复与目标联系人组关联", prompt)
        self.assertIn("逐字连续的一段", prompt)
        self.assertIn(quote, evidence)

    async def test_prompt_accepts_phone_shared_by_target_contact_group(self):
        mobile = "18612345678"
        quote = f"联系人：陈亚男、陈戟；联系电话：{mobile}"
        evidence = quote
        expected = {
            "matched": True,
            "person_name": "陈戟",
            "mobile": mobile,
            "evidence_quote": quote,
            "confidence": 0.9,
            "reason": "目标人明确位于共享联系人组内",
        }
        gateway = MockGateway(json.dumps(expected, ensure_ascii=False))

        result = await MODULE.run_judge(
            {
                "association_name": "测试协会",
                "person_name": "陈戟",
                "text": evidence,
            },
            gateway,
        )

        self.assertEqual(result, expected)
        prompt = gateway.kwargs["messages"][1]["content"]
        self.assertIn("联系人/联络人", prompt)
        self.assertIn("组内每个人", prompt)
        self.assertIn("多人共用", prompt)

    async def test_prompt_rejects_phone_exclusively_bound_to_another_person(self):
        mobile = "18612345678"
        evidence = f"张三手机：{mobile}；陈戟另无手机"
        expected = {
            "matched": False,
            "person_name": "",
            "mobile": "",
            "evidence_quote": "",
            "confidence": 0.95,
            "reason": "号码明确排他绑定给张三，目标人另无手机",
        }
        gateway = MockGateway(json.dumps(expected, ensure_ascii=False))

        result = await MODULE.run_judge(
            {
                "association_name": "测试协会",
                "person_name": "陈戟",
                "text": evidence,
            },
            gateway,
        )

        self.assertEqual(result, expected)
        self.assertIn("排他绑定给另一个人", gateway.kwargs["messages"][1]["content"])

    async def test_prompt_prefers_repeated_candidate_over_single_candidate(self):
        repeated_mobile = "13912345678"
        single_mobile = "18612345678"
        quote = f"结果四：联系人刘宝龙、陈戟；联系电话：{repeated_mobile}"
        evidence = (
            f"结果一：联系人陈戟；联系电话：{single_mobile}\n"
            f"结果二：联系人刘甲、陈戟；联系电话：{repeated_mobile}\n"
            f"结果三：联络人刘乙、陈戟；手机：{repeated_mobile}\n"
            f"{quote}"
        )
        expected = {
            "matched": True,
            "person_name": "陈戟",
            "mobile": repeated_mobile,
            "evidence_quote": quote,
            "confidence": 0.94,
            "reason": "该号码在更多独立结果中重复与目标联系人组关联",
        }
        gateway = MockGateway(json.dumps(expected, ensure_ascii=False))

        result = await MODULE.run_judge(
            {
                "association_name": "测试协会",
                "person_name": "陈戟",
                "text": evidence,
            },
            gateway,
        )

        self.assertEqual(result["mobile"], repeated_mobile)
        prompt = gateway.kwargs["messages"][1]["content"]
        self.assertIn("优先选择", prompt)
        self.assertIn("而不是只出现一次的候选", prompt)

    def test_rejects_invalid_json_fence_and_schema_types(self):
        invalid_values = [
            "not json",
            "```json\n[]\n```",
            json.dumps(
                {
                    "matched": "true",
                    "person_name": "姓名",
                    "mobile": "",
                    "evidence_quote": "",
                    "confidence": 1,
                    "reason": "",
                }
            ),
            json.dumps(
                {
                    "matched": False,
                    "person_name": "",
                    "mobile": "",
                    "evidence_quote": "",
                    "confidence": True,
                    "reason": "",
                    "extra": "rejected",
                }
            ),
            json.dumps(
                {
                    "matched": False,
                    "person_name": "",
                    "mobile": "",
                    "evidence_quote": "",
                    "confidence": 2,
                    "reason": "",
                }
            ),
        ]
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises((ValueError, json.JSONDecodeError)):
                MODULE._parse_json_content(value)

    async def test_returns_exact_provider_usage_for_parent_run(self):
        expected = {
            "matched": False,
            "person_name": "",
            "mobile": "",
            "evidence_quote": "",
            "confidence": 0.8,
            "reason": "not found",
        }

        class UsageGateway:
            async def chat(self, **_kwargs):
                return {
                    "content": json.dumps(expected),
                    "usage": {
                        "prompt_tokens": 90,
                        "cached_tokens": 30,
                        "completion_tokens": 10,
                        "total_tokens": 130,
                    },
                }

        result = await MODULE.run_judge(
            {"association_name": "协会", "person_name": "张三", "text": "证据"},
            UsageGateway(),
        )
        self.assertEqual(
            result["token_usage"],
            {
                "prompt_tokens": 90,
                "cached_tokens": 30,
                "completion_tokens": 10,
                "total_tokens": 100,
                "call_count": 1,
            },
        )

    def test_invalid_provider_usage_is_not_partially_estimated(self):
        self.assertEqual(
            MODULE._usage({
                "usage": {
                    "prompt_tokens": 90,
                    "cached_tokens": 30,
                    "completion_tokens": -1,
                    "total_tokens": 100,
                }
            }),
            {
                "prompt_tokens": 0,
                "cached_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "call_count": 0,
            },
        )
    def test_main_failure_stderr_does_not_echo_input(self):
        secret = "王承展 18511597486 原始证据"
        process = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            # 缺少 person_name/text，在调用真实 Gateway 前即安全失败。
            input=json.dumps({"association_name": secret}, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")
        self.assertNotIn(secret, process.stderr)
        self.assertNotIn("18511597486", process.stderr)
        self.assertRegex(process.stderr, r"^judge_failed:\w+\n$")


if __name__ == "__main__":
    unittest.main()
