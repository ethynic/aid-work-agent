"""Offline contract/fixture checks; not a runtime authorization validator."""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {
    p.name: json.loads(p.read_text(encoding="utf-8"))
    for p in sorted((ROOT / "schemas").glob("*.schema.json"))
}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
CASES = [
    case
    for p in sorted((ROOT / "fixtures").glob("*.json"))
    for case in json.loads(p.read_text(encoding="utf-8"))
]


class RuntimeContractTests(unittest.TestCase):
    def test_schemas_compile_without_remote_resolution(self):
        for name, schema in SCHEMAS.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)

    def test_fixtures_encode_allowed_and_forbidden_boundaries(self):
        names = set()
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.assertNotIn(case["name"], names)
                names.add(case["name"])
                validator = Draft202012Validator(
                    SCHEMAS[case["schema"]], registry=REGISTRY
                )
                self.assertEqual(validator.is_valid(case["value"]), case["valid"])

    def test_each_management_method_has_a_consumable_request(self):
        # No declared UI operation should require clients to guess its shape.
        methods = SCHEMAS["management-request.schema.json"]["properties"]["method"]["enum"]
        fixtures = {
            c["value"]["method"] for c in CASES
            if c["valid"] and c["schema"] == "management-request.schema.json"
        }
        self.assertEqual(set(methods), fixtures)

    def test_reply_codes_are_unambiguous_and_old_envelopes_are_rejected(self):
        validator = Draft202012Validator(SCHEMAS["management-response.schema.json"], registry=REGISTRY)
        value = {"request_id": "test", "method": "plugins.list", "code": 0, "error": "", "result": {"instance_id": "host", "revision": 0, "plugins": []}}
        self.assertTrue(validator.is_valid(value))
        for changes in ({"code": 1}, {"error": "failed"}, {"code": "0"}, {"code": False}, {"result": None}):
            with self.subTest(changes=changes):
                self.assertFalse(validator.is_valid({**value, **changes}))
        self.assertTrue(validator.is_valid({**value, "code": 999, "error": "unknown error", "result": None}))
        self.assertFalse(validator.is_valid({"request_id": "test", "method": "plugins.list", "result": []}))

    def test_output_error_does_not_erase_business_effect(self):
        validator = Draft202012Validator(SCHEMAS["tool-output.schema.json"], registry=REGISTRY)
        value = {"code": 100, "error": "result requires reconciliation", "effect": "unknown", "complete": True}
        self.assertTrue(validator.is_valid(value))
        self.assertTrue(validator.is_valid({**value, "effect": "applied", "complete": False}))
        self.assertTrue(validator.is_valid({**value, "code": 0, "error": "", "effect": "none"}))
        for changes in ({"code": 0}, {"error": ""}, {"success": False}, {"code": "EXECUTION_UNKNOWN"}):
            with self.subTest(changes=changes):
                self.assertFalse(validator.is_valid({**value, **changes}))

    def test_skill_requires_handbook_and_cannot_supply_local_execution_paths(self):
        value = next(c["value"] for c in CASES if c["name"] == "skill_handbook_and_entry")
        validator = Draft202012Validator(SCHEMAS["plugin-contract.schema.json"], registry=REGISTRY)
        for path in ("../private.md", "C:/private.md", "/private.md", "references/../../private.md", "references\\private.md", "references/./private.md"):
            with self.subTest(path=path):
                bad = json.loads(json.dumps(value))
                bad["documents"][0]["path"] = path
                self.assertFalse(validator.is_valid(bad))
                bad = json.loads(json.dumps(value))
                bad["code_map"][0]["path"] = path
                self.assertFalse(validator.is_valid(bad))
        bad = json.loads(json.dumps(value))
        bad["entries"][0]["executable"] = "python.exe"
        self.assertFalse(validator.is_valid(bad))

    def test_embedded_example_schemas_can_validate_actual_arguments(self):
        # Metadata examples must be executable constraints, not decorative JSON.
        skill = next(c["value"] for c in CASES if c["name"] == "skill_handbook_and_entry")
        args = Draft202012Validator(skill["entries"][0]["args_schema"])
        self.assertTrue(args.is_valid([]))
        self.assertTrue(args.is_valid(["status"]))
        self.assertFalse(args.is_valid([123]))
        self.assertFalse(args.is_valid(["a"] * 17))
        mcp = next(c["value"] for c in CASES if c["name"] == "mcp_tool_contract")
        inputs = Draft202012Validator(mcp["tools"][0]["input_schema"])
        self.assertTrue(inputs.is_valid({}))
        self.assertFalse(inputs.is_valid({"shell": "run arbitrary command"}))


if __name__ == "__main__":
    print(f"Checking {len(SCHEMAS)} schemas and {len(CASES)} fixture cases.")
    unittest.main(verbosity=2)
