import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest


PATH = pathlib.Path(__file__).parents[1] / "scripts" / "ocr_adapter.py"
SPEC = importlib.util.spec_from_file_location("wechat_ocr_adapter", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class OcrAdapterTests(unittest.TestCase):
    def test_accepts_detail_caller_temp_prefix(self):
        path = (
            pathlib.Path(tempfile.gettempdir())
            / "wechat-ocr-detail-contract.png"
        )
        path.write_bytes(b"synthetic")
        try:
            result = MODULE.run_ocr(
                {"image_paths": [str(path)]},
                lambda **_: {"success": True, "full_text": "detail evidence"},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["text"], "detail evidence")
            self.assertEqual(result["image_count"], 1)
        finally:
            path.unlink(missing_ok=True)

    def test_reports_exact_count_for_one_two_and_three_images(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                paths = [
                    pathlib.Path(tempfile.gettempdir())
                    / f"wechat-ocr-count-{count}-{index}.png"
                    for index in range(count)
                ]
                for path in paths:
                    path.write_bytes(b"synthetic")
                try:
                    result = MODULE.run_ocr(
                        {"image_paths": [str(path) for path in paths]},
                        lambda **_: {"success": True, "full_text": "text"},
                    )
                    self.assertEqual(result["image_count"], count)
                finally:
                    for path in paths:
                        path.unlink(missing_ok=True)

    def test_combines_at_most_three_images(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = pathlib.Path(tempfile.gettempdir()) / f"wechat-ocr-{index}-test.png"
                path.write_bytes(b"synthetic")
                paths.append(str(path))

            def mock_ocr(**kwargs):
                return {"success": True, "full_text": pathlib.Path(kwargs["file_path"]).stem}

            try:
                result = MODULE.run_ocr({"image_paths": paths}, mock_ocr)
                self.assertTrue(result["ok"])
                self.assertEqual(
                    result["text"], "wechat-ocr-0-test\n\nwechat-ocr-1-test"
                )
                self.assertEqual(result["image_count"], 2)
            finally:
                for path in paths:
                    pathlib.Path(path).unlink(missing_ok=True)

    def test_provider_failure_is_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(tempfile.gettempdir()) / "wechat-ocr-provider-failure.png"
            path.write_bytes(b"synthetic")
            try:
                with self.assertRaises(RuntimeError):
                    MODULE.run_ocr(
                        {"image_paths": [str(path)]},
                        lambda **_: {"success": False},
                    )
            finally:
                path.unlink(missing_ok=True)

    def test_rejects_more_than_three_images(self):
        with self.assertRaises(ValueError):
            MODULE.run_ocr({"image_paths": ["x.png"] * 4}, lambda **_: {})

    def test_rejects_non_rpa_path_and_empty_or_oversized_text(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = pathlib.Path(directory) / "wechat-ocr-outside.png"
            outside.write_bytes(b"synthetic")
            with self.assertRaises(ValueError):
                MODULE.run_ocr(
                    {"image_paths": [str(outside)]},
                    lambda **_: {"success": True, "full_text": "secret"},
                )
        path = pathlib.Path(tempfile.gettempdir()) / "wechat-ocr-invalid-text.png"
        path.write_bytes(b"synthetic")
        try:
            for text in ("", "x" * 2_000_001):
                with self.assertRaises(ValueError):
                    MODULE.run_ocr(
                        {"image_paths": [str(path)]},
                        lambda value=text, **_: {"success": True, "full_text": value},
                    )
        finally:
            path.unlink(missing_ok=True)

    def test_main_failure_stderr_does_not_echo_path_or_text(self):
        secret = "C:/secret/王承展-18511597486.png"
        process = subprocess.run(
            [sys.executable, str(PATH)],
            input='{"image_paths":["' + secret + '"]}',
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
        self.assertRegex(process.stderr, r"^ocr_failed:\w+\n$")


if __name__ == "__main__":
    unittest.main()
