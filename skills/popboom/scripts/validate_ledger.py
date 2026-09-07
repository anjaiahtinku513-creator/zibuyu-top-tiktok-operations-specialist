#!/usr/bin/env python3
"""Deterministic, standard-library validator for persistent PopBoom ledgers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


_output_helper_path = Path(__file__).resolve().parents[3] / "scripts" / "validator_result_output.py"
_output_helper_spec = importlib.util.spec_from_file_location("zibuyu_validator_result_output", _output_helper_path)
if _output_helper_spec is None or _output_helper_spec.loader is None:
    raise RuntimeError(f"cannot load validator output helper: {_output_helper_path}")
_result_output = importlib.util.module_from_spec(_output_helper_spec)
_output_helper_spec.loader.exec_module(_result_output)


ROUTES = {"mcp_declared", "mcp_observed", "mcp_provisional", "ui", "blocked"}
STATES = {
    "planned", "submission_started", "submission_unknown", "submitted",
    "queued", "running", "succeeded", "failed", "pending_timeout", "blocked",
}
TERMINAL_STATES = {"succeeded", "failed", "blocked"}
INFLIGHT_STATES = {
    "submission_unknown", "submitted", "queued",
    "running", "pending_timeout",
}
NEW_SUBMISSION_STATES = {"planned", "submission_started"}
REQUIRED_TOOLS = {
    "upload_images", "generate_video", "check_task", "query_balance",
    "list_custom_portraits",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZIBUYU_APPAREL_KIND = "zibuyu_apparel"
GENERIC_POPBOOM_KIND = "generic_popboom"
WORKFLOW_KINDS = {ZIBUYU_APPAREL_KIND, GENERIC_POPBOOM_KIND}
UPLOAD_TRANSPORTS = {"local_file_hook", "url_reuse", "popboom_ui", "legacy_inline_base64"}
NEW_UPLOAD_TRANSPORTS = {"local_file_hook", "url_reuse", "popboom_ui"}
CONTENT_VARIANTS = {"original", "derivative"}
DIRECTOR_SCHEMA_VERSION = "1.4"
DIRECTOR_QUALITY_CONTRACT_ID = "zibuyu_ugc_quality_v4"
DIRECTOR_SERIALIZER_ID = "canonical_prompt_v7"
DIRECTOR_DEADLINE_CONTRACT_ID = "zibuyu_three_layer_deadlines_v1"
MARKET_PROMPT_CONTRACT_ID = "zibuyu_market_prompt_v1"
HISTORICAL_V6_DIRECTOR_SCHEMA_VERSION = "1.4"
HISTORICAL_V6_DIRECTOR_QUALITY_CONTRACT_ID = "zibuyu_ugc_quality_v3"
HISTORICAL_V6_DIRECTOR_SERIALIZER_ID = "canonical_prompt_v6"
LEGACY_DIRECTOR_SCHEMA_VERSION = "1.3"
LEGACY_DIRECTOR_QUALITY_CONTRACT_ID = "zibuyu_ugc_quality_v2"
LEGACY_DIRECTOR_SERIALIZER_ID = "canonical_prompt_v5"
DIRECTOR_RECEIPT_HASH_FIELDS = (
    "batch_compile_sha256", "quality_plan_sha256", "research_bundle_sha256", "canonical_beats_sha256",
    "compiled_text_sha256",
)
V6_DIRECTOR_RECEIPT_HASH_FIELDS = DIRECTOR_RECEIPT_HASH_FIELDS + (
    "market_prompt_profile_sha256", "generation_controls_sha256",
    "voiceover_review_sha256",
)
V7_DIRECTOR_RECEIPT_HASH_FIELDS = V6_DIRECTOR_RECEIPT_HASH_FIELDS + (
    "three_layer_deadlines_sha256",
)
V6_DIRECTOR_RECEIPT_FIELDS = (
    "variant_id", "timeline_id", "timeline_version", "batch_compile_sha256",
    "schema_version", "quality_contract_id", "serializer_id",
    "quality_plan_sha256", "research_bundle_sha256", "canonical_beats_sha256",
    "compiled_text_sha256", "market_prompt_contract_id",
    "market_prompt_profile_id", "market_prompt_profile_sha256",
    "generation_controls_sha256", "voiceover_review_sha256", "market",
    "voiceover_language", "director_valid", "eligible_for_new_submission",
)
V7_DIRECTOR_RECEIPT_FIELDS = V6_DIRECTOR_RECEIPT_FIELDS + (
    "deadline_contract_id", "three_layer_deadlines_sha256",
)
MARKET_MODEL_LANGUAGE_PROFILES = {
    ("US", "美1", "en-US", "us_champion_v1"),
    ("US", "美2", "en-US", "us_champion_v1"),
    ("US", "美3", "en-US", "us_champion_v1"),
    ("DE", "德1", "de-DE", "de_champion_v1"),
    ("DE", "德2", "de-DE", "de_champion_v1"),
    ("DE", "德3", "de-DE", "de_champion_v1"),
}
LEGACY_V5_RESUME_CONTRACT_ID = "legacy_v5_exact_resume_v1"
LEGACY_V5_RESUME_ASSERTIONS = {
    "compiled_prompt", "reference_bindings_and_order", "model_identity",
    "settings", "director_receipt", "outbound_request",
}
MAX_INFLIGHT_SOURCES = {"user"}
MAX_INFLIGHT_EVIDENCE_KINDS = {
    "user": "user_explicit",
}
RATE_LIMIT_MARKERS = {
    "rate_limit", "rate_limited", "too_many_requests", "concurrency_limit",
    "concurrency_limited",
}
JOB_NEXT_ACTIONS = {
    "generate_video", "reconcile_submission", "check_task", "poll", "wait",
    "download_video", "report", "complete", "none",
}
STATE_NEXT_ACTIONS = {
    "planned": {"generate_video"},
    "submission_started": {"generate_video"},
    "submission_unknown": {"reconcile_submission"},
    "submitted": {"check_task", "poll", "wait"},
    "queued": {"check_task", "poll", "wait"},
    "running": {"check_task", "poll", "wait"},
    "pending_timeout": {"check_task", "poll", "wait"},
    "succeeded": {"download_video", "report", "complete", "none"},
    "failed": {"report", "complete", "none"},
    "blocked": {"wait", "none"},
}
PAID_RESUBMISSION_ACTIONS = {"generate_video", "retry", "resubmit", "new_task"}
DANGEROUS_RESUBMISSION_FLAGS = (
    "resubmit_allowed", "allow_resubmit", "retry_submission", "generate_new_task",
)
SENSITIVE_KEYS = {
    "authorization", "bearer", "bearer_token", "token", "api_key", "apikey",
    "access_token", "refresh_token", "password", "passwd", "secret",
    "client_secret", "credential", "credentials", "cookie", "cookies",
    "set_cookie", "image_data", "image_bytes", "binary_data", "file_content",
}
OUTBOUND_REQUEST_KEYS = {"server_name", "tool_name", "arguments"}
OUTBOUND_REQUIRED_ARGUMENT_KEYS = {
    "prompt", "ref_image_urls", "model", "duration", "resolution", "ratio",
    "product_info",
}
OUTBOUND_OPTIONAL_ARGUMENT_KEYS = set()
OUTBOUND_ARGUMENT_KEYS = OUTBOUND_REQUIRED_ARGUMENT_KEYS | OUTBOUND_OPTIONAL_ARGUMENT_KEYS
PRODUCT_INFO_FIXED_BRAND = "拓展平台"
PRODUCT_INFO_REQUIRED_KEYS = {"brand", "sku"}
ZERO_HUMAN_IDENTITY_AUDIT_VERSION = "zero_human_identity_pixels_v1"
ZERO_HUMAN_IDENTITY_INSPECTION_METHOD = "full_resolution_visual_inspection"
ZERO_HUMAN_IDENTITY_CUE_FIELDS = (
    "skin_present", "face_present", "hair_present", "neck_chest_collarbone_present",
    "shoulders_arms_wrists_present", "hands_fingers_nails_present",
    "tattoos_jewelry_present", "person_specific_body_shape_present",
)


class DuplicateKeyError(ValueError):
    pass


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def no_duplicate_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def load_json_text(text):
    return json.loads(text, object_pairs_hook=no_duplicate_object)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_sha256(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def zero_human_identity_audit(image_sha256):
    audit = {
        "audit_version": ZERO_HUMAN_IDENTITY_AUDIT_VERSION,
        "audited_sha256": image_sha256,
        "inspection_method": ZERO_HUMAN_IDENTITY_INSPECTION_METHOD,
        "reviewed_at": "2026-07-30T08:10:00Z",
        "passed": True,
    }
    audit.update({field: False for field in ZERO_HUMAN_IDENTITY_CUE_FIELDS})
    return audit


def director_receipt_contract(receipt):
    if not isinstance(receipt, dict):
        return None
    if (receipt.get("schema_version") == LEGACY_DIRECTOR_SCHEMA_VERSION and
            receipt.get("quality_contract_id") == LEGACY_DIRECTOR_QUALITY_CONTRACT_ID and
            receipt.get("serializer_id") == LEGACY_DIRECTOR_SERIALIZER_ID):
        return "legacy_v5"
    if (receipt.get("schema_version") == HISTORICAL_V6_DIRECTOR_SCHEMA_VERSION and
            receipt.get("quality_contract_id") == HISTORICAL_V6_DIRECTOR_QUALITY_CONTRACT_ID and
            receipt.get("serializer_id") == HISTORICAL_V6_DIRECTOR_SERIALIZER_ID):
        return "historical_v6"
    if (receipt.get("quality_contract_id") == DIRECTOR_QUALITY_CONTRACT_ID or
            receipt.get("serializer_id") == DIRECTOR_SERIALIZER_ID or
            receipt.get("deadline_contract_id") is not None or
            receipt.get("three_layer_deadlines_sha256") is not None):
        return "v7"
    if (receipt.get("schema_version") == DIRECTOR_SCHEMA_VERSION or
            receipt.get("quality_contract_id") == HISTORICAL_V6_DIRECTOR_QUALITY_CONTRACT_ID or
            receipt.get("serializer_id") == HISTORICAL_V6_DIRECTOR_SERIALIZER_ID or
            receipt.get("market_prompt_contract_id") is not None):
        return "historical_v6"
    if receipt.get("serializer_id") == LEGACY_DIRECTOR_SERIALIZER_ID:
        return "legacy_v5"
    return None


def legacy_v5_paid_package(row):
    """Return the immutable paid payload covered by an exact legacy-v5 resume."""
    return {
        "job_key": row.get("job_key"),
        "variant_id": row.get("variant_id"),
        "color_name": row.get("color_name"),
        "model_name": row.get("model_name"),
        "model_preset": row.get("model_preset"),
        "asset_id": row.get("asset_id"),
        "asset_identity_sha256": row.get("asset_identity_sha256"),
        "asset_identity_version": row.get("asset_identity_version"),
        "batch_compile_id": row.get("batch_compile_id"),
        "timeline_id": row.get("timeline_id"),
        "timeline_version": row.get("timeline_version"),
        "compiled_prompt": row.get("compiled_prompt"),
        "prompt_sha256": row.get("prompt_sha256"),
        "director_receipt": row.get("director_receipt"),
        "reference_bindings": row.get("reference_bindings"),
        "generation_model": row.get("generation_model"),
        "duration": row.get("duration"),
        "resolution": row.get("resolution"),
        "ratio": row.get("ratio"),
        "route": row.get("route"),
        "submission_transport": row.get("submission_transport"),
        "product_info": row.get("product_info") if "product_info" in row else None,
        "outbound_request": row.get("outbound_request"),
        "outbound_request_sha256": row.get("outbound_request_sha256"),
    }


def market_prompt_binding(receipt):
    if director_receipt_contract(receipt) not in {"v7", "historical_v6"}:
        return None
    return {
        key: receipt.get(key) for key in (
            "market_prompt_contract_id", "market_prompt_profile_id",
            "market_prompt_profile_sha256", "generation_controls_sha256",
            "voiceover_review_sha256", "market", "voiceover_language",
        )
    }


def authorized_outbound_request(job_key, job):
    receipt = job.get("director_receipt") if isinstance(job.get("director_receipt"), dict) else None
    result = {
        "job_key": job_key,
        "outbound_request_sha256": job.get("outbound_request_sha256"),
        "outbound_request": copy.deepcopy(job.get("outbound_request")),
    }
    if receipt is not None:
        result["director_receipt_sha256"] = canonical_json_sha256(receipt)
    binding = market_prompt_binding(receipt)
    if binding is not None:
        result["market_prompt_binding"] = binding
    legacy = job.get("legacy_v5_exact_resume")
    if isinstance(legacy, dict):
        result["legacy_v5_exact_resume_sha256"] = canonical_json_sha256(legacy)
    return result


def paid_request_signature(row):
    references = [
        {"role": item.get("role"), "url": item.get("url"), "sha256": item.get("sha256")}
        for item in row.get("reference_bindings", []) if isinstance(item, dict)
    ]
    payload = {
        "references": references,
        "asset": row.get("asset_id"),
        "prompt": row.get("prompt_sha256"),
        "duration": row.get("duration"),
        "resolution": row.get("resolution"),
        "ratio": row.get("ratio"),
        "model": row.get("generation_model", "sd2"),
    }
    # For Zibuyu apparel, the paid fingerprint also commits to the exact
    # director-validation receipt. Generic standalone PopBoom signatures stay
    # byte-for-byte compatible with the historical payload above.
    if row.get("job_kind") == ZIBUYU_APPAREL_KIND:
        receipt = row.get("director_receipt")
        payload["job_kind"] = ZIBUYU_APPAREL_KIND
        payload["director_receipt"] = {
            key: receipt.get(key) if isinstance(receipt, dict) else None
            for key in (
                "variant_id", "timeline_id", "timeline_version",
                "batch_compile_sha256", "schema_version", "quality_contract_id",
                "serializer_id", "quality_plan_sha256", "research_bundle_sha256", "canonical_beats_sha256",
                "compiled_text_sha256", "director_valid",
                "eligible_for_new_submission",
            )
        }
        contract = director_receipt_contract(receipt)
        if contract in {"v7", "historical_v6"}:
            payload["director_receipt"].update({
                key: receipt.get(key) for key in (
                    "market_prompt_contract_id", "market_prompt_profile_id",
                    "market_prompt_profile_sha256", "generation_controls_sha256",
                    "voiceover_review_sha256", "market", "voiceover_language",
                )
            })
            if contract == "v7":
                payload["director_receipt"].update({
                    "deadline_contract_id": receipt.get("deadline_contract_id"),
                    "three_layer_deadlines_sha256": receipt.get("three_layer_deadlines_sha256"),
                })
        elif contract == "legacy_v5" and isinstance(row.get("legacy_v5_exact_resume"), dict):
            payload["legacy_v5_exact_resume"] = row.get("legacy_v5_exact_resume")
        # canonical_prompt_v5/v6 commits to the exact outbound MCP request and to
        # every identity-bearing image byte digest. Historical terminal v4
        # rows omit these fields and intentionally retain their old signature
        # so they remain readable for poll/report/audit only.
        if (isinstance(receipt, dict) and
                receipt.get("serializer_id") in {
                    LEGACY_DIRECTOR_SERIALIZER_ID, HISTORICAL_V6_DIRECTOR_SERIALIZER_ID,
                    DIRECTOR_SERIALIZER_ID,
                }) or any(
                    key in row for key in (
                        "asset_identity_sha256", "asset_identity_version",
                        "outbound_request", "outbound_request_sha256",
                    )
                ):
            payload["asset_identity_sha256"] = row.get("asset_identity_sha256")
            payload["asset_identity_version"] = row.get("asset_identity_version")
            payload["reference_bindings"] = row.get("reference_bindings")
            payload["outbound_request"] = row.get("outbound_request")
            payload["outbound_request_sha256"] = row.get("outbound_request_sha256")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def has_rate_limit_signal(row):
    text = " ".join(str(row.get(key, "")) for key in ("error_code", "error_message")).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return bool(re.search(r"(?:^|_)429(?:_|$)", normalized)) or any(
        marker in normalized for marker in RATE_LIMIT_MARKERS
    )


def error_sort_key(error):
    """Match the contract's deterministic error-category precedence."""
    code = str(error.get("code", ""))
    category_tokens = (
        (1, ("sku", "variant", "reference", "artifact_binding", "fingerprint",
             "paid_request", "job_key", "duplicate_record", "duplicate_artifact")),
        (2, ("timeline", "projection")),
        (3, ("differentiation", "creative_overlap")),
        (4, ("endpoint", "transport", "route", "capability", "model", "asset",
             "balance", "execution_policy", "user_concurrency", "max_inflight")),
        (6, ("poll", "download", "video_url", "delivery", "completion_report")),
        (5, ("submission", "wave", "inflight", "rate_limit", "resubmit",
             "record_id", "state_field", "next_action", "effective_max")),
    )
    category = 0
    for candidate, tokens in category_tokens:
        if any(token in code for token in tokens):
            category = candidate
            break
    return (category, code, str(error.get("path", "")), str(error.get("message", "")))


def load_director_module():
    path = (Path(__file__).resolve().parents[2] / "seedance-ugc-cn-director" /
            "scripts" / "validate_batch_compile.py")
    if not path.is_file():
        raise ValueError(f"sibling director validator is unavailable: {path}")
    module_name = "zibuyu_seedance_batch_validator_for_popboom"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load sibling director validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "validate", None)):
        raise ValueError("sibling director validator does not expose validate(document)")
    return module


def batch_compile_evidence_from_bytes(raw_bytes, source_path="<memory>", module=None):
    try:
        payload = raw_bytes.decode("utf-8-sig")
    except AttributeError as exc:
        raise ValueError("batch compile evidence must be raw bytes") from exc
    document = load_json_text(payload)
    director = module or load_director_module()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    result = director.validate(document)
    attach_receipts = getattr(director, "_attach_director_receipts", None)
    if not callable(attach_receipts):
        raise ValueError("sibling director validator does not expose receipt attachment")
    attach_receipts(result, document, digest)
    return {
        "path": str(source_path), "document": document, "sha256": digest,
        "director_result": result,
    }


def load_batch_compile_evidence(path):
    source = Path(path).expanduser().resolve()
    return batch_compile_evidence_from_bytes(source.read_bytes(), source, load_director_module())


