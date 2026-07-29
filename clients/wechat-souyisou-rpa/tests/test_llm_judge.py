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
