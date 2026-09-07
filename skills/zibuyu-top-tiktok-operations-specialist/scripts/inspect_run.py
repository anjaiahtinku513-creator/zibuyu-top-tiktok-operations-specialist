#!/usr/bin/env python3
"""Print a bounded, read-only routing snapshot; never authorize production or publication.

Only persisted JSON metadata is inspected. This is not ledger-history, creative,
media, delivery, or permission validation. No prompts, captions, credentials,
remote video URLs, or arbitrary JSON values are emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_JOBS = 144
MAX_FILES = MAX_JOBS * 2 + 2
STATES = {"planned", "submission_started", "submission_unknown", "submitted",
          "queued", "running", "succeeded", "failed", "pending_timeout", "blocked"}
QA_STATES = {"keep", "fix_in_post", "reroll", "rewrite", "stop_or_rescope", "unverified", "identity_mismatch"}
PLAN_CONTRACTS = {"zibuyu_quality_gated_streaming_v1", "zibuyu_quality_gated_streaming_v2"}
SKILL = Path(__file__).resolve().parents[1]


class InspectionError(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InspectionError("duplicate_json_key")
        result[key] = value
    return result


def _identifier(value):
    return (isinstance(value, str) and 0 < len(value) <= 160
            and value == value.strip() and not any(ord(c) < 32 for c in value))


def _variant_id(value):
    return (_identifier(value) and value not in {".", ".."} and not value.endswith(".")
            and not any(c in value for c in '/\\:<>"|?*'))


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive(value):
    return _integer(value) and value > 0


def _enum(value, allowed):
    return isinstance(value, str) and value in allowed


class Inspector:
    def __init__(self, requested):
        self.requested = requested
        self.root = None
        self.sources = []
        self.missing = []
        self.errors = []
        self.jobs = []
        self.bytes_read = 0
        self.record_ids = set()
        self.job_keys = set()

    def error(self, code, path):
        self.errors.append({"code": code, "path": str(path)})

    def source(self, path, role):
        if not path.exists():
            self.missing.append({"role": role, "path": str(path)})
            return None, None
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root) or not resolved.is_file():
                raise InspectionError("source_outside_run_or_not_file")
            if len(self.sources) >= MAX_FILES:
                raise InspectionError("file_count_limit")
            if resolved.stat().st_size > MAX_FILE_BYTES:
                raise InspectionError("file_size_limit")
            with resolved.open("rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
            self.bytes_read += len(data)
            if len(data) > MAX_FILE_BYTES or self.bytes_read > MAX_TOTAL_BYTES:
                raise InspectionError("read_size_limit")
            digest = hashlib.sha256(data).hexdigest()
            self.sources.append({"role": role, "path": str(resolved), "sha256": digest, "bytes": len(data)})
            value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(InspectionError("nonfinite_json")))
            if not isinstance(value, dict):
                raise InspectionError("json_object_required")
            return value, digest
        except InspectionError as exc:
            self.error(str(exc), path)
        except (OSError, UnicodeError, ValueError, RecursionError):
            self.error("unreadable_or_malformed_json", path)
        return None, None

    def variants(self, document, field, path):
        rows = document.get(field)
        if not isinstance(rows, list) or not rows or len(rows) > MAX_JOBS:
            self.error("invalid_or_excessive_variants", path)
            return {}
        result = {}
        for row in rows:
            variant = row.get("variant_id") if isinstance(row, dict) else None
            if not _variant_id(variant) or variant in result:
                self.error("invalid_or_duplicate_variant", path)
                return {}
            result[variant] = row
        return result

    def job(self, row, path, compile_path):
        if len(self.jobs) >= MAX_JOBS:
            self.error("job_count_limit", path)
            return
        if not isinstance(row, dict):
            self.error("job_object_required", path)
            return
        variant = row.get("variant_id")
        key, state, record = row.get("job_key"), row.get("state"), row.get("record_id")
        qa = row.get("rendered_quality_status")
        issues = []
        if not _variant_id(variant):
            issues.append("invalid_variant_id")
        if not _identifier(key) or key in self.job_keys:
            issues.append("missing_or_duplicate_job_key")
        else:
            self.job_keys.add(key)
        if not _enum(state, STATES):
            issues.append("unknown_job_state")
        if record is not None and not _positive(record):
            issues.append("invalid_record_id")
        if _positive(record):
            if record in self.record_ids:
                issues.append("duplicate_record_id")
            self.record_ids.add(record)
        if qa is not None and not _enum(qa, QA_STATES):
            issues.append("unknown_qa_status")
        reported = row.get("completion_reported")
        if reported is not None and not isinstance(reported, bool):
            issues.append("invalid_completion_reported")
        if row.get("resubmit_allowed") is True and record is not None:
            issues.append("accepted_record_resubmit_flag")
        action = "blocked"
        reason = "persisted_state_requires_review"
        if _enum(state, {"submission_unknown", "submission_started"}):
            action, reason = "reconcile_submission", "acceptance_must_be_reconciled"
        elif _enum(state, {"submitted", "queued", "running", "pending_timeout"}):
            if _positive(record):
                action, reason = "query_only", "query_existing_record"
            else:
                action, reason = "reconcile_submission", "accepted_state_missing_record"
        elif state == "planned":
            if record is not None:
                action, reason = "query_only", "record_prevents_new_submission"
                issues.append("planned_state_has_record")
            else:
                action, reason = "new_planning", "requires_full_preflight_and_existing_authorization_gate"
        elif state == "succeeded":
            if not _positive(record):
                issues.append("succeeded_missing_record")
            elif qa is None or qa == "unverified":
                action, reason = "qa", "platform_success_is_not_quality_acceptance"
            elif qa != "keep":
                action, reason = "deliver", "report_recorded_quality_failure_without_automatic_retake"
            elif reported is True:
                action, reason = "complete", "recorded_delivery_complete_still_not_publication_authorization"
            else:
                action, reason = "deliver", "run_delivery_validator_before_reporting"
        elif state == "failed":
            action, reason = ("complete", "recorded_failure_delivered") if reported is True else (
                "deliver", "include_failed_job_in_delivery_no_automatic_retake")
        if issues:
            for issue in issues:
                self.error(issue, path)
            # A positively identified accepted record may still be queried even
            # when its ledger needs repair. No malformed input authorizes a write.
            if _enum(state, {"submission_unknown", "submission_started"}):
                action = "reconcile_submission"
            else:
                action = "query_only" if _positive(record) else "blocked"
        self.jobs.append({"job_key": key if _identifier(key) else None,
                          "variant_id": variant if _variant_id(variant) else None,
                          "record_id": record if _positive(record) else None,
                          "state": state if _enum(state, STATES) else "unknown",
                          "recorded_quality_status": qa if _enum(qa, QA_STATES) else "unverified",
                          "completion_reported": reported is True,
                          "ledger_path": str(path), "batch_compile_path": str(compile_path),
                          "next_action": action, "reason": reason,
                          "automatic_resubmission_allowed": False})

    def release(self, directory, plan=None, plan_sha=None, planned_variant=None):
        compile_path, ledger_path = directory / "batch-compile.json", directory / "ledger.json"
        batch, compile_sha = self.source(compile_path, "batch_compile")
        ledger, _ = self.source(ledger_path, "ledger")
        variants = self.variants(batch, "variants", compile_path) if batch else {}
        if batch and plan:
            variant = planned_variant["variant_id"]
            binding = batch.get("streaming_release")
            if not isinstance(binding, dict):
                self.error("missing_streaming_binding", compile_path)
            else:
                bound_path = binding.get("plan_path")
                expected_run = f"{plan.get('batch_run_id')}:{variant}"
                if (not isinstance(bound_path, str) or not Path(bound_path).is_absolute()
                        or Path(bound_path).resolve() != self.root / "batch-plan.json"
                        or binding.get("plan_sha256") != plan_sha
                        or binding.get("contract_id") != plan.get("contract_id")
                        or binding.get("variant_id") != variant
                        or binding.get("child_run_id") != expected_run):
                    self.error("streaming_parent_binding_mismatch", compile_path)
                if ledger and ledger.get("run_id") != expected_run:
                    self.error("streaming_child_run_mismatch", ledger_path)
            if set(variants) != {variant}:
                self.error("streaming_release_variant_mismatch", compile_path)
            for field in ("batch_compile_id", "sku_family_id", "batch_key"):
                if batch.get(field) != plan.get(field):
                    self.error("streaming_shared_binding_mismatch", compile_path)
            if variant in variants and any(variants[variant].get(field) != planned_variant.get(field)
                                           for field in ("color_name", "caption", "hashtags")):
                self.error("streaming_variant_drift", compile_path)
        elif batch and "streaming_release" in batch:
            self.error("noncanonical_streaming_child", compile_path)
        if not ledger:
            return
        if not _identifier(ledger.get("run_id")):
            self.error("missing_run_id", ledger_path)
        revision = ledger.get("ledger_revision")
        if revision is not None and not _integer(revision):
            self.error("invalid_ledger_revision", ledger_path)
        for source in reversed(self.sources):
            if source["role"] == "ledger" and source["path"] == str(ledger_path.resolve()):
                source["ledger_revision"] = revision if _integer(revision) else None
                break
        if batch and ledger.get("batch_compile_sha256") != compile_sha:
            self.error("missing_or_drifted_compile_hash", ledger_path)
        rows = ledger.get("jobs")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_JOBS:
            self.error("missing_or_excessive_jobs", ledger_path)
            return
        seen = set()
        for row in rows:
            self.job(row, ledger_path, compile_path)
            if isinstance(row, dict) and _variant_id(row.get("variant_id")):
                seen.add(row["variant_id"])
        if batch and seen != set(variants):
            self.error("ledger_compile_variant_mismatch", ledger_path)

    def inspect(self):
        requested = Path(self.requested)
        if not requested.is_absolute():
            raise InspectionError("absolute_run_directory_required")
        requested = requested.resolve(strict=True)
        if not requested.is_dir():
            raise InspectionError("run_directory_required")
        self.root = requested.parent.parent if requested.parent.name == "releases" else requested
        streaming = requested.parent.name == "releases" or (self.root / "batch-plan.json").exists()
        if streaming:
            plan, plan_sha = self.source(self.root / "batch-plan.json", "batch_plan")
            if plan:
                planned = self.variants(plan, "planned_variants", self.root / "batch-plan.json")
                if (not _enum(plan.get("contract_id"), PLAN_CONTRACTS) or plan.get("plan_locked") is not True
                        or not _identifier(plan.get("batch_run_id"))
                        or not _positive(plan.get("planned_variant_count"))
                        or plan.get("planned_variant_count") != len(planned)):
                    self.error("invalid_or_unlocked_streaming_plan", self.root / "batch-plan.json")
                if (self.root / "parent-closure.json").exists():
                    self.error("streaming_parent_closed_or_migrated", self.root / "parent-closure.json")
                release_root = self.root / "releases"
                if release_root.exists():
                    with os.scandir(release_root) as children:
                        for index, child in enumerate(children):
                            if index >= MAX_JOBS:
                                raise InspectionError("release_directory_limit")
                            child_path = Path(child.path)
                            if child.is_dir() and ((child_path / "ledger.json").exists()
                                                   or (child_path / "batch-compile.json").exists()) and child.name not in planned:
                                self.error("unplanned_streaming_release", child_path)
                for variant, row in planned.items():
                    self.release(release_root / variant, plan, plan_sha, row)
        else:
            self.release(self.root)
        # Detect concurrent changes; do not publish a mixed-version snapshot.
        for source in self.sources:
            try:
                with Path(source["path"]).open("rb") as handle:
                    current = handle.read(MAX_FILE_BYTES + 1)
                if hashlib.sha256(current).hexdigest() != source["sha256"]:
                    self.error("source_changed_during_inspection", source["path"])
            except OSError:
                self.error("source_changed_during_inspection", source["path"])
        actions = {job["next_action"] for job in self.jobs}
        if self.errors or self.missing:
            action = "blocked"
        else:
            action = next((candidate for candidate in ("reconcile_submission", "blocked", "query_only", "qa", "deliver", "new_planning", "complete")
                           if candidate in actions), "blocked")
        snapshot = {"artifact_type": "zibuyu_run_inspection", "schema_version": "1.0", "snapshot_only": True,
                    "valid": not self.errors and not self.missing,
                    "validation_scope": "persisted_metadata_routing_only",
                    "paid_submission_allowed": False, "publication_authorized": False,
                    "quality_verified_by_inspector": False, "delivery_verified_by_inspector": False,
                    "requested_dir": str(requested), "run_dir": str(self.root),
                    "workflow": "streaming_parent" if streaming else "barrier",
                    "next_action": action, "job_count": len(self.jobs), "jobs": self.jobs,
                    "sources": self.sources, "missing_artifacts": self.missing, "errors": self.errors,
                    "references": {"resume_and_delivery": str(SKILL / "references" / "resume-and-delivery.md"),
                                   "poll_and_reconcile": str(SKILL.parent / "popboom" / "references" / "poll-and-reconcile.md"),
                                   "production": str(SKILL / "SKILL.md"),
                                   "validate_delivery": str(SKILL / "scripts" / "validate_delivery_pack.py"),
                                   "validate_ledger": str(SKILL.parent / "popboom" / "scripts" / "validate_ledger.py")}}
        revision_input = {key: snapshot[key] for key in ("run_dir", "sources", "missing_artifacts", "errors")}
        snapshot["artifact_revision_sha256"] = hashlib.sha256(json.dumps(
            revision_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return snapshot


def inspect_run(run_dir):
    return Inspector(run_dir).inspect()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Absolute barrier run or streaming parent/child directory")
    args = parser.parse_args(argv)
    try:
        result = inspect_run(args.run_dir)
    except (OSError, ValueError, TypeError, RecursionError):
        result = {"artifact_type": "zibuyu_run_inspection", "valid": False,
                  "next_action": "blocked", "snapshot_only": True,
                  "paid_submission_allowed": False, "publication_authorized": False,
                  "errors": [{"code": "run_unreadable_or_outside_supported_limits"}]}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
