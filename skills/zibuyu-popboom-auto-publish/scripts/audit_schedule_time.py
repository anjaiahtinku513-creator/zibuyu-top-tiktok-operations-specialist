#!/usr/bin/env python3
"""Audit a saved platform schedule readback, without network or ledger access.

Usage: python audit_schedule_time.py INPUT.json [--output REPORT.json]
Only an explicit output path is written. Exit 0 means passed; exit 2 means the
evidence needs review or the observed instant differs from the expected instant.
IANA zones require system zoneinfo data or an available tzdata installation.
Evidence references are required provenance labels; this tool does not fetch or
authenticate their contents or establish the trust of a supplied timezone.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
READBACK_KINDS = {"check_publish", "platform_schedule_readback"}
# A space before an offset is accepted for literal platform display strings.
# No timezone abbreviations, partial dates, trailing text or duplicate offsets.
TIMESTAMP = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[Tt ]"
    r"(?P<clock>[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?)"
    r"(?:[ ]?(?P<offset>Z|[+-][0-9]{2}:?[0-9]{2}))?"
)


class AuditIssue(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise AuditIssue("missing_or_invalid_" + field, field + " must be a non-empty string.")
    return value.strip()


def _parse(value, field, require_offset):
    text = _text(value, field)
    match = TIMESTAMP.fullmatch(text)
    if match is None:
        raise AuditIssue("invalid_" + field, field + " is not one unambiguous date/time; use Z, +HH:MM or +HHMM when including an offset.")
    offset = match["offset"]
    if offset in {"-00:00", "-0000"}:
        raise AuditIssue("unknown_offset_" + field, field + " uses negative zero, which cannot establish a known UTC offset.")
    if offset and offset != "Z":
        digits = offset[1:].replace(":", "")
        if int(digits[:2]) > 23 or int(digits[2:]) > 59:
            raise AuditIssue("invalid_offset_" + field, field + " contains an out-of-range offset.")
    if require_offset and offset is None:
        raise AuditIssue("missing_offset_" + field, field + " must include an explicit UTC offset.")
    suffix = "+00:00" if offset == "Z" else (offset or "")
    try:
        return datetime.fromisoformat(match["date"] + "T" + match["clock"] + suffix)
    except ValueError as exc:
        raise AuditIssue("invalid_" + field, field + " has an invalid calendar date or clock time.") from exc


def _utc_text(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_zone(value, field):
    name = _text(value, field)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise AuditIssue("timezone_data_unavailable", "Cannot load IANA timezone " + name + "; the name may be invalid or system/tzdata files are unavailable. No timezone fallback was used.") from exc
    except (ValueError, OSError) as exc:
        raise AuditIssue("invalid_" + field, field + " must identify an available IANA timezone.") from exc


def _zone_instants(local, zone):
    """Find real instants for a wall time; round trips reject spring gaps."""
    naive = local.replace(tzinfo=None)
    instants = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        back = instant.astimezone(zone)
        if back.replace(tzinfo=None) == naive and back.utcoffset() == candidate.utcoffset():
            instants[instant] = candidate.utcoffset()
    return instants


def _resolve_with_zone(value, zone, field):
    instants = _zone_instants(value, zone)
    if not instants:
        raise AuditIssue("nonexistent_" + field, field + " falls in a DST clock-forward gap and does not exist in the supplied timezone.")
    if value.tzinfo is not None:
        instant = value.astimezone(UTC)
        if instant not in instants or instants[instant] != value.utcoffset():
            raise AuditIssue("timezone_offset_mismatch_" + field, field + " offset disagrees with the supplied IANA timezone on that date.")
        # An explicit, valid offset disambiguates a repeated fall-back wall time.
        return instant
    if len(instants) > 1:
        raise AuditIssue("ambiguous_" + field, field + " occurs twice during a DST clock-back transition; obtain a readback with an explicit offset.")
    return next(iter(instants))


def audit_schedule_time(data, now=None):
    """Return a verdict from explicit input evidence; never infer a timezone.

    ``now`` may be an aware datetime or offset-bearing timestamp for reproducible
    offline audits. ``observed_at`` may be at most 60 seconds ahead of this clock.
    At least one non-empty string ``schedule_id`` or ``log_id`` is required;
    their distinct identifier fields are preserved in the result. An optional
    offset-bearing ``evidence_not_before`` sets the earliest allowed observation.
    Without it, historical as-of evidence is not subject to an arbitrary TTL.
    A past expected schedule is valid for reconciliation of an existing action.
    ``observed_timezone`` is accepted only with ``timezone_evidence_ref`` and is
    used solely as the explicitly asserted platform timezone, never the account
    timezone. The caller is responsible for authenticating that assertion.
    """
    result = {"status": "needs_review", "expected_utc": None, "observed_utc": None,
              "delta_seconds": None, "reason": "", "reason_code": ""}
    try:
        if not isinstance(data, dict):
            raise AuditIssue("invalid_input", "Input must be a JSON object.")
        identifiers = {field: data[field].strip() for field in ("schedule_id", "log_id")
                       if isinstance(data.get(field), str) and data[field].strip()}
        if not identifiers:
            raise AuditIssue("missing_or_invalid_record_id", "At least one of schedule_id or log_id must be a non-empty string.")
        result.update(identifiers)
        for field in ("evidence_ref", "evidence_kind"):
            result[field] = _text(data.get(field), field)
        if result["evidence_kind"] not in READBACK_KINDS:
            raise AuditIssue("unsupported_evidence_kind", "Only check_publish or platform_schedule_readback evidence can verify a schedule; creation receipts and local plans cannot.")
        observed_at = _parse(data.get("observed_at"), "observed_at", True)
        if now is None:
            clock = datetime.now(UTC)
        elif isinstance(now, str):
            clock = _parse(now, "now", True)
        elif isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None:
            clock = now
        else:
            raise AuditIssue("invalid_now", "Audit clock must be an aware datetime or an offset-bearing timestamp.")
        if observed_at.astimezone(UTC) - clock.astimezone(UTC) > timedelta(seconds=60):
            raise AuditIssue("observed_at_in_future", "Evidence observation is more than 60 seconds ahead of the audit clock.")
        result["observed_at_utc"] = _utc_text(observed_at)
        if "evidence_not_before" in data:
            not_before = _parse(data["evidence_not_before"], "evidence_not_before", True)
            result["evidence_not_before_utc"] = _utc_text(not_before)
            if observed_at.astimezone(UTC) < not_before.astimezone(UTC):
                raise AuditIssue("observation_before_evidence_window", "Evidence observation predates the explicit evidence_not_before boundary; obtain a readback within the requested reconciliation window.")
        expected = _parse(data.get("expected_time"), "expected_time", True)
        if "expected_timezone" in data:
            expected_utc = _resolve_with_zone(expected, _load_zone(data["expected_timezone"], "expected_timezone"), "expected_time")
        else:
            expected_utc = expected.astimezone(UTC)
        result["expected_utc"] = _utc_text(expected_utc)
        observed = _parse(data.get("observed_time"), "observed_time", False)
        if "observed_timezone" in data:
            result["timezone_evidence_ref"] = _text(data.get("timezone_evidence_ref"), "timezone_evidence_ref")
            zone = _load_zone(data["observed_timezone"], "observed_timezone")
            observed_utc = _resolve_with_zone(observed, zone, "observed_time")
        elif (observed.tzinfo is None and result['evidence_kind'] == 'check_publish'
              and data.get('platform_contract') == 'popboom_beijing_caption_v1'):
            submitted = _parse(data.get('submitted_time'), 'submitted_time', True)
            if submitted.utcoffset() != timedelta(hours=8) or submitted.astimezone(UTC) != expected_utc:
                raise AuditIssue('invalid_beijing_submission', 'Contract requires an equivalent explicit +08:00 submitted_time.')
            result['timezone_evidence_ref'] = 'popboom_beijing_caption_v1:user_confirmed_2026-09-11'
            observed_utc = observed.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(UTC)
        elif observed.tzinfo is None:
            raise AuditIssue("observed_time_missing_offset", "Platform readback has no UTC offset. Supply an explicitly evidenced observed_timezone and timezone_evidence_ref; computer/account timezone must not be inferred.")
        else:
            observed_utc = observed.astimezone(UTC)
        result["observed_utc"] = _utc_text(observed_utc)
        delta = (observed_utc - expected_utc).total_seconds()
        result["delta_seconds"] = delta
        result["status"] = "passed" if delta == 0 else "mismatch"
        result["reason_code"] = "same_instant" if delta == 0 else "different_instant"
        result["reason"] = ("The evidenced platform readback and expected schedule represent the same instant."
                            if delta == 0 else "The evidenced platform readback differs from the expected instant by " + str(delta) + " seconds (observed minus expected).")
    except AuditIssue as exc:
        result["reason_code"] = exc.code
        result["reason"] = str(exc)
    except (OverflowError, ValueError, TypeError) as exc:
        # Boundary years can overflow when converting an otherwise valid local
        # datetime. Malformed input must never accidentally become a pass.
        result["reason_code"] = "invalid_time_value"
        result["reason"] = "Cannot safely interpret timestamp values: " + str(exc)
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("infile", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.infile.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
        result = audit_schedule_time(data)
    except (OSError, UnicodeError, ValueError) as exc:
        result = {"status": "needs_review", "expected_utc": None, "observed_utc": None,
                  "delta_seconds": None, "reason_code": "input_read_error", "reason": str(exc)}
    if args.output is not None:
        try:
            if args.output.resolve() == args.infile.resolve():
                raise ValueError("Output must not overwrite the input evidence.")
            if args.output.name.lower() in {"manifest.json", "ledger.json", "batch-compile.json"}:
                raise ValueError("Output must be an audit report, not a manifest, ledger or compile file.")
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            result.update(status="needs_review", reason_code="output_write_error", reason=str(exc))
    # ASCII escaping makes CLI output safe on Windows terminals using GBK.
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
