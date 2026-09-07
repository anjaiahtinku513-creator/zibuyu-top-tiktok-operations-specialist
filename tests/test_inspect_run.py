"""Offline routing invariants: inspect_run never mutates or authorizes work."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (Path(__file__).resolve().parents[1] / "skills" /
          "zibuyu-top-tiktok-operations-specialist" / "scripts" / "inspect_run.py")
spec = importlib.util.spec_from_file_location("inspect_run", SCRIPT)
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class InspectRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def variant(self, name):
        return {"variant_id": name, "color_name": name,
                "caption": "PRIVATE_CAPTION_DO_NOT_PRINT", "hashtags": ["#PRIVATE_TAG"]}

    def release(self, directory=None, names=("white",), plan=None, plan_sha=None):
        directory = directory or self.root
        variants = [self.variant(name) for name in names]
        batch = {"schema_version": "1.4", "batch_compile_id": "compile-1", "sku_family_id": "sku-1",
                 "batch_key": {"model_preset": "德1", "market": "DE"}, "variants": variants,
                 "renderings": {"prompt": {"compiled_text": "PRIVATE_PROMPT_DO_NOT_PRINT"}}}
        run_id = "fixture-run"
        if plan:
            run_id = f"{plan['batch_run_id']}:{names[0]}"
            batch["streaming_release"] = {"contract_id": plan["contract_id"],
                "plan_path": str(self.root / "batch-plan.json"), "plan_sha256": plan_sha,
                "variant_id": names[0], "child_run_id": run_id}
        compile_sha = write_json(directory / "batch-compile.json", batch)
        jobs = [{"job_key": hashlib.sha256(name.encode()).hexdigest(), "variant_id": name,
                 "color_name": name, "state": "running", "record_id": 100 + sum(map(ord, name)),
                 "next_action": "poll", "rendered_quality_status": "unverified",
                 "completion_reported": False, "resubmit_allowed": False,
                 "outbound_request": {"prompt": "PRIVATE_OUTBOUND_DO_NOT_PRINT"}}
                for name in names]
        ledger = {"schema_version": "1.0", "run_id": run_id, "ledger_revision": 3,
                  "batch_compile_sha256": compile_sha, "batch_compile_id": "compile-1",
                  "sku_family_id": "sku-1", "jobs": jobs}
        write_json(directory / "ledger.json", ledger)
        return batch, ledger

    def streaming(self, omit=None):
        planned = [self.variant(name) for name in ("white", "black")]
        plan = {"contract_id": "zibuyu_quality_gated_streaming_v2", "plan_locked": True,
                "batch_run_id": "fixture-parent", "batch_compile_id": "compile-1", "sku_family_id": "sku-1",
                "batch_key": {"model_preset": "德1", "market": "DE"},
                "planned_variant_count": 2, "planned_variants": planned}
        digest = write_json(self.root / "batch-plan.json", plan)
        for variant in planned:
            if variant["variant_id"] != omit:
                self.release(self.root / "releases" / variant["variant_id"],
                             (variant["variant_id"],), plan, digest)
        return plan

    def inspect(self, directory=None):
        return inspector.inspect_run(str(directory or self.root))

    def test_running_snapshot_stable_compact_and_read_only(self):
        self.release()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        first, second = self.inspect(), self.inspect()
        self.assertTrue(first["valid"])
        self.assertEqual(first["next_action"], "query_only")
        self.assertEqual(first["artifact_revision_sha256"], second["artifact_revision_sha256"])
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        encoded = json.dumps(first)
        for secret in ("PRIVATE_PROMPT", "PRIVATE_CAPTION", "PRIVATE_TAG", "PRIVATE_OUTBOUND"):
            self.assertNotIn(secret, encoded)
        self.assertFalse(first["paid_submission_allowed"])
        self.assertFalse(first["publication_authorized"])
        self.assertEqual(next(s for s in first["sources"] if s["role"] == "ledger")["ledger_revision"], 3)

    def test_platform_success_only_routes_to_qa(self):
        _, ledger = self.release()
        ledger["jobs"][0].update(state="succeeded", completion_reported=True)
        write_json(self.root / "ledger.json", ledger)
        result = self.inspect()
        self.assertEqual(result["next_action"], "qa")
        self.assertFalse(result["quality_verified_by_inspector"])
        self.assertFalse(result["delivery_verified_by_inspector"])

    def test_recorded_qa_and_report_route_without_repeating_finished_work(self):
        _, ledger = self.release()
        job = ledger["jobs"][0]
        for qa, reported, action in [("keep", False, "deliver"), ("keep", True, "complete"),
                                     ("reroll", False, "deliver"), ("identity_mismatch", False, "deliver")]:
            with self.subTest(qa=qa, reported=reported):
                job.update(state="succeeded", rendered_quality_status=qa, completion_reported=reported)
                write_json(self.root / "ledger.json", ledger)
                result = self.inspect()
                self.assertEqual(result["next_action"], action)
                self.assertFalse(result["publication_authorized"])
                self.assertFalse(result["jobs"][0]["automatic_resubmission_allowed"])

    def test_unknown_or_started_submission_reconciles_with_or_without_record(self):
        _, ledger = self.release()
        for state in ("submission_unknown", "submission_started"):
            for record in (None, 123):
                with self.subTest(state=state, record=record):
                    ledger["jobs"][0].update(state=state, record_id=record)
                    write_json(self.root / "ledger.json", ledger)
                    result = self.inspect()
                    self.assertEqual(result["next_action"], "reconcile_submission")
                    self.assertFalse(result["paid_submission_allowed"])

    def test_missing_record_in_accepted_state_reconciles(self):
        _, ledger = self.release()
        ledger["jobs"][0]["record_id"] = None
        write_json(self.root / "ledger.json", ledger)
        self.assertEqual(self.inspect()["next_action"], "reconcile_submission")

    def test_record_prevents_resubmit_even_when_ledger_says_planned(self):
        _, ledger = self.release()
        ledger["jobs"][0].update(state="planned", next_action="generate_video", resubmit_allowed=True)
        write_json(self.root / "ledger.json", ledger)
        result = self.inspect()
        self.assertFalse(result["valid"])
        self.assertEqual(result["next_action"], "blocked")
        self.assertEqual(result["jobs"][0]["next_action"], "query_only")
        self.assertFalse(result["jobs"][0]["automatic_resubmission_allowed"])

    def test_planning_does_not_authorize_payment(self):
        _, ledger = self.release()
        ledger["jobs"][0].update(state="planned", record_id=None, next_action="generate_video")
        write_json(self.root / "ledger.json", ledger)
        result = self.inspect()
        self.assertEqual(result["next_action"], "new_planning")
        self.assertFalse(result["paid_submission_allowed"])

    def test_failed_job_delivers_without_retake(self):
        _, ledger = self.release()
        ledger["jobs"][0].update(state="failed", record_id=None, next_action="report")
        write_json(self.root / "ledger.json", ledger)
        self.assertEqual(self.inspect()["next_action"], "deliver")

    def test_streaming_child_resolves_all_intended_releases(self):
        self.streaming()
        result = self.inspect(self.root / "releases" / "white")
        self.assertTrue(result["valid"])
        self.assertEqual(result["run_dir"], str(self.root))
        self.assertEqual(result["workflow"], "streaming_parent")
        self.assertEqual({j["variant_id"] for j in result["jobs"]}, {"white", "black"})

    def test_missing_sibling_blocks_parent_but_preserves_safe_query(self):
        self.streaming(omit="black")
        result = self.inspect(self.root / "releases" / "white")
        self.assertFalse(result["valid"])
        self.assertEqual(result["next_action"], "blocked")
        self.assertEqual(result["jobs"][0]["next_action"], "query_only")
        self.assertEqual(len(result["missing_artifacts"]), 2)

    def test_malformed_child_compile_still_resolves_parent(self):
        self.streaming()
        (self.root / "releases" / "white" / "batch-compile.json").write_text("{invalid", encoding="utf-8")
        result = self.inspect(self.root / "releases" / "white")
        self.assertEqual(result["run_dir"], str(self.root))
        self.assertEqual(result["job_count"], 2)
        self.assertFalse(result["valid"])

    def test_hash_drift_changes_revision_and_blocks(self):
        _, ledger = self.release()
        before = self.inspect()
        ledger["batch_compile_sha256"] = "0" * 64
        write_json(self.root / "ledger.json", ledger)
        after = self.inspect()
        self.assertFalse(after["valid"])
        self.assertNotEqual(before["artifact_revision_sha256"], after["artifact_revision_sha256"])

    def test_streaming_parent_scope_drift_blocked(self):
        self.streaming()
        child = self.root / "releases" / "white" / "batch-compile.json"
        batch = json.loads(child.read_text(encoding="utf-8"))
        batch["streaming_release"]["plan_path"] = str(self.root / "other-plan.json")
        write_json(child, batch)
        self.assertFalse(self.inspect()["valid"])

    def test_parent_closure_and_unplanned_release_are_blocked(self):
        self.streaming()
        write_json(self.root / "parent-closure.json", {})
        self.release(self.root / "releases" / "extra", ("extra",))
        result = self.inspect()
        codes = {error["code"] for error in result["errors"]}
        self.assertIn("streaming_parent_closed_or_migrated", codes)
        self.assertIn("unplanned_streaming_release", codes)

    def test_duplicate_and_unknown_values_fail_closed(self):
        _, base = self.release(names=("white", "black"))
        for mutation in (lambda l: l["jobs"][0].update(state="READY_TO_PAY"),
                         lambda l: l["jobs"][0].update(state=[]),
                         lambda l: l["jobs"][0].update(record_id=True),
                         lambda l: l["jobs"][0].update(rendered_quality_status="PASS"),
                         lambda l: l["jobs"][0].update(rendered_quality_status={}),
                         lambda l: l["jobs"][1].update(record_id=l["jobs"][0]["record_id"]),
                         lambda l: l.update(ledger_revision=-1)):
            ledger = copy.deepcopy(base)
            mutation(ledger)
            write_json(self.root / "ledger.json", ledger)
            result = self.inspect()
            self.assertFalse(result["valid"])
            self.assertEqual(result["next_action"], "blocked")

    def test_duplicate_json_key_and_missing_artifacts(self):
        self.release()
        (self.root / "ledger.json").write_text('{"jobs":[],"jobs":[]}', encoding="utf-8")
        self.assertFalse(self.inspect()["valid"])
        (self.root / "ledger.json").unlink()
        result = self.inspect()
        self.assertEqual(result["missing_artifacts"][0]["role"], "ledger")
        self.assertEqual(result["next_action"], "blocked")

    def test_file_size_and_job_count_are_bounded(self):
        self.release(names=("white", "black"))
        with patch.object(inspector, "MAX_FILE_BYTES", 32):
            self.assertFalse(self.inspect()["valid"])
        with patch.object(inspector, "MAX_JOBS", 1):
            self.assertFalse(self.inspect()["valid"])

    def test_noncanonical_streaming_is_blocked(self):
        batch, ledger = self.release()
        batch["streaming_release"] = {"variant_id": "white"}
        ledger["batch_compile_sha256"] = write_json(self.root / "batch-compile.json", batch)
        write_json(self.root / "ledger.json", ledger)
        self.assertFalse(self.inspect()["valid"])

    def test_symlink_escape_blocked(self):
        _, ledger = self.release()
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / "ledger.json"
            write_json(target, ledger)
            (self.root / "ledger.json").unlink()
            try:
                (self.root / "ledger.json").symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable on this host")
            result = self.inspect()
            self.assertIn("source_outside_run_or_not_file", {e["code"] for e in result["errors"]})

    def test_concurrent_ledger_change_is_detected(self):
        _, ledger = self.release()
        original_job = inspector.Inspector.job

        def change_after_read(instance, row, path, compile_path):
            original_job(instance, row, path, compile_path)
            ledger["ledger_revision"] += 1
            write_json(path, ledger)

        with patch.object(inspector.Inspector, "job", change_after_read):
            result = self.inspect()
        self.assertFalse(result["valid"])
        self.assertIn("source_changed_during_inspection", {e["code"] for e in result["errors"]})

    def test_cli_returns_one_json_object_without_tracebacks_for_missing_run(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = inspector.main([str(self.root / "missing")])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["next_action"], "blocked")
        self.assertFalse(result["publication_authorized"])


if __name__ == "__main__":
    unittest.main()