class Validator:
    def __init__(self, registry, runtime, execution_policy, previous_ledger=None,
                 batch_compile_evidence=None, legacy_source_ledger=None):
        self.registry = registry
        self.runtime = runtime
        self.execution_policy = execution_policy
        self.previous_ledger = previous_ledger
        self.batch_compile_evidence = batch_compile_evidence
        self.legacy_source_ledger = legacy_source_ledger
        self.errors = []
        policy_wave = execution_policy.get("wave_control", {})
        self.base_max_inflight = policy_wave.get("max_inflight", 12)
        self.effective_max_inflight = self.base_max_inflight
        self.max_inflight_source = policy_wave.get("max_inflight_source", "user")
        self.planned_job_count = 0
        self.planned_wave_count = 0
        self.next_wave_index = None
        self.available_submission_slots = 0
        self.paid_submission_allowed = False
        self.authorized_submission_job_keys = []
        self.authorized_outbound_requests = []
        self.history_verified = False
        self.streaming_parent_checked = False

    def add(self, code, path, message):
        if len(self.errors) < 200:
            self.errors.append({"code": code, "path": path, "message": message})

    def req_str(self, obj, key, path):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            self.add("required_string", f"{path}.{key}", "must be a non-empty string")
            return None
        return value

    def req_list(self, obj, key, path):
        value = obj.get(key)
        if not isinstance(value, list):
            self.add("required_list", f"{path}.{key}", "must be an array")
            return []
        return value

    def timestamp(self, obj, key, path, nullable=False):
        if key not in obj:
            self.add("missing_field", f"{path}.{key}", "field is required")
            return None
        value = obj[key]
        if value is None and nullable:
            return None
        parsed = parse_time(value)
        if parsed is None:
            self.add("invalid_timestamp", f"{path}.{key}", "must be an RFC 3339 timestamp with timezone")
        return parsed

    def scan_secrets(self, value, path="$"):
        stack = [(value, path)]
        while stack:
            current, current_path = stack.pop()
            if isinstance(current, dict):
                for key, child in current.items():
                    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
                    child_path = f"{current_path}.{key}"
                    if ("base64" in normalized or normalized in SENSITIVE_KEYS or
                            normalized.endswith("_token")):
                        self.add("prohibited_secret_field", child_path, "credential/base64 fields must not be persisted")
                    stack.append((child, child_path))
            elif isinstance(current, list):
                stack.extend((child, f"{current_path}[{i}]") for i, child in enumerate(current))
            elif isinstance(current, str):
                if re.search(r"(?i)\bbearer\s+\S+|data:[^,;]+;base64,", current):
                    self.add("prohibited_secret_value", current_path, "bearer/base64 data must not be persisted")
                elif re.search(r"(?i)[?&](?:access_token|api_key|token|secret|password)=", current):
                    self.add("prohibited_secret_value", current_path, "credential-bearing URLs must not be persisted")
                elif len(current) >= 256 and len(current) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", current):
                    self.add("prohibited_base64_value", current_path, "probable base64 payload must not be persisted")

    def validate_history(self, ledger, jobs):
        start_errors = len(self.errors)
        revision = ledger.get("ledger_revision")
        previous_hash = ledger.get("previous_ledger_sha256")
        if not is_int(revision) or revision < 0:
            self.add("invalid_ledger_revision", "$.ledger_revision",
                     "must be a non-negative integer")
            return
        if revision == 0:
            if previous_hash is not None:
                self.add("unexpected_previous_ledger_sha256", "$.previous_ledger_sha256",
                         "revision 0 must not claim a previous snapshot")
            if self.previous_ledger is not None:
                self.add("unexpected_previous_ledger", "$previous",
                         "revision 0 must not be validated against a previous snapshot")
            return
        if not isinstance(previous_hash, str) or not SHA256_RE.fullmatch(previous_hash):
            self.add("invalid_previous_ledger_sha256", "$.previous_ledger_sha256",
                     "revision above 0 requires the canonical SHA-256 of the previous snapshot")
        previous = self.previous_ledger
        if not isinstance(previous, dict):
            self.add("missing_previous_ledger", "$previous",
                     "revision above 0 requires --previous-ledger for history verification")
            return
        self.scan_secrets(previous, "$previous")
        if previous_hash != canonical_json_sha256(previous):
            self.add("previous_ledger_sha256_mismatch", "$.previous_ledger_sha256",
                     "must hash the exact canonical previous ledger snapshot")
        if previous.get("ledger_revision") != revision - 1:
            self.add("ledger_revision_gap", "$.ledger_revision",
                     "ledger revisions must advance by exactly one")
        for key in ("run_id", "sku_family_id", "batch_compile_id", "created_at"):
            if previous.get(key) != ledger.get(key):
                self.add("ledger_history_identity_mismatch", f"$.{key}",
                         "run identity fields are immutable across revisions")

        previous_wave = previous.get("wave_control")
        current_wave = ledger.get("wave_control")
        if not isinstance(previous_wave, dict) or not isinstance(current_wave, dict):
            self.add("invalid_history_wave_control", "$.wave_control",
                     "both revisions require wave_control objects")
        else:
            immutable_wave_fields = (
                "policy_id", "max_inflight", "max_inflight_source",
                "max_inflight_evidence", "max_jobs_per_wave", "submission_strategy",
                "fill_wave_without_wait", "next_wave_release", "planned_job_count",
                "submission_waves",
            )
            for key in immutable_wave_fields:
                if previous_wave.get(key) != current_wave.get(key):
                    self.add("wave_history_mutation", f"$.wave_control.{key}",
                             "batch wave plan and configured concurrency are immutable within a run")
            previous_events = previous_wave.get("rate_limit_events")
            current_events = current_wave.get("rate_limit_events")
            if not isinstance(previous_events, list) or not isinstance(current_events, list):
                self.add("invalid_rate_limit_event_history", "$.wave_control.rate_limit_events",
                         "both revisions require rate_limit_events arrays")
            elif current_events[:len(previous_events)] != previous_events:
                self.add("rate_limit_event_history_rollback", "$.wave_control.rate_limit_events",
                         "rate-limit events are append-only and previous events must remain an exact prefix")

        previous_jobs = previous.get("jobs")
        if not isinstance(previous_jobs, list):
            self.add("invalid_previous_jobs", "$previous.jobs", "must be an array")
            previous_jobs = []
        previous_keys = [job.get("job_key") for job in previous_jobs if isinstance(job, dict)]
        current_keys = [job.get("job_key") for job in jobs if isinstance(job, dict)]
        if previous_keys != current_keys:
            self.add("job_history_mutation", "$.jobs",
                     "job order and membership are immutable within a run")
        current_by_key = {
            job.get("job_key"): job for job in jobs
            if isinstance(job, dict) and isinstance(job.get("job_key"), str)
        }
        for index, previous_job in enumerate(previous_jobs):
            if not isinstance(previous_job, dict):
                continue
            key = previous_job.get("job_key")
            current_job = current_by_key.get(key)
            if current_job is None:
                continue
            if previous_job.get("request_fingerprint") != current_job.get("request_fingerprint"):
                self.add("request_fingerprint_history_mutation",
                         f"$.jobs[{index}].request_fingerprint",
                         "paid request fingerprint is immutable within a run")
            previous_record = previous_job.get("record_id")
            if previous_record is not None:
                if current_job.get("record_id") != previous_record:
                    self.add("accepted_record_history_mutation", f"$.jobs[{index}].record_id",
                             "an accepted record_id may never be cleared or replaced")
                if current_job.get("state") in {"planned", "submission_started",
                                                "submission_unknown", "blocked"}:
                    self.add("accepted_job_state_regression", f"$.jobs[{index}].state",
                             "an accepted job may never return to a submission state")
            if (previous_job.get("state") in TERMINAL_STATES and
                    current_job.get("state") != previous_job.get("state")):
                self.add("terminal_job_state_regression", f"$.jobs[{index}].state",
                         "terminal job state is immutable within a run")
        self.history_verified = len(self.errors) == start_errors

    def capability(self, ledger):
        cap = ledger.get("capability_snapshot")
        if not isinstance(cap, dict):
            self.add("invalid_capability_snapshot", "$.capability_snapshot", "must be an object")
            return set(), None, False
        self.timestamp(cap, "captured_at", "$.capability_snapshot", False)
        cap_version = self.req_str(cap, "server_version", "$.capability_snapshot")
        top_version = ledger.get("server_version")
        if cap_version and top_version and cap_version != top_version:
            self.add("server_version_mismatch", "$.server_version", "must match capability_snapshot.server_version")
        if ledger.get("server_name") != "PopBoom":
            self.add("server_name_mismatch", "$.server_name", "must equal the canonical PopBoom connection name")
        raw_tools = cap.get("tool_names")
        tools = set()
        if isinstance(raw_tools, list):
            for i, tool in enumerate(raw_tools):
                name = tool if isinstance(tool, str) else tool.get("name") if isinstance(tool, dict) else None
                if not isinstance(name, str) or not name:
                    self.add("invalid_tool_name", f"$.capability_snapshot.tool_names[{i}]", "tool name must be a non-empty string")
                elif name in tools:
                    self.add("duplicate_tool_name", f"$.capability_snapshot.tool_names[{i}]", "tool names must be unique")
                else:
                    tools.add(name)
        else:
            self.add("missing_tool_names", "$.capability_snapshot.tool_names", "tool names must be persisted")
        for name in sorted(REQUIRED_TOOLS - tools):
            self.add("missing_required_tool", "$.capability_snapshot.tool_names", f"missing required tool: {name}")
        generated = cap.get("generate_video")
        if not isinstance(generated, dict):
            self.add("missing_generate_video_capability", "$.capability_snapshot.generate_video", "relevant schema summary is required")
            generated = {}
        schema_hash = generated.get("schema_sha256")
        if not isinstance(schema_hash, str) or not SHA256_RE.fullmatch(schema_hash):
            self.add("invalid_schema_sha256", "$.capability_snapshot.generate_video.schema_sha256", "normalized live schema hash is required")
        durations = generated.get("declared_durations", [])
        if not isinstance(durations, list) or any(not is_int(x) or x <= 0 for x in durations):
            self.add("invalid_declared_durations", "$.capability_snapshot.generate_video.declared_durations", "must be an array of positive integers")
            durations = []
        elif len(set(durations)) != len(durations):
            self.add("duplicate_declared_duration", "$.capability_snapshot.generate_video.declared_durations", "durations must be unique")
        for key in ("declared_resolutions", "declared_ratios"):
            values = generated.get(key)
            if not isinstance(values, list) or not values or any(not isinstance(x, str) or not x for x in values):
                self.add("invalid_capability_values", f"$.capability_snapshot.generate_video.{key}", "must be a non-empty string array")
            elif len(set(values)) != len(values):
                self.add("duplicate_capability_value", f"$.capability_snapshot.generate_video.{key}", "values must be unique")
        download_available = cap.get("download_video_available")
        if not isinstance(download_available, bool):
            self.add("invalid_boolean", "$.capability_snapshot.download_video_available", "must be boolean")
        return set(durations), cap_version, download_available is True

    def observed_15s_capability(self):
        generated = self.runtime.get("generate_video")
        if not isinstance(generated, dict):
            return None
        duration_15 = generated.get("duration_15")
        if (not isinstance(duration_15, dict) or
                duration_15.get("mode") != "mcp_observed" or
                duration_15.get("valid_for_new_submission") is not True):
            return None
        evidence = duration_15.get("accepted_evidence")
        if not isinstance(evidence, dict):
            return None
        if (not isinstance(evidence.get("evidence_id"), str) or
                not evidence.get("evidence_id") or
                parse_time(evidence.get("observed_at")) is None or
                not is_int(evidence.get("record_id")) or
                evidence.get("record_id") <= 0 or
                not isinstance(evidence.get("task_id"), str) or
                not evidence.get("task_id") or
                evidence.get("requested_duration_seconds") != 15 or
                not isinstance(evidence.get("resolution"), str) or
                not evidence.get("resolution") or
                not isinstance(evidence.get("ratio"), str) or
                not evidence.get("ratio") or
                evidence.get("model") != "sd2" or
                not isinstance(evidence.get("cost_points"), (int, float)) or
                isinstance(evidence.get("cost_points"), bool) or
                evidence.get("cost_points") <= 0 or
                evidence.get("terminal_status") != "succeeded"):
            return None
        return evidence

    def route_evidence(self, ledger, cap_version):
        evidence = ledger.get("route_evidence")
        if not isinstance(evidence, dict):
            self.add("invalid_route_evidence", "$.route_evidence", "must be an object")
            return
        route = ledger.get("route")
        expected = {"mcp_declared": "declared", "mcp_observed": "accepted_observed",
                    "mcp_provisional": "provisional_observed",
                    "ui": "ui_operable", "blocked": "blocked"}.get(route)
        if evidence.get("kind") != expected:
            self.add("route_evidence_mismatch", "$.route_evidence.kind", "must match the selected route")
        eversion = evidence.get("evidence_server_version")
        if route in {"mcp_declared", "mcp_observed", "mcp_provisional"} and eversion != cap_version:
            self.add("route_evidence_version_mismatch", "$.route_evidence.evidence_server_version", "MCP evidence and capability versions must be identical")
        if eversion is not None and (not isinstance(eversion, str) or not eversion):
            self.add("invalid_evidence_version", "$.route_evidence.evidence_server_version", "must be null or a non-empty string")
        observation = evidence.get("observation_id")
        measured = evidence.get("observed_duration_seconds")
        if route == "mcp_observed":
            baseline = self.observed_15s_capability()
            if baseline is None:
                self.add(
                    "observed_15s_baseline_unavailable",
                    "$.route_evidence",
                    "runtime-capabilities.json must contain a valid successful 15s observation enabled for new submissions",
                )
            else:
                expected_values = {
                    "observation_id": baseline["evidence_id"],
                    "observed_duration_seconds": baseline["requested_duration_seconds"],
                    "accepted_record_id": baseline["record_id"],
                    "accepted_task_id": baseline["task_id"],
                    "accepted_terminal_status": baseline["terminal_status"],
                }
                for key, expected_value in expected_values.items():
                    if evidence.get(key) != expected_value:
                        self.add(
                            "observed_15s_evidence_mismatch",
                            f"$.route_evidence.{key}",
                            "must exactly match the accepted 15s runtime capability baseline",
                        )
        elif route == "mcp_provisional":
            if not isinstance(observation, str) or not observation:
                self.add("missing_provisional_observation", "$.route_evidence.observation_id", "provisional route requires an observation ID")
            if not isinstance(measured, (int, float)) or isinstance(measured, bool) or measured <= 0:
                self.add("missing_provisional_observation", "$.route_evidence.observed_duration_seconds", "provisional route requires a positive measured duration")
        else:
            if observation is not None and (not isinstance(observation, str) or not observation):
                self.add("invalid_observation_id", "$.route_evidence.observation_id", "must be null or a non-empty string")
            if measured is not None and (not isinstance(measured, (int, float)) or isinstance(measured, bool) or measured <= 0):
                self.add("invalid_observed_duration", "$.route_evidence.observed_duration_seconds", "must be null or positive")
        self.timestamp(evidence, "verified_at", "$.route_evidence", False)

    def validate(self, ledger):
        if not isinstance(ledger, dict):
            self.add("invalid_root", "$", "ledger must be a JSON object")
            return self.result(None, None, 0, 0, 0)
        self.scan_secrets(ledger)
        for key in ("schema_version", "run_id", "sku_family_id", "batch_compile_id", "endpoint",
                    "transport", "server_name", "server_version", "route"):
            self.req_str(ledger, key, "$")
        if ledger.get("schema_version") != "1.0":
            self.add("unsupported_schema_version", "$.schema_version", "must equal 1.0")
        workflow_kind = ledger.get("workflow_kind")
        if workflow_kind is not None and workflow_kind not in WORKFLOW_KINDS:
            self.add("invalid_workflow_kind", "$.workflow_kind",
                     "when present, workflow_kind must be zibuyu_apparel or generic_popboom")
        canonical = self.runtime.get("endpoint")
        if ledger.get("endpoint") != canonical or ledger.get("endpoint") != self.registry.get("endpoint", canonical):
            self.add("invalid_endpoint", "$.endpoint", "must equal the canonical registry/runtime endpoint")
        if ledger.get("transport") != self.runtime.get("transport") or ledger.get("transport") != "streamable_http":
            self.add("invalid_transport", "$.transport", "must equal streamable_http")
        route = ledger.get("route")
        if route not in ROUTES:
            self.add("invalid_route", "$.route", "must be mcp_declared, mcp_observed, mcp_provisional, ui, or blocked")
        created = self.timestamp(ledger, "created_at", "$", False)
        updated = self.timestamp(ledger, "updated_at", "$", False)
        if created and updated and updated < created:
            self.add("timestamp_order", "$.updated_at", "must not precede created_at")
        durations, cap_version, download_available = self.capability(ledger)
        self.route_evidence(ledger, cap_version)
        self.validate_balance(ledger)
        jobs = self.req_list(ledger, "jobs", "$")
        requires_upload_provenance = any(
            isinstance(row, dict) and row.get("state") in NEW_SUBMISSION_STATES for row in jobs
        )
        models = self.validate_models(self.req_list(ledger, "models", "$"))
        artifacts = self.validate_artifacts(
            self.req_list(ledger, "artifacts", "$"), requires_upload_provenance
        )
        if not models:
            self.add("empty_models", "$.models", "at least one validated model is required")
        if not artifacts:
            self.add("empty_artifacts", "$.artifacts", "at least one artifact is required")
        if not jobs:
            self.add("empty_jobs", "$.jobs", "at least one job is required")
        self.validate_jobs(ledger, jobs, models, artifacts, durations, download_available, created, updated)
        self.validate_history(ledger, jobs)
        self.validate_concurrency(ledger, jobs)
        return self.result(ledger.get("run_id"), route, len(models), len(artifacts), len(jobs))

    def validate_legacy_v5_exact_resume(self, ledger, row, path):
        """Authorize only a byte-stable, explicitly approved, previously validated v5 package."""
        auth = row.get("legacy_v5_exact_resume")
        if not isinstance(auth, dict):
            self.add(
                "legacy_v5_resume_authorization_required",
                f"{path}.legacy_v5_exact_resume",
                "schema 1.3/canonical_prompt_v5 is read-only unless an exact-resume authorization is bound",
            )
            return
        expected_keys = {
            "contract_id", "user_explicit", "authorization_scope", "authorization_id",
            "authorized_at", "source_run_id", "source_ledger_sha256", "source_job_key",
            "batch_compile_sha256", "director_receipt_sha256", "paid_package_sha256",
            "prior_director_validated", "prior_submission_eligible", "unchanged_assertions",
        }
        if set(auth) != expected_keys:
            self.add(
                "legacy_v5_resume_authorization_shape_mismatch",
                f"{path}.legacy_v5_exact_resume",
                "must contain exactly the deterministic legacy-v5 resume authorization fields",
            )
        if auth.get("contract_id") != LEGACY_V5_RESUME_CONTRACT_ID:
            self.add(
                "legacy_v5_resume_contract_mismatch",
                f"{path}.legacy_v5_exact_resume.contract_id",
                f"must equal {LEGACY_V5_RESUME_CONTRACT_ID}",
            )
        if auth.get("user_explicit") is not True:
            self.add(
                "legacy_v5_resume_user_approval_missing",
                f"{path}.legacy_v5_exact_resume.user_explicit",
                "an exact legacy resume requires explicit user approval",
            )
        if auth.get("authorization_scope") != "exact_existing_compile_no_rewrite":
            self.add(
                "legacy_v5_resume_scope_mismatch",
                f"{path}.legacy_v5_exact_resume.authorization_scope",
                "must authorize only the exact existing compile with no rewrite",
            )
        self.req_str(auth, "authorization_id", f"{path}.legacy_v5_exact_resume")
        self.timestamp(auth, "authorized_at", f"{path}.legacy_v5_exact_resume", False)
        self.req_str(auth, "source_run_id", f"{path}.legacy_v5_exact_resume")
        self.req_str(auth, "source_job_key", f"{path}.legacy_v5_exact_resume")
        for field in (
                "source_ledger_sha256", "batch_compile_sha256",
                "director_receipt_sha256", "paid_package_sha256"):
            value = auth.get(field)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                self.add(
                    "legacy_v5_resume_hash_invalid",
                    f"{path}.legacy_v5_exact_resume.{field}",
                    "must be a lowercase SHA-256 hex digest",
                )
        if auth.get("prior_director_validated") is not True:
            self.add(
                "legacy_v5_prior_validation_missing",
                f"{path}.legacy_v5_exact_resume.prior_director_validated",
                "the source v5 package must have a prior valid director receipt",
            )
        if auth.get("prior_submission_eligible") is not True:
            self.add(
                "legacy_v5_prior_eligibility_missing",
                f"{path}.legacy_v5_exact_resume.prior_submission_eligible",
                "the source v5 package must have been submission-eligible before the v6 migration",
            )
        assertions = auth.get("unchanged_assertions")
        if (not isinstance(assertions, dict) or set(assertions) != LEGACY_V5_RESUME_ASSERTIONS or
                any(assertions.get(field) is not True for field in LEGACY_V5_RESUME_ASSERTIONS)):
            self.add(
                "legacy_v5_unchanged_assertion_invalid",
                f"{path}.legacy_v5_exact_resume.unchanged_assertions",
                "every prompt, reference-order, identity, setting, receipt, and outbound assertion must be true",
            )

        receipt = row.get("director_receipt") if isinstance(row.get("director_receipt"), dict) else {}
        receipt_digest = canonical_json_sha256(receipt)
        package_digest = canonical_json_sha256(legacy_v5_paid_package(row))
        if auth.get("batch_compile_sha256") != ledger.get("batch_compile_sha256"):
            self.add(
                "legacy_v5_batch_compile_hash_mismatch",
                f"{path}.legacy_v5_exact_resume.batch_compile_sha256",
                "must equal the exact current ledger batch_compile_sha256",
            )
        if auth.get("director_receipt_sha256") != receipt_digest:
            self.add(
                "legacy_v5_director_receipt_hash_mismatch",
                f"{path}.legacy_v5_exact_resume.director_receipt_sha256",
                "must hash the exact unchanged legacy director_receipt",
            )
        if auth.get("paid_package_sha256") != package_digest:
            self.add(
                "legacy_v5_paid_package_hash_mismatch",
                f"{path}.legacy_v5_exact_resume.paid_package_sha256",
                "must hash the exact unchanged prompt, references/order, identity, settings, receipt, and outbound request",
            )

        source = self.legacy_source_ledger
        if not isinstance(source, dict):
            self.add(
                "legacy_v5_source_ledger_required",
                "$legacy_source_ledger",
                "exact v5 resume requires --legacy-source-ledger containing the prior planned package",
            )
            return
        self.scan_secrets(source, "$legacy_source_ledger")
        if canonical_json_sha256(source) != auth.get("source_ledger_sha256"):
            self.add(
                "legacy_v5_source_ledger_hash_mismatch",
                f"{path}.legacy_v5_exact_resume.source_ledger_sha256",
                "must hash the exact canonical prior ledger",
            )
        if source.get("schema_version") != "1.0" or source.get("workflow_kind") != ZIBUYU_APPAREL_KIND:
            self.add(
                "legacy_v5_source_ledger_invalid",
                "$legacy_source_ledger",
                "source must be a schema-1.0 Zibuyu apparel ledger",
            )
        if source.get("run_id") != auth.get("source_run_id"):
            self.add(
                "legacy_v5_source_run_mismatch",
                f"{path}.legacy_v5_exact_resume.source_run_id",
                "must equal the exact source ledger run_id",
            )
        if source.get("run_id") == ledger.get("run_id"):
            self.add(
                "legacy_v5_resume_run_reuse",
                "$.run_id",
                "create a new resume run; do not mutate an existing v5 ledger fingerprint in place",
            )
        for field in ("sku_family_id", "batch_compile_id", "batch_compile_sha256"):
            if source.get(field) != ledger.get(field):
                self.add(
                    "legacy_v5_source_batch_mismatch",
                    f"$legacy_source_ledger.{field}",
                    "source and resume ledgers must bind the same exact batch compile",
                )
        source_jobs = source.get("jobs") if isinstance(source.get("jobs"), list) else []
        matches = [
            item for item in source_jobs if isinstance(item, dict) and
            item.get("job_key") == auth.get("source_job_key")
        ]
        if len(matches) != 1:
            self.add(
                "legacy_v5_source_job_mismatch",
                f"{path}.legacy_v5_exact_resume.source_job_key",
                "must resolve exactly once in the prior ledger",
            )
            return
        source_job = matches[0]
        if (source_job.get("state") != "planned" or source_job.get("record_id") is not None or
                source_job.get("task_id") is not None or
                source_job.get("submission_started_at") is not None or
                source_job.get("submitted_at") is not None):
            self.add(
                "legacy_v5_source_already_submitted",
                "$legacy_source_ledger.jobs",
                "the exact-resume source job must be previously validated but never submitted",
            )
        if source_job.get("legacy_v5_exact_resume") is not None:
            self.add(
                "legacy_v5_resume_chaining_forbidden",
                "$legacy_source_ledger.jobs",
                "a legacy resume must point directly to the original v5 package, not another resume",
            )
        source_receipt = source_job.get("director_receipt")
        if director_receipt_contract(source_receipt) != "legacy_v5":
            self.add(
                "legacy_v5_source_receipt_invalid",
                "$legacy_source_ledger.jobs.director_receipt",
                "source job must carry the original schema-1.3 canonical_prompt_v5 receipt",
            )
        elif (source_receipt.get("director_valid") is not True or
              source_receipt.get("eligible_for_new_submission") is not True):
            self.add(
                "legacy_v5_source_not_prevalidated",
                "$legacy_source_ledger.jobs.director_receipt",
                "source receipt must prove prior valid and submission-eligible director validation",
            )
        if source_job.get("request_fingerprint") != sha256_text(paid_request_signature(source_job)):
            self.add(
                "legacy_v5_source_fingerprint_mismatch",
                "$legacy_source_ledger.jobs.request_fingerprint",
                "source job must retain its original deterministic paid-request fingerprint",
            )
        if canonical_json_sha256(source_receipt) != auth.get("director_receipt_sha256"):
            self.add(
                "legacy_v5_source_receipt_changed",
                "$legacy_source_ledger.jobs.director_receipt",
                "source and resume director receipts must be byte-equivalent after canonicalization",
            )
        source_package_digest = canonical_json_sha256(legacy_v5_paid_package(source_job))
        if (source_package_digest != package_digest or
                source_package_digest != auth.get("paid_package_sha256")):
            self.add(
                "legacy_v5_source_package_changed",
                "$legacy_source_ledger.jobs",
                "prompt, references/order, identity, settings, hashes, and outbound request must remain exact",
            )

    def validate_zero_human_identity_audit(self, owner, path, expected_sha256):
        audit = owner.get("identity_cue_audit") if isinstance(owner, dict) else None
        expected_keys = {
            "audit_version", "audited_sha256", "inspection_method", "reviewed_at", "passed",
            *ZERO_HUMAN_IDENTITY_CUE_FIELDS,
        }
        if not isinstance(audit, dict) or set(audit) != expected_keys:
            self.add("identity_cue_audit_invalid", f"{path}.identity_cue_audit",
                     "new apparel work requires the complete zero-human-identity cue audit")
            return None
        if (audit.get("audit_version") != ZERO_HUMAN_IDENTITY_AUDIT_VERSION or
                audit.get("audited_sha256") != expected_sha256 or
                audit.get("inspection_method") != ZERO_HUMAN_IDENTITY_INSPECTION_METHOD or
                audit.get("passed") is not True or
                any(audit.get(field) is not False for field in ZERO_HUMAN_IDENTITY_CUE_FIELDS)):
            self.add("identity_cue_audit_failed", f"{path}.identity_cue_audit",
                     "the audit must bind the exact garment bytes and mark every human-identity cue false")
        self.timestamp(audit, "reviewed_at", f"{path}.identity_cue_audit", False)
        digest = canonical_json_sha256(audit)
        if owner.get("identity_cue_audit_sha256") != digest:
            self.add("identity_cue_audit_sha256_mismatch", f"{path}.identity_cue_audit_sha256",
                     "must equal the canonical SHA-256 of the complete cue audit")
        return digest

    def validate_zibuyu_outbound_request(self, row, path, artifacts, apparel_bindings):
        """Validate one canonical_prompt_v5/v6 request from identity bytes to MCP args."""
        preset = self.registry.get("presets", {}).get(row.get("model_preset"))
        if not isinstance(preset, dict):
            preset = {}
        identity_digest = row.get("asset_identity_sha256")
        identity_version = row.get("asset_identity_version")
        if not isinstance(identity_digest, str) or not SHA256_RE.fullmatch(identity_digest):
            self.add("invalid_asset_identity_sha256", f"{path}.asset_identity_sha256",
                     "new Zibuyu apparel work requires the verified fixed-portrait image SHA-256")
        expected_digest = preset.get("identity_image_sha256")
        expected_version = preset.get("identity_version")
        if (not isinstance(expected_digest, str) or
                not SHA256_RE.fullmatch(expected_digest) or
                not isinstance(expected_version, str) or not expected_version.strip()):
            self.add("model_identity_pin_missing", f"$registry.presets.{row.get('model_preset')}",
                     "the fixed-model registry must pin identity_image_sha256 and identity_version before new apparel payment")
        else:
            if identity_digest != expected_digest:
                self.add("asset_identity_sha256_mismatch", f"{path}.asset_identity_sha256",
                         "must equal the selected fixed-model registry identity image SHA-256")
            if identity_version != expected_version:
                self.add("asset_identity_version_mismatch", f"{path}.asset_identity_version",
                         "must equal the selected fixed-model registry identity version")

        if len(apparel_bindings) != 1:
            return
        garment = apparel_bindings[0]
        current_identity_gate = director_receipt_contract(row.get("director_receipt")) == "v7"
        if garment.get("human_identity_pixels_absent") is not True:
            self.add("apparel_human_identity_pixels_present",
                     f"{path}.reference_bindings",
                     "new apparel references must declare human_identity_pixels_absent true")
        garment_artifacts = [
            artifact for artifact in artifacts
            if artifact["id"] == garment.get("artifact_id")
        ]
        if (len(garment_artifacts) != 1 or
                garment_artifacts[0].get("human_identity_pixels_absent") is not True):
            self.add("apparel_artifact_human_identity_pixels_present",
                     f"{path}.reference_bindings",
                     "the bound apparel artifact must independently declare human_identity_pixels_absent true")
        if current_identity_gate:
            binding_audit_sha256 = self.validate_zero_human_identity_audit(
                garment, f"{path}.reference_bindings[apparel_three_view]", garment.get("sha256"))
            artifact_audit_sha256 = None
            if len(garment_artifacts) == 1:
                artifact_audit_sha256 = self.validate_zero_human_identity_audit(
                    garment_artifacts[0], "$artifacts[bound_apparel_three_view]", garment.get("sha256"))
            if (binding_audit_sha256 is not None and artifact_audit_sha256 is not None and
                    binding_audit_sha256 != artifact_audit_sha256):
                self.add("identity_cue_audit_binding_mismatch", f"{path}.reference_bindings",
                         "the batch reference, ledger binding, and uploaded artifact must use the same cue audit")

        outbound = row.get("outbound_request")
        if not isinstance(outbound, dict):
            self.add("missing_outbound_request", f"{path}.outbound_request",
                     "new Zibuyu apparel work requires the exact ordered MCP request")
            outbound = {}
        if set(outbound) != OUTBOUND_REQUEST_KEYS:
            self.add("outbound_request_shape_mismatch", f"{path}.outbound_request",
                     "must contain exactly server_name, tool_name, and arguments")
        if outbound.get("server_name") != "PopBoom":
            self.add("outbound_server_mismatch", f"{path}.outbound_request.server_name",
                     "must equal PopBoom")
        if outbound.get("tool_name") != "generate_video":
            self.add("outbound_tool_mismatch", f"{path}.outbound_request.tool_name",
                     "must equal generate_video")
        arguments = outbound.get("arguments")
        if not isinstance(arguments, dict):
            self.add("invalid_outbound_arguments", f"{path}.outbound_request.arguments",
                     "must be the exact generate_video argument object")
            arguments = {}
        argument_keys = set(arguments)
        if (not OUTBOUND_REQUIRED_ARGUMENT_KEYS.issubset(argument_keys) or
                not argument_keys.issubset(OUTBOUND_ARGUMENT_KEYS)):
            self.add("outbound_argument_shape_mismatch", f"{path}.outbound_request.arguments",
                     "must contain the six canonical video arguments plus required product_info and no other fields")
        expected_arguments = {
            "prompt": row.get("compiled_prompt"),
            "model": row.get("generation_model"),
            "duration": row.get("duration"),
            "resolution": row.get("resolution"),
            "ratio": row.get("ratio"),
        }
        for key, expected in expected_arguments.items():
            if arguments.get(key) != expected:
                self.add("outbound_parameter_mismatch",
                         f"{path}.outbound_request.arguments.{key}",
                         "must exactly equal the validated ledger parameter")
        if (not isinstance(arguments.get("product_info"), str) or
                arguments.get("product_info") != row.get("product_info")):
            self.add("outbound_parameter_mismatch",
                     f"{path}.outbound_request.arguments.product_info",
                     "product_info must be a string exactly copied from the ledger job")
        product_info = row.get("product_info")
        if not isinstance(product_info, str) or not product_info.strip():
            self.add("missing_product_info", f"{path}.product_info",
                     "new PopBoom video work requires product_info with brand 拓展平台 and a non-empty sku")
        else:
            try:
                parsed_product_info = json.loads(product_info)
            except json.JSONDecodeError:
                parsed_product_info = None
                self.add("invalid_product_info", f"{path}.product_info",
                         "must be a JSON string with exactly brand and sku")
            if isinstance(parsed_product_info, dict):
                if set(parsed_product_info) != PRODUCT_INFO_REQUIRED_KEYS:
                    self.add("invalid_product_info", f"{path}.product_info",
                             "must contain exactly brand and sku")
                if parsed_product_info.get("brand") != PRODUCT_INFO_FIXED_BRAND:
                    self.add("invalid_product_info_brand", f"{path}.product_info.brand",
                             "brand must equal 拓展平台")
                sku = parsed_product_info.get("sku")
                if not isinstance(sku, str) or not sku.strip():
                    self.add("missing_product_info_sku", f"{path}.product_info.sku",
                             "sku must be the user-provided batch item number")
        if row.get("generation_model") != "sd2":
            self.add("invalid_generation_model", f"{path}.generation_model",
                     "new Zibuyu apparel video requests require explicit generation_model sd2")
        if (row.get("submission_transport") != "mcp_streamable_http" or
                row.get("route") not in {"mcp_declared", "mcp_observed"}):
            self.add("outbound_transport_mismatch", f"{path}.submission_transport",
                     "the validator-authorized outbound_request requires the MCP streamable-HTTP route")
        expected_urls = [f"asset://{row.get('asset_id')}"] + [
            binding.get("url") for binding in row.get("reference_bindings", [])
            if isinstance(binding, dict)
        ]
        if arguments.get("ref_image_urls") != expected_urls:
            self.add("outbound_reference_order_mismatch",
                     f"{path}.outbound_request.arguments.ref_image_urls",
                     "must be the fixed-model asset first, followed by garment references in binding order")
        prompt = row.get("compiled_prompt")
        identity_role_v5 = (isinstance(prompt, str) and re.search(
            r"(?im)^(?=[^\r\n]*@Image1)(?=[^\r\n]*identity only)(?![^\r\n]*garment identity only)[^\r\n]*$",
            prompt,
        ))
        garment_role_v5 = (isinstance(prompt, str) and re.search(
            r"(?im)^(?=[^\r\n]*@Image2)(?=[^\r\n]*garment identity only)[^\r\n]*$",
            prompt,
        ))
        identity_role_v6 = (isinstance(prompt, str) and re.search(
            r"(?im)^(?=[^\r\n]*@Image1)(?=[^\r\n]*only for the fixed creator's)(?=[^\r\n]*never use it as garment evidence)[^\r\n]*$",
            prompt,
        ))
        garment_role_v6 = (isinstance(prompt, str) and re.search(
            r"(?im)^(?=[^\r\n]*@Image2)(?=[^\r\n]*only for the garment's exact)(?=[^\r\n]*no usable human identity)[^\r\n]*$",
            prompt,
        ))
        if not ((identity_role_v5 and garment_role_v5) or
                (identity_role_v6 and garment_role_v6)):
            self.add("outbound_prompt_role_map_mismatch", f"{path}.compiled_prompt",
                     "canonical_prompt_v5/v6 must map @Image1 to fixed-model identity only and @Image2 to garment identity only")
        outbound_digest = row.get("outbound_request_sha256")
        expected_outbound_digest = canonical_json_sha256(outbound)
        if (not isinstance(outbound_digest, str) or
                not SHA256_RE.fullmatch(outbound_digest) or
                outbound_digest != expected_outbound_digest):
            self.add("outbound_request_sha256_mismatch", f"{path}.outbound_request_sha256",
                     "must hash the exact sorted compact outbound_request JSON")

    def validate_zibuyu_director_receipt(self, ledger, row, path):
        """Hard-bind one Zibuyu paid job to an exact motion-rich compile receipt."""
        ledger_digest = ledger.get("batch_compile_sha256")
        if not isinstance(ledger_digest, str) or not SHA256_RE.fullmatch(ledger_digest):
            self.add("invalid_batch_compile_sha256", "$.batch_compile_sha256",
                     "Zibuyu apparel requires the lowercase SHA-256 of the exact batch-compile JSON")
        receipt = row.get("director_receipt")
        if not isinstance(receipt, dict):
            self.add("missing_director_receipt", f"{path}.director_receipt",
                     "Zibuyu apparel requires the exact director validation receipt")
            return None
        contract = director_receipt_contract(receipt)
        if contract is None:
            self.add(
                "unsupported_director_receipt_contract",
                f"{path}.director_receipt",
                "new work requires schema 1.4/canonical_prompt_v7 with three-layer deadlines; only strict exact-resume schema 1.3/v5 is accepted",
            )
        if receipt.get("variant_id") != row.get("variant_id"):
            self.add("director_variant_mismatch", f"{path}.director_receipt.variant_id",
                     "must equal the paid job variant_id")
        if receipt.get("timeline_id") != row.get("timeline_id"):
            self.add("director_timeline_mismatch", f"{path}.director_receipt.timeline_id",
                     "must equal the paid job timeline_id")
        if receipt.get("timeline_version") != row.get("timeline_version"):
            self.add("director_timeline_mismatch", f"{path}.director_receipt.timeline_version",
                     "must equal the paid job timeline_version")
        hash_fields = (
            V7_DIRECTOR_RECEIPT_HASH_FIELDS if contract == "v7"
            else V6_DIRECTOR_RECEIPT_HASH_FIELDS if contract == "historical_v6"
            else DIRECTOR_RECEIPT_HASH_FIELDS
        )
        for field in hash_fields:
            value = receipt.get(field)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                self.add("invalid_director_receipt_hash", f"{path}.director_receipt.{field}",
                         "must be a lowercase SHA-256 hex digest")
        if receipt.get("batch_compile_sha256") != ledger_digest:
            self.add("director_batch_compile_hash_mismatch",
                     f"{path}.director_receipt.batch_compile_sha256",
                     "must equal ledger.batch_compile_sha256 for the exact validated compile")
        if contract == "v7":
            expected_literals = {
                "schema_version": DIRECTOR_SCHEMA_VERSION,
                "quality_contract_id": DIRECTOR_QUALITY_CONTRACT_ID,
                "serializer_id": DIRECTOR_SERIALIZER_ID,
                "market_prompt_contract_id": MARKET_PROMPT_CONTRACT_ID,
                "deadline_contract_id": DIRECTOR_DEADLINE_CONTRACT_ID,
            }
        elif contract == "historical_v6":
            expected_literals = {
                "schema_version": HISTORICAL_V6_DIRECTOR_SCHEMA_VERSION,
                "quality_contract_id": HISTORICAL_V6_DIRECTOR_QUALITY_CONTRACT_ID,
                "serializer_id": HISTORICAL_V6_DIRECTOR_SERIALIZER_ID,
                "market_prompt_contract_id": MARKET_PROMPT_CONTRACT_ID,
            }
        else:
            expected_literals = {
                "schema_version": LEGACY_DIRECTOR_SCHEMA_VERSION,
                "quality_contract_id": LEGACY_DIRECTOR_QUALITY_CONTRACT_ID,
                "serializer_id": LEGACY_DIRECTOR_SERIALIZER_ID,
            }
        for field, expected in expected_literals.items():
            if receipt.get(field) != expected:
                self.add("legacy_director_receipt", f"{path}.director_receipt.{field}",
                         f"new Zibuyu apparel submissions require {field}={expected}")
        if contract in {"v7", "historical_v6"}:
            for field in ("market_prompt_profile_id", "market", "voiceover_language"):
                self.req_str(receipt, field, f"{path}.director_receipt")
            market_tuple = (
                receipt.get("market"), row.get("model_preset"),
                receipt.get("voiceover_language"), receipt.get("market_prompt_profile_id"),
            )
            if market_tuple not in MARKET_MODEL_LANGUAGE_PROFILES:
                self.add(
                    "market_model_language_profile_mismatch",
                    f"{path}.director_receipt",
                    "must be exactly US+美1/美2/美3+en-US+us_champion_v1 or DE+德1/德2/德3+de-DE+de_champion_v1",
                )
        if receipt.get("director_valid") is not True:
            self.add("director_validation_not_valid", f"{path}.director_receipt.director_valid",
                     "must be true from the current director validator result")
        if contract == "historical_v6" and row.get("state") in {"planned", "submission_started"}:
            self.add(
                "historical_director_receipt_read_only", f"{path}.director_receipt",
                "canonical_prompt_v6 receipts are historical read-only evidence and cannot authorize a new paid submission",
            )
        elif contract != "historical_v6" and receipt.get("eligible_for_new_submission") is not True:
            self.add("director_submission_not_eligible",
                     f"{path}.director_receipt.eligible_for_new_submission",
                     "must be true from the current director validator result")

        prompt = row.get("compiled_prompt")
        prompt_digest = row.get("prompt_sha256")
        receipt_digest = receipt.get("compiled_text_sha256")
        if receipt_digest != prompt_digest or (isinstance(prompt, str) and
                                                receipt_digest != sha256_text(prompt)):
            self.add("director_compiled_text_hash_mismatch",
                     f"{path}.director_receipt.compiled_text_sha256",
                     "must equal prompt_sha256 and hash the exact submitted compiled_prompt")

        # Each serializer retains deterministic creator-performance and proof markers.
        # Requiring the serializer-specific markers prevents a legacy/static prompt from
        # being relabelled while leaving full creative validation to the director.
        if isinstance(prompt, str):
            duration = row.get("duration")
            required_actions = (
                max(1, (duration + 3) // 4)
                if is_int(duration) and duration > 0 else 1
            )
            if contract in {"v7", "historical_v6"}:
                cut_count = sum(1 for line in prompt.splitlines() if line.startswith("Cut "))
                required_cuts = 3 if is_int(duration) and duration >= 15 else 1
                modern_markers = [
                    "Use @Image1 only for the fixed creator's face",
                    "Performance flow: recognition, animated proof, then close-detail conviction.",
                    "Each claim names one garment part or effect",
                    "Use exactly two naturally connected arms and hands",
                    "Carry gaze, weight, hand occupancy, props, and garment state forward.",
                ]
                if contract == "v7":
                    modern_markers.extend([
                        "GLOBAL NON-NEGOTIABLES:",
                        "SUBJECT, GARMENT, SCENE, LIGHT, AND SOUND NON-NEGOTIABLES:",
                        "Shot non-negotiables:",
                    ])
                prompt_shape_valid = (
                    all(marker in prompt for marker in modern_markers)
                    and required_cuts <= cut_count <= 4
                    and prompt.count("the readable proof resolves as") >= required_actions
                    and prompt.count("The left hand starts") >= required_actions
                    and prompt.count("the right hand starts") >= required_actions
                    and (contract != "v7" or prompt.count("Shot non-negotiables:") >= required_actions)
                )
            else:
                shot_count = sum(1 for line in prompt.splitlines() if line.startswith("Shot "))
                required_focused_proofs = min(3, required_actions)
                required_shots = 3 if is_int(duration) and duration >= 15 else 2
                v5_markers = [
                    "authentic creator-led UGC phone video delivered as one continuous friend-to-friend recommendation.",
                    "Macro phase recognition/result Hook",
                    "Macro phase animated proof",
                    "the filming hand continuously holds the phone",
                    "exactly two natural arms connected shoulder-to-wrist-to-hand",
                    "Actions remain sequential, one garment target per internal beat",
                ]
                if is_int(duration) and duration >= 15:
                    v5_markers.append("Macro phase close/detail conviction")
                prompt_shape_valid = (
                    all(marker in prompt for marker in v5_markers)
                    and required_shots <= shot_count <= 3
                    and prompt.count(" Focus: ") >= required_focused_proofs
                    and prompt.count("Action: ") >= required_actions
                    and prompt.count("Hand plan: ") >= required_actions
                )
            if not prompt_shape_valid:
                self.add("legacy_or_static_director_prompt", f"{path}.compiled_prompt",
                          "must be exact canonical_prompt_v5/v6/v7 evidence-bound creator output with duration-scaled "
                          "macro-phase, focused-proof, action, complete hand-plan, and current three-layer deadline markers")
        if contract == "legacy_v5":
            self.validate_legacy_v5_exact_resume(ledger, row, path)
        return contract

    def validate_zibuyu_batch_compile_evidence(self, ledger, row, path):
        evidence = self.batch_compile_evidence
        if not isinstance(evidence, dict):
            self.add("missing_batch_compile_evidence", "$batch_compile",
                     "Zibuyu submission_started authorization requires --batch-compile")
            return
        document = evidence.get("document")
        director_result = evidence.get("director_result")
        exact_digest = evidence.get("sha256")
        if not isinstance(document, dict) or not isinstance(director_result, dict):
            self.add("invalid_batch_compile_evidence", "$batch_compile",
                     "batch compile evidence must contain the parsed document and current director result")
            return
        if not isinstance(exact_digest, str) or not SHA256_RE.fullmatch(exact_digest):
            self.add("invalid_batch_compile_evidence", "$batch_compile.sha256",
                     "exact persisted batch file SHA-256 is invalid")
        if exact_digest != ledger.get("batch_compile_sha256"):
            self.add("batch_compile_file_hash_mismatch", "$.batch_compile_sha256",
                     "must equal the SHA-256 of the exact --batch-compile file bytes")
        receipt = row.get("director_receipt") if isinstance(row.get("director_receipt"), dict) else {}
        contract = director_receipt_contract(receipt)
        if contract == "legacy_v5":
            expected_schema = LEGACY_DIRECTOR_SCHEMA_VERSION
            expected_quality = LEGACY_DIRECTOR_QUALITY_CONTRACT_ID
        elif contract == "historical_v6":
            expected_schema = HISTORICAL_V6_DIRECTOR_SCHEMA_VERSION
            expected_quality = HISTORICAL_V6_DIRECTOR_QUALITY_CONTRACT_ID
        else:
            expected_schema = DIRECTOR_SCHEMA_VERSION
            expected_quality = DIRECTOR_QUALITY_CONTRACT_ID
        if document.get("schema_version") != expected_schema:
            self.add("batch_compile_schema_mismatch", "$batch_compile.schema_version",
                     f"receipt contract requires director schema {expected_schema}")
        if document.get("quality_contract_id") != expected_quality:
            self.add("batch_compile_quality_contract_mismatch", "$batch_compile.quality_contract_id",
                     f"receipt contract requires {expected_quality}")
        if contract in {"v7", "historical_v6"} and document.get("market_prompt_contract_id") != MARKET_PROMPT_CONTRACT_ID:
            self.add(
                "batch_compile_market_contract_mismatch",
                "$batch_compile.market_prompt_contract_id",
                f"new Zibuyu paid work requires {MARKET_PROMPT_CONTRACT_ID}",
            )
        if document.get("batch_compile_id") != ledger.get("batch_compile_id"):
            self.add("batch_compile_identity_mismatch", "$batch_compile.batch_compile_id",
                     "external batch_compile_id must equal the ledger identity")
        if document.get("sku_family_id") != ledger.get("sku_family_id"):
            self.add("batch_compile_identity_mismatch", "$batch_compile.sku_family_id",
                     "external sku_family_id must equal the ledger identity")
        batch_key = document.get("batch_key")
        if not isinstance(batch_key, dict):
            batch_key = {}
        external_job_bindings = (
            ("duration_seconds", "duration", "external_duration_mismatch"),
            ("aspect_ratio", "ratio", "external_aspect_ratio_mismatch"),
            ("resolution", "resolution", "external_resolution_mismatch"),
            ("model_preset", "model_preset", "external_model_preset_mismatch"),
        )
        for batch_field, job_field, code in external_job_bindings:
            if batch_key.get(batch_field) != row.get(job_field):
                self.add(
                    code,
                    f"{path}.{job_field}",
                    f"must exactly equal --batch-compile batch_key.{batch_field}",
                )
        streaming_release = document.get("streaming_release")
        if isinstance(streaming_release, dict):
            child_run_id = streaming_release.get("child_run_id")
            if not isinstance(child_run_id, str) or not child_run_id.strip():
                self.add("streaming_child_run_id_missing", "$batch_compile.streaming_release.child_run_id",
                         "streaming release must declare the immutable child run ID")
            elif ledger.get("run_id") != child_run_id:
                self.add("streaming_child_run_id_mismatch", "$.run_id",
                         "streaming child ledger run_id must equal batch compile streaming_release.child_run_id")
            self.validate_streaming_parent_release_set(ledger, evidence, streaming_release)
        if director_result.get("valid") is not True:
            self.add("external_director_validation_failed", "$batch_compile",
                     "current sibling director validator must return valid")
        if contract == "v7" and director_result.get("eligible_for_new_submission") is not True:
            self.add("external_director_validation_failed", "$batch_compile",
                     "schema 1.4/v7 requires current director eligibility for new submission")

        variants = document.get("variants")
        if not isinstance(variants, list):
            variants = []
        matches = [variant for variant in variants if isinstance(variant, dict) and
                   variant.get("variant_id") == row.get("variant_id")]
        if len(matches) != 1:
            self.add("external_variant_owner_mismatch", f"{path}.variant_id",
                     "job variant_id must resolve exactly once in --batch-compile")
            return
        variant = matches[0]
        if variant.get("color_name") != row.get("color_name"):
            self.add("external_variant_owner_mismatch", f"{path}.color_name",
                     "job color_name must belong to the same external variant")
        timeline = variant.get("canonical_timeline")
        if not isinstance(timeline, dict):
            timeline = {}
        if (timeline.get("timeline_id") != row.get("timeline_id") or
                timeline.get("timeline_version") != row.get("timeline_version")):
            self.add("external_timeline_mismatch", f"{path}.timeline_id",
                     "job timeline ID/version must equal the selected external variant timeline")
        renderings = variant.get("renderings")
        prompt = renderings.get("prompt") if isinstance(renderings, dict) else None
        if not isinstance(prompt, dict):
            self.add("external_prompt_missing", "$batch_compile.variants.renderings.prompt",
                     "selected variant must contain the validated compiled prompt")
            prompt = {}
        expected_serializer = (
            LEGACY_DIRECTOR_SERIALIZER_ID if contract == "legacy_v5"
            else HISTORICAL_V6_DIRECTOR_SERIALIZER_ID if contract == "historical_v6"
            else DIRECTOR_SERIALIZER_ID
        )
        if prompt.get("serializer_id") != expected_serializer:
            self.add(
                "external_prompt_serializer_mismatch",
                "$batch_compile.variants.renderings.prompt.serializer_id",
                f"must equal {expected_serializer} for the selected receipt contract",
            )
        expected_fields = {
            "variant_id": variant.get("variant_id"),
            "timeline_id": timeline.get("timeline_id"),
            "timeline_version": timeline.get("timeline_version"),
            "batch_compile_sha256": exact_digest,
            "schema_version": document.get("schema_version"),
            "quality_contract_id": document.get("quality_contract_id"),
            "serializer_id": prompt.get("serializer_id"),
            "quality_plan_sha256": prompt.get("quality_plan_sha256"),
            "research_bundle_sha256": prompt.get("research_bundle_sha256"),
            "canonical_beats_sha256": prompt.get("canonical_beats_sha256"),
            "compiled_text_sha256": prompt.get("compiled_text_sha256"),
            "director_valid": True,
            "eligible_for_new_submission": (
                True if contract != "historical_v6"
                else receipt.get("eligible_for_new_submission")
            ),
        }
        if contract in {"v7", "historical_v6"}:
            batch_profile_id = batch_key.get("market_prompt_profile_id")
            generation_controls = variant.get("generation_controls")
            voiceover_review = variant.get("voiceover_review")
            if not isinstance(generation_controls, dict):
                self.add(
                    "external_generation_controls_missing",
                    "$batch_compile.variants.generation_controls",
                    "schema 1.4 modern prompts require the structured generation-controls object",
                )
            elif prompt.get("generation_controls_sha256") != canonical_json_sha256(generation_controls):
                self.add(
                    "external_generation_controls_hash_mismatch",
                    "$batch_compile.variants.renderings.prompt.generation_controls_sha256",
                    "must hash the exact canonical generation_controls object",
                )
            if not isinstance(voiceover_review, list):
                self.add(
                    "external_voiceover_review_missing",
                    "$batch_compile.variants.voiceover_review",
                    "schema 1.4 modern prompts require the beat-aligned target-language and Chinese review array",
                )
            elif prompt.get("voiceover_review_sha256") != canonical_json_sha256(voiceover_review):
                self.add(
                    "external_voiceover_review_hash_mismatch",
                    "$batch_compile.variants.renderings.prompt.voiceover_review_sha256",
                    "must hash the exact canonical voiceover_review object",
                )
            prompt_market_fields = {
                "market_prompt_contract_id": document.get("market_prompt_contract_id"),
                "market_prompt_profile_id": batch_profile_id,
                "market_prompt_profile_sha256": prompt.get("market_prompt_profile_sha256"),
                "generation_controls_sha256": prompt.get("generation_controls_sha256"),
                "voiceover_review_sha256": prompt.get("voiceover_review_sha256"),
            }
            for field, expected in prompt_market_fields.items():
                if receipt.get(field) != expected:
                    self.add(
                        "external_market_prompt_binding_mismatch",
                        f"{path}.director_receipt.{field}",
                        "must equal the market contract/profile/control/review binding in the exact batch compile",
                    )
            if contract == "v7":
                deadlines = variant.get("three_layer_deadlines")
                if not isinstance(deadlines, dict):
                    self.add(
                        "external_three_layer_deadlines_missing",
                        "$batch_compile.variants.three_layer_deadlines",
                        "schema 1.4/v7 requires global, asset, and per-shot deadline layers",
                    )
                else:
                    if deadlines.get("contract_id") != DIRECTOR_DEADLINE_CONTRACT_ID:
                        self.add(
                            "external_deadline_contract_mismatch",
                            "$batch_compile.variants.three_layer_deadlines.contract_id",
                            f"must equal {DIRECTOR_DEADLINE_CONTRACT_ID}",
                        )
                    deadline_hash = canonical_json_sha256(deadlines)
                    if prompt.get("three_layer_deadlines_sha256") != deadline_hash:
                        self.add(
                            "external_three_layer_deadlines_hash_mismatch",
                            "$batch_compile.variants.renderings.prompt.three_layer_deadlines_sha256",
                            "must hash the exact canonical three-layer deadline object",
                        )
                    if receipt.get("deadline_contract_id") != deadlines.get("contract_id"):
                        self.add(
                            "external_deadline_contract_mismatch",
                            f"{path}.director_receipt.deadline_contract_id",
                            "must bind the exact deadline contract from the paid variant",
                        )
                    if receipt.get("three_layer_deadlines_sha256") != deadline_hash:
                        self.add(
                            "external_three_layer_deadlines_hash_mismatch",
                            f"{path}.director_receipt.three_layer_deadlines_sha256",
                            "must bind the exact deadline object from the paid variant",
                        )
                expected_fields.update({
                    "deadline_contract_id": DIRECTOR_DEADLINE_CONTRACT_ID,
                    "three_layer_deadlines_sha256": prompt.get("three_layer_deadlines_sha256"),
                })
            external_receipts = director_result.get("director_receipts")
            if not isinstance(external_receipts, list):
                external_receipts = []
            external_matches = [
                item for item in external_receipts if isinstance(item, dict) and
                item.get("variant_id") == variant.get("variant_id")
            ]
            if len(external_matches) != 1:
                self.add(
                    "external_director_receipt_missing",
                    "$batch_compile.director_result.director_receipts",
                    "current director validator must return exactly one receipt for the paid variant",
                )
            else:
                external_receipt = external_matches[0]
                receipt_fields = (
                    V7_DIRECTOR_RECEIPT_FIELDS if contract == "v7"
                    else V6_DIRECTOR_RECEIPT_FIELDS
                )
                for field in receipt_fields:
                    if receipt.get(field) != external_receipt.get(field):
                        self.add(
                            "external_director_receipt_mismatch",
                            f"{path}.director_receipt.{field}",
                            "receipt must equal the current director validator result for the exact batch compile",
                        )
        for field, expected in expected_fields.items():
            if receipt.get(field) != expected:
                self.add("external_director_receipt_mismatch",
                         f"{path}.director_receipt.{field}",
                         "receipt must equal the selected variant in the exact externally validated batch")
        if (row.get("compiled_prompt") != prompt.get("compiled_text") or
                row.get("prompt_sha256") != prompt.get("compiled_text_sha256")):
            self.add("external_compiled_prompt_mismatch", f"{path}.compiled_prompt",
                     "paid job prompt must be the selected external variant compiled_text verbatim")

        external_references = variant.get("references")
        if not isinstance(external_references, list):
            external_references = []
        apparel_bindings = [binding for binding in row.get("reference_bindings", [])
                            if isinstance(binding, dict) and
                            binding.get("role") == "apparel_three_view"]
        current_identity_gate = director_receipt_contract(receipt) == "v7"
        for binding in apparel_bindings:
            owners = [reference for reference in external_references
                      if isinstance(reference, dict) and
                      reference.get("reference_id") == binding.get("reference_id") and
                      reference.get("variant_id") == row.get("variant_id") and
                      reference.get("role") == "apparel_three_view" and
                      reference.get("sha256") == binding.get("sha256") and
                      reference.get("human_identity_pixels_absent") is True and
                      binding.get("human_identity_pixels_absent") is True and
                      (not current_identity_gate or (
                          reference.get("identity_cue_audit") == binding.get("identity_cue_audit") and
                          reference.get("identity_cue_audit_sha256") == binding.get("identity_cue_audit_sha256")
                      ))]
            if len(owners) != 1:
                self.add("external_reference_owner_mismatch", f"{path}.reference_bindings",
                         "apparel reference ID, variant owner, role, SHA-256, zero-human audit, and human_identity_pixels_absent true "
                         "must match the selected external variant")

    def validate_streaming_parent_release_set(self, current_ledger, evidence, current_binding):
        if self.streaming_parent_checked:
            return
        self.streaming_parent_checked = True
        source_path = evidence.get("path")
        if not isinstance(source_path, str) or source_path.startswith("<"):
            return
        current_compile_path = Path(source_path).expanduser().resolve()
        plan_value = current_binding.get("plan_path")
        plan_sha256 = current_binding.get("plan_sha256")
        if not isinstance(plan_value, str) or not Path(plan_value).is_absolute():
            self.add("streaming_parent_plan_path_invalid", "$batch_compile.streaming_release.plan_path",
                     "streaming parent scan requires an absolute plan path")
            return
        plan_path = Path(plan_value).expanduser().resolve()
        releases_root = plan_path.parent / "releases"
        if current_compile_path.parent.parent != releases_root:
            self.add("streaming_release_layout_mismatch", "$batch_compile",
                     "streaming compile must be releases/<variant_id>/batch-compile.json beside its parent plan")
            return

        seen_variants = set()
        active_states = INFLIGHT_STATES | {"submission_started"}
        active_count = sum(
            1 for job in current_ledger.get("jobs", [])
            if isinstance(job, dict) and job.get("state") in active_states
        )
        current_events = current_ledger.get("wave_control", {}).get("rate_limit_events", [])
        rate_latched = isinstance(current_events, list) and bool(current_events)
        compile_paths = sorted(releases_root.glob("*/batch-compile.json")) if releases_root.is_dir() else []
        if current_compile_path not in compile_paths:
            compile_paths.append(current_compile_path)

        for compile_path in compile_paths:
            try:
                other_document = load_json_text(compile_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                self.add("streaming_sibling_compile_invalid", str(compile_path), str(exc))
                continue
            other_binding = other_document.get("streaming_release")
            if not isinstance(other_binding, dict):
                self.add("streaming_sibling_compile_invalid", str(compile_path),
                         "every child compile under releases must contain streaming_release")
                continue
            other_variant_id = other_binding.get("variant_id")
            if not isinstance(other_variant_id, str) or not other_variant_id.strip():
                self.add("streaming_sibling_variant_invalid", str(compile_path),
                         "streaming sibling requires a non-empty variant_id")
                continue
            if other_variant_id in seen_variants:
                self.add("streaming_sibling_variant_duplicate", str(compile_path),
                         "a parent may contain only one release compile per variant")
            seen_variants.add(other_variant_id)
            if compile_path.parent.name != other_variant_id:
                self.add("streaming_release_layout_mismatch", str(compile_path),
                         "release directory name must exactly equal streaming variant_id")
            try:
                other_plan_path = Path(str(other_binding.get("plan_path"))).expanduser().resolve()
            except (OSError, ValueError):
                other_plan_path = None
            if other_plan_path != plan_path or other_binding.get("plan_sha256") != plan_sha256:
                self.add("streaming_parent_plan_hash_mismatch", str(compile_path),
                         "all released siblings must bind one exact immutable parent plan path and SHA-256")

            if compile_path == current_compile_path:
                continue
            sibling_ledger_path = compile_path.with_name("ledger.json")
            try:
                sibling_ledger = load_json_text(sibling_ledger_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                self.add("streaming_sibling_ledger_invalid", str(sibling_ledger_path), str(exc))
                continue
            if sibling_ledger.get("run_id") != other_binding.get("child_run_id"):
                self.add("streaming_sibling_run_id_mismatch", str(sibling_ledger_path),
                         "sibling ledger run_id must equal its compile child_run_id")
            sibling_jobs = sibling_ledger.get("jobs")
            if not isinstance(sibling_jobs, list) or len(sibling_jobs) != 1:
                self.add("streaming_sibling_job_count_invalid", str(sibling_ledger_path),
                         "each streaming sibling ledger must contain exactly one job")
                sibling_jobs = []
            active_count += sum(
                1 for job in sibling_jobs
                if isinstance(job, dict) and job.get("state") in active_states
            )
            events = sibling_ledger.get("wave_control", {}).get("rate_limit_events", [])
            if isinstance(events, list) and events:
                rate_latched = True

        if len(seen_variants) > 12:
            self.add("streaming_parent_release_limit_exceeded", str(releases_root),
                     "streaming parent may contain at most 12 one-job releases")
        if active_count > 12:
            self.add("streaming_parent_inflight_exceeded", str(releases_root),
                     "aggregate streaming child inflight count may not exceed 12")
        if rate_latched and active_count > 1:
            self.add("streaming_parent_rate_limit_latch_violation", str(releases_root),
                     "after any child rate-limit signal, at most one child may be submission_started or inflight")

    def validate_balance(self, ledger):
        gate = ledger.get("balance_gate")
        if not isinstance(gate, dict):
            self.add("invalid_balance_gate", "$.balance_gate", "must be an object")
            return
        self.timestamp(gate, "queried_at", "$.balance_gate", False)
        balance, estimate = gate.get("balance"), gate.get("estimated_worst_case_cost")
        for key, value in (("balance", balance), ("estimated_worst_case_cost", estimate)):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                self.add("invalid_balance_value", f"$.balance_gate.{key}", "passed gate requires a non-negative number")
        self.req_str(gate, "unit", "$.balance_gate")
        if gate.get("estimate_status") != "known":
            self.add("unknown_cost_estimate", "$.balance_gate.estimate_status", "must equal known before paid submission")
        if gate.get("passed") is not True:
            self.add("balance_gate_not_passed", "$.balance_gate.passed", "must be true before paid submission")
        if (isinstance(balance, (int, float)) and not isinstance(balance, bool) and
                isinstance(estimate, (int, float)) and not isinstance(estimate, bool) and balance < estimate):
            self.add("insufficient_balance", "$.balance_gate.balance", "must cover estimated_worst_case_cost")

    def validate_models(self, rows):
        presets = self.registry.get("presets", {})
        result, seen_names, seen_assets = {}, set(), set()
        for i, row in enumerate(rows):
            path = f"$.models[{i}]"
            if not isinstance(row, dict):
                self.add("invalid_model", path, "must be an object"); continue
            name = self.req_str(row, "name", path)
            asset = self.req_str(row, "asset_id", path)
            status = self.req_str(row, "validation_status", path)
            self.timestamp(row, "validated_at", path, False)
            expected = presets.get(name) if name else None
            if not isinstance(expected, dict):
                self.add("unknown_model_preset", f"{path}.name", "model is absent from the selected registry")
            elif asset != expected.get("asset_id"):
                self.add("model_asset_mismatch", f"{path}.asset_id", "asset ID does not match the selected registry")
            if status != "valid":
                self.add("model_not_validated", f"{path}.validation_status", "fixed model must have valid status")
            if name in seen_names or asset in seen_assets:
                self.add("duplicate_model", path, "model names and asset IDs must be unique")
            if name: seen_names.add(name)
            if asset: seen_assets.add(asset)
            if name and asset and status == "valid" and expected and asset == expected.get("asset_id"):
                result[name] = asset
        return result

    def validate_artifacts(self, rows, require_upload_provenance=False):
        result, ids, hashes = [], set(), set()
        for i, row in enumerate(rows):
            path = f"$.artifacts[{i}]"
            if not isinstance(row, dict):
                self.add("invalid_artifact", path, "must be an object"); continue
            artifact_id = self.req_str(row, "artifact_id", path)
            variant = self.req_str(row, "variant_id", path)
            local_path = self.req_str(row, "local_path", path)
            filename = self.req_str(row, "filename", path)
            if filename and ("/" in filename or "\\" in filename):
                self.add("invalid_filename", f"{path}.filename", "must be a filename, not a path")
            digest = self.req_str(row, "sha256", path)
            if digest and not SHA256_RE.fullmatch(digest):
                self.add("invalid_sha256", f"{path}.sha256", "must be a lowercase SHA-256 hex digest")
            if artifact_id in ids:
                self.add("duplicate_artifact_id", f"{path}.artifact_id", "artifact_id must be unique")
            if digest in hashes:
                self.add("duplicate_artifact_sha256", f"{path}.sha256", "identical content must be deduplicated")
            ids.add(artifact_id); hashes.add(digest)
            size = row.get("bytes")
            if not is_int(size) or size < 0:
                self.add("invalid_artifact_bytes", f"{path}.bytes", "must be a non-negative integer")
            self.timestamp(row, "mtime", path, False)
            provenance_fields = (
                "upload_transport", "content_variant", "source_sha256",
                "transformation_reason", "transformation_authorized",
            )
            provenance_present = any(key in row for key in provenance_fields)
            if require_upload_provenance:
                for key in provenance_fields:
                    if key not in row:
                        self.add("missing_upload_provenance", f"{path}.{key}",
                                 "new submissions require complete upload provenance")
            upload_transport = row.get("upload_transport")
            content_variant = row.get("content_variant")
            source_sha256 = row.get("source_sha256")
            transformation_reason = row.get("transformation_reason")
            transformation_authorized = row.get("transformation_authorized")
            if provenance_present or require_upload_provenance:
                if upload_transport not in UPLOAD_TRANSPORTS:
                    self.add("invalid_upload_transport", f"{path}.upload_transport",
                             "must identify the local Hook, URL reuse, PopBoom UI, or historical inline Base64")
                elif require_upload_provenance and upload_transport not in NEW_UPLOAD_TRANSPORTS:
                    self.add("legacy_upload_transport", f"{path}.upload_transport",
                             "legacy inline Base64 cannot authorize a new upload")
                if content_variant not in CONTENT_VARIANTS:
                    self.add("invalid_content_variant", f"{path}.content_variant",
                             "must be original or derivative")
                elif content_variant == "original":
                    if source_sha256 is not None or transformation_reason is not None:
                        self.add("original_transformation_mismatch", path,
                                 "original artifacts require null source_sha256 and transformation_reason")
                    if transformation_authorized is not False:
                        self.add("original_transformation_mismatch", f"{path}.transformation_authorized",
                                 "original artifacts require transformation_authorized false")
                else:
                    if not isinstance(source_sha256, str) or not SHA256_RE.fullmatch(source_sha256):
                        self.add("invalid_derivative_source_sha256", f"{path}.source_sha256",
                                 "derivatives require the original lowercase SHA-256")
                    if not isinstance(transformation_reason, str) or not transformation_reason.strip():
                        self.add("missing_transformation_reason", f"{path}.transformation_reason",
                                 "derivatives require a non-empty transformation reason")
                    if transformation_authorized is not True:
                        self.add("missing_transformation_authorization", f"{path}.transformation_authorized",
                                 "derivatives require explicit user authorization")
            status = row.get("upload_status")
            if status not in {"pending", "uploading", "uploaded", "failed"}:
                self.add("invalid_upload_status", f"{path}.upload_status", "invalid upload status")
            for key in ("uploaded_url", "uploaded_at", "url_expiry", "url_status", "last_verified_at"):
                if key not in row:
                    self.add("missing_field", f"{path}.{key}", "field is required")
            url, uploaded_at = row.get("uploaded_url"), row.get("uploaded_at")
            url_status = row.get("url_status")
            if url_status not in {"unknown", "usable", "missing", "expired", "unreachable"}:
                self.add("invalid_url_status", f"{path}.url_status", "invalid artifact URL status")
            if status == "uploaded":
                if not is_url(url): self.add("invalid_uploaded_url", f"{path}.uploaded_url", "uploaded artifacts require an HTTP(S) URL")
                if parse_time(uploaded_at) is None: self.add("invalid_uploaded_at", f"{path}.uploaded_at", "uploaded artifacts require a timestamp")
            elif url is not None or uploaded_at is not None:
                self.add("upload_status_mismatch", path, "only uploaded artifacts may have uploaded_url/uploaded_at")
            if url_status == "usable" and status != "uploaded":
                self.add("upload_status_mismatch", f"{path}.url_status", "usable URL status requires uploaded artifact status")
            for key in ("url_expiry", "last_verified_at"):
                value = row.get(key)
                if value is not None and parse_time(value) is None:
                    self.add("invalid_timestamp", f"{path}.{key}", "must be null or an RFC 3339 timestamp")
                if value is not None and status != "uploaded":
                    self.add("upload_status_mismatch", f"{path}.{key}", "verification/expiry timestamps require uploaded status")
            result.append({"id": artifact_id, "variant": variant, "path": local_path, "url": url,
                           "sha256": digest, "status": status, "url_status": url_status,
                           "upload_transport": upload_transport, "content_variant": content_variant,
                           "human_identity_pixels_absent": row.get("human_identity_pixels_absent"),
                           "identity_cue_audit": row.get("identity_cue_audit"),
                           "identity_cue_audit_sha256": row.get("identity_cue_audit_sha256")})
        return result

    def validate_jobs(self, ledger, rows, models, artifacts, durations, download_available, created, updated):
        keys, fingerprints, records, signatures, prompt_variants = set(), set(), set(), set(), {}
        for i, row in enumerate(rows):
            path = f"$.jobs[{i}]"
            if not isinstance(row, dict): self.add("invalid_job", path, "must be an object"); continue
            required_strings = ("job_key", "variant_id", "color_name", "model_name", "model_preset", "asset_id",
                                "batch_compile_id", "timeline_id", "compiled_prompt", "prompt_sha256",
                                "request_fingerprint", "resolution", "ratio", "route", "state")
            values = {key: self.req_str(row, key, path) for key in required_strings}
            job_kind = row.get("job_kind")
            workflow_kind = ledger.get("workflow_kind")
            if job_kind is not None and job_kind not in WORKFLOW_KINDS:
                self.add("invalid_job_kind", f"{path}.job_kind",
                         "when present, job_kind must be zibuyu_apparel or generic_popboom")
            if workflow_kind in WORKFLOW_KINDS and job_kind != workflow_kind:
                self.add("workflow_job_kind_mismatch", f"{path}.job_kind",
                         "job_kind must exactly match ledger workflow_kind")
            if job_kind in WORKFLOW_KINDS and workflow_kind != job_kind:
                self.add("workflow_job_kind_mismatch", "$.workflow_kind",
                         "ledger workflow_kind must exactly match every explicit job_kind")
            if row.get("state") == "submission_started" and workflow_kind not in WORKFLOW_KINDS:
                self.add("paid_workflow_kind_required", "$.workflow_kind",
                         "submission_started authorization requires explicit zibuyu_apparel or generic_popboom")
            if row.get("state") == "submission_started" and job_kind != workflow_kind:
                self.add("paid_job_kind_required", f"{path}.job_kind",
                         "submission_started authorization requires job_kind matching workflow_kind")
            zibuyu_apparel = workflow_kind == ZIBUYU_APPAREL_KIND and job_kind == ZIBUYU_APPAREL_KIND
            raw_bindings = row.get("reference_bindings")
            carries_apparel_reference = isinstance(raw_bindings, list) and any(
                isinstance(binding, dict) and binding.get("role") == "apparel_three_view"
                for binding in raw_bindings
            )
            if (row.get("state") in {"planned", "submission_started"} and
                    carries_apparel_reference and not zibuyu_apparel):
                self.add("apparel_workflow_kind_required", f"{path}.job_kind",
                         "a planned or paid job carrying apparel_three_view must use "
                         "zibuyu_apparel and pass the external director gate; generic_popboom "
                         "cannot downgrade an apparel job")
            if zibuyu_apparel and row.get("state") in {"planned", "submission_started"}:
                self.validate_zibuyu_director_receipt(ledger, row, path)
                if row.get("state") == "submission_started":
                    self.validate_zibuyu_batch_compile_evidence(ledger, row, path)
            job_key, fingerprint = values["job_key"], values["request_fingerprint"]
            for value, seen, code, field in ((job_key, keys, "duplicate_job_key", "job_key"),
                                             (fingerprint, fingerprints, "duplicate_request_fingerprint", "request_fingerprint")):
                if value in seen: self.add(code, f"{path}.{field}", f"{field} must be unique")
                if value: seen.add(value)
                if value and not SHA256_RE.fullmatch(value): self.add("invalid_sha256", f"{path}.{field}", "must be a lowercase SHA-256 hex digest")
            prompt = values["compiled_prompt"]
            if prompt and values["prompt_sha256"] != sha256_text(prompt):
                self.add("prompt_hash_mismatch", f"{path}.prompt_sha256", "must hash compiled_prompt exactly as UTF-8")
            previous = prompt_variants.get(values["prompt_sha256"])
            variant_color = (values["variant_id"], values["color_name"])
            if previous and previous != variant_color:
                self.add("cross_variant_prompt_reuse", f"{path}.prompt_sha256", "different colors/variants must not reuse a prompt hash")
            elif values["prompt_sha256"]: prompt_variants[values["prompt_sha256"]] = variant_color
            if values["batch_compile_id"] != ledger.get("batch_compile_id"):
                self.add("batch_compile_mismatch", f"{path}.batch_compile_id", "must match ledger batch_compile_id")
            if values["model_preset"] != values["model_name"]:
                self.add("model_preset_mismatch", f"{path}.model_preset", "must equal model_name")
            if models.get(values["model_name"]) != values["asset_id"]:
                self.add("job_model_asset_mismatch", f"{path}.asset_id", "must bind a valid ledger model and registry asset")
            timeline_version = row.get("timeline_version")
            if not is_int(timeline_version) or timeline_version < 1:
                self.add("invalid_timeline_version", f"{path}.timeline_version", "must be a positive integer")
            duration = row.get("duration")
            if not is_int(duration) or duration <= 0:
                self.add("invalid_duration", f"{path}.duration", "must be a positive integer")
            job_route = values["route"]
            if job_route not in ROUTES: self.add("invalid_route", f"{path}.route", "invalid route")
            if job_route != ledger.get("route"): self.add("route_mismatch", f"{path}.route", "must match ledger route")
            if job_route in {"mcp_declared", "mcp_observed", "mcp_provisional"}:
                generated = ledger.get("capability_snapshot", {}).get("generate_video", {})
                declared_resolutions = generated.get("declared_resolutions", [])
                declared_ratios = generated.get("declared_ratios", [])
                if values["resolution"] not in declared_resolutions:
                    self.add("undeclared_resolution", f"{path}.resolution",
                             "MCP resolution is absent from the live declaration")
                if values["ratio"] not in declared_ratios:
                    self.add("undeclared_ratio", f"{path}.ratio",
                             "MCP ratio is absent from the live declaration")
            transport = row.get("submission_transport")
            expected_transport = ({"mcp_declared": "mcp_streamable_http", "mcp_observed": "mcp_streamable_http",
                                   "mcp_provisional": "mcp_streamable_http",
                                   "ui": "popboom_ui"}).get(job_route)
            if transport not in {"mcp_streamable_http", "popboom_ui"}:
                self.add("invalid_submission_transport", f"{path}.submission_transport", "must be mcp_streamable_http or popboom_ui")
            elif expected_transport and transport != expected_transport:
                self.add("submission_transport_mismatch", f"{path}.submission_transport", "must match the selected route")
            requested = row.get("requested_duration", ledger.get("requested_duration"))
            if requested is not None and (not is_int(requested) or requested <= 0):
                self.add("invalid_requested_duration", f"{path}.requested_duration", "must be a positive integer")
            elif requested is not None and duration != requested:
                self.add("duration_downgrade", f"{path}.duration", "must equal requested_duration; 15s must never become 10s")
            if duration == 15:
                if job_route == "mcp_declared" and 15 not in durations:
                    self.add("invalid_15s_route", f"{path}.route", "mcp_declared requires live schema declaration of 15s")
                elif job_route == "mcp_observed" and 15 in durations:
                    self.add("observed_route_when_declared", f"{path}.route", "use mcp_declared when the live schema explicitly declares 15s")
                elif job_route == "mcp_observed" and self.observed_15s_capability() is None:
                    self.add("invalid_15s_observed_route", f"{path}.route", "mcp_observed requires an enabled successful 15s runtime baseline")
                elif job_route == "mcp_provisional" and row.get("state") in NEW_SUBMISSION_STATES:
                    self.add(
                        "invalid_15s_provisional_route",
                        f"{path}.route",
                        "mcp_provisional is historical poll/report-only and cannot authorize a new 15s submission",
                    )
            elif job_route == "mcp_observed":
                self.add("invalid_observed_route", f"{path}.route", "mcp_observed is reserved for validator-bound 15s capability")
            elif job_route == "mcp_provisional":
                self.add("invalid_provisional_route", f"{path}.route", "mcp_provisional is reserved for observed 15s capability")
            elif job_route == "mcp_declared" and is_int(duration) and duration not in durations:
                self.add("undeclared_duration", f"{path}.duration", "MCP duration is absent from the live declaration")
            bindings = row.get("reference_bindings")
            reference_values, reference_ids, apparel_bindings = [], set(), []
            if not isinstance(bindings, list) or not bindings:
                self.add("missing_reference_binding", f"{path}.reference_bindings", "at least one structured reference binding is required")
                bindings = []
            for j, binding in enumerate(bindings):
                bpath = f"{path}.reference_bindings[{j}]"
                if not isinstance(binding, dict): self.add("invalid_reference_binding", bpath, "must be an object"); continue
                for key in ("reference_id", "artifact_id", "variant_id", "role", "url", "sha256"):
                    self.req_str(binding, key, bpath)
                reference_id = binding.get("reference_id")
                if reference_id in reference_ids: self.add("duplicate_reference_id", f"{bpath}.reference_id", "must be unique within a job")
                reference_ids.add(reference_id)
                if binding.get("role") == "apparel_three_view": apparel_bindings.append(binding)
                if not is_url(binding.get("url")): self.add("invalid_uploaded_url", f"{bpath}.url", "must be an HTTP(S) URL")
                if not SHA256_RE.fullmatch(str(binding.get("sha256", ""))): self.add("invalid_sha256", f"{bpath}.sha256", "must be a lowercase SHA-256 hex digest")
                exact = [a for a in artifacts if a["id"] == binding.get("artifact_id") and
                         a["variant"] == binding.get("variant_id") and a["url"] == binding.get("url") and
                         a["sha256"] == binding.get("sha256")]
                if len(exact) != 1:
                    self.add("artifact_binding_mismatch", bpath, "artifact ID, variant, URL, and SHA-256 must all match one artifact")
                else:
                    artifact = exact[0]
                    if artifact["variant"] != values["variant_id"]:
                        self.add("artifact_variant_mismatch", f"{bpath}.variant_id", "reference artifact belongs to another job variant")
                    if artifact["status"] != "uploaded" or artifact["url_status"] != "usable":
                        self.add("artifact_not_usable", bpath, "paid reference must resolve to an uploaded, usable artifact URL")
                reference_values.append({"role": binding.get("role"), "url": binding.get("url"), "sha256": binding.get("sha256")})
            if zibuyu_apparel and len(apparel_bindings) != 1:
                self.add("apparel_reference_count", f"{path}.reference_bindings", "exactly one apparel_three_view binding is required")
            if zibuyu_apparel and row.get("state") in NEW_SUBMISSION_STATES:
                self.validate_zibuyu_outbound_request(
                    row, path, artifacts, apparel_bindings,
                )
            signature = paid_request_signature(row)
            expected_fingerprint = sha256_text(signature)
            if values["request_fingerprint"] != expected_fingerprint:
                self.add("request_fingerprint_mismatch", f"{path}.request_fingerprint", "must hash the normalized paid request exactly")
            if signature in signatures: self.add("duplicate_paid_request", path, "normalized paid request duplicates another job")
            signatures.add(signature)
            record = self.validate_job_state(row, path, created, updated, download_available)
            if record is not None:
                if record in records: self.add("duplicate_record_id", f"{path}.record_id", "record_id must bind exactly one job")
                records.add(record)

    def validate_job_state(self, row, path, created, updated, download_available):
        state = row.get("state")
        if state not in STATES: self.add("invalid_state", f"{path}.state", "invalid job state")
        for key in ("record_id", "task_id", "video_url", "error_code", "error_message"):
            if key not in row: self.add("missing_field", f"{path}.{key}", "field is required")
        times = {key: self.timestamp(row, key, path, True) for key in
                 ("submission_started_at", "submitted_at", "last_polled_at", "next_poll_at")}
        record = row.get("record_id")
        if record is not None and (not is_int(record) or record <= 0):
            self.add("invalid_record_id", f"{path}.record_id", "must be null or a positive integer"); record = None
        task_id = row.get("task_id")
        if task_id is not None and (not isinstance(task_id, str) or not task_id):
            self.add("invalid_task_id", f"{path}.task_id", "must be null or a non-empty string")
        attempts = row.get("poll_attempts")
        if not is_int(attempts) or attempts < 0: self.add("invalid_poll_attempts", f"{path}.poll_attempts", "must be a non-negative integer")
        next_action = row.get("next_action")
        if not isinstance(next_action, str) or next_action not in JOB_NEXT_ACTIONS:
            self.add("invalid_next_action", f"{path}.next_action", "must be a supported explicit scheduler action")
        elif state in STATE_NEXT_ACTIONS and next_action not in STATE_NEXT_ACTIONS[state]:
            self.add("next_action_state_mismatch", f"{path}.next_action",
                     "next_action is not safe for the current persisted state")
        resubmit_allowed = row.get("resubmit_allowed")
        if resubmit_allowed is not False:
            self.add("unsafe_resubmission_flag", f"{path}.resubmit_allowed",
                     "must be explicitly false; first submission is controlled by state and wave release")
        for key in ("submission_reported", "completion_reported"):
            if not isinstance(row.get(key), bool): self.add("invalid_boolean", f"{path}.{key}", "must be boolean")
        video = row.get("video_url")
        if video is not None and not is_url(video): self.add("invalid_video_url", f"{path}.video_url", "must be null or an HTTP(S) URL")
        video_status = row.get("video_url_status")
        if video_status not in {"unknown", "usable", "missing", "unusable", "expired"}:
            self.add("invalid_video_url_status", f"{path}.video_url_status", "invalid video URL status")
        if video_status == "usable" and not is_url(video):
            self.add("video_url_status_mismatch", f"{path}.video_url_status", "usable status requires an HTTP(S) video_url")
        if video is None and video_status == "usable":
            self.add("video_url_status_mismatch", f"{path}.video_url", "missing URL cannot be usable")
        download = self.validate_download(row.get("download"), f"{path}.download", download_available)
        pre = state in {"planned", "blocked"}; uncertain = state in {"submission_started", "submission_unknown"}
        active = state in {"submitted", "queued", "running", "succeeded", "failed", "pending_timeout"}
        if pre:
            if record is not None or task_id is not None or video is not None or any(times.values()) or attempts not in {0, None}:
                self.add("state_field_mismatch", path, "planned/blocked jobs cannot contain submission/task fields")
            if state == "blocked" and not (row.get("error_code") or row.get("error_message")):
                self.add("missing_block_reason", path, "blocked jobs require an error code or message")
        if uncertain:
            if times["submission_started_at"] is None or times["submitted_at"] is not None or record is not None or task_id is not None or video is not None:
                self.add("state_field_mismatch", path, "submission_started/unknown requires only submission_started_at")
            if times["last_polled_at"] is not None or times["next_poll_at"] is not None or attempts not in {0, None}:
                self.add("state_field_mismatch", path, "unknown submissions must be reconciled before polling or retry")
        if active:
            if record is None or times["submission_started_at"] is None or times["submitted_at"] is None:
                self.add("state_field_mismatch", path, "submitted and later states require record_id and both submission timestamps")
            if state in {"submitted", "queued", "running", "pending_timeout", "failed"} and video is not None:
                self.add("state_field_mismatch", f"{path}.video_url", "this state must not contain a video URL")
            if state in {"succeeded", "failed"} and times["last_polled_at"] is None:
                self.add("state_field_mismatch", f"{path}.last_polled_at", "terminal state requires a poll timestamp")
            if state == "failed" and not (row.get("error_code") or row.get("error_message")):
                self.add("missing_failure_reason", path, "failed jobs require an error code or message")
            if state == "pending_timeout" and (times["last_polled_at"] is None or not is_int(attempts) or attempts < 1):
                self.add("state_field_mismatch", path, "pending_timeout requires polling history and retained record_id")
            if state in {"succeeded", "failed"} and times["next_poll_at"] is not None:
                self.add("state_field_mismatch", f"{path}.next_poll_at", "terminal jobs cannot schedule another poll")
        if state != "succeeded" and download and download.get("reason") != "not_required":
            self.add("download_state_mismatch", f"{path}.download.reason", "download is permitted only after success")
        if state == "succeeded":
            if video_status == "usable":
                if download and download.get("reason") not in {"not_required", "local_mirror"}:
                    self.add("download_state_mismatch", f"{path}.download.reason", "usable check_task URL needs no download unless a local mirror is requested")
            elif video_status == "missing":
                if download and download.get("reason") != "missing_url":
                    self.add("download_state_mismatch", f"{path}.download.reason", "missing successful URL requires missing_url reason")
            elif video_status in {"unknown", "unusable", "expired"}:
                self.add("video_url_status_mismatch", f"{path}.video_url_status", "successful status must classify its URL as usable or missing")
        dangerous = any(row.get(key) is True for key in DANGEROUS_RESUBMISSION_FLAGS)
        dangerous = dangerous or str(next_action).lower() in PAID_RESUBMISSION_ACTIONS
        if state in {"submission_unknown", "pending_timeout"} and dangerous:
            self.add("unsafe_resubmission", path,
                     "unknown/timeout jobs must reconcile or resume, never create a new paid task")
        if record is not None and dangerous:
            self.add("unsafe_accepted_resubmission", path,
                     "a job with a record_id is permanently poll/download/report-only within this run")
        if state == "blocked" and has_rate_limit_signal(row):
            self.add("rate_limit_job_terminalized", path,
                     "retryable limit work must remain planned or submission_unknown, not terminal blocked")
        if row.get("submission_reported") is True and record is None:
            self.add("report_state_mismatch", f"{path}.submission_reported", "cannot report submission without record_id")
        if row.get("completion_reported") is True and state not in {"succeeded", "failed"}:
            self.add("report_state_mismatch", f"{path}.completion_reported", "completion may be reported only for terminal jobs")
        start, submitted, polled = times["submission_started_at"], times["submitted_at"], times["last_polled_at"]
        if created and start and start < created: self.add("timestamp_order", f"{path}.submission_started_at", "must not precede ledger creation")
        if start and submitted and submitted < start: self.add("timestamp_order", f"{path}.submitted_at", "must not precede submission start")
        if submitted and polled and polled < submitted: self.add("timestamp_order", f"{path}.last_polled_at", "must not precede submitted_at")
        if updated and polled and polled > updated: self.add("timestamp_order", f"{path}.last_polled_at", "must not exceed ledger updated_at")
        return record

    def validate_download(self, download, path, available):
        if not isinstance(download, dict):
            self.add("invalid_download", path, "must be an object")
            return None
        for key in ("reason", "state", "attempts", "started_at", "completed_at", "local_path",
                    "sha256", "bytes", "error_code"):
            if key not in download: self.add("missing_field", f"{path}.{key}", "field is required")
        reason, state, attempts = download.get("reason"), download.get("state"), download.get("attempts")
        if reason not in {"not_required", "missing_url", "local_mirror"}:
            self.add("invalid_download_reason", f"{path}.reason", "invalid download reason")
        if state not in {"not_required", "pending", "succeeded", "failed"}:
            self.add("invalid_download_state", f"{path}.state", "invalid download state")
        if not is_int(attempts) or attempts not in {0, 1}:
            self.add("download_attempt_limit", f"{path}.attempts", "download_video may be called at most once")
        started = self.timestamp(download, "started_at", path, True)
        completed = self.timestamp(download, "completed_at", path, True)
        if reason == "not_required":
            if state != "not_required" or attempts != 0 or any(download.get(k) is not None for k in
                    ("started_at", "completed_at", "local_path", "sha256", "bytes", "error_code")):
                self.add("download_state_mismatch", path, "not_required download must have zero attempts and null metadata")
        else:
            if not available:
                self.add("download_unavailable", path, "download_video is required by this record but absent from the capability snapshot")
            if state in {"succeeded", "failed"} and (attempts != 1 or started is None or completed is None):
                self.add("download_state_mismatch", path, "terminal download requires one attempt and start/completion timestamps")
            if state == "pending" and completed is not None:
                self.add("download_state_mismatch", f"{path}.completed_at", "pending download cannot be completed")
            if state == "succeeded":
                if not isinstance(download.get("local_path"), str) or not download.get("local_path"):
                    self.add("missing_download_artifact", f"{path}.local_path", "successful download requires local_path")
                if not SHA256_RE.fullmatch(str(download.get("sha256", ""))):
                    self.add("invalid_sha256", f"{path}.sha256", "successful download requires lowercase SHA-256")
                if not is_int(download.get("bytes")) or download.get("bytes") < 0:
                    self.add("invalid_download_bytes", f"{path}.bytes", "successful download requires non-negative bytes")
            if state == "failed" and not isinstance(download.get("error_code"), str):
                self.add("missing_download_error", f"{path}.error_code", "failed download requires error_code")
        if started and completed and completed < started:
            self.add("timestamp_order", f"{path}.completed_at", "must not precede started_at")
        return download

    def validate_max_inflight_evidence(self, ledger, wave, maximum, source):
        path = "$.wave_control.max_inflight_evidence"
        evidence = wave.get("max_inflight_evidence")
        required = source == "user"
        if evidence is None:
            if required:
                self.add("missing_max_inflight_evidence", path,
                         "concurrency above the default or historical reuse requires evidence")
            return
        if not isinstance(evidence, dict):
            self.add("invalid_max_inflight_evidence", path, "must be an object or null")
            return

        expected_kind = MAX_INFLIGHT_EVIDENCE_KINDS.get(source)
        if expected_kind and evidence.get("kind") != expected_kind:
            self.add("max_inflight_evidence_kind_mismatch", f"{path}.kind",
                     "evidence kind must match max_inflight_source")
        allowed = evidence.get("allowed_max_inflight")
        if not is_int(allowed) or allowed < 1:
            self.add("invalid_max_inflight_evidence_limit", f"{path}.allowed_max_inflight",
                     "must be a positive integer")
        elif maximum > allowed:
            self.add("max_inflight_evidence_limit_exceeded", f"{path}.allowed_max_inflight",
                     "max_inflight may not exceed the evidenced limit")
        self.timestamp(evidence, "observed_at", path, False)
        self.req_str(evidence, "evidence_id", path)

        evidence_version = evidence.get("server_version")
        if source in {"server", "historical_success"}:
            cap = ledger.get("capability_snapshot")
            cap_version = cap.get("server_version") if isinstance(cap, dict) else None
            if (not isinstance(evidence_version, str) or not evidence_version or
                    evidence_version != ledger.get("server_version") or
                    evidence_version != cap_version):
                self.add("max_inflight_evidence_version_mismatch", f"{path}.server_version",
                         "server and historical evidence must match both live server versions")
        elif evidence_version is not None and (not isinstance(evidence_version, str) or not evidence_version):
            self.add("invalid_max_inflight_evidence_version", f"{path}.server_version",
                     "must be null or a non-empty string")
        if source in {"server", "user", "historical_success"} and evidence.get("endpoint") != ledger.get("endpoint"):
            self.add("max_inflight_evidence_endpoint_mismatch", f"{path}.endpoint",
                     "capacity evidence must match the current canonical endpoint")

        if source == "historical_success":
            source_run = self.req_str(evidence, "source_run_id", path)
            if source_run and source_run == ledger.get("run_id"):
                self.add("historical_source_run_reused", f"{path}.source_run_id",
                         "historical evidence must come from an earlier run")
            source_hash = evidence.get("source_ledger_sha256")
            if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
                self.add("invalid_historical_ledger_sha256", f"{path}.source_ledger_sha256",
                         "historical evidence requires a lowercase source-ledger SHA-256")
            peak = evidence.get("observed_peak_inflight")
            successful = evidence.get("successful_jobs")
            rate_errors = evidence.get("rate_limit_errors")
            if not is_int(peak) or peak < 1 or not is_int(successful) or successful < 1:
                self.add("invalid_historical_capacity", path,
                         "observed_peak_inflight and successful_jobs must be positive integers")
            elif maximum > peak or (is_int(allowed) and allowed > peak) or peak > successful:
                self.add("historical_capacity_exceeded", path,
                         "requested/evidenced concurrency must not exceed observed successful capacity")
            if not is_int(rate_errors) or rate_errors != 0:
                self.add("historical_rate_limit_evidence", f"{path}.rate_limit_errors",
                         "historical capacity evidence requires zero rate/concurrency-limit errors")

    def validate_submission_waves(self, wave, jobs, effective_maximum):
        path = "$.wave_control"
        policy_wave = self.execution_policy.get("wave_control", {})
        wave_size = policy_wave.get("max_jobs_per_wave")
        if wave.get("planned_job_count") != len(jobs):
            self.add("planned_job_count_mismatch", f"{path}.planned_job_count",
                     "must equal the number of ledger jobs")
        if wave.get("max_jobs_per_wave") != wave_size:
            self.add("max_jobs_per_wave_mismatch", f"{path}.max_jobs_per_wave",
                     "must equal the persistent execution policy")
        if wave.get("submission_strategy") != policy_wave.get("submission_strategy"):
            self.add("submission_strategy_mismatch", f"{path}.submission_strategy",
                     "must equal the persistent execution policy")
        if wave.get("fill_wave_without_wait") is not True:
            self.add("fill_wave_policy_mismatch", f"{path}.fill_wave_without_wait",
                     "all jobs in the released wave must be submitted without polling waits")
        if wave.get("next_wave_release") != "previous_wave_terminal":
            self.add("wave_release_policy_mismatch", f"{path}.next_wave_release",
                     "the next wave may start only after the previous wave is terminal")

        job_keys = [job.get("job_key") for job in jobs if isinstance(job, dict)]
        expected = [job_keys[i:i + wave_size] for i in range(0, len(job_keys), wave_size)] if wave_size else []
        raw_waves = wave.get("submission_waves")
        actual, seen = [], set()
        if not isinstance(raw_waves, list) or not raw_waves:
            self.add("invalid_submission_waves", f"{path}.submission_waves",
                     "must be a non-empty array")
            raw_waves = []
        for i, item in enumerate(raw_waves):
            item_path = f"{path}.submission_waves[{i}]"
            if not isinstance(item, dict):
                self.add("invalid_submission_wave", item_path, "must be an object")
                continue
            if item.get("wave_index") != i + 1:
                self.add("invalid_wave_index", f"{item_path}.wave_index",
                         "wave indexes must start at 1 and be contiguous")
            keys = item.get("job_keys")
            if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not key for key in keys):
                self.add("invalid_wave_job_keys", f"{item_path}.job_keys",
                         "must be a non-empty string array")
                keys = []
            if len(keys) > wave_size:
                self.add("wave_size_exceeds_policy", f"{item_path}.job_keys",
                         "a submission wave may contain at most 12 jobs")
            for key in keys:
                if key in seen:
                    self.add("duplicate_wave_job_key", f"{item_path}.job_keys",
                             "a job may appear in exactly one wave")
                seen.add(key)
            actual.append(keys)
        if actual != expected:
            self.add("wave_plan_mismatch", f"{path}.submission_waves",
                     "waves must preserve job order and partition jobs as 12-per-wave plus one remainder")

        self.planned_job_count = len(job_keys)
        self.planned_wave_count = len(actual)
        state_by_key = {job.get("job_key"): job.get("state") for job in jobs if isinstance(job, dict)}
        job_by_key = {job.get("job_key"): job for job in jobs if isinstance(job, dict)}
        for i in range(1, len(actual)):
            current_started = any(state_by_key.get(key) not in {"planned", "blocked"} for key in actual[i])
            previous_terminal = all(state_by_key.get(key) in TERMINAL_STATES for prior in actual[:i] for key in prior)
            if current_started and not previous_terminal:
                self.add("wave_started_before_prior_terminal",
                         f"{path}.submission_waves[{i}]",
                         "later waves cannot start while an earlier wave is non-terminal")

        active = sum(1 for job in jobs if isinstance(job, dict) and job.get("state") in INFLIGHT_STATES)
        capacity = max(0, effective_maximum - active)
        for i, keys in enumerate(actual):
            previous_terminal = all(state_by_key.get(key) in TERMINAL_STATES for prior in actual[:i] for key in prior)
            started = [key for key in keys if state_by_key.get(key) == "submission_started"]
            planned = [key for key in keys if state_by_key.get(key) == "planned"]
            if previous_terminal and (started or planned):
                self.next_wave_index = i + 1
                if started:
                    if len(started) > capacity:
                        self.add("submission_started_exceeds_capacity",
                                 f"{path}.submission_waves[{i}]",
                                 "submission_started jobs exceed the current effective concurrency capacity")
                    self.available_submission_slots = min(capacity, len(started))
                    if len(started) <= capacity:
                        self.authorized_submission_job_keys = started
                        self.authorized_outbound_requests = [
                            authorized_outbound_request(key, job_by_key[key])
                            for key in started
                            if isinstance(job_by_key.get(key, {}).get("outbound_request"), dict)
                        ]
                        self.paid_submission_allowed = True
                else:
                    self.available_submission_slots = min(capacity, len(planned))
                    self.paid_submission_allowed = False
                break

    def validate_rate_limit_events(self, ledger, wave, jobs):
        path = "$.wave_control.rate_limit_events"
        raw_events = wave.get("rate_limit_events")
        if not isinstance(raw_events, list):
            self.add("invalid_rate_limit_events", path, "must be an append-only array")
            raw_events = []
        jobs_by_key = {
            job.get("job_key"): job for job in jobs
            if isinstance(job, dict) and isinstance(job.get("job_key"), str)
        }
        events, seen_ids, previous_time = [], set(), None
        for index, event in enumerate(raw_events):
            event_path = f"{path}[{index}]"
            if not isinstance(event, dict):
                self.add("invalid_rate_limit_event", event_path, "must be an object")
                continue
            evidence_id = self.req_str(event, "evidence_id", event_path)
            if evidence_id in seen_ids:
                self.add("duplicate_rate_limit_event", f"{event_path}.evidence_id",
                         "rate-limit evidence IDs must be unique within a run")
            if evidence_id:
                seen_ids.add(evidence_id)
            if event.get("batch_run_id") != ledger.get("run_id"):
                self.add("rate_limit_event_run_mismatch", f"{event_path}.batch_run_id",
                         "event must be scoped to the current run_id")
            trigger_key = self.req_str(event, "trigger_job_key", event_path)
            trigger = jobs_by_key.get(trigger_key)
            if trigger is None:
                self.add("rate_limit_event_job_mismatch", f"{event_path}.trigger_job_key",
                         "event must identify a job in this ledger")
            fingerprint = event.get("trigger_request_fingerprint")
            if (not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint) or
                    (trigger is not None and fingerprint != trigger.get("request_fingerprint"))):
                self.add("rate_limit_event_fingerprint_mismatch",
                         f"{event_path}.trigger_request_fingerprint",
                         "event must retain the triggering job request fingerprint")
            observed = self.timestamp(event, "observed_at", event_path, False)
            if previous_time and observed and observed < previous_time:
                self.add("rate_limit_event_order", f"{event_path}.observed_at",
                         "append-only rate-limit events must be chronological")
            if observed:
                previous_time = observed
            if not has_rate_limit_signal(event):
                self.add("invalid_rate_limit_event_signal", event_path,
                         "event error code/message must contain a configured limit marker")
            outcome = event.get("submission_outcome")
            if outcome not in {"confirmed_not_accepted", "accepted", "unknown"}:
                self.add("invalid_rate_limit_event_outcome", f"{event_path}.submission_outcome",
                         "must classify whether the triggering submission was accepted")
            event_record = event.get("record_id")
            if outcome == "accepted":
                if not is_int(event_record) or event_record < 1:
                    self.add("missing_rate_limit_event_record", f"{event_path}.record_id",
                             "accepted rate-limit events require the retained record_id")
                elif trigger is not None and trigger.get("record_id") != event_record:
                    self.add("rate_limit_event_record_mismatch", f"{event_path}.record_id",
                             "accepted event record_id must remain bound to the triggering job")
            elif event_record is not None:
                self.add("unexpected_rate_limit_event_record", f"{event_path}.record_id",
                         "unaccepted or unknown events must not invent a record_id")
            events.append(event)

        signal_jobs = [job for job in jobs if isinstance(job, dict) and has_rate_limit_signal(job)]
        for job in signal_jobs:
            matches = [event for event in events
                       if event.get("trigger_job_key") == job.get("job_key") and
                       event.get("error_code") == job.get("error_code") and
                       event.get("error_message") == job.get("error_message")]
            if not matches:
                self.add("missing_rate_limit_event", path,
                         "every current rate/concurrency signal must be appended to the batch event log")
        return events, signal_jobs

    def validate_execution_policy(self, ledger, wave, jobs, maximum, source):
        policy = self.execution_policy
        policy_wave = policy.get("wave_control", {})
        policy_id = policy.get("policy_id")
        if wave.get("policy_id") != policy_id:
            self.add("execution_policy_mismatch", "$.wave_control.policy_id",
                     "ledger must snapshot the active long-term execution policy")
        if maximum != policy_wave.get("max_inflight") or source != "user":
            self.add("user_concurrency_policy_mismatch", "$.wave_control",
                     "base concurrency must remain max_inflight 12 with source user")
        expected_evidence = policy_wave.get("max_inflight_evidence", {})
        evidence = wave.get("max_inflight_evidence")
        if not isinstance(evidence, dict):
            self.add("missing_user_concurrency_evidence", "$.wave_control.max_inflight_evidence",
                     "long-term user concurrency requires its user_explicit evidence")
        else:
            for key in ("kind", "allowed_max_inflight", "observed_at", "evidence_id", "endpoint", "server_version"):
                if evidence.get(key) != expected_evidence.get(key):
                    self.add("user_concurrency_evidence_mismatch",
                             f"$.wave_control.max_inflight_evidence.{key}",
                             "must exactly match the persistent user concurrency policy")

        events, signal_jobs = self.validate_rate_limit_events(ledger, wave, jobs)
        override = wave.get("rate_limit_override")
        effective = wave.get("effective_max_inflight")
        latched = bool(events) or bool(signal_jobs)
        if latched:
            if effective != self.execution_policy.get("rate_limit_policy", {}).get("effective_max_inflight"):
                self.add("rate_limit_not_reduced", "$.wave_control.effective_max_inflight",
                         "a rate/concurrency signal must reduce effective concurrency to 1")
            if not isinstance(override, dict):
                self.add("missing_rate_limit_override", "$.wave_control.rate_limit_override",
                         "a rate-limit event latches a persisted override for the rest of the run")
            else:
                if (override.get("kind") != "rate_limit_response" or
                        override.get("scope") != "batch" or
                        override.get("batch_run_id") != ledger.get("run_id") or
                        override.get("allowed_max_inflight") != 1):
                    self.add("invalid_rate_limit_override", "$.wave_control.rate_limit_override",
                             "override must be batch-scoped rate_limit_response with allowed_max_inflight 1")
                self.timestamp(override, "observed_at", "$.wave_control.rate_limit_override", False)
                self.req_str(override, "evidence_id", "$.wave_control.rate_limit_override")
                self.req_str(override, "trigger_job_key", "$.wave_control.rate_limit_override")
                if not has_rate_limit_signal({"error_code": override.get("error_code"),
                                              "error_message": override.get("error_message")}):
                    self.add("invalid_rate_limit_override_signal", "$.wave_control.rate_limit_override",
                             "override must retain the triggering limit code or message")
                if events:
                    latest = events[-1]
                    for key in ("evidence_id", "batch_run_id", "trigger_job_key",
                                "trigger_request_fingerprint", "error_code", "error_message",
                                "observed_at"):
                        if override.get(key) != latest.get(key):
                            self.add("rate_limit_override_event_mismatch",
                                     f"$.wave_control.rate_limit_override.{key}",
                                     "override must exactly match the newest append-only rate-limit event")
        else:
            if override is not None:
                self.add("orphan_rate_limit_override", "$.wave_control.rate_limit_override",
                         "override requires an append-only rate-limit event in this run")
            if effective != maximum:
                self.add("effective_max_inflight_mismatch", "$.wave_control.effective_max_inflight",
                         "without a limit override, effective concurrency must equal the configured 12")
        if not is_int(effective) or effective < 1:
            self.add("invalid_effective_max_inflight", "$.wave_control.effective_max_inflight",
                     "must be a positive integer")
            effective = maximum
        self.base_max_inflight = maximum
        self.effective_max_inflight = effective
        self.max_inflight_source = source
        self.validate_submission_waves(wave, jobs, effective)

    def validate_concurrency(self, ledger, jobs):
        wave = ledger.get("wave_control")
        if not isinstance(wave, dict):
            self.add("invalid_wave_control", "$.wave_control", "must be an object")
            return
        maximum = wave.get("max_inflight")
        if not is_int(maximum) or maximum < 1:
            self.add("invalid_max_inflight", "$.wave_control.max_inflight", "must be a positive integer")
            maximum = self.execution_policy.get("wave_control", {}).get("max_inflight", 12)
        source = wave.get("max_inflight_source")
        if source not in MAX_INFLIGHT_SOURCES:
            self.add("invalid_max_inflight_source", "$.wave_control.max_inflight_source", "invalid wave limit source")
        self.timestamp(wave, "max_inflight_updated_at", "$.wave_control", False)
        self.validate_max_inflight_evidence(ledger, wave, maximum, source)
        self.validate_execution_policy(ledger, wave, jobs, maximum, source)

    def result(self, run_id, route, model_count, artifact_count, job_count):
        errors = sorted(self.errors, key=error_sort_key)
        if errors:
            return {"valid": False, "primary_error": errors[0], "errors": errors}
        return {"valid": True, "schema_version": "1.0", "run_id": run_id, "route": route,
                "counts": {"models": model_count, "artifacts": artifact_count, "jobs": job_count},
                "base_max_inflight": self.base_max_inflight,
                "effective_max_inflight": self.effective_max_inflight,
                "max_inflight_source": self.max_inflight_source,
                "history_verified": self.history_verified,
                "wave_plan": {"planned_jobs": self.planned_job_count,
                              "planned_waves": self.planned_wave_count,
                              "next_wave_index": self.next_wave_index,
                              "available_submission_slots": self.available_submission_slots,
                              "authorized_submission_job_keys": self.authorized_submission_job_keys,
                              "authorized_outbound_requests": self.authorized_outbound_requests,
                              "paid_submission_allowed": (self.paid_submission_allowed and
                                                          self.history_verified)}}


def configured_wave_control(execution_policy, job_keys, now):
    configured = execution_policy["wave_control"]
    size = configured["max_jobs_per_wave"]
    return {
        "policy_id": execution_policy["policy_id"],
        "max_inflight": configured["max_inflight"],
        "max_inflight_source": configured["max_inflight_source"],
        "max_inflight_updated_at": now,
        "max_inflight_evidence": copy.deepcopy(configured["max_inflight_evidence"]),
        "effective_max_inflight": configured["max_inflight"],
        "max_jobs_per_wave": size,
        "submission_strategy": configured["submission_strategy"],
        "fill_wave_without_wait": configured["fill_wave_without_wait"],
        "next_wave_release": configured["next_wave_release"],
        "planned_job_count": len(job_keys),
        "submission_waves": [
            {"wave_index": index + 1, "job_keys": job_keys[start:start + size]}
            for index, start in enumerate(range(0, len(job_keys), size))
        ],
        "rate_limit_events": [],
        "rate_limit_override": None,
    }


def make_test_ledger(runtime, registry, execution_policy):
    now, prompt = "2026-07-11T08:00:00Z", "蓝色连衣裙的稳定 15 秒 UGC 脚本"
    name = next(iter(registry["presets"])); asset = registry["presets"][name]["asset_id"]
    digest = "1" * 64; url = "https://cdn.example.invalid/three-view.png"
    ledger = {
        "schema_version": "1.0", "ledger_revision": 0, "previous_ledger_sha256": None,
        "run_id": "run-self-test", "sku_family_id": "sku-1",
        "batch_compile_id": "compile-1", "endpoint": runtime["endpoint"], "transport": runtime["transport"],
        "server_name": "PopBoom", "server_version": runtime["server"]["version"],
        "capability_snapshot": {"captured_at": now, "server_version": runtime["server"]["version"],
                                "tool_names": sorted(REQUIRED_TOOLS), "generate_video": {
                                    "schema_sha256": "4" * 64,
                                    "declared_durations": copy.deepcopy(runtime["generate_video"]["declared_duration_seconds"]),
                                    "declared_resolutions": ["720p"], "declared_ratios": ["9:16"]},
                                "download_video_available": True},
        "route": "mcp_provisional", "route_evidence": {"kind": "provisional_observed",
            "evidence_server_version": runtime["server"]["version"], "observation_id": "obs-15s-1",
            "observed_duration_seconds": 15.07, "verified_at": now},
        "wave_control": configured_wave_control(execution_policy, ["2" * 64], now),
        "balance_gate": {"queried_at": now, "balance": 100.0, "estimated_worst_case_cost": 30.0,
                         "unit": "credits", "estimate_status": "known", "passed": True},
        "requested_duration": 15, "created_at": now, "updated_at": "2026-07-11T08:05:00Z",
        "models": [{"name": name, "asset_id": asset, "validation_status": "valid", "validated_at": now}],
        "artifacts": [{"artifact_id": "art-1", "variant_id": "blue", "local_path": "C:/refs/blue.png", "filename": "blue.png",
                       "sha256": digest, "bytes": 123, "mtime": now,
                       "upload_transport": "local_file_hook", "content_variant": "original",
                       "source_sha256": None, "transformation_reason": None,
                       "transformation_authorized": False, "upload_status": "uploaded",
                       "uploaded_url": url, "uploaded_at": now, "url_expiry": None, "url_status": "usable", "last_verified_at": now}],
        "jobs": [{"job_key": "2" * 64, "variant_id": "blue", "color_name": "蓝色", "model_name": name,
                  "model_preset": name, "asset_id": asset, "batch_compile_id": "compile-1", "timeline_id": "tl-blue",
                  "timeline_version": 1, "compiled_prompt": prompt, "prompt_sha256": sha256_text(prompt),
                  "reference_bindings": [{"reference_id": "ref-blue", "artifact_id": "art-1", "variant_id": "blue",
                                          "role": "apparel_three_view", "url": url, "sha256": digest}],
                  "request_fingerprint": "3" * 64, "resolution": "720p", "duration": 15,
                  "ratio": "9:16", "route": "mcp_provisional", "submission_transport": "mcp_streamable_http",
                  "state": "succeeded", "record_id": 123,
                  "task_id": "task-123", "submission_started_at": "2026-07-11T08:01:00Z",
                  "submitted_at": "2026-07-11T08:01:01Z", "last_polled_at": "2026-07-11T08:04:00Z",
                  "next_poll_at": None, "poll_attempts": 2, "video_url": "https://cdn.example.invalid/video.mp4", "video_url_status": "usable",
                  "download": {"reason": "not_required", "state": "not_required", "attempts": 0,
                               "started_at": None, "completed_at": None, "local_path": None, "sha256": None,
                               "bytes": None, "error_code": None},
                   "error_code": None, "error_message": None, "next_action": "report",
                   "resubmit_allowed": False, "submission_reported": True, "completion_reported": True}],
    }
    ledger["jobs"][0]["request_fingerprint"] = sha256_text(paid_request_signature(ledger["jobs"][0]))
    return ledger


def make_planned_batch_ledger(runtime, registry, execution_policy, count):
    ledger = make_test_ledger(runtime, registry, execution_policy)
    ledger["workflow_kind"] = GENERIC_POPBOOM_KIND
    ledger["route"] = "ui"
    ledger["route_evidence"] = {
        "kind": "ui_operable", "evidence_server_version": None,
        "observation_id": None, "observed_duration_seconds": None,
        "verified_at": "2026-07-20T08:00:00Z",
    }
    now = "2026-07-11T08:00:00Z"
    base_artifact = ledger["artifacts"][0]
    base_job = ledger["jobs"][0]
    artifacts, jobs = [], []
    for index in range(count):
        number = index + 1
        variant = f"variant-{number:02d}"
        digest = sha256_text(f"artifact-{number}")
        url = f"https://cdn.example.invalid/three-view-{number}.png"
        artifact = copy.deepcopy(base_artifact)
        artifact.update({
            "artifact_id": f"art-{number:02d}", "variant_id": variant,
            "local_path": f"C:/refs/{variant}.png", "filename": f"{variant}.png",
            "sha256": digest, "bytes": 100 + number, "uploaded_url": url,
        })
        artifacts.append(artifact)

        prompt = f"颜色变体 {number} 的稳定 15 秒 UGC 脚本"
        job = copy.deepcopy(base_job)
        job.update({
            "job_kind": GENERIC_POPBOOM_KIND,
            "job_key": sha256_text(f"job-{number}"), "variant_id": variant,
            "color_name": f"颜色{number}", "timeline_id": f"timeline-{number:02d}",
            "compiled_prompt": prompt, "prompt_sha256": sha256_text(prompt),
            "state": "planned", "record_id": None, "task_id": None,
            "submission_started_at": None, "submitted_at": None,
            "last_polled_at": None, "next_poll_at": None, "poll_attempts": 0,
            "video_url": None, "video_url_status": "unknown",
            "error_code": None, "error_message": None,
            "route": "ui", "submission_transport": "popboom_ui",
            "next_action": "generate_video", "resubmit_allowed": False,
            "submission_reported": False, "completion_reported": False,
        })
        job["reference_bindings"] = [{
            "reference_id": f"ref-{number:02d}", "artifact_id": artifact["artifact_id"],
            "variant_id": variant, "role": "generic_reference", "url": url,
            "sha256": digest,
        }]
        job["download"] = {
            "reason": "not_required", "state": "not_required", "attempts": 0,
            "started_at": None, "completed_at": None, "local_path": None,
            "sha256": None, "bytes": None, "error_code": None,
        }
        job["request_fingerprint"] = sha256_text(paid_request_signature(job))
        jobs.append(job)
    ledger["artifacts"] = artifacts
    ledger["jobs"] = jobs
    ledger["wave_control"] = configured_wave_control(
        execution_policy, [job["job_key"] for job in jobs], now)
    ledger["balance_gate"].update({
        "balance": float(max(100, count * 10)),
        "estimated_worst_case_cost": float(count * 3),
    })
    return ledger


def make_zibuyu_apparel_ledger(runtime, registry, execution_policy, evidence):
    """Bind one paid ledger fixture to a real current-director batch document."""
    ledger = make_planned_batch_ledger(runtime, registry, execution_policy, 1)
    document = evidence["document"]
    variant = document["variants"][0]
    timeline = variant["canonical_timeline"]
    prompt = variant["renderings"]["prompt"]
    reference = variant["references"][0]
    compile_digest = evidence["sha256"]
    eligible_models = [
        (name, preset) for name, preset in registry["presets"].items()
        if isinstance(preset, dict) and
        SHA256_RE.fullmatch(str(preset.get("identity_image_sha256", ""))) and
        isinstance(preset.get("identity_version"), str) and preset.get("identity_version")
    ]
    requested_model = document.get("batch_key", {}).get("model_preset")
    model_name, model_preset = next(
        ((name, preset) for name, preset in eligible_models if name == requested_model),
        eligible_models[0],
    )
    asset_id = model_preset["asset_id"]
    accepted = runtime["generate_video"]["duration_15"]["accepted_evidence"]
    ledger.update({
        "workflow_kind": ZIBUYU_APPAREL_KIND,
        "batch_compile_sha256": compile_digest,
        "batch_compile_id": document["batch_compile_id"],
        "sku_family_id": document["sku_family_id"],
        "route": "mcp_observed",
        "route_evidence": {
            "kind": "accepted_observed",
            "evidence_server_version": runtime["server"]["version"],
            "observation_id": accepted["evidence_id"],
            "observed_duration_seconds": accepted["requested_duration_seconds"],
            "accepted_record_id": accepted["record_id"],
            "accepted_task_id": accepted["task_id"],
            "accepted_terminal_status": accepted["terminal_status"],
            "verified_at": "2026-07-30T08:10:00Z",
        },
        "models": [{
            "name": model_name, "asset_id": asset_id,
            "validation_status": "valid", "validated_at": "2026-07-30T08:10:00Z",
        }],
    })
    artifact = ledger["artifacts"][0]
    artifact.update({
        "variant_id": variant["variant_id"],
        "sha256": reference["sha256"],
        "human_identity_pixels_absent": True,
    })
    if isinstance(reference.get("identity_cue_audit"), dict):
        artifact.update({
            "identity_cue_audit": copy.deepcopy(reference["identity_cue_audit"]),
            "identity_cue_audit_sha256": reference.get("identity_cue_audit_sha256"),
        })
    result_receipts = evidence.get("director_result", {}).get("director_receipts", [])
    result_receipt = next((
        item for item in result_receipts if isinstance(item, dict) and
        item.get("variant_id") == variant["variant_id"]
    ), None)
    director_receipt = {
        "variant_id": variant["variant_id"],
        "timeline_id": timeline["timeline_id"],
        "timeline_version": timeline["timeline_version"],
        "batch_compile_sha256": compile_digest,
        "schema_version": document["schema_version"],
        "quality_contract_id": document["quality_contract_id"],
        "serializer_id": prompt["serializer_id"],
        "quality_plan_sha256": prompt["quality_plan_sha256"],
        "research_bundle_sha256": prompt["research_bundle_sha256"],
        "canonical_beats_sha256": prompt["canonical_beats_sha256"],
        "compiled_text_sha256": prompt["compiled_text_sha256"],
        "director_valid": evidence["director_result"].get("valid") is True,
        "eligible_for_new_submission": evidence["director_result"].get("eligible_for_new_submission") is True,
    }
    if isinstance(result_receipt, dict):
        receipt_fields = (
            V7_DIRECTOR_RECEIPT_FIELDS
            if result_receipt.get("serializer_id") == DIRECTOR_SERIALIZER_ID
            else V6_DIRECTOR_RECEIPT_FIELDS
            if result_receipt.get("serializer_id") == HISTORICAL_V6_DIRECTOR_SERIALIZER_ID
            else tuple(director_receipt)
        )
        director_receipt.update({key: result_receipt.get(key) for key in receipt_fields})
    job = ledger["jobs"][0]
    job.update({
        "job_kind": ZIBUYU_APPAREL_KIND,
        "variant_id": variant["variant_id"],
        "color_name": variant["color_name"],
        "batch_compile_id": document["batch_compile_id"],
        "timeline_id": timeline["timeline_id"],
        "timeline_version": timeline["timeline_version"],
        "compiled_prompt": prompt["compiled_text"],
        "prompt_sha256": prompt["compiled_text_sha256"],
        "model_name": model_name,
        "model_preset": model_name,
        "asset_id": asset_id,
        "asset_identity_sha256": model_preset["identity_image_sha256"],
        "asset_identity_version": model_preset["identity_version"],
        "generation_model": "sd2",
        "route": "mcp_observed",
        "submission_transport": "mcp_streamable_http",
        "director_receipt": director_receipt,
    })
    job["reference_bindings"][0].update({
        "reference_id": reference["reference_id"],
        "artifact_id": artifact["artifact_id"],
        "variant_id": variant["variant_id"],
        "role": "apparel_three_view",
        "sha256": reference["sha256"],
        "human_identity_pixels_absent": True,
    })
    if isinstance(reference.get("identity_cue_audit"), dict):
        job["reference_bindings"][0].update({
            "identity_cue_audit": copy.deepcopy(reference["identity_cue_audit"]),
            "identity_cue_audit_sha256": reference.get("identity_cue_audit_sha256"),
        })
    job["product_info"] = json.dumps(
        {"brand": PRODUCT_INFO_FIXED_BRAND, "sku": "SELF-TEST"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    job["outbound_request"] = {
        "server_name": "PopBoom",
        "tool_name": "generate_video",
        "arguments": {
            "prompt": job["compiled_prompt"],
            "ref_image_urls": [f"asset://{asset_id}", artifact["uploaded_url"]],
            "model": "sd2", "duration": job["duration"],
            "resolution": job["resolution"], "ratio": job["ratio"],
            "product_info": job["product_info"],
        },
    }
    job["outbound_request_sha256"] = canonical_json_sha256(job["outbound_request"])
    job["request_fingerprint"] = sha256_text(paid_request_signature(job))
    return ledger


def attach_legacy_v5_exact_resume(ledger, source_ledger):
    """Bind a self-test resume ledger to an exact previously validated v5 source."""
    source_job = source_ledger["jobs"][0]
    ledger["run_id"] = f"{source_ledger['run_id']}-legacy-v5-resume"
    job = ledger["jobs"][0]
    job["legacy_v5_exact_resume"] = {
        "contract_id": LEGACY_V5_RESUME_CONTRACT_ID,
        "user_explicit": True,
        "authorization_scope": "exact_existing_compile_no_rewrite",
        "authorization_id": "user-approved-legacy-v5-resume-self-test",
        "authorized_at": "2026-08-04T08:00:00Z",
        "source_run_id": source_ledger["run_id"],
        "source_ledger_sha256": canonical_json_sha256(source_ledger),
        "source_job_key": source_job["job_key"],
        "batch_compile_sha256": ledger["batch_compile_sha256"],
        "director_receipt_sha256": canonical_json_sha256(job["director_receipt"]),
        "paid_package_sha256": canonical_json_sha256(legacy_v5_paid_package(job)),
        "prior_director_validated": True,
        "prior_submission_eligible": True,
        "unchanged_assertions": {
            field: True for field in sorted(LEGACY_V5_RESUME_ASSERTIONS)
        },
    }
    job["request_fingerprint"] = sha256_text(paid_request_signature(job))
    return ledger


def self_test(registry, runtime, execution_policy):
    test_registry = copy.deepcopy(registry)
    if not registry.get("presets"):
        test_registry = {**registry, "presets": {
            "美1": {
                "asset_id": "asset-self-test-model",
                "identity_version": "self-test-identity-v1",
                "identity_image_sha256": sha256_text("self-test-identity-image"),
            },
        }}
    else:
        for name, preset in test_registry["presets"].items():
            if not isinstance(preset, dict):
                continue
            preset.setdefault("identity_version", f"self-test-{name}-identity-v1")
            preset.setdefault("identity_image_sha256", sha256_text(f"self-test-{name}-identity-image"))
    base = make_test_ledger(runtime, test_registry, execution_policy)
    director_module = load_director_module()
    if not callable(getattr(director_module, "_fixture_v13", None)):
        raise ValueError("current sibling director validator lacks its schema-1.3 fixture")
    director_document = director_module._fixture_v13()
    director_document["batch_key"]["model_preset"] = next(iter(test_registry["presets"]))
    director_document["batch_key"]["resolution"] = "720p"
    director_document = director_module.compile_document(director_document)
    director_bytes = (json.dumps(director_document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    director_evidence_v5 = batch_compile_evidence_from_bytes(
        director_bytes, "<current-director-schema-1.3-fixture>", director_module)
    legacy_v5_prior_evidence = copy.deepcopy(director_evidence_v5)
    legacy_v5_prior_evidence["director_result"]["valid"] = True
    legacy_v5_prior_evidence["director_result"]["eligible_for_new_submission"] = True
    for receipt in legacy_v5_prior_evidence["director_result"].get("director_receipts", []):
        if isinstance(receipt, dict):
            receipt["director_valid"] = True
            receipt["eligible_for_new_submission"] = True
    if not callable(getattr(director_module, "_fixture_v14", None)):
        raise ValueError("current sibling director validator lacks its schema-1.4 fixture")
    director_document_v14 = director_module._fixture_v14(market="US")
    director_document_v14["batch_key"]["resolution"] = "720p"
    director_document_v14 = director_module.compile_document(director_document_v14)
    director_bytes_v14 = (
        json.dumps(director_document_v14, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    director_evidence = batch_compile_evidence_from_bytes(
        director_bytes_v14, "<current-director-schema-1.4-fixture>", director_module,
    )

    # Exercise the shortest non-exploitative declared-duration apparel shape accepted
    # by the current director contract: one visible Hook, one claim-proof beat, and
    # one final human verdict/CTA beat across two setup runs. This guards against the ledger accidentally
    # reintroducing the default 15s action/phase floors for an explicitly requested 5s job.
    director_document_5s = copy.deepcopy(director_document_v14)
    director_document_5s["compile_mode"] = "single_compile"
    director_document_5s["batch_key"]["duration_seconds"] = 5
    director_document_5s["batch_key"]["resolution"] = "480p"
    director_document_5s["variants"] = [director_document_5s["variants"][0]]
    director_variant_5s = director_document_5s["variants"][0]
    director_timeline_5s = director_variant_5s["canonical_timeline"]
    director_timeline_5s["duration_seconds"] = 5
    source_beats_5s = director_timeline_5s["beats"]
    director_beats_5s = source_beats_5s[:2] + [source_beats_5s[-1]]
    for beat, (start, end) in zip(
            director_beats_5s, ((0, 1.5), (1.5, 3), (3, 5))):
        beat["start_seconds"] = start
        beat["end_seconds"] = end
    for beat, setup_id in zip(director_beats_5s, ("cut-a", "cut-b", "cut-b")):
        beat["camera_setup_id"] = setup_id
    director_timeline_5s["beats"] = director_beats_5s
    director_variant_5s["quality_plan"]["claim_proof_plan"] = (
        director_variant_5s["quality_plan"]["claim_proof_plan"][:1]
    )
    director_beats_5s[0]["spoken_line"] = "Looks boxy?"
    director_beats_5s[-1]["spoken_line"] = "Link below; I'm sold."
    review_by_beat = {
        row.get("beat_id"): row for row in director_variant_5s["voiceover_review"]
        if isinstance(row, dict)
    }
    director_variant_5s["voiceover_review"] = [
        review_by_beat[beat["beat_id"]] for beat in director_beats_5s
    ]
    for review_row, beat in zip(director_variant_5s["voiceover_review"], director_beats_5s):
        review_row["market_line"] = beat.get("spoken_line")
    director_variant_5s["creative_delta"]["spoken_intent_ids"] = [
        beat["spoken_intent_id"] for beat in director_beats_5s
        if beat.get("purpose") != "cta"
    ]
    director_variant_5s["creative_delta"]["hook_text_original"] = director_beats_5s[0]["spoken_line"]
    director_module._refresh_fixture_three_layer_deadlines(director_document_5s)
    director_document_5s = director_module.compile_document(director_document_5s)
    director_bytes_5s = (
        json.dumps(director_document_5s, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    director_evidence_5s = batch_compile_evidence_from_bytes(
        director_bytes_5s, "<current-director-schema-1.4-5s-fixture>", director_module)
    cases = []

    def add_case(name, value, valid, expected=None, metrics=None, previous=None,
                 batch_compile_evidence=None, legacy_source_ledger=None):
        cases.append((name, value, valid, expected, metrics, previous,
                      batch_compile_evidence, legacy_source_ledger))

    def advance_revision(value):
        previous = copy.deepcopy(value)
        value["ledger_revision"] = previous["ledger_revision"] + 1
        value["previous_ledger_sha256"] = canonical_json_sha256(previous)
        return previous

    def latch_rate_limit(value, job_index, error_code, error_message,
                         outcome="confirmed_not_accepted", record_id=None, suffix="1"):
        trigger = value["jobs"][job_index]
        event = {
            "evidence_id": f"rate-limit-self-test-{suffix}",
            "batch_run_id": value["run_id"],
            "trigger_job_key": trigger["job_key"],
            "trigger_request_fingerprint": trigger["request_fingerprint"],
            "error_code": error_code,
            "error_message": error_message,
            "observed_at": f"2026-07-11T08:00:{int(suffix):02d}Z",
            "submission_outcome": outcome,
            "record_id": record_id,
        }
        value["wave_control"]["rate_limit_events"].append(event)
        value["wave_control"].update({
            "effective_max_inflight": 1,
            "rate_limit_override": {
                "kind": "rate_limit_response", "scope": "batch",
                "batch_run_id": event["batch_run_id"], "allowed_max_inflight": 1,
                "trigger_job_key": event["trigger_job_key"],
                "trigger_request_fingerprint": event["trigger_request_fingerprint"],
                "error_code": error_code, "error_message": error_message,
                "observed_at": event["observed_at"], "evidence_id": event["evidence_id"],
            },
        })
        return event

    def refresh_paid_fingerprint(value, job_index=0):
        job = value["jobs"][job_index]
        job["request_fingerprint"] = sha256_text(paid_request_signature(job))

    def refresh_outbound_and_paid_fingerprints(value, job_index=0):
        job = value["jobs"][job_index]
        if isinstance(job.get("outbound_request"), dict):
            job["outbound_request_sha256"] = canonical_json_sha256(job["outbound_request"])
        refresh_paid_fingerprint(value, job_index)

    def set_observed_15s_route(value):
        accepted = runtime["generate_video"]["duration_15"]["accepted_evidence"]
        value["route"] = "mcp_observed"
        value["route_evidence"] = {
            "kind": "accepted_observed",
            "evidence_server_version": runtime["server"]["version"],
            "observation_id": accepted["evidence_id"],
            "observed_duration_seconds": accepted["requested_duration_seconds"],
            "accepted_record_id": accepted["record_id"],
            "accepted_task_id": accepted["task_id"],
            "accepted_terminal_status": accepted["terminal_status"],
            "verified_at": "2026-07-30T08:10:00Z",
        }
        for index, job in enumerate(value["jobs"]):
            job.update({
                "route": "mcp_observed",
                "submission_transport": "mcp_streamable_http",
            })
            refresh_paid_fingerprint(value, index)

    def make_zibuyu_declared_5s(evidence=director_evidence_5s):
        value = make_zibuyu_apparel_ledger(
            runtime, test_registry, execution_policy, evidence)
        value["requested_duration"] = 5
        value["route"] = "mcp_declared"
        value["route_evidence"] = {
            "kind": "declared",
            "evidence_server_version": runtime["server"]["version"],
            "observation_id": None,
            "observed_duration_seconds": None,
            "verified_at": "2026-07-30T08:10:00Z",
        }
        generated = value["capability_snapshot"]["generate_video"]
        if 5 not in generated["declared_durations"]:
            raise ValueError("current runtime capability fixture must declare 5 seconds")
        generated["declared_resolutions"] = ["480p", "720p"]
        job = value["jobs"][0]
        job.update({
            "duration": 5,
            "resolution": "480p",
            "route": "mcp_declared",
            "submission_transport": "mcp_streamable_http",
        })
        job["outbound_request"]["arguments"].update({
            "duration": 5,
            "resolution": "480p",
        })
        refresh_outbound_and_paid_fingerprints(value)
        return value

    add_case("positive_user12_completed", base, True, metrics={"jobs": 1, "waves": 1, "effective": 12})
    generic_explicit = copy.deepcopy(base)
    generic_explicit["workflow_kind"] = GENERIC_POPBOOM_KIND
    generic_explicit["jobs"][0]["job_kind"] = GENERIC_POPBOOM_KIND
    add_case("positive_explicit_generic_without_director_receipt", generic_explicit, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12})

    for real_market in ("US", "DE"):
        real_document = director_module._fixture_v14(market=real_market)
        real_document["batch_key"]["resolution"] = "720p"
        real_document = director_module.compile_document(real_document)
        real_bytes = (
            json.dumps(real_document, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        real_evidence = batch_compile_evidence_from_bytes(
            real_bytes, f"<current-director-schema-1.4-{real_market.lower()}-fixture>",
            director_module,
        )
        real_planned = make_zibuyu_apparel_ledger(
            runtime, test_registry, execution_policy, real_evidence,
        )
        add_case(
            f"positive_real_v7_{real_market.lower()}_planned_integration",
            real_planned, True,
            metrics={"jobs": 1, "waves": 1, "effective": 12},
            batch_compile_evidence=real_evidence,
        )
        real_started = copy.deepcopy(real_planned)
        real_previous = advance_revision(real_started)
        real_started["jobs"][0].update({
            "state": "submission_started",
            "submission_started_at": "2026-08-04T08:02:00Z",
        })
        add_case(
            f"positive_real_v7_{real_market.lower()}_authorizes_paid_integration",
            real_started, True,
            metrics={
                "jobs": 1, "waves": 1, "effective": 12,
                "history": True, "paid": True, "authorized": 1,
                "market_binding": True,
            },
            previous=real_previous,
            batch_compile_evidence=real_evidence,
        )

    observed_planned = make_planned_batch_ledger(
        runtime, test_registry, execution_policy, 1)
    set_observed_15s_route(observed_planned)
    add_case("positive_new_15s_mcp_observed_planned", observed_planned, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12})

    missing_upload_provenance = copy.deepcopy(observed_planned)
    missing_upload_provenance["artifacts"][0].pop("upload_transport")
    add_case("new_submission_requires_upload_provenance", missing_upload_provenance, False,
             "missing_upload_provenance")

    unauthorized_derivative = copy.deepcopy(observed_planned)
    unauthorized_derivative["artifacts"][0].update({
        "content_variant": "derivative",
        "source_sha256": "9" * 64,
        "transformation_reason": "server size rejection",
        "transformation_authorized": False,
    })
    add_case("new_derivative_requires_user_authorization", unauthorized_derivative, False,
             "missing_transformation_authorization")

    observed_started = copy.deepcopy(observed_planned)
    observed_started_previous = advance_revision(observed_started)
    observed_started["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-30T08:11:00Z",
    })
    add_case("positive_new_15s_mcp_observed_authorizes_paid", observed_started, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": True, "paid": True, "slots": 1, "authorized": 1},
             previous=observed_started_previous)

    observed_evidence_tamper = copy.deepcopy(observed_planned)
    observed_evidence_tamper["route_evidence"]["accepted_record_id"] = 200705
    add_case("mcp_observed_rejects_evidence_tamper", observed_evidence_tamper, False,
             "observed_15s_evidence_mismatch")

    observed_wrong_duration = copy.deepcopy(observed_planned)
    observed_wrong_duration["requested_duration"] = 10
    observed_wrong_duration["jobs"][0]["duration"] = 10
    refresh_paid_fingerprint(observed_wrong_duration)
    add_case("mcp_observed_rejects_non_15s_job", observed_wrong_duration, False,
             "invalid_observed_route")

    invalidated_provisional = make_planned_batch_ledger(
        runtime, test_registry, execution_policy, 1)
    invalidated_provisional["route"] = "mcp_provisional"
    invalidated_provisional["route_evidence"] = {
        "kind": "provisional_observed",
        "evidence_server_version": runtime["server"]["version"],
        "observation_id": "obs-historical-15s",
        "observed_duration_seconds": 15.07,
        "verified_at": "2026-07-11T08:00:00Z",
    }
    invalidated_provisional["jobs"][0].update({
        "route": "mcp_provisional",
        "submission_transport": "mcp_streamable_http",
    })
    refresh_paid_fingerprint(invalidated_provisional)
    add_case("new_15s_provisional_route_rejected", invalidated_provisional, False,
             "invalid_15s_provisional_route")

    omitted_paid_kind = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    omitted_paid_kind.pop("workflow_kind")
    omitted_paid_kind["jobs"][0].pop("job_kind")
    omitted_paid_previous = advance_revision(omitted_paid_kind)
    omitted_paid_kind["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("paid_authorization_rejects_omitted_workflow_kind", omitted_paid_kind, False,
             "paid_workflow_kind_required", previous=omitted_paid_previous)

    disguised_apparel = make_planned_batch_ledger(
        runtime, test_registry, execution_policy, 1)
    disguised_apparel["jobs"][0]["reference_bindings"][0]["role"] = "apparel_three_view"
    refresh_paid_fingerprint(disguised_apparel)
    disguised_apparel_previous = advance_revision(disguised_apparel)
    disguised_apparel["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("generic_kind_cannot_downgrade_apparel_paid_job", disguised_apparel, False,
             "apparel_workflow_kind_required", previous=disguised_apparel_previous)

    zibuyu_started = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    zibuyu_started_previous = advance_revision(zibuyu_started)
    zibuyu_started["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("positive_zibuyu_evidence_receipt_authorizes_paid", zibuyu_started, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": True, "paid": True, "authorized": 1,
                      "market_binding": True},
             previous=zibuyu_started_previous,
             batch_compile_evidence=director_evidence)

    ordinary_v5 = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, legacy_v5_prior_evidence)
    add_case(
        "legacy_v5_planned_without_exact_resume_rejected", ordinary_v5, False,
        "legacy_v5_resume_authorization_required",
    )

    legacy_v5_source = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, legacy_v5_prior_evidence)
    legacy_v5_resume_planned = attach_legacy_v5_exact_resume(
        copy.deepcopy(legacy_v5_source), legacy_v5_source,
    )
    add_case(
        "positive_legacy_v5_exact_resume_planned",
        legacy_v5_resume_planned,
        True,
        metrics={"jobs": 1, "waves": 1, "effective": 12},
        legacy_source_ledger=legacy_v5_source,
    )
    legacy_v5_resume_started = copy.deepcopy(legacy_v5_resume_planned)
    legacy_v5_resume_previous = advance_revision(legacy_v5_resume_started)
    legacy_v5_resume_started["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-08-04T08:01:00Z",
    })
    add_case(
        "positive_legacy_v5_exact_resume_authorizes_paid",
        legacy_v5_resume_started,
        True,
        metrics={"jobs": 1, "waves": 1, "effective": 12,
                 "history": True, "paid": True, "authorized": 1,
                 "legacy_binding": True},
        previous=legacy_v5_resume_previous,
        batch_compile_evidence=director_evidence_v5,
        legacy_source_ledger=legacy_v5_source,
    )

    for legacy_name, legacy_mutate in (
        (
            "legacy_v5_prompt_changed_rejected",
            lambda value: value["jobs"][0].update({"compiled_prompt": value["jobs"][0]["compiled_prompt"] + " changed"}),
        ),
        (
            "legacy_v5_reference_changed_rejected",
            lambda value: value["jobs"][0]["reference_bindings"][0].update({"sha256": "0" * 64}),
        ),
        (
            "legacy_v5_receipt_hash_changed_rejected",
            lambda value: value["jobs"][0]["director_receipt"].update({"quality_plan_sha256": "0" * 64}),
        ),
    ):
        drifted = copy.deepcopy(legacy_v5_resume_planned)
        legacy_mutate(drifted)
        refresh_outbound_and_paid_fingerprints(drifted)
        add_case(
            legacy_name, drifted, False, "legacy_v5_paid_package_hash_mismatch",
            legacy_source_ledger=legacy_v5_source,
        )

    for field, case_name in (
        ("market_prompt_profile_sha256", "v7_market_profile_hash_drift_rejected"),
        ("generation_controls_sha256", "v7_generation_controls_hash_drift_rejected"),
        ("voiceover_review_sha256", "v7_voiceover_review_hash_drift_rejected"),
        ("three_layer_deadlines_sha256", "v7_three_layer_deadlines_hash_drift_rejected"),
    ):
        drifted = make_zibuyu_apparel_ledger(
            runtime, test_registry, execution_policy, director_evidence)
        drifted["jobs"][0]["director_receipt"][field] = "0" * 64
        refresh_paid_fingerprint(drifted)
        drifted_previous = advance_revision(drifted)
        drifted["jobs"][0].update({
            "state": "submission_started", "submission_started_at": "2026-08-04T08:01:00Z",
        })
        add_case(
            case_name, drifted, False, "external_director_receipt_mismatch",
            previous=drifted_previous, batch_compile_evidence=director_evidence,
        )

    cross_market = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    cross_market["jobs"][0]["director_receipt"].update({
        "market": "DE", "voiceover_language": "de-DE",
        "market_prompt_profile_id": "de_champion_v1",
    })
    refresh_paid_fingerprint(cross_market)
    add_case(
        "v7_cross_market_receipt_rejected", cross_market, False,
        "market_model_language_profile_mismatch",
    )

    zibuyu_5s_planned = make_zibuyu_declared_5s()
    add_case("positive_zibuyu_5s_declared_planned", zibuyu_5s_planned, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12})

    zibuyu_5s_started = copy.deepcopy(zibuyu_5s_planned)
    zibuyu_5s_started_previous = advance_revision(zibuyu_5s_started)
    zibuyu_5s_started["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("positive_zibuyu_5s_declared_authorizes_paid", zibuyu_5s_started, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": True, "paid": True, "authorized": 1},
             previous=zibuyu_5s_started_previous,
             batch_compile_evidence=director_evidence_5s)

    zibuyu_5s_duration_drift = make_zibuyu_declared_5s(director_evidence)
    zibuyu_5s_duration_drift_previous = advance_revision(zibuyu_5s_duration_drift)
    zibuyu_5s_duration_drift["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("zibuyu_external_duration_drift", zibuyu_5s_duration_drift, False,
             "external_duration_mismatch",
             previous=zibuyu_5s_duration_drift_previous,
             batch_compile_evidence=director_evidence)

    zibuyu_5s_resolution_drift = make_zibuyu_declared_5s()
    zibuyu_5s_resolution_drift["jobs"][0]["resolution"] = "720p"
    zibuyu_5s_resolution_drift["jobs"][0]["outbound_request"]["arguments"]["resolution"] = "720p"
    refresh_outbound_and_paid_fingerprints(zibuyu_5s_resolution_drift)
    zibuyu_5s_resolution_drift_previous = advance_revision(zibuyu_5s_resolution_drift)
    zibuyu_5s_resolution_drift["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("zibuyu_external_resolution_drift", zibuyu_5s_resolution_drift, False,
             "external_resolution_mismatch",
             previous=zibuyu_5s_resolution_drift_previous,
             batch_compile_evidence=director_evidence_5s)

    zibuyu_5s_ratio_drift = make_zibuyu_declared_5s()
    zibuyu_5s_ratio_drift["capability_snapshot"]["generate_video"]["declared_ratios"].append("16:9")
    zibuyu_5s_ratio_drift["jobs"][0]["ratio"] = "16:9"
    zibuyu_5s_ratio_drift["jobs"][0]["outbound_request"]["arguments"]["ratio"] = "16:9"
    refresh_outbound_and_paid_fingerprints(zibuyu_5s_ratio_drift)
    zibuyu_5s_ratio_drift_previous = advance_revision(zibuyu_5s_ratio_drift)
    zibuyu_5s_ratio_drift["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("zibuyu_external_aspect_ratio_drift", zibuyu_5s_ratio_drift, False,
             "external_aspect_ratio_mismatch",
             previous=zibuyu_5s_ratio_drift_previous,
             batch_compile_evidence=director_evidence_5s)

    mismatched_model_document = director_module._fixture_v14(market="DE")
    original_model = "德1"
    mismatched_model_document["batch_key"]["model_preset"] = "德2"
    mismatched_model_document["batch_key"]["resolution"] = "720p"
    mismatched_model_document = director_module.compile_document(mismatched_model_document)
    mismatched_model_bytes = (
        json.dumps(mismatched_model_document, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    mismatched_model_evidence = batch_compile_evidence_from_bytes(
        mismatched_model_bytes, "<current-director-schema-1.4-model-drift>", director_module,
    )
    zibuyu_5s_model_drift = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, mismatched_model_evidence,
    )
    original_preset = test_registry["presets"][original_model]
    zibuyu_5s_model_drift["models"] = [{
        "name": original_model,
        "asset_id": original_preset["asset_id"],
        "validation_status": "valid",
        "validated_at": "2026-07-30T08:10:00Z",
    }]
    model_drift_job = zibuyu_5s_model_drift["jobs"][0]
    model_drift_job.update({
        "model_name": original_model,
        "model_preset": original_model,
        "asset_id": original_preset["asset_id"],
        "asset_identity_sha256": original_preset["identity_image_sha256"],
        "asset_identity_version": original_preset["identity_version"],
    })
    model_drift_job["outbound_request"]["arguments"]["ref_image_urls"][0] = (
        f"asset://{original_preset['asset_id']}"
    )
    refresh_outbound_and_paid_fingerprints(zibuyu_5s_model_drift)
    zibuyu_5s_model_drift_previous = advance_revision(zibuyu_5s_model_drift)
    zibuyu_5s_model_drift["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("zibuyu_external_model_preset_drift", zibuyu_5s_model_drift, False,
             "external_model_preset_mismatch",
             previous=zibuyu_5s_model_drift_previous,
             batch_compile_evidence=mismatched_model_evidence)

    zibuyu_5s_undeclared_resolution = make_zibuyu_declared_5s()
    zibuyu_5s_undeclared_resolution["jobs"][0]["resolution"] = "999p"
    zibuyu_5s_undeclared_resolution["jobs"][0]["outbound_request"]["arguments"]["resolution"] = "999p"
    refresh_outbound_and_paid_fingerprints(zibuyu_5s_undeclared_resolution)
    add_case("zibuyu_mcp_undeclared_resolution", zibuyu_5s_undeclared_resolution, False,
             "undeclared_resolution")

    zibuyu_5s_undeclared_ratio = make_zibuyu_declared_5s()
    zibuyu_5s_undeclared_ratio["jobs"][0]["ratio"] = "1:1"
    zibuyu_5s_undeclared_ratio["jobs"][0]["outbound_request"]["arguments"]["ratio"] = "1:1"
    refresh_outbound_and_paid_fingerprints(zibuyu_5s_undeclared_ratio)
    add_case("zibuyu_mcp_undeclared_ratio", zibuyu_5s_undeclared_ratio, False,
             "undeclared_ratio")

    historical_v5_terminal = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, legacy_v5_prior_evidence)
    historical_v5_terminal["updated_at"] = "2026-08-03T08:05:00Z"
    historical_v5_job = historical_v5_terminal["jobs"][0]
    historical_v5_job.update({
        "state": "succeeded", "record_id": 205555, "task_id": "task-v5-historical",
        "submission_started_at": "2026-08-03T08:01:00Z",
        "submitted_at": "2026-08-03T08:01:01Z",
        "last_polled_at": "2026-08-03T08:04:00Z", "next_poll_at": None,
        "poll_attempts": 2, "video_url": "https://cdn.example.invalid/v5-history.mp4",
        "video_url_status": "usable", "next_action": "report",
        "submission_reported": True, "completion_reported": True,
    })
    refresh_paid_fingerprint(historical_v5_terminal)
    add_case(
        "positive_historical_terminal_v5_remains_read_poll_report_compatible",
        historical_v5_terminal, True,
        metrics={"jobs": 1, "waves": 1, "effective": 12},
    )
    historical_v5_generate = copy.deepcopy(historical_v5_terminal)
    historical_v5_generate["jobs"][0]["next_action"] = "generate_video"
    add_case(
        "historical_v5_record_id_never_returns_to_generate",
        historical_v5_generate, False, "unsafe_accepted_resubmission",
    )

    historical_v4_terminal = copy.deepcopy(base)
    historical_v4_terminal["workflow_kind"] = ZIBUYU_APPAREL_KIND
    historical_v4_job = historical_v4_terminal["jobs"][0]
    historical_v4_job["job_kind"] = ZIBUYU_APPAREL_KIND
    historical_v4_job["director_receipt"] = {
        "variant_id": historical_v4_job["variant_id"],
        "timeline_id": historical_v4_job["timeline_id"],
        "timeline_version": historical_v4_job["timeline_version"],
        "batch_compile_sha256": "a" * 64,
        "schema_version": "1.3",
        "quality_contract_id": LEGACY_DIRECTOR_QUALITY_CONTRACT_ID,
        "serializer_id": "canonical_prompt_v4",
        "quality_plan_sha256": "b" * 64,
        "research_bundle_sha256": "c" * 64,
        "canonical_beats_sha256": "d" * 64,
        "compiled_text_sha256": historical_v4_job["prompt_sha256"],
        "director_valid": True,
        "eligible_for_new_submission": True,
    }
    refresh_paid_fingerprint(historical_v4_terminal)
    add_case("positive_historical_terminal_v4_remains_auditable",
             historical_v4_terminal, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12})

    outbound_mutations = [
        ("zibuyu_missing_asset_identity_sha256",
         lambda x: x["jobs"][0].pop("asset_identity_sha256"),
         "invalid_asset_identity_sha256"),
        ("zibuyu_wrong_asset_identity_sha256",
         lambda x: x["jobs"][0].update({"asset_identity_sha256": "0" * 64}),
         "asset_identity_sha256_mismatch"),
        ("zibuyu_missing_identity_version",
         lambda x: x["jobs"][0].pop("asset_identity_version"),
         "asset_identity_version_mismatch"),
        ("zibuyu_missing_outbound_request",
         lambda x: x["jobs"][0].pop("outbound_request"),
         "missing_outbound_request"),
        ("zibuyu_outbound_reference_reordered",
         lambda x: x["jobs"][0]["outbound_request"]["arguments"]["ref_image_urls"].reverse(),
         "outbound_reference_order_mismatch"),
        ("zibuyu_outbound_prompt_tamper",
         lambda x: x["jobs"][0]["outbound_request"]["arguments"].update({"prompt": "tampered"}),
         "outbound_parameter_mismatch"),
        ("zibuyu_outbound_duration_tamper",
         lambda x: x["jobs"][0]["outbound_request"]["arguments"].update({"duration": 10}),
         "outbound_parameter_mismatch"),
        ("zibuyu_reference_human_pixels_present",
         lambda x: x["jobs"][0]["reference_bindings"][0].update({"human_identity_pixels_absent": False}),
         "apparel_human_identity_pixels_present"),
        ("zibuyu_artifact_human_pixels_present",
         lambda x: x["artifacts"][0].update({"human_identity_pixels_absent": False}),
         "apparel_artifact_human_identity_pixels_present"),
        ("zibuyu_missing_reference_identity_cue_audit",
         lambda x: x["jobs"][0]["reference_bindings"][0].pop("identity_cue_audit"),
         "identity_cue_audit_invalid"),
        ("zibuyu_reference_hands_present_despite_passed_summary",
         lambda x: (
             x["jobs"][0]["reference_bindings"][0]["identity_cue_audit"].update({
                 "hands_fingers_nails_present": True
             }),
             x["jobs"][0]["reference_bindings"][0].update({
                 "identity_cue_audit_sha256": canonical_json_sha256(
                     x["jobs"][0]["reference_bindings"][0]["identity_cue_audit"])
             }),
         ),
         "identity_cue_audit_failed"),
        ("zibuyu_reference_identity_audit_wrong_bytes",
         lambda x: (
             x["jobs"][0]["reference_bindings"][0]["identity_cue_audit"].update({
                 "audited_sha256": "0" * 64
             }),
             x["jobs"][0]["reference_bindings"][0].update({
                 "identity_cue_audit_sha256": canonical_json_sha256(
                     x["jobs"][0]["reference_bindings"][0]["identity_cue_audit"])
             }),
         ),
         "identity_cue_audit_failed"),
    ]
    for name, mutate, expected in outbound_mutations:
        value = make_zibuyu_apparel_ledger(
            runtime, test_registry, execution_policy, director_evidence)
        mutate(value)
        refresh_outbound_and_paid_fingerprints(value)
        add_case(name, value, False, expected)

    unsigned_outbound_tamper = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    unsigned_outbound_tamper["jobs"][0]["outbound_request"]["arguments"]["ratio"] = "16:9"
    add_case("zibuyu_unsigned_outbound_payload_tamper", unsigned_outbound_tamper, False,
             "outbound_request_sha256_mismatch")

    product_info_bound = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    product_info_bound["jobs"][0]["product_info"] = (
        '{"brand":"拓展平台","sku":"SELF-TEST"}'
    )
    product_info_bound["jobs"][0]["outbound_request"]["arguments"]["product_info"] = product_info_bound["jobs"][0]["product_info"]
    refresh_outbound_and_paid_fingerprints(product_info_bound)
    add_case("positive_zibuyu_product_info_exactly_bound", product_info_bound, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12})

    product_info_tamper = copy.deepcopy(product_info_bound)
    product_info_tamper["jobs"][0]["outbound_request"]["arguments"]["product_info"] = '{"brand":"拓展平台","sku":"OTHER"}'
    refresh_outbound_and_paid_fingerprints(product_info_tamper)
    add_case("zibuyu_product_info_mismatch", product_info_tamper, False,
             "outbound_parameter_mismatch")

    product_info_missing = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    product_info_missing["jobs"][0].pop("product_info")
    product_info_missing["jobs"][0]["outbound_request"]["arguments"].pop("product_info")
    refresh_outbound_and_paid_fingerprints(product_info_missing)
    add_case("zibuyu_product_info_missing", product_info_missing, False,
             "outbound_argument_shape_mismatch")

    product_info_wrong_brand = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    product_info_wrong_brand["jobs"][0]["product_info"] = '{"brand":"Zibuyu","sku":"SELF-TEST"}'
    product_info_wrong_brand["jobs"][0]["outbound_request"]["arguments"]["product_info"] = product_info_wrong_brand["jobs"][0]["product_info"]
    refresh_outbound_and_paid_fingerprints(product_info_wrong_brand)
    add_case("zibuyu_product_info_wrong_brand", product_info_wrong_brand, False,
             "invalid_product_info_brand")

    product_info_missing_sku = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    product_info_missing_sku["jobs"][0]["product_info"] = '{"brand":"拓展平台","sku":""}'
    product_info_missing_sku["jobs"][0]["outbound_request"]["arguments"]["product_info"] = product_info_missing_sku["jobs"][0]["product_info"]
    refresh_outbound_and_paid_fingerprints(product_info_missing_sku)
    add_case("zibuyu_product_info_missing_sku", product_info_missing_sku, False,
             "missing_product_info_sku")

    streaming_evidence = copy.deepcopy(director_evidence)
    streaming_evidence["document"]["streaming_release"] = {
        "child_run_id": "streaming-parent:variant-ma",
    }
    streaming_bytes = (json.dumps(
        streaming_evidence["document"], ensure_ascii=False, indent=2,
    ) + "\n").encode("utf-8")
    streaming_evidence["sha256"] = hashlib.sha256(streaming_bytes).hexdigest()
    for receipt in streaming_evidence["director_result"].get("director_receipts", []):
        receipt["batch_compile_sha256"] = streaming_evidence["sha256"]
    streaming_started = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, streaming_evidence)
    streaming_started["run_id"] = "streaming-parent:variant-ma"
    streaming_started_previous = advance_revision(streaming_started)
    streaming_started["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("positive_streaming_child_run_binding", streaming_started, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": True, "paid": True, "authorized": 1},
             previous=streaming_started_previous,
             batch_compile_evidence=streaming_evidence)
    streaming_run_drift = copy.deepcopy(streaming_started)
    streaming_run_drift_previous = copy.deepcopy(streaming_started_previous)
    streaming_run_drift["run_id"] = "unbound-child-run"
    streaming_run_drift_previous["run_id"] = "unbound-child-run"
    streaming_run_drift["previous_ledger_sha256"] = canonical_json_sha256(streaming_run_drift_previous)
    add_case("streaming_child_run_binding_rejects_drift", streaming_run_drift, False,
             "streaming_child_run_id_mismatch", previous=streaming_run_drift_previous,
             batch_compile_evidence=streaming_evidence)

    zibuyu_mutations = [
        ("zibuyu_missing_job_kind",
         lambda x: x["jobs"][0].pop("job_kind"),
         "workflow_job_kind_mismatch"),
        ("zibuyu_missing_director_receipt",
         lambda x: x["jobs"][0].pop("director_receipt"),
         "missing_director_receipt"),
        ("zibuyu_legacy_schema_receipt",
         lambda x: x["jobs"][0]["director_receipt"].update({"schema_version": "1.1"}),
         "legacy_director_receipt"),
        ("zibuyu_legacy_quality_receipt",
         lambda x: x["jobs"][0]["director_receipt"].update({"quality_contract_id": "zibuyu_ugc_quality_v1"}),
         "legacy_director_receipt"),
        ("zibuyu_legacy_serializer_receipt",
         lambda x: x["jobs"][0]["director_receipt"].update({"serializer_id": "canonical_prompt_v2"}),
         "legacy_director_receipt"),
        ("zibuyu_missing_quality_plan_hash",
         lambda x: x["jobs"][0]["director_receipt"].pop("quality_plan_sha256"),
         "invalid_director_receipt_hash"),
        ("zibuyu_missing_research_bundle_hash",
         lambda x: x["jobs"][0]["director_receipt"].pop("research_bundle_sha256"),
         "invalid_director_receipt_hash"),
        ("zibuyu_batch_compile_hash_mismatch",
         lambda x: x["jobs"][0]["director_receipt"].update({"batch_compile_sha256": "a" * 64}),
         "director_batch_compile_hash_mismatch"),
        ("zibuyu_compiled_text_hash_mismatch",
         lambda x: x["jobs"][0]["director_receipt"].update({"compiled_text_sha256": "b" * 64}),
         "director_compiled_text_hash_mismatch"),
        ("zibuyu_director_not_valid",
         lambda x: x["jobs"][0]["director_receipt"].update({"director_valid": False}),
         "director_validation_not_valid"),
        ("zibuyu_not_eligible_for_submission",
         lambda x: x["jobs"][0]["director_receipt"].update({"eligible_for_new_submission": False}),
         "director_submission_not_eligible"),
        ("zibuyu_receipt_variant_mismatch",
         lambda x: x["jobs"][0]["director_receipt"].update({"variant_id": "variant-other"}),
         "director_variant_mismatch"),
        ("zibuyu_receipt_timeline_mismatch",
         lambda x: x["jobs"][0]["director_receipt"].update({"timeline_version": 999}),
         "director_timeline_mismatch"),
    ]
    for name, mutate, expected in zibuyu_mutations:
        value = make_zibuyu_apparel_ledger(
            runtime, test_registry, execution_policy, director_evidence)
        mutate(value)
        refresh_paid_fingerprint(value)
        add_case(name, value, False, expected)

    static_zibuyu = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    static_prompt = "Global: authentic UGC. Shot 1: creator stands still and speaks about the top."
    static_job = static_zibuyu["jobs"][0]
    static_job["compiled_prompt"] = static_prompt
    static_job["prompt_sha256"] = sha256_text(static_prompt)
    static_job["director_receipt"]["compiled_text_sha256"] = sha256_text(static_prompt)
    refresh_paid_fingerprint(static_zibuyu)
    add_case("zibuyu_static_prompt_with_relabelled_receipt", static_zibuyu, False,
             "legacy_or_static_director_prompt")

    unbound_receipt = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    unbound_receipt["jobs"][0]["director_receipt"]["quality_plan_sha256"] = "c" * 64
    add_case("zibuyu_receipt_tamper_without_fingerprint_refresh", unbound_receipt, False,
             "request_fingerprint_mismatch")

    missing_external = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    missing_external_previous = advance_revision(missing_external)
    missing_external["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("zibuyu_paid_rejects_missing_batch_compile_file", missing_external, False,
             "missing_batch_compile_evidence", previous=missing_external_previous)

    tampered_external = copy.deepcopy(director_evidence)
    tampered_external["sha256"] = "d" * 64
    tampered_external_ledger = make_zibuyu_apparel_ledger(
        runtime, test_registry, execution_policy, director_evidence)
    tampered_external_previous = advance_revision(tampered_external_ledger)
    tampered_external_ledger["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("zibuyu_paid_rejects_batch_file_hash_tamper", tampered_external_ledger, False,
             "batch_compile_file_hash_mismatch", previous=tampered_external_previous,
             batch_compile_evidence=tampered_external)
    for count, expected_waves in ((5, 1), (10, 1), (12, 1), (13, 2), (25, 3)):
        add_case(f"positive_user12_jobs_{count}",
                 make_planned_batch_ledger(runtime, test_registry, execution_policy, count),
                 True, metrics={"jobs": count, "waves": expected_waves, "effective": 12})

    rate_cases = (
        ("http_429", "HTTP_429", "request rejected", "1"),
        ("too_many_requests", "RATE_LIMIT", "too_many_requests", "2"),
        ("concurrency_limit", "LIMIT", "concurrency_limit", "3"),
    )
    rate_ok = None
    for name, code, message, suffix in rate_cases:
        value = make_planned_batch_ledger(runtime, test_registry, execution_policy, 2)
        value["jobs"][0].update({"error_code": code, "error_message": message})
        latch_rate_limit(value, 0, code, message, suffix=suffix)
        add_case(f"positive_rate_latch_{name}", value, True,
                 metrics={"jobs": 2, "waves": 1, "effective": 1})
        if rate_ok is None:
            rate_ok = value

    rate_recovered = copy.deepcopy(rate_ok)
    recovered = rate_recovered["jobs"][0]
    recovered.update({
        "state": "queued", "record_id": 9001, "task_id": "task-9001",
        "submission_started_at": "2026-07-11T08:01:00Z",
        "submitted_at": "2026-07-11T08:01:01Z", "next_action": "check_task",
        "error_code": None, "error_message": None,
    })
    add_case("positive_rate_latch_survives_trigger_recovery", rate_recovered, True,
             metrics={"jobs": 2, "waves": 1, "effective": 1, "slots": 0})

    partial_rate = make_planned_batch_ledger(runtime, test_registry, execution_policy, 5)
    for index in range(3):
        partial_rate["jobs"][index].update({
            "state": "queued", "record_id": 9100 + index,
            "task_id": f"task-{9100 + index}",
            "submission_started_at": "2026-07-11T08:01:00Z",
            "submitted_at": "2026-07-11T08:01:01Z", "next_action": "check_task",
        })
    partial_rate["jobs"][3].update({"error_code": "HTTP_429",
                                     "error_message": "too_many_requests"})
    latch_rate_limit(partial_rate, 3, "HTTP_429", "too_many_requests", suffix="4")
    add_case("positive_partial_acceptance_rate_latch", partial_rate, True,
             metrics={"jobs": 5, "waves": 1, "effective": 1, "slots": 0})

    accepted_poll = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    accepted_poll["jobs"][0].update({
        "state": "queued", "record_id": 9201, "task_id": "task-9201",
        "submission_started_at": "2026-07-11T08:01:00Z",
        "submitted_at": "2026-07-11T08:01:01Z", "next_action": "check_task",
    })
    add_case("positive_accepted_record_poll_only", accepted_poll, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12, "slots": 0})

    unknown_reconcile = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    unknown_reconcile["jobs"][0].update({
        "state": "submission_unknown", "submission_started_at": "2026-07-11T08:01:00Z",
        "next_action": "reconcile_submission",
    })
    add_case("positive_unknown_reconcile_only", unknown_reconcile, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12, "slots": 0})

    new_run = copy.deepcopy(rate_ok)
    new_run["run_id"] = "run-self-test-after-rate-limit"
    new_run["jobs"][0].update({"error_code": None, "error_message": None})
    new_run["wave_control"].update({
        "effective_max_inflight": 12, "rate_limit_events": [],
        "rate_limit_override": None,
    })
    add_case("positive_new_run_resets_rate_latch", new_run, True,
             metrics={"jobs": 2, "waves": 1, "effective": 12})

    revision_zero = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    add_case("positive_revision0_preflight_no_paid", revision_zero, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": False, "paid": False, "slots": 1})

    anchored_planned = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    anchored_planned_previous = advance_revision(anchored_planned)
    add_case("positive_anchored_planned_selects_slot", anchored_planned, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": True, "paid": False, "slots": 1},
             previous=anchored_planned_previous)

    started_submission = copy.deepcopy(anchored_planned)
    started_submission_previous = advance_revision(started_submission)
    started_submission["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("positive_anchored_submission_started_allows_paid", started_submission, True,
             metrics={"jobs": 1, "waves": 1, "effective": 12,
                      "history": True, "paid": True, "slots": 1, "authorized": 1},
             previous=started_submission_previous)

    for count in (10, 12):
        started_batch = make_planned_batch_ledger(runtime, test_registry, execution_policy, count)
        advance_revision(started_batch)
        started_batch_previous = advance_revision(started_batch)
        for job in started_batch["jobs"]:
            job.update({"state": "submission_started",
                        "submission_started_at": "2026-07-11T08:01:00Z"})
        add_case(f"positive_anchored_parallel_started_{count}", started_batch, True,
                 metrics={"jobs": count, "waves": 1, "effective": 12,
                          "history": True, "paid": True, "slots": count,
                          "authorized": count},
                 previous=started_batch_previous)

    started_25 = make_planned_batch_ledger(runtime, test_registry, execution_policy, 25)
    advance_revision(started_25)
    started_25_previous = advance_revision(started_25)
    for job in started_25["jobs"][:12]:
        job.update({"state": "submission_started",
                    "submission_started_at": "2026-07-11T08:01:00Z"})
    add_case("positive_anchored_first_wave_12_of_25", started_25, True,
             metrics={"jobs": 25, "waves": 3, "effective": 12,
                      "history": True, "paid": True, "slots": 12,
                      "authorized": 12},
             previous=started_25_previous)

    anchored_rate = copy.deepcopy(rate_ok)
    anchored_rate_previous = advance_revision(anchored_rate)
    anchored_rate["jobs"][0].update({"error_code": None, "error_message": None})
    add_case("positive_anchored_rate_latch_selects_one", anchored_rate, True,
             metrics={"jobs": 2, "waves": 1, "effective": 1,
                      "history": True, "paid": False, "slots": 1},
             previous=anchored_rate_previous)

    anchored_rate_started = copy.deepcopy(anchored_rate)
    anchored_rate_started_previous = advance_revision(anchored_rate_started)
    anchored_rate_started["jobs"][0].update({
        "state": "submission_started", "submission_started_at": "2026-07-11T08:01:00Z",
    })
    add_case("positive_anchored_rate_started_allows_one", anchored_rate_started, True,
             metrics={"jobs": 2, "waves": 1, "effective": 1,
                      "history": True, "paid": True, "slots": 1, "authorized": 1},
             previous=anchored_rate_started_previous)

    mutations = [
        ("secret_leak", lambda x: x.update({"bearer_token": "Bearer hidden"}), "prohibited_secret_field"),
        ("duplicate_fingerprint", lambda x: x["jobs"].append(copy.deepcopy(x["jobs"][0])), "duplicate_request_fingerprint"),
        ("fingerprint_tamper", lambda x: x["jobs"][0].update({"request_fingerprint": "3" * 64}), "request_fingerprint_mismatch"),
        ("missing_record_id", lambda x: x["jobs"][0].update({"record_id": None}), "state_field_mismatch"),
        ("wrong_15s_route", lambda x: x.update({"route": "mcp_declared"}) or x["jobs"][0].update({"route": "mcp_declared"}), "invalid_15s_route"),
        ("duplicate_sha", lambda x: x["artifacts"].append({**copy.deepcopy(x["artifacts"][0]), "artifact_id": "art-2", "variant_id": "red", "local_path": "C:/refs/red.png"}), "duplicate_artifact_sha256"),
        ("wrong_asset", lambda x: x["models"][0].update({"asset_id": "asset-wrong"}), "model_asset_mismatch"),
        ("route_evidence_version", lambda x: x["route_evidence"].update({"evidence_server_version": "different"}), "route_evidence_version_mismatch"),
        ("balance_not_passed", lambda x: x["balance_gate"].update({"passed": False}), "balance_gate_not_passed"),
        ("binding_sha", lambda x: x["jobs"][0]["reference_bindings"][0].update({"sha256": "5" * 64}), "artifact_binding_mismatch"),
        ("binding_variant", lambda x: x["jobs"][0]["reference_bindings"][0].update({"variant_id": "red"}), "artifact_binding_mismatch"),
        ("wrong_submission_transport", lambda x: x["jobs"][0].update({"submission_transport": "popboom_ui"}), "submission_transport_mismatch"),
        ("second_download_attempt", lambda x: x["jobs"][0]["download"].update({"attempts": 2}), "download_attempt_limit"),
        ("accepted_record_resubmit_flag", lambda x: x["jobs"][0].update({"resubmit_allowed": True}),
         "unsafe_accepted_resubmission"),
        ("accepted_record_generate_action", lambda x: x["jobs"][0].update({"next_action": "generate_video"}),
         "unsafe_accepted_resubmission"),
        ("default_disguised", lambda x: x["wave_control"].update({
            "max_inflight": 2, "max_inflight_source": "default",
            "max_inflight_evidence": None, "effective_max_inflight": 2}),
         "user_concurrency_policy_mismatch"),
        ("user12_missing_evidence", lambda x: x["wave_control"].pop("max_inflight_evidence"),
         "missing_max_inflight_evidence"),
        ("user12_allowed_not12", lambda x: x["wave_control"]["max_inflight_evidence"].update({"allowed_max_inflight": 11}),
         "max_inflight_evidence_limit_exceeded"),
        ("user12_wrong_evidence_id", lambda x: x["wave_control"]["max_inflight_evidence"].update({"evidence_id": "different"}),
         "user_concurrency_evidence_mismatch"),
    ]
    for name, mutate, expected in mutations:
        value = copy.deepcopy(base)
        mutate(value)
        add_case(name, value, False, expected)

    split_small = make_planned_batch_ledger(runtime, test_registry, execution_policy, 5)
    keys = [job["job_key"] for job in split_small["jobs"]]
    split_small["wave_control"]["submission_waves"] = [
        {"wave_index": 1, "job_keys": keys[:3]}, {"wave_index": 2, "job_keys": keys[3:]},
    ]
    add_case("small_batch_split", split_small, False, "wave_plan_mismatch")

    wave_13 = make_planned_batch_ledger(runtime, test_registry, execution_policy, 13)
    wave_13["wave_control"]["submission_waves"] = [{
        "wave_index": 1, "job_keys": [job["job_key"] for job in wave_13["jobs"]],
    }]
    add_case("wave_capacity_13", wave_13, False, "wave_size_exceeds_policy")

    missing_wave_job = make_planned_batch_ledger(runtime, test_registry, execution_policy, 13)
    missing_wave_job["wave_control"]["submission_waves"][-1]["job_keys"] = []
    add_case("wave_missing_job", missing_wave_job, False, "wave_plan_mismatch")

    duplicate_wave_job = make_planned_batch_ledger(runtime, test_registry, execution_policy, 13)
    duplicate_wave_job["wave_control"]["submission_waves"][-1]["job_keys"] = [
        duplicate_wave_job["wave_control"]["submission_waves"][0]["job_keys"][0]]
    add_case("wave_duplicate_job", duplicate_wave_job, False, "duplicate_wave_job_key")

    rate_missing = make_planned_batch_ledger(runtime, test_registry, execution_policy, 2)
    rate_missing["jobs"][0].update({"error_code": "HTTP_429",
                                     "error_message": "concurrency_limit"})
    add_case("rate_signal_without_event", rate_missing, False, "missing_rate_limit_event")

    bad_override = copy.deepcopy(rate_ok)
    bad_override["wave_control"]["effective_max_inflight"] = 12
    bad_override["wave_control"]["rate_limit_override"]["allowed_max_inflight"] = 2
    add_case("rate_override_not1", bad_override, False, "rate_limit_not_reduced")

    rate_parallel_overflow = copy.deepcopy(anchored_rate)
    rate_parallel_previous = advance_revision(rate_parallel_overflow)
    for job in rate_parallel_overflow["jobs"]:
        job.update({"state": "submission_started",
                    "submission_started_at": "2026-07-11T08:01:00Z"})
    add_case("rate_latch_rejects_parallel_started", rate_parallel_overflow, False,
             "submission_started_exceeds_capacity", previous=rate_parallel_previous)

    missing_override = copy.deepcopy(rate_ok)
    missing_override["jobs"][0].update({"error_code": None, "error_message": None})
    missing_override["wave_control"]["rate_limit_override"] = None
    add_case("latched_event_without_override", missing_override, False, "missing_rate_limit_override")

    restored_early = copy.deepcopy(rate_ok)
    restored_early["jobs"][0].update({"error_code": None, "error_message": None})
    restored_early["wave_control"].update({"effective_max_inflight": 12,
                                            "rate_limit_override": None})
    add_case("latched_event_restored_to12", restored_early, False, "rate_limit_not_reduced")

    history_rollback = copy.deepcopy(rate_ok)
    history_rollback_previous = advance_revision(history_rollback)
    history_rollback["jobs"][0].update({"error_code": None, "error_message": None})
    history_rollback["wave_control"].update({
        "effective_max_inflight": 12, "rate_limit_events": [],
        "rate_limit_override": None,
    })
    add_case("same_run_rate_event_history_rollback", history_rollback, False,
             "rate_limit_event_history_rollback", previous=history_rollback_previous)

    missing_history = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    advance_revision(missing_history)
    add_case("revision_missing_previous_ledger", missing_history, False,
             "missing_previous_ledger")

    accepted_history = copy.deepcopy(base)
    accepted_history_previous = advance_revision(accepted_history)
    accepted_history["jobs"][0].update({
        "state": "planned", "record_id": None, "task_id": None,
        "submission_started_at": None, "submitted_at": None,
        "last_polled_at": None, "next_poll_at": None, "poll_attempts": 0,
        "video_url": None, "video_url_status": "unknown",
        "error_code": None, "error_message": None, "next_action": "generate_video",
        "submission_reported": False, "completion_reported": False,
    })
    add_case("accepted_record_history_rollback", accepted_history, False,
             "accepted_record_history_mutation", previous=accepted_history_previous)

    mismatched_event = copy.deepcopy(rate_ok)
    mismatched_event["wave_control"]["rate_limit_override"]["trigger_request_fingerprint"] = "f" * 64
    add_case("rate_override_event_mismatch", mismatched_event, False,
             "rate_limit_override_event_mismatch")

    terminalized_limit = copy.deepcopy(rate_ok)
    terminalized_limit["jobs"][0].update({"state": "blocked", "next_action": "none"})
    add_case("rate_limit_work_terminalized", terminalized_limit, False,
             "rate_limit_job_terminalized")

    early_wave = make_planned_batch_ledger(runtime, test_registry, execution_policy, 13)
    early_wave["jobs"][12].update({"state": "submission_started",
                                    "submission_started_at": "2026-07-11T08:01:00Z"})
    add_case("wave_started_early", early_wave, False, "wave_started_before_prior_terminal")

    unsafe_retry = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    unsafe_retry["jobs"][0].update({"state": "submission_unknown",
                                    "submission_started_at": "2026-07-11T08:01:00Z",
                                    "next_action": "reconcile_submission",
                                    "resubmit_allowed": True})
    add_case("unknown_resubmit_flag", unsafe_retry, False, "unsafe_resubmission")

    unsafe_unknown_action = make_planned_batch_ledger(runtime, test_registry, execution_policy, 1)
    unsafe_unknown_action["jobs"][0].update({"state": "submission_unknown",
                                             "submission_started_at": "2026-07-11T08:01:00Z",
                                             "next_action": "generate_video"})
    add_case("unknown_generate_action", unsafe_unknown_action, False, "unsafe_resubmission")

    details, passed = [], 0
    for (name, value, should_be_valid, expected, metrics, previous,
         batch_evidence, legacy_source) in cases:
        result = Validator(test_registry, runtime, execution_policy, previous,
                           batch_evidence, legacy_source).validate(value)
        codes = {error["code"] for error in result.get("errors", [])}
        metric_match = True
        if should_be_valid and metrics:
            plan = result.get("wave_plan", {})
            metric_match = (
                result.get("base_max_inflight") == 12 and
                result.get("effective_max_inflight") == metrics["effective"] and
                result.get("max_inflight_source") == "user" and
                plan.get("planned_jobs") == metrics["jobs"] and
                plan.get("planned_waves") == metrics["waves"] and
                ("slots" not in metrics or
                 plan.get("available_submission_slots") == metrics["slots"]) and
                ("history" not in metrics or
                 result.get("history_verified") is metrics["history"]) and
                ("paid" not in metrics or
                 plan.get("paid_submission_allowed") is metrics["paid"]) and
                ("authorized" not in metrics or
                 len(plan.get("authorized_submission_job_keys", [])) == metrics["authorized"]) and
                ("market_binding" not in metrics or bool(
                    plan.get("authorized_outbound_requests", []) and
                    plan["authorized_outbound_requests"][0].get("market_prompt_binding")
                ) is metrics["market_binding"]) and
                ("legacy_binding" not in metrics or bool(
                    plan.get("authorized_outbound_requests", []) and
                    plan["authorized_outbound_requests"][0].get("legacy_v5_exact_resume_sha256")
                ) is metrics["legacy_binding"])
            )
        ok = (result["valid"] is should_be_valid and
              (expected is None or expected in codes) and metric_match)
        passed += int(ok)
        details.append({"name": name, "passed": ok})

    with tempfile.TemporaryDirectory(prefix="zibuyu-parent-scan-self-test-") as temp_dir:
        parent_dir = Path(temp_dir)
        plan_path = parent_dir / "batch-plan.json"
        plan_path.write_text("{}", encoding="utf-8")
        plan_sha256 = hashlib.sha256(b"{}").hexdigest()
        black_dir = parent_dir / "releases" / "black"
        blue_dir = parent_dir / "releases" / "blue"
        black_dir.mkdir(parents=True)
        blue_dir.mkdir(parents=True)
        black_binding = {
            "plan_path": str(plan_path.resolve()), "plan_sha256": plan_sha256,
            "variant_id": "black", "child_run_id": "parent:black",
        }
        blue_binding = {
            "plan_path": str(plan_path.resolve()), "plan_sha256": plan_sha256,
            "variant_id": "blue", "child_run_id": "parent:blue",
        }
        black_compile = black_dir / "batch-compile.json"
        blue_compile = blue_dir / "batch-compile.json"
        black_compile.write_text(json.dumps({"streaming_release": black_binding}), encoding="utf-8")
        blue_compile.write_text(json.dumps({"streaming_release": blue_binding}), encoding="utf-8")
        sibling_ledger = {
            "run_id": "parent:blue", "jobs": [{"state": "running"}],
            "wave_control": {"rate_limit_events": []},
        }
        (blue_dir / "ledger.json").write_text(json.dumps(sibling_ledger), encoding="utf-8")
        current_ledger = {
            "run_id": "parent:black", "jobs": [{"state": "submission_started"}],
            "wave_control": {"rate_limit_events": []},
        }
        parent_evidence = {"path": str(black_compile.resolve())}

        parent_validator = Validator(test_registry, runtime, execution_policy)
        parent_validator.validate_streaming_parent_release_set(current_ledger, parent_evidence, black_binding)
        parent_scan_ok = not parent_validator.errors
        passed += int(parent_scan_ok)
        details.append({"name": "streaming_parent_sibling_scan_positive", "passed": parent_scan_ok})

        sibling_ledger["wave_control"]["rate_limit_events"] = [{"evidence_id": "rate-1"}]
        (blue_dir / "ledger.json").write_text(json.dumps(sibling_ledger), encoding="utf-8")
        latch_validator = Validator(test_registry, runtime, execution_policy)
        latch_validator.validate_streaming_parent_release_set(current_ledger, parent_evidence, black_binding)
        latch_codes = {error["code"] for error in latch_validator.errors}
        latch_ok = "streaming_parent_rate_limit_latch_violation" in latch_codes
        passed += int(latch_ok)
        details.append({"name": "streaming_parent_rate_latch_cross_child", "passed": latch_ok})

        sibling_ledger["wave_control"]["rate_limit_events"] = []
        (blue_dir / "ledger.json").write_text(json.dumps(sibling_ledger), encoding="utf-8")
        blue_binding["plan_sha256"] = "0" * 64
        blue_compile.write_text(json.dumps({"streaming_release": blue_binding}), encoding="utf-8")
        drift_validator = Validator(test_registry, runtime, execution_policy)
        drift_validator.validate_streaming_parent_release_set(current_ledger, parent_evidence, black_binding)
        drift_codes = {error["code"] for error in drift_validator.errors}
        drift_ok = "streaming_parent_plan_hash_mismatch" in drift_codes
        passed += int(drift_ok)
        details.append({"name": "streaming_parent_plan_hash_cross_child", "passed": drift_ok})

    total = len(details)
    output = {"valid": passed == total,
              "self_test": {"passed": passed, "failed": total - passed, "cases": details}}
    if not output["valid"]:
        output["primary_error"] = {"code": "self_test_failed", "path": "$",
                                   "message": "one or more internal cases failed"}
        output["errors"] = [output["primary_error"]]
    return output


def validate_execution_policy_asset(policy, runtime):
    if not isinstance(policy, dict) or policy.get("schema_version") != "1.0":
        raise ValueError("invalid execution-policy schema")
    if policy.get("policy_id") != "popboom-user-concurrency-12-v1":
        raise ValueError("unexpected execution-policy ID")
    if policy.get("endpoint") != runtime.get("endpoint") or parse_time(policy.get("updated_at")) is None:
        raise ValueError("execution policy endpoint/timestamp mismatch")
    wave = policy.get("wave_control")
    if not isinstance(wave, dict):
        raise ValueError("execution policy wave_control is required")
    evidence = wave.get("max_inflight_evidence")
    if (wave.get("max_inflight") != 12 or wave.get("max_inflight_source") != "user" or
            wave.get("max_jobs_per_wave") != 12 or
            wave.get("submission_strategy") != "barrier_waves" or
            wave.get("fill_wave_without_wait") is not True or
            wave.get("next_wave_release") != "previous_wave_terminal"):
        raise ValueError("execution policy must define user concurrency 12 and barrier waves")
    if (not isinstance(evidence, dict) or evidence.get("kind") != "user_explicit" or
            evidence.get("allowed_max_inflight") != 12 or
            evidence.get("endpoint") != runtime.get("endpoint") or
            evidence.get("server_version") is not None or
            parse_time(evidence.get("observed_at")) is None or
            not isinstance(evidence.get("evidence_id"), str) or not evidence.get("evidence_id")):
        raise ValueError("execution policy user_explicit evidence is invalid")
    fallback = policy.get("rate_limit_policy")
    required_markers = {"429", "HTTP_429", "too_many_requests", "concurrency_limit"}
    if (not isinstance(fallback, dict) or fallback.get("effective_max_inflight") != 1 or
            fallback.get("before_next_paid_submission") is not True or
            fallback.get("accepted_jobs_action") != "poll_only" or
            fallback.get("latch_scope") != "batch" or
            fallback.get("event_log_field") != "rate_limit_events" or
            fallback.get("events_append_only") is not True or
            fallback.get("reset_condition") != "new_run_id" or
            not required_markers.issubset(set(fallback.get("markers", [])))):
        raise ValueError("execution policy rate-limit fallback is invalid")
    history = policy.get("history_policy")
    if (not isinstance(history, dict) or
            history.get("revision_field") != "ledger_revision" or
            history.get("previous_hash_field") != "previous_ledger_sha256" or
            history.get("hash_canonicalization") != "utf8_sorted_keys_compact_json" or
            history.get("previous_snapshot_required_after_revision") != 0 or
            history.get("revision_zero_paid_allowed") is not False or
            history.get("paid_requires_history_verified") is not True or
            history.get("paid_authorization_field") != "authorized_submission_job_keys" or
            history.get("normal_authorized_batch_max") != 12 or
            history.get("rate_latched_authorized_batch_max") != 1 or
            history.get("rate_limit_events_prefix_immutable") is not True or
            history.get("accepted_record_id_immutable") is not True):
        raise ValueError("execution policy previous-ledger history gate is invalid")
    return policy


def load_assets(registry_path=None):
    skill_dir = Path(__file__).resolve().parent.parent
    runtime_path = skill_dir / "assets" / "runtime-capabilities.json"
    policy_path = skill_dir / "assets" / "execution-policy.json"
    selected_registry = Path(registry_path).expanduser() if registry_path else skill_dir / "assets" / "fixed-model-registry.json"
    with runtime_path.open("r", encoding="utf-8-sig") as handle: runtime = load_json_text(handle.read())
    with policy_path.open("r", encoding="utf-8-sig") as handle: execution_policy = load_json_text(handle.read())
    with selected_registry.open("r", encoding="utf-8-sig") as handle: registry = load_json_text(handle.read())
    if isinstance(registry, dict) and not isinstance(registry.get("presets"), dict) and isinstance(registry.get("models"), list):
        presets = {}
        for model in registry["models"]:
            if not isinstance(model, dict):
                continue
            name = model.get("name")
            asset_id = model.get("popboom_asset_id") or model.get("asset_id")
            if isinstance(name, str) and name and isinstance(asset_id, str) and asset_id:
                presets[name] = {**model, "asset_id": asset_id}
        registry = {**registry, "presets": presets}
    if not isinstance(runtime, dict) or not isinstance(registry, dict) or not isinstance(registry.get("presets"), dict):
        raise ValueError("invalid runtime capability or model registry asset")
    return registry, runtime, validate_execution_policy_asset(execution_policy, runtime)


def failure(code, message, path="$"):
    error = {"code": code, "path": path, "message": message}
    return {"valid": False, "primary_error": error, "errors": [error]}


def main(argv=None):
    parser = JsonParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="ledger JSON path, or - for stdin")
    parser.add_argument("--model-registry", help="alternate team model registry JSON")
    parser.add_argument("--previous-ledger",
                        help="immediately previous ledger snapshot required when ledger_revision > 0")
    parser.add_argument("--batch-compile",
                        help="exact persisted batch-compile JSON required for Zibuyu submission_started authorization")
    parser.add_argument(
        "--legacy-source-ledger",
        help="exact prior planned schema-1.3/v5 ledger required only for legacy_v5_exact_resume",
    )
    parser.add_argument("--self-test", action="store_true", help="run in-memory validator tests")
    parser.add_argument("--summary", action="store_true", help="print a compact non-authorizing summary; requires --result-file")
    parser.add_argument("--result-file", help="atomically save complete validation JSON, including exact outbound requests")
    output_ready = False
    skill_dir = Path(__file__).resolve().parent.parent
    protected_paths = [Path(__file__), _output_helper_path,
                       skill_dir / "assets" / "runtime-capabilities.json",
                       skill_dir / "assets" / "execution-policy.json",
                       skill_dir / "assets" / "fixed-model-registry.json"]
    director_scripts = skill_dir.parent / "seedance-ugc-cn-director" / "scripts"
    protected_paths.extend(director_scripts / name for name in (
        "validate_batch_compile.py", "market_prompt_contract.py", "validate_streaming_plan.py"))
    try:
        args = parser.parse_args(argv)
        protected_paths.extend(Path(value) for value in (
            args.input, args.previous_ledger, args.batch_compile, args.legacy_source_ledger,
            args.model_registry) if value and value != "-")
        _result_output.validate_output_options(args.summary, args.result_file, protected_paths)
        output_ready = True
        registry, runtime, execution_policy = load_assets(args.model_registry)
        if args.self_test:
            if args.input or args.previous_ledger or args.batch_compile or args.legacy_source_ledger:
                raise ValueError("--self-test does not accept ledger inputs")
            result = self_test(registry, runtime, execution_policy)
        else:
            if not args.input: raise ValueError("ledger input is required")
            text = sys.stdin.buffer.read().decode("utf-8-sig") if args.input == "-" else Path(args.input).read_text(encoding="utf-8-sig")
            previous = None
            if args.previous_ledger:
                previous = load_json_text(Path(args.previous_ledger).read_text(encoding="utf-8-sig"))
            legacy_source = None
            if args.legacy_source_ledger:
                legacy_source = load_json_text(
                    Path(args.legacy_source_ledger).read_text(encoding="utf-8-sig")
                )
            batch_evidence = (load_batch_compile_evidence(args.batch_compile)
                              if args.batch_compile else None)
            document = load_json_text(text)
            if args.result_file:
                for dependency in (document, previous, legacy_source,
                                   batch_evidence.get("document") if batch_evidence else None):
                    protected_paths.extend(_result_output.document_input_paths(dependency))
            result = Validator(registry, runtime, execution_policy, previous,
                               batch_evidence, legacy_source).validate(document)
    except _result_output.ResultOutputError as exc:
        output_ready = False
        result = failure("result_output_error", str(exc))
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        result = failure("input_error", str(exc))
    if output_ready:
        try:
            result = _result_output.prepare_output(result, summary=args.summary,
                                                   result_file=args.result_file, protected_paths=protected_paths)
        except _result_output.ResultOutputError as exc:
            result = failure("result_output_error", str(exc))
    emit(result)
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
