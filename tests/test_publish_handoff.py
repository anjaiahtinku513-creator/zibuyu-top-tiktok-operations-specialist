"""Offline integration tests for the all-video publish handoff boundary."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "zibuyu-top-tiktok-operations-specialist" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("publish_handoff", SCRIPTS / "build_publish_handoff.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class PublishHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def variant(self, variant_id):
        return {"variant_id": variant_id, "color_name": variant_id,
                "caption": f"Dein {variant_id} Look für jeden Tag.",
                "hashtags": ["#Outfit", "#Alltag", "#Mode", "#Kleidung", "#TikTokShopDE"]}

    def release(self, directory, variants, plan=None, plan_sha=None):
        batch = {"batch_compile_id": "compile-1", "sku_family_id": "sku-1",
                 "batch_key": {"model_preset": "德1", "market": "DE"},
                 "variants": variants}
        run_id = "fixture-run"
        if plan is not None:
            variant_id = variants[0]["variant_id"]
            run_id = f"{plan['batch_run_id']}:{variant_id}"
            batch["streaming_release"] = {
                "contract_id": plan["contract_id"], "plan_path": str(self.root / "batch-plan.json"),
                "plan_sha256": plan_sha, "variant_id": variant_id, "child_run_id": run_id}
        compile_sha = write_json(directory / "batch-compile.json", batch)
        jobs = []
        for variant in variants:
            variant_id = variant["variant_id"]
            record_id = 100 + sum(ord(c) for c in variant_id)
            jobs.append({"job_key": hashlib.sha256(variant_id.encode()).hexdigest(),
                         "variant_id": variant_id, "color_name": variant_id, "model_preset": "德1",
                         "state": "succeeded", "rendered_quality_status": "keep", "completion_reported": True,
                         "record_id": record_id, "video_url": f"https://example.invalid/{variant_id}.mp4",
                         "video_url_status": "usable", "director_receipt": {"batch_compile_sha256": compile_sha}})
        ledger = {"run_id": run_id, "batch_compile_id": "compile-1", "sku_family_id": "sku-1",
                  "batch_compile_sha256": compile_sha, "jobs": jobs}
        write_json(directory / "ledger.json", ledger)
        return batch, ledger

    def barrier(self):
        return self.release(self.root, [self.variant("white"), self.variant("black")])

    def streaming(self, omit=None):
        variants = [self.variant("white"), self.variant("black")]
        plan = {"contract_id": "zibuyu_quality_gated_streaming_v2", "plan_locked": True,
                "batch_run_id": "fixture-parent", "batch_compile_id": "compile-1", "sku_family_id": "sku-1",
                "planned_variant_count": len(variants), "planned_variants": variants,
                "batch_key": {"model_preset": "德1", "market": "DE"}}
        plan_sha = write_json(self.root / "batch-plan.json", plan)
        for variant in variants:
            if variant["variant_id"] != omit:
                self.release(self.root / "releases" / variant["variant_id"], [variant], plan, plan_sha)
        return plan

    def assert_blocked(self, message, run_dir=None):
        with self.assertRaisesRegex((handoff.HandoffError, OSError, ValueError), message):
            handoff.build(str(run_dir or self.root))
        self.assertFalse((self.root / "publish-handoff.json").exists())

    def test_barrier_success_verbatim_copy_and_no_production_mutation(self):
        self.barrier()
        originals = {p: p.read_bytes() for p in self.root.glob("*.json")}
        result = handoff.build(str(self.root))
        self.assertTrue(result["valid"])
        output = read_json(self.root / "publish-handoff.json")
        self.assertTrue(output["readiness"])
        self.assertFalse(output["publication_authorized"])
        self.assertTrue(output["snapshot_only"])
        self.assertTrue(output["revalidate_before_publication"])
        self.assertEqual(output["item_count"], 2)
        delivery = handoff.validate_delivery(read_json(self.root / "batch-compile.json"), read_json(self.root / "ledger.json"))
        self.assertEqual([i["copy_ready_caption"] for i in output["items"]],
                         [i["copy_ready_caption"] for i in delivery["items"]])
        for source in output["sources"]:
            self.assertTrue(Path(source["path"]).is_absolute())
            self.assertEqual(source["sha256"], hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest())
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
        second = handoff.build(str(self.root))
        self.assertEqual(result["handoff_sha256"], second["handoff_sha256"])

    def test_streaming_child_resolves_complete_parent(self):
        self.streaming()
        child = self.root / "releases" / "white"
        result = handoff.build(str(child))
        self.assertEqual(result["run_dir"], str(self.root))
        self.assertFalse((child / "publish-handoff.json").exists())
        output = read_json(self.root / "publish-handoff.json")
        self.assertEqual(output["workflow"], "streaming_parent")
        self.assertEqual(len(output["sources"]), 5)
        self.assertEqual({i["variant_id"] for i in output["items"]}, {"white", "black"})

    def test_missing_planned_color_and_child_bypass_blocked(self):
        self.streaming(omit="black")
        self.assert_blocked("black", self.root / "releases" / "white")

    def test_missing_barrier_video_blocked(self):
        _, ledger = self.barrier()
        ledger["jobs"].pop()
        write_json(self.root / "ledger.json", ledger)
        self.assert_blocked("Missing intended videos")

    def test_non_success_qa_and_undelivered_copy_blocked(self):
        _, base = self.barrier()
        changes = [("state", s, "every intended") for s in ("running", "failed", "planned", "submission_unknown")]
        changes += [("rendered_quality_status", s, "recorded rendered QA") for s in ("unverified", "retake", None)]
        changes += [("completion_reported", False, "complete video")]
        for field, value, message in changes:
            with self.subTest(field=field, value=value):
                ledger = copy.deepcopy(base)
                ledger["jobs"][0][field] = value
                write_json(self.root / "ledger.json", ledger)
                self.assert_blocked(message)

    def test_invalid_copy_runs_real_delivery_validator(self):
        batch, ledger = self.barrier()
        batch["variants"][0]["hashtags"].pop()
        digest = write_json(self.root / "batch-compile.json", batch)
        ledger["batch_compile_sha256"] = digest
        for job in ledger["jobs"]:
            job["director_receipt"]["batch_compile_sha256"] = digest
        write_json(self.root / "ledger.json", ledger)
        self.assert_blocked("DELIVERY_HASHTAG_COUNT")

    def test_compile_hash_drift_invalidates_old_snapshot(self):
        batch, _ = self.barrier()
        handoff.build(str(self.root))
        batch["variants"][0]["caption"] = "Geändert."
        write_json(self.root / "batch-compile.json", batch)
        self.assert_blocked("Compile SHA-256 drift")

    def test_malformed_compile_invalidates_old_snapshot(self):
        self.barrier()
        handoff.build(str(self.root))
        (self.root / "batch-compile.json").write_text("{invalid", encoding="utf-8")
        self.assert_blocked("Expecting")

    def test_missing_source_invalidates_old_snapshot(self):
        self.streaming()
        handoff.build(str(self.root))
        (self.root / "releases" / "black" / "ledger.json").unlink()
        self.assert_blocked("black", self.root / "releases" / "white")

    def test_plan_hash_drift_blocked(self):
        plan = self.streaming()
        plan["changed_after_release"] = True
        write_json(self.root / "batch-plan.json", plan)
        self.assert_blocked("Streaming parent SHA-256 drift")

    def test_wrong_parent_path_blocked_even_for_identical_plan_bytes(self):
        self.streaming()
        other_plan = self.root / "other-plan.json"
        other_plan.write_bytes((self.root / "batch-plan.json").read_bytes())
        child = self.root / "releases" / "white"
        batch, ledger = read_json(child / "batch-compile.json"), read_json(child / "ledger.json")
        batch["streaming_release"]["plan_path"] = str(other_plan)
        digest = write_json(child / "batch-compile.json", batch)
        ledger["batch_compile_sha256"] = digest
        ledger["jobs"][0]["director_receipt"]["batch_compile_sha256"] = digest
        write_json(child / "ledger.json", ledger)
        self.assert_blocked("Wrong streaming parent path")

    def test_ambiguous_retakes_blocked_without_silent_selection(self):
        _, ledger = self.barrier()
        retake = copy.deepcopy(ledger["jobs"][0])
        retake.update(job_key="new-job", record_id=999)
        ledger["jobs"].append(retake)
        write_json(self.root / "ledger.json", ledger)
        self.assert_blocked("Multiple outputs/retakes")

    def test_unknown_remote_location_blocked(self):
        _, ledger = self.barrier()
        ledger["jobs"][0]["video_url_status"] = "unknown"
        write_json(self.root / "ledger.json", ledger)
        self.assert_blocked("not recorded usable")

    def local_video(self):
        _, ledger = self.barrier()
        video = self.root / "delivered.mp4"
        video.write_bytes(b"offline fixture; QA is recorded in ledger")
        digest, size = hashlib.sha256(video.read_bytes()).hexdigest(), video.stat().st_size
        ledger["jobs"][0]["video_url_status"] = "missing"
        ledger["jobs"][0]["download"] = {"state": "succeeded", "local_path": str(video), "sha256": digest, "bytes": size}
        write_json(self.root / "ledger.json", ledger)
        return video, digest, size

    def test_local_delivery_binds_actual_hash_and_bytes(self):
        video, digest, size = self.local_video()
        handoff.build(str(self.root))
        output = read_json(self.root / "publish-handoff.json")
        self.assertIsNone(output["items"][0]["video_url"])
        self.assertEqual(output["items"][0]["local_video_path"], str(video))
        self.assertEqual(output["items"][0]["local_video_sha256"], digest)
        self.assertEqual(output["items"][0]["local_video_bytes"], size)
        self.assertIn({"role": "local_video", "path": str(video), "sha256": digest, "bytes": size}, output["sources"])

    def test_local_video_tamper_invalidates_prior_handoff(self):
        video, _, _ = self.local_video()
        handoff.build(str(self.root))
        video.write_bytes(b"replacement contents after QA")
        self.assert_blocked("local video SHA-256 changed")

    def test_local_video_changed_during_build_blocked(self):
        video, _, _ = self.local_video()
        actual_identity = handoff._file_identity
        def identity_then_tamper(path):
            result = actual_identity(path)
            if path == video:
                video.write_bytes(b"changed after first video hash")
            return result
        with patch.object(handoff, "_file_identity", side_effect=identity_then_tamper):
            self.assert_blocked("Source changed while building")

    def test_unsafe_variant_and_relative_run_blocked(self):
        plan = self.streaming()
        plan["planned_variants"][0]["variant_id"] = "../../outside"
        write_json(self.root / "batch-plan.json", plan)
        self.assert_blocked("Unsafe")
        self.assert_blocked("absolute", Path("relative-run"))

    def test_unrelated_existing_output_preserved(self):
        self.barrier()
        path = self.root / "publish-handoff.json"
        write_json(path, {"unrelated_user_data": True})
        original = path.read_bytes()
        with self.assertRaisesRegex(handoff.HandoffError, "unrelated"):
            handoff.build(str(self.root))
        self.assertEqual(original, path.read_bytes())

    def test_mid_validation_source_change_blocked(self):
        self.barrier()
        real_validate = handoff.validate_delivery
        def mutate_then_validate(batch, ledger):
            value = read_json(self.root / "ledger.json")
            value["changed_during_build"] = True
            write_json(self.root / "ledger.json", value)
            return real_validate(batch, ledger)
        with patch.object(handoff, "validate_delivery", side_effect=mutate_then_validate):
            self.assert_blocked("Source changed while building")

    def test_cli_reports_json_failure_for_bad_input(self):
        self.barrier()
        (self.root / "ledger.json").write_text('{"jobs":[],"jobs":[]}', encoding="utf-8")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = handoff.main([str(self.root)])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(captured.getvalue())["readiness"])
        self.assertFalse((self.root / "publish-handoff.json").exists())


if __name__ == "__main__":
    unittest.main()
