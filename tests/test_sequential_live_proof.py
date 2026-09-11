"""Offline regression tests using synthetic director fixtures only."""
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "seedance-ugc-cn-director" / "scripts"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BATCH = load_module("sequential_proof_batch_tests", SCRIPTS / "validate_batch_compile.py")
LEDGER = load_module(
    "sequential_proof_ledger_tests", ROOT / "skills" / "popboom" / "scripts" / "validate_ledger.py",
)
FIELD = "sequential_live_proof"
ERROR = "SEQUENTIAL_LIVE_PROOF_INVALID"


class SequentialLiveProofTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def variant(document: dict[str, Any]) -> dict[str, Any]:
        return document["variants"][0]

    def beat(self, document: dict[str, Any]) -> dict[str, Any]:
        return self.variant(document)["canonical_timeline"]["beats"][1]

    def timing(self, document: dict[str, Any]) -> dict[str, Any]:
        return self.beat(document)[FIELD]

    def proof(self, document: dict[str, Any]) -> dict[str, Any]:
        return self.variant(document)["quality_plan"]["claim_proof_plan"][0]

    def claim(self, document: dict[str, Any]) -> dict[str, Any]:
        claim_id = self.proof(document)["claim_id"]
        return next(c for c in document["shared_core"]["claims_registry"] if c["claim_id"] == claim_id)

    def recompile(self, document: dict[str, Any]) -> None:
        variant = self.variant(document)
        for row, beat in zip(variant["voiceover_review"], variant["canonical_timeline"]["beats"]):
            row.update({
                "beat_id": beat.get("beat_id"), "speech_mode": beat.get("speech_mode"),
                "spoken_language": beat.get("spoken_language"), "market_line": beat.get("spoken_line"),
                "silence_reason": beat.get("silence_reason"),
            })
        BATCH._refresh_fixture_three_layer_deadlines(document)
        variant["renderings"] = BATCH._compile_renderings_v7(
            variant, document["batch_key"], document["shared_core"]["research_bundle"],
        )

    def fixture(self, action: str = "pull_release") -> dict[str, Any]:
        document = BATCH._fixture_v14(market="DE", delivery_mode="de_live_simple")
        variant = self.variant(document)
        intervals = ((0, 2), (2, 5.25), (5.25, 7.5), (7.5, 10), (10, 13), (13, 15))
        for beat, interval in zip(variant["canonical_timeline"]["beats"], intervals):
            beat["start_seconds"], beat["end_seconds"] = interval

        claim, proof, beat = self.claim(document), self.proof(document), self.beat(document)
        claim.update({"feature_id": "fit", "product_part_id": "fit", "spoken_claim_terms": ["Passform"]})
        for item in document["shared_core"]["research_bundle"]["evidence_items"]:
            if item["evidence_id"] in claim["evidence_ids"]:
                item["statement"] = "Synthetic test garment has visibly loose fit allowance."
        endpoint = "fit allowance remains visible with released garment and clear hands"
        allowance = action in {"pull_release", "pinch_release"}
        if action == "pull_release":
            action_text = "both hands pull the existing fit allowance once by one centimetre and release completely"
            active, hands, performer = "both", "two", "micro"
        elif action == "pinch_release":
            action_text = "the right hand pinches existing fit allowance once and releases completely"
            active, hands, performer = "right", "one", "micro"
        elif action == "turn_settle":
            action_text = "the creator turns the body once to reveal fit and settles"
            active, hands, performer = "none", "body", "simple"
        elif action == "walk_settle":
            action_text = "the creator takes one step to reveal fit and settles"
            active, hands, performer = "none", "body", "simple"
        else:
            raise ValueError(action)
        proof.update({
            "proof_target": "fit", "product_part_id": "fit", "framing_class": "chest_to_hem",
            "action_type": action, "hands_required": hands, "expected_visible_change": endpoint,
        })
        beat.update({
            "proof_target": "fit", "product_part_id": "fit", "proof_action_type": action,
            "framing": "face and torso to hem showing fit", "camera_motivation": "keep fit and face readable together",
            "core_action": action_text, "action_target": "fit", "product_point": "fit",
            "product_visibility": "full", "proof_endpoint": endpoint, "visible_endpoint": endpoint,
            "spoken_line": "Die Passform bleibt hier locker.", "posture_id": "relaxed_three_quarter",
            "motion_budget": {"camera": "locked", "performer": performer, "active_hands": {"both": "two", "right": "one", "none": "zero"}[active]},
            "hand_plan": BATCH._motion_hand_plan(
                active, "performs the coordinated action and releases" if active == "both" else "stays anchored",
                "performs the declared action and releases" if active != "none" else "stays anchored",
            ),
            "left_hand_state": "left hand ends relaxed and clear at left side",
            "right_hand_state": "right hand ends relaxed and clear at right side",
        })
        timing = {
            "contract_id": "sequential_live_proof_v1",
            "action_window_seconds": [2, 2.75], "settle_window_seconds": [2.75, 3],
            "speech_window_seconds": [3, 5.25],
            "speech_motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "zero"},
            "proof_endpoint_visible_during_speech": True, "speech_endpoint": endpoint,
            "speech_hand_anchors": {side: beat["hand_plan"][side]["end_anchor"] for side in ("left", "right")},
            "claim_scope": "visible_loose_allowance" if allowance else "visible_structure",
        }
        if allowance:
            timing.update({"allowance_displacement_cm": 1, "fabric_extension_forbidden": True})
        beat[FIELD] = timing
        variant["voiceover_review"][1]["zh_cn_translation"] = "这里的版型依然宽松。"
        variant["caption"] = "Die Passform löst die Sorge vor einem kastenförmigen Oversize-Look."
        variant["caption_claim_bindings"][0]["text_anchor"] = "Passform"
        self.recompile(document)
        return document

    def assert_valid(self, document: dict[str, Any]) -> dict[str, Any]:
        result = BATCH.validate(document)
        self.assertTrue(result["valid"], result.get("errors"))
        return result

    def assert_rejects(self, document: dict[str, Any], *codes: str) -> None:
        result = BATCH.validate(document)
        self.assertFalse(result["valid"], "invalid sequential proof was accepted")
        actual = {e["code"] for e in result["errors"]}
        for code in codes:
            self.assertIn(code, actual, result["errors"])

    def test_two_hand_allowance_and_one_hand_pinch_are_eligible(self) -> None:
        for action in ("pull_release", "pinch_release"):
            with self.subTest(action=action):
                result = self.assert_valid(self.fixture(action))
                self.assertTrue(result["eligible_for_new_submission"])

    def test_structural_body_actions_are_eligible_after_settling(self) -> None:
        for action in ("turn_settle", "walk_settle"):
            with self.subTest(action=action):
                self.assert_valid(self.fixture(action))

    def test_serializer_carries_exact_windows_and_silent_then_live_instruction(self) -> None:
        document = self.fixture()
        self.assert_valid(document)
        text = self.variant(document)["renderings"]["prompt"]["compiled_text"]
        for fragment in ("2-2.75s", "2.75-3s", "Only during 3-5.25s", "in silence", "neither hand moves", "do not add off-screen narration"):
            self.assertIn(fragment, text)
        self.assertNotIn("move detail, walking, turning, or two-hand proof to off-screen voiceover", text)
        self.assertNotIn("sequential_live_proof_v1", text)
        self.assertNotIn("这里的版型依然宽松", text)

    def test_legacy_complex_live_proof_without_opt_in_stays_rejected(self) -> None:
        document = self.fixture()
        self.beat(document).pop(FIELD)
        self.recompile(document)
        self.assert_rejects(document, "DELIVERY_MODE_CONFLICT", "UNSUPPORTED_PERFORMANCE_DEMO")

    def test_unchanged_old_us_and_de_fixtures_remain_valid(self) -> None:
        for kwargs in ({"market": "US"}, {"market": "DE"}, {"market": "DE", "delivery_mode": "de_live_simple"}):
            with self.subTest(**kwargs):
                document = BATCH._fixture_v14(**kwargs)
                before = copy.deepcopy(self.variant(document)["renderings"])
                self.recompile(document)
                self.assertEqual(before, self.variant(document)["renderings"])
                self.assert_valid(document)

    def test_malformed_contract_objects_are_rejected(self) -> None:
        mutations = (
            ("null", lambda t: None), ("list", lambda t: []), ("empty", lambda t: {}),
            ("unknown", lambda t: {**t, "extra": True}),
            ("contract", lambda t: {**t, "contract_id": "sequential_live_proof_v0"}),
            ("missing", lambda t: {k: v for k, v in t.items() if k != "speech_endpoint"}),
            ("truthy", lambda t: {**t, "proof_endpoint_visible_during_speech": 1}),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                document = self.fixture()
                self.beat(document)[FIELD] = mutate(self.timing(document))
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_invalid_absolute_windows_are_rejected(self) -> None:
        mutations = {
            "gap": ("settle_window_seconds", [2.8, 3]),
            "overlap": ("speech_window_seconds", [2.9, 5.25]),
            "start_outside": ("action_window_seconds", [1.9, 2.75]),
            "end_outside": ("speech_window_seconds", [3, 5.3]),
            "backward": ("action_window_seconds", [2, 1.9]),
            "action_short": ("action_window_seconds", [2, 2.499]),
            "settle_short": ("settle_window_seconds", [2.75, 2.999]),
            "speech_short": ("speech_window_seconds", [3.251, 5.25]),
            "boolean": ("action_window_seconds", [True, 2.75]),
            "string": ("action_window_seconds", ["2", 2.75]),
            "nan": ("action_window_seconds", [float("nan"), 2.75]),
            "infinity": ("action_window_seconds", [float("inf"), 2.75]),
            "precision": ("action_window_seconds", [2, 2.7501]),
            "length": ("action_window_seconds", [2, 2.5, 2.75]),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                document = self.fixture()
                self.timing(document)[field] = value
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_contiguous_minimum_windows_pass_and_short_speech_fails(self) -> None:
        document = self.fixture()
        timing = self.timing(document)
        timing.update({"action_window_seconds": [2, 2.5], "settle_window_seconds": [2.5, 3.25], "speech_window_seconds": [3.25, 5.25]})
        self.recompile(document)
        self.assert_valid(document)
        timing.update({"action_window_seconds": [2, 3.001], "settle_window_seconds": [3.001, 3.251], "speech_window_seconds": [3.251, 5.25]})
        self.recompile(document)
        self.assert_rejects(document, ERROR)

    def test_word_budget_uses_actual_speech_window(self) -> None:
        document = self.fixture()
        self.beat(document)["spoken_line"] = "Die Passform bleibt hier nach dieser Bewegung sichtbar locker."
        self.recompile(document)
        self.assert_rejects(document, ERROR)

    def test_wrong_contexts_cannot_use_opt_in(self) -> None:
        cases = ("US", "hybrid", "handheld", "old_schema", "hook", "cta", "silent", "mouth_hidden", "no_lipsync")
        for case in cases:
            with self.subTest(case=case):
                document = self.fixture()
                beat, variant = self.beat(document), self.variant(document)
                if case == "US":
                    document["batch_key"]["market"] = "US"
                elif case == "hybrid":
                    variant["generation_controls"]["delivery_mode"] = "de_hybrid_proof"
                elif case == "handheld":
                    variant["generation_controls"]["camera_mode"] = "creator_handheld"
                elif case == "old_schema":
                    document["schema_version"] = "1.3"
                elif case in {"hook", "cta"}:
                    beat["purpose"] = case
                elif case == "silent":
                    beat.update({"speech_mode": "none", "spoken_line": None, "spoken_language": None, "lip_sync_required": False})
                elif case == "mouth_hidden":
                    beat["mouth_visibility"] = "not_visible"
                elif case == "no_lipsync":
                    beat["lip_sync_required"] = False
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_speech_motion_budget_rejects_moving_hands_torso_and_camera(self) -> None:
        for axis, value in (("active_hands", "one"), ("active_hands", "two"), ("active_hands", "none"), ("performer", "active"), ("performer", "simple"), ("camera", "active"), ("camera", "subtle")):
            with self.subTest(axis=axis, value=value):
                document = self.fixture()
                self.timing(document)["speech_motion_budget"][axis] = value
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_scope_displacement_and_extension_limits_are_enforced(self) -> None:
        for field, value in (("claim_scope", "visible_structure"), ("allowance_displacement_cm", 2.001), ("allowance_displacement_cm", 0), ("allowance_displacement_cm", True), ("fabric_extension_forbidden", False)):
            with self.subTest(field=field, value=value):
                document = self.fixture()
                self.timing(document)[field] = value
                self.recompile(document)
                self.assert_rejects(document, ERROR)
        document = self.fixture()
        self.timing(document)["allowance_displacement_cm"] = 2
        self.recompile(document)
        self.assert_valid(document)

    def test_performance_language_in_german_remains_rejected(self) -> None:
        for term in ("elastisch", "atmungsaktiv", "blickdicht", "knitterarm"):
            with self.subTest(term=term):
                document = self.fixture()
                self.beat(document)["spoken_line"] = f"Die Passform ist {term}."
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_claim_must_remain_visible_only_and_exactly_bound(self) -> None:
        for field, value in (("assertion_level", "qualitative"), ("assertion_kind", "stretch_attribute"), ("claim_mode", "direct"), ("product_part_id", "material_performance")):
            with self.subTest(field=field):
                document = self.fixture()
                self.claim(document)[field] = value
                self.recompile(document)
                self.assert_rejects(document, ERROR)
        document = self.fixture()
        self.proof(document)["beat_id"] = "different-proof-beat"
        self.recompile(document)
        self.assert_rejects(document, ERROR)

    def test_unreleased_hand_anchors_remain_rejected(self) -> None:
        for anchor in ("left hand gripping fabric", "left hand holds the fabric", "left hand keeps fabric taut"):
            with self.subTest(anchor=anchor):
                document = self.fixture()
                self.beat(document)["hand_plan"]["left"]["end_anchor"] = anchor
                self.timing(document)["speech_hand_anchors"]["left"] = anchor
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_speech_endpoint_and_hand_anchors_must_match_canonical_end_state(self) -> None:
        for field, value in (("speech_endpoint", "different endpoint"), ("speech_hand_anchors", {"left": "different", "right": "right relaxed side"}), ("proof_endpoint_visible_during_speech", False)):
            with self.subTest(field=field):
                document = self.fixture()
                self.timing(document)[field] = value
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_wrong_hand_count_and_combined_actions_are_not_waived(self) -> None:
        document = self.fixture()
        self.beat(document)["motion_budget"]["active_hands"] = "one"
        self.recompile(document)
        self.assert_rejects(document, "HAND_ACTIVITY_MISMATCH", "ANATOMY_RISK_OVERLOAD")
        document = self.fixture()
        self.beat(document)["core_action"] += " and turns the body once"
        self.recompile(document)
        self.assert_rejects(document, "ACTION_SEQUENCE_OVERLOAD")

    def test_action_text_cannot_contradict_silent_action_window(self) -> None:
        document = self.fixture()
        self.beat(document)["core_action"] += " while speaking the German line"
        self.recompile(document)
        self.assert_rejects(document, ERROR)

    def test_expression_and_audio_cannot_request_simultaneous_action_and_speech(self) -> None:
        for field, text in (
            ("micro_expression", "keeps pulling fit allowance while speaking with a pleased smile"),
            ("audio", "the creator speaks the German line while pulling the fit allowance"),
        ):
            with self.subTest(field=field):
                document = self.fixture()
                self.beat(document)[field] = text
                self.recompile(document)
                self.assert_rejects(document, ERROR)

    def test_structural_proof_cannot_move_hands_at_speech_anchor(self) -> None:
        document = self.fixture("turn_settle")
        anchor = "right hand repeatedly moves over the garment during speech"
        self.beat(document)["hand_plan"]["right"]["end_anchor"] = anchor
        self.timing(document)["speech_hand_anchors"]["right"] = anchor
        self.recompile(document)
        self.assert_rejects(document, ERROR)

    def test_speech_may_keep_a_natural_smile_and_room_sound(self) -> None:
        document = self.fixture()
        self.beat(document)["micro_expression"] = "a pleased small smile while speaking with naturally softened eyes"
        self.beat(document)["audio"] = "the creator speaks the German line with quiet natural room sound"
        self.recompile(document)
        self.assert_valid(document)

    def test_structural_proof_may_keep_hand_stationary_inside_pocket(self) -> None:
        document = self.fixture("turn_settle")
        anchor = "right hand rests stationary inside the pocket"
        hand = self.beat(document)["hand_plan"]["right"]
        hand.update({"start_anchor": anchor, "action": "stays anchored", "end_anchor": anchor})
        self.beat(document)["right_hand_state"] = anchor
        self.timing(document)["speech_hand_anchors"]["right"] = anchor
        self.recompile(document)
        self.assert_valid(document)

    def test_script_broll_and_prompt_cannot_omit_or_tamper_timing(self) -> None:
        for name, items in (("script", "beats"), ("broll", "shots"), ("prompt", "beats")):
            for omit in (False, True):
                with self.subTest(name=name, omit=omit):
                    document = self.fixture()
                    projected = self.variant(document)["renderings"][name][items][1]
                    if omit:
                        projected.pop(FIELD)
                    else:
                        projected[FIELD]["speech_window_seconds"][0] = 3.1
                    self.assert_rejects(document, "TIMELINE_ACTION_DRIFT")

    def test_timing_changes_invalidate_canonical_and_compiled_text_hashes(self) -> None:
        document = self.fixture()
        prompt = self.variant(document)["renderings"]["prompt"]
        old_beats_hash, old_text_hash = prompt["canonical_beats_sha256"], prompt["compiled_text_sha256"]
        self.timing(document)["allowance_displacement_cm"] = 1.5
        self.recompile(document)
        self.assert_valid(document)
        prompt = self.variant(document)["renderings"]["prompt"]
        self.assertNotEqual(old_beats_hash, prompt["canonical_beats_sha256"])
        self.assertNotEqual(old_text_hash, prompt["compiled_text_sha256"])
        prompt["canonical_beats_sha256"] = old_beats_hash
        prompt["compiled_text_sha256"] = old_text_hash
        self.assert_rejects(document, "CANONICAL_BEATS_HASH_MISMATCH", "PROMPT_HASH_MISMATCH")

    def test_director_receipt_and_paid_fingerprint_bind_sequential_windows(self) -> None:
        document = self.fixture()
        result = self.assert_valid(document)
        BATCH._attach_director_receipts(result, document, "a" * 64)
        receipt = result["director_receipts"][0]
        prompt = self.variant(document)["renderings"]["prompt"]
        self.assertEqual(receipt["canonical_beats_sha256"], prompt["canonical_beats_sha256"])
        row = {"job_kind": "zibuyu_apparel", "director_receipt": receipt}
        old_signature = LEDGER.paid_request_signature(row)
        self.timing(document)["speech_window_seconds"] = [3.1, 5.25]
        self.timing(document)["settle_window_seconds"] = [2.75, 3.1]
        self.recompile(document)
        new_result = self.assert_valid(document)
        BATCH._attach_director_receipts(new_result, document, "a" * 64)
        row["director_receipt"] = new_result["director_receipts"][0]
        self.assertNotEqual(old_signature, LEDGER.paid_request_signature(row))


if __name__ == "__main__":
    unittest.main()
