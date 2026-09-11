"""Offline schedule semantics tests. Fixtures are synthetic, not user records.

Run with an interpreter providing IANA tzdata; the bundled Codex runtime does.
Missing-data behavior is tested independently. DST tests never silently skip.
"""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError


SCRIPT = Path(__file__).resolve().parents[1] / "skills/zibuyu-popboom-auto-publish/scripts/audit_schedule_time.py"
SPEC = importlib.util.spec_from_file_location("schedule_time_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
NOW = datetime(2030, 5, 1, 12, tzinfo=timezone.utc)


def fixture(**changes):
    data = {"expected_time": "2030-07-12T07:00:00-04:00",
            "observed_time": "2030-07-12T11:00:00Z", "observed_at": "2030-05-01T12:00:00Z",
            "schedule_id": "synthetic-schedule-1", "evidence_ref": "fixture:readback-1",
            "evidence_kind": "check_publish"}
    data.update(changes)
    return data


class ScheduleTimeAuditTests(unittest.TestCase):
    def check(self, data, status, reason=None):
        original = copy.deepcopy(data)
        result = audit.audit_schedule_time(data, now=NOW)
        self.assertEqual(data, original, "audit must not mutate its input")
        self.assertEqual(result["status"], status, result)
        if reason:
            self.assertEqual(result["reason_code"], reason, result)
        return result

    def test_confirmed_popboom_beijing_contract(self):
        data=fixture(observed_time="2030-07-12 19:00:00", submitted_time="2030-07-12T19:00:00+08:00", platform_contract="popboom_beijing_caption_v1")
        self.check(data,"passed")
        data["observed_time"]="2030-07-12 07:00:00"
        self.check(data,"mismatch")
        data["submitted_time"]="2030-07-12T07:00:00+08:00"
        self.check(data,"needs_review","invalid_beijing_submission")
        data["submitted_time"]="2030-07-12T19:00:00+08:00"
        data["evidence_kind"]="platform_schedule_readback"
        self.check(data,"needs_review","observed_time_missing_offset")

    def test_equal_clock_wrong_zone_is_twelve_hours_early(self):
        r = self.check(fixture(observed_time="2030-07-12T07:00:00+08:00"), "mismatch")
        self.assertEqual(r["delta_seconds"], -43200)

    def test_same_wall_time_without_offset_never_passes(self):
        self.check(fixture(observed_time="2030-07-12 07:00:00", expected_timezone="America/New_York"), "needs_review", "observed_time_missing_offset")

    def test_equivalent_utc_colon_and_compact_offsets(self):
        for value in ("2030-07-12T11:00:00Z", "2030-07-12T19:00:00+08:00", "2030-07-12 19:00:00 +0800", "2030-07-12 07:00:00 -0400"):
            with self.subTest(value=value):
                r = self.check(fixture(observed_time=value), "passed")
                self.assertEqual(r["expected_utc"], "2030-07-12T11:00:00Z")
                self.assertEqual(r["observed_utc"], r["expected_utc"])

    def test_new_york_winter_and_summer_actual_offsets(self):
        for expected, observed in (("2030-01-12T07:00:00-05:00", "2030-01-12T12:00:00Z"),
                                   ("2030-07-12T07:00:00-04:00", "2030-07-12T11:00:00Z")):
            with self.subTest(expected=expected):
                self.check(fixture(expected_time=expected, expected_timezone="America/New_York", observed_time=observed), "passed")

    def test_berlin_winter_and_summer(self):
        for expected, observed in (("2030-01-12T07:00:00+01:00", "2030-01-12T06:00:00Z"),
                                   ("2030-07-12T07:00:00+02:00", "2030-07-12T05:00:00Z")):
            with self.subTest(expected=expected):
                self.check(fixture(expected_time=expected, expected_timezone="Europe/Berlin", observed_time=observed), "passed")

    def test_cross_day_midnight_and_six_am(self):
        for expected, observed in (("2030-07-12T12:00:00-04:00", "2030-07-13T00:00:00+08:00"),
                                   ("2030-07-12T18:00:00-04:00", "2030-07-13T06:00:00+08:00")):
            with self.subTest(expected=expected):
                self.check(fixture(expected_time=expected, observed_time=observed), "passed")

    def test_naive_readback_requires_explicit_platform_zone_evidence(self):
        data = fixture(observed_time="2030-07-12 19:00:00", observed_timezone="Asia/Shanghai")
        self.check(data, "needs_review", "missing_or_invalid_timezone_evidence_ref")
        data["timezone_evidence_ref"] = "fixture:platform-timezone-label"
        self.check(data, "passed")
        data["observed_time"] = "2030-07-12 07:00:00"
        self.assertEqual(self.check(data, "mismatch")["delta_seconds"], -43200)

    def test_expected_timezone_does_not_supply_observed_timezone(self):
        self.check(fixture(observed_time="2030-07-12 07:00:00", expected_timezone="America/New_York", timezone_evidence_ref="fixture:zone"), "needs_review", "observed_time_missing_offset")

    def test_invalid_dates_offsets_and_duplicate_offsets(self):
        for value in ("2030-02-30T07:00:00Z", "2030-07-12T24:00:00Z", "2030-07-12T07:00:00+24:00", "2030-07-12T07:00:00+04:60", "2030-07-12T07:00:00Z+08:00", "2030-07-12 07:00:00 EDT", "2030-07-12T07:00:00-00:00", "2030-07-12T07:00:00-0000"):
            with self.subTest(value=value):
                self.check(fixture(observed_time=value), "needs_review")

    def test_wrong_expected_dst_offset_is_not_accepted(self):
        self.check(fixture(expected_time="2030-07-12T07:00:00-05:00", expected_timezone="America/New_York"), "needs_review", "timezone_offset_mismatch_expected_time")

    def test_wrong_observed_offset_conflicts_with_evidenced_zone(self):
        self.check(fixture(observed_time="2030-07-12T07:00:00-05:00", observed_timezone="America/New_York", timezone_evidence_ref="fixture:platform-zone"), "needs_review", "timezone_offset_mismatch_observed_time")

    def test_dst_fall_back_naive_readback_is_ambiguous(self):
        self.check(fixture(expected_time="2030-11-03T01:30:00-04:00", observed_time="2030-11-03 01:30:00", observed_timezone="America/New_York", timezone_evidence_ref="fixture:platform-zone"), "needs_review", "ambiguous_observed_time")

    def test_dst_fall_back_explicit_offset_disambiguates_both_occurrences(self):
        for offset, utc_hour in (("-04:00", "05"), ("-05:00", "06")):
            with self.subTest(offset=offset):
                r = self.check(fixture(expected_time="2030-11-03T01:30:00" + offset, expected_timezone="America/New_York", observed_time="2030-11-03T" + utc_hour + ":30:00Z"), "passed")
                self.assertEqual(r["expected_utc"], "2030-11-03T" + utc_hour + ":30:00Z")

    def test_dst_spring_gap_is_rejected_even_with_claimed_offset(self):
        for observed in ("2030-03-10 02:30:00", "2030-03-10T02:30:00-05:00"):
            with self.subTest(observed=observed):
                self.check(fixture(observed_time=observed, observed_timezone="America/New_York", timezone_evidence_ref="fixture:platform-zone"), "needs_review", "nonexistent_observed_time")
        self.check(fixture(expected_time="2030-03-10T02:30:00-05:00", expected_timezone="America/New_York"), "needs_review", "nonexistent_expected_time")

    def test_berlin_gap_and_repeated_hour(self):
        for observed, reason in (("2030-03-31 02:30:00", "nonexistent_observed_time"), ("2030-10-27 02:30:00", "ambiguous_observed_time")):
            with self.subTest(observed=observed):
                self.check(fixture(observed_time=observed, observed_timezone="Europe/Berlin", timezone_evidence_ref="fixture:platform-zone"), "needs_review", reason)

    def test_missing_tzdata_never_falls_back_to_machine_timezone(self):
        with patch.object(audit, "ZoneInfo", side_effect=ZoneInfoNotFoundError("fixture missing tzdata")):
            r = self.check(fixture(expected_timezone="America/New_York"), "needs_review", "timezone_data_unavailable")
            self.assertIn("tzdata", r["reason"])
            self.check(fixture(), "passed")  # Explicit offsets do not need tzdata.

    def test_creation_echo_local_plan_and_missing_evidence_are_rejected(self):
        for kind in ("creation_receipt", "local_plan", "", "publish_video"):
            with self.subTest(kind=kind):
                self.check(fixture(evidence_kind=kind), "needs_review")
        for field in ("schedule_id", "evidence_ref", "evidence_kind", "observed_at", "expected_time", "observed_time"):
            with self.subTest(missing=field):
                data = fixture()
                del data[field]
                self.check(data, "needs_review")
        self.check(fixture(evidence_kind="platform_schedule_readback"), "passed")

    def test_log_only_record_preserves_identifier_kind(self):
        data = fixture(log_id="synthetic-log-1")
        del data["schedule_id"]
        r = self.check(data, "passed")
        self.assertEqual(r["log_id"], "synthetic-log-1")
        self.assertNotIn("schedule_id", r)
        data["schedule_id"] = None
        r = self.check(data, "passed")
        self.assertNotIn("schedule_id", r)

    def test_both_identifiers_are_retained_when_supplied(self):
        r = self.check(fixture(log_id="synthetic-log-1"), "passed")
        self.assertEqual(r["schedule_id"], "synthetic-schedule-1")
        self.assertEqual(r["log_id"], "synthetic-log-1")
        for schedule, log in ((None, None), ("", " "), (False, 123), ({}, [])):
            with self.subTest(schedule=schedule, log=log):
                self.check(fixture(schedule_id=schedule, log_id=log), "needs_review", "missing_or_invalid_record_id")

    def test_evidence_lower_bound_compares_instants_and_includes_boundary(self):
        r = self.check(fixture(evidence_not_before="2030-05-01T08:00:00-04:00"), "passed")
        self.assertEqual(r["evidence_not_before_utc"], "2030-05-01T12:00:00Z")
        self.check(fixture(evidence_not_before="2030-05-01T20:00:00+0800"), "passed")
        self.check(fixture(evidence_not_before="2030-05-01T12:00:00.000001Z"), "needs_review", "observation_before_evidence_window")
        self.check(fixture(evidence_not_before="2030-05-01T11:59:59Z"), "passed")

    def test_evidence_lower_bound_must_be_offset_aware_and_valid(self):
        for value in ("2030-05-01 12:00:00", "2030-02-30T12:00:00Z", "2030-05-01T12:00:00-00:00", None):
            with self.subTest(value=value):
                self.check(fixture(evidence_not_before=value), "needs_review")

    def test_historical_as_of_evidence_has_no_implicit_ttl(self):
        r = self.check(fixture(observed_at="2000-01-01T00:00:00Z"), "passed")
        self.assertNotIn("evidence_not_before_utc", r)
        self.check(fixture(observed_at="2000-01-01T00:00:00Z", evidence_not_before="2030-05-01T11:00:00Z"), "needs_review", "observation_before_evidence_window")

    def test_observation_clock_validation_and_future_skew_boundary(self):
        for value in ("2030-05-01 12:00:00", "2030-02-30T12:00:00Z", "2030-05-01T12:01:01Z"):
            with self.subTest(observed_at=value):
                self.check(fixture(observed_at=value), "needs_review")
        self.check(fixture(observed_at="2030-05-01T12:01:00Z"), "passed")
        self.check(fixture(observed_at="2030-05-01T20:00:00+0800"), "passed")

    def test_invalid_expected_missing_offset_and_boundary_overflow(self):
        self.check(fixture(expected_time="2030-07-12 07:00:00"), "needs_review", "missing_offset_expected_time")
        self.check(fixture(expected_time="0001-01-01T00:00:00+01:00"), "needs_review", "invalid_time_value")
        self.check([], "needs_review", "invalid_input")

    def test_subsecond_difference_is_not_hidden_by_rounding(self):
        r = self.check(fixture(observed_time="2030-07-12T11:00:00.000001Z"), "mismatch")
        self.assertEqual(r["delta_seconds"], 0.000001)

    def test_cli_exit_codes_output_and_no_implicit_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "input.json", root / "audit.json"
            data = fixture(observed_at="2000-01-01T00:00:00Z")
            source.write_text(json.dumps(data), encoding="utf-8")
            invoke = lambda *extra: subprocess.run([sys.executable, "-B", str(SCRIPT), str(source), *extra], capture_output=True, text=True)
            r = invoke()
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual({p.name for p in root.iterdir()}, {"input.json"})
            r = invoke("--output", str(output))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "passed")
            original = source.read_bytes()
            self.assertEqual(invoke("--output", str(source)).returncode, 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(invoke("--output", str(root / "ledger.json")).returncode, 2)
            self.assertFalse((root / "ledger.json").exists())
            data["observed_time"] = "2030-07-12T07:00:00+08:00"
            source.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(invoke().returncode, 2)
            source.write_text('{"expected_time":"one","expected_time":"two"}', encoding="utf-8")
            r = invoke()
            self.assertEqual(r.returncode, 2)
            self.assertEqual(json.loads(r.stdout)["reason_code"], "input_read_error")


if __name__ == "__main__":
    unittest.main()
