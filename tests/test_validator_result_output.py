"""Offline checks for compact validator output without losing paid-request evidence."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


director = load_module("summary_director", "skills/seedance-ugc-cn-director/scripts/validate_batch_compile.py")
ledger = load_module("summary_ledger", "skills/popboom/scripts/validate_ledger.py")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def invoke(module, args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = module.main([str(value) for value in args])
    return code, json.loads(output.getvalue()), output.getvalue()


class ValidatorResultOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry, self.runtime, self.policy = ledger.load_assets()
        self.batch = write_json(self.root / "batch.json", director._fixture_v14(market="US"))
        self.ledger = write_json(self.root / "ledger.json",
                                 ledger.make_test_ledger(self.runtime, self.registry, self.policy))

    def inputs(self):
        return ((director, self.batch), (ledger, self.ledger))

    def assert_persisted(self, path, summary, expected):
        raw = path.read_bytes()
        self.assertEqual(json.loads(raw), expected)
        self.assertEqual(summary["result_file"], str(path.resolve()))
        self.assertEqual(summary["result_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(summary["valid"], expected["valid"])
        self.assertTrue(summary["summary_only"])

    def test_default_output_and_result_file_are_identical(self):
        for module, source in self.inputs():
            with self.subTest(module=module.__name__):
                code, expected, stdout = invoke(module, [source])
                self.assertEqual(code, 0, expected)
                result_file = self.root / (module.__name__ + "-full.json")
                saved_code, actual, saved_stdout = invoke(module, [source, "--result-file", result_file])
                self.assertEqual(saved_code, code)
                self.assertEqual(actual, expected)
                self.assertEqual(saved_stdout, stdout)
                self.assertEqual(result_file.read_bytes(), stdout.encode("utf-8"))

    def test_summary_keeps_complete_evidence_and_verifiable_hash(self):
        for module, source in self.inputs():
            with self.subTest(module=module.__name__):
                code, expected, _ = invoke(module, [source])
                result_file = self.root / (module.__name__ + "-summary.json")
                actual_code, summary, stdout = invoke(module, [source, "--summary", "--result-file", result_file])
                self.assertEqual(actual_code, code)
                self.assert_persisted(result_file, summary, expected)
                self.assertIn("counts", summary)
                self.assertEqual(summary["errors"], [])
                self.assertNotIn("wave_plan", summary)
                self.assertNotIn("director_receipts", summary)
                self.assertNotIn("compiled_prompt", stdout)

    def test_summary_requires_evidence_destination(self):
        for module, source in self.inputs():
            with self.subTest(module=module.__name__):
                code, result, _ = invoke(module, [source, "--summary"])
                self.assertEqual(code, 1)
                self.assertFalse(result["valid"])
                self.assertIn("--result-file", result["errors"][0]["message"])

    def test_invalid_json_and_schema_keep_original_errors_and_exit_code(self):
        for module, _ in self.inputs():
            for content in ("{bad json", "{}"):
                with self.subTest(module=module.__name__, content=content):
                    source = self.root / "invalid.json"
                    source.write_text(content, encoding="utf-8")
                    code, expected, _ = invoke(module, [source])
                    result_file = self.root / "invalid-result.json"
                    actual_code, summary, _ = invoke(module, [source, "--summary", "--result-file", result_file])
                    self.assertEqual(code, 1)
                    self.assertEqual(actual_code, code)
                    self.assert_persisted(result_file, summary, expected)
                    self.assertEqual(summary["error_count"], len(expected["errors"]))

    def test_result_cannot_replace_input_or_hardlink(self):
        for module, source in self.inputs():
            original = source.read_bytes()
            alias = self.root / (module.__name__ + "-alias.json")
            os.link(source, alias)
            for target in (source, alias):
                with self.subTest(module=module.__name__, target=target.name):
                    code, result, _ = invoke(module, [source, "--summary", "--result-file", target])
                    self.assertEqual(code, 1)
                    self.assertFalse(result["valid"])
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual(alias.read_bytes(), original)

    def test_result_cannot_replace_compiled_output(self):
        compiled = self.root / "compiled.json"
        compiled.write_bytes(b"untouched")
        code, result, _ = invoke(director, [self.batch, "--compile", "--output", compiled,
                                             "--summary", "--result-file", compiled])
        self.assertEqual(code, 1)
        self.assertFalse(result["valid"])
        self.assertEqual(compiled.read_bytes(), b"untouched")

    def test_in_place_compilation_remains_supported_with_separate_result(self):
        result_file = self.root / "compiled-result.json"
        code, summary, _ = invoke(director, [self.batch, "--compile", "--output", self.batch,
                                             "--summary", "--result-file", result_file])
        self.assertEqual(code, 0, summary)
        full = json.loads(result_file.read_bytes())
        self.assertTrue(full["compiled"])
        self.assertEqual(full["batch_compile_sha256"], hashlib.sha256(self.batch.read_bytes()).hexdigest())
        self.assertEqual(invoke(director, [self.batch])[0], 0)

    def test_missing_output_directory_fails_closed(self):
        for module, source in self.inputs():
            with self.subTest(module=module.__name__):
                destination = self.root / "missing" / "result.json"
                code, result, _ = invoke(module, [source, "--summary", "--result-file", destination])
                self.assertEqual(code, 1)
                self.assertFalse(result["valid"])
                self.assertNotIn("wave_plan", result)
                self.assertFalse(destination.exists())

    def test_atomic_replace_failure_keeps_prior_file_and_returns_failure(self):
        for module, source in self.inputs():
            with self.subTest(module=module.__name__):
                destination = self.root / "prior-result.json"
                destination.write_bytes(b"previous evidence")
                with patch.object(module._result_output.os, "replace", side_effect=OSError("disk error")):
                    code, result, _ = invoke(module, [source, "--summary", "--result-file", destination])
                self.assertEqual(code, 1)
                self.assertFalse(result["valid"])
                self.assertEqual(destination.read_bytes(), b"previous evidence")
                self.assertNotIn("result_sha256", result)
                self.assertEqual(list(self.root.glob(".validator-result-*.tmp")), [])

    def test_compact_errors_exclude_prompt_text_and_cap_count(self):
        secret_prompt = "full director prompt must stay in the evidence file " * 100
        result = {"valid": False, "errors": [{"code": "BAD_PROMPT", "path": f"$.jobs[{i}]",
                                               "message": secret_prompt} for i in range(30)]}
        destination = self.root / "many-errors.json"
        summary = director._result_output.prepare_output(result, summary=True, result_file=destination)
        self.assert_persisted(destination, summary, result)
        self.assertEqual(summary["error_count"], 30)
        self.assertEqual(len(summary["errors"]), 20)
        self.assertTrue(summary["errors_truncated"])
        self.assertNotIn(secret_prompt, json.dumps(summary))

    def paid_fixture(self):
        document = json.loads(self.batch.read_bytes())
        document["batch_key"]["resolution"] = "720p"
        write_json(self.batch, director.compile_document(document))
        evidence = ledger.load_batch_compile_evidence(self.batch)
        current = ledger.make_zibuyu_apparel_ledger(self.runtime, self.registry, self.policy, evidence)
        previous = copy.deepcopy(current)
        current["ledger_revision"] += 1
        current["previous_ledger_sha256"] = ledger.canonical_json_sha256(previous)
        current["jobs"][0].update({"state": "submission_started",
                                    "submission_started_at": "2026-07-11T08:01:00Z"})
        previous_path = write_json(self.root / "previous.json", previous)
        write_json(self.ledger, current)
        return current, [self.ledger, "--previous-ledger", previous_path, "--batch-compile", self.batch]

    def test_paid_authorization_payload_is_exact_and_summary_never_authorizes(self):
        current, args = self.paid_fixture()
        code, expected, _ = invoke(ledger, args)
        self.assertEqual(code, 0, expected)
        self.assertTrue(expected["history_verified"])
        self.assertTrue(expected["wave_plan"]["paid_submission_allowed"])
        authorized = expected["wave_plan"]["authorized_outbound_requests"][0]
        self.assertEqual(authorized["outbound_request"], current["jobs"][0]["outbound_request"])
        result_file = self.root / "paid-result.json"
        actual_code, summary, stdout = invoke(ledger, args + ["--summary", "--result-file", result_file])
        self.assertEqual(actual_code, 0)
        self.assert_persisted(result_file, summary, expected)
        self.assertEqual(summary["counts"]["authorized_outbound_requests"], 1)
        self.assertNotIn("paid_submission_allowed", stdout)
        self.assertNotIn("wave_plan", summary)
        self.assertNotIn(current["jobs"][0]["compiled_prompt"], stdout)

    def test_paid_guard_failure_is_identical_in_full_and_summary_modes(self):
        current, args = self.paid_fixture()
        current["jobs"][0]["outbound_request"]["arguments"]["prompt"] += " altered"
        write_json(self.ledger, current)
        code, expected, _ = invoke(ledger, args)
        self.assertEqual(code, 1)
        destination = self.root / "rejected-paid.json"
        actual_code, summary, _ = invoke(ledger, args + ["--summary", "--result-file", destination])
        self.assertEqual(actual_code, code)
        self.assert_persisted(destination, summary, expected)
        self.assertFalse(summary["valid"])

    def test_ledger_result_cannot_overwrite_previous_batch_or_registry(self):
        _, args = self.paid_fixture()
        registry_file = write_json(self.root / "registry.json", self.registry)
        args += ["--model-registry", registry_file]
        for destination in (self.root / "previous.json", self.batch, registry_file):
            with self.subTest(destination=destination.name):
                original = destination.read_bytes()
                code, result, _ = invoke(ledger, args + ["--result-file", destination])
                self.assertEqual(code, 1)
                self.assertFalse(result["valid"])
                self.assertEqual(destination.read_bytes(), original)

    def test_streaming_plan_and_sibling_inputs_are_protected(self):
        plan = self.root / "batch-plan.json"
        plan.write_bytes(b"immutable parent plan")
        sibling = self.root / "releases" / "other" / "ledger.json"
        sibling.parent.mkdir(parents=True)
        sibling.write_bytes(b"sibling ledger")
        document = json.loads(self.batch.read_bytes())
        document["streaming_release"] = {"plan_path": str(plan)}
        write_json(self.batch, document)
        for destination in (plan, sibling):
            with self.subTest(destination=str(destination)):
                original = destination.read_bytes()
                code, result, _ = invoke(director, [self.batch, "--compile", "--output", self.batch,
                                                     "--result-file", destination])
                self.assertEqual(code, 1)
                self.assertFalse(result["valid"])
                self.assertEqual(destination.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
