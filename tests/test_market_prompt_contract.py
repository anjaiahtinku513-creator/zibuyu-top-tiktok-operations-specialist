from __future__ import annotations

import copy
import hashlib
import importlib.util
import re
import unittest
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PLUGIN_ROOT / "skills" / "seedance-ugc-cn-director" / "scripts"
GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MARKET = _load_module("zibuyu_test_market_prompt_contract", SCRIPT_DIR / "market_prompt_contract.py")
BATCH = _load_module("zibuyu_test_validate_batch_compile", SCRIPT_DIR / "validate_batch_compile.py")


class MarketPromptContractTests(unittest.TestCase):
    maxDiff = None

    def fixture(self, **kwargs: Any) -> dict[str, Any]:
        return BATCH._fixture_v14(**kwargs)

    def result(self, document: dict[str, Any]) -> dict[str, Any]:
        return BATCH.validate(document)

    def codes(self, document: dict[str, Any]) -> set[str]:
        return {item["code"] for item in self.result(document).get("errors", [])}

    def assert_rejects(self, document: dict[str, Any], *expected_codes: str) -> None:
        result = self.result(document)
        self.assertFalse(result["valid"], result)
        actual_codes = {item["code"] for item in result["errors"]}
        for expected in expected_codes:
            self.assertIn(expected, actual_codes, result)

    def recompile(self, document: dict[str, Any]) -> None:
        shared = document["shared_core"]
        variant = document["variants"][0]
        BATCH._refresh_fixture_three_layer_deadlines(document)
        variant["renderings"] = BATCH._compile_renderings_v7(
            variant, document["batch_key"], shared.get("research_bundle"),
        )

    def compile_current_deadlines(self, document: dict[str, Any]) -> None:
        shared = document["shared_core"]
        variant = document["variants"][0]
        variant["renderings"] = BATCH._compile_renderings_v7(
            variant, document["batch_key"], shared.get("research_bundle"),
        )

    def sync_voiceover_review(self, document: dict[str, Any]) -> None:
        variant = document["variants"][0]
        for row, beat in zip(variant["voiceover_review"], variant["canonical_timeline"]["beats"]):
            row.update({
                "beat_id": beat.get("beat_id"),
                "speech_mode": beat.get("speech_mode"),
                "spoken_language": beat.get("spoken_language"),
                "market_line": beat.get("spoken_line"),
                "silence_reason": beat.get("silence_reason"),
            })

    def compiled_text(self, document: dict[str, Any]) -> str:
        return document["variants"][0]["renderings"]["prompt"]["compiled_text"]

    def test_public_contract_exports_are_stable(self) -> None:
        self.assertEqual(MARKET.MARKET_PROMPT_CONTRACT_ID, "zibuyu_market_prompt_v1")
        self.assertEqual(MARKET.QUALITY_CONTRACT_ID, "zibuyu_ugc_quality_v4")
        self.assertEqual(MARKET.PROMPT_SERIALIZER_ID, "canonical_prompt_v7")
        self.assertEqual(MARKET.DEADLINE_CONTRACT_ID, "zibuyu_three_layer_deadlines_v1")
        self.assertEqual(
            MARKET.GENERATION_CONTROL_FIELDS,
            (
                "prompt_shell_mode", "delivery_mode", "camera_mode", "music_mode",
                "verdict_mode", "commerce_cta_mode", "screen_text_policy",
            ),
        )
        self.assertEqual(
            MARKET.SCENE_STRATEGY_REQUIRED_FIELDS,
            (
                "wear_context", "scene_role", "primary_scene_id", "primary_scene_description",
                "occasion", "buyer_styling_question", "outfit_answer", "video_form",
                "location_plan", "bedroom_policy",
            ),
        )
        self.assertTrue({"cafe_social", "beach_vacation", "commercial_street"}.issubset(MARKET.SCENE_IDS))
        self.assertEqual(
            MARKET.ALLOWED_MARKET_TUPLES,
            {
                ("US", "美1", "en-US", "us_champion_v1"),
                ("US", "美2", "en-US", "us_champion_v1"),
                ("US", "美3", "en-US", "us_champion_v1"),
                ("DE", "德1", "de-DE", "de_champion_v1"),
                ("DE", "德2", "de-DE", "de_champion_v1"),
                ("DE", "德3", "de-DE", "de_champion_v1"),
            },
        )

    def test_canonical_hash_ignores_mapping_insertion_order(self) -> None:
        self.assertEqual(
            MARKET.canonical_sha256({"b": 2, "a": [1, 3]}),
            MARKET.canonical_sha256({"a": [1, 3], "b": 2}),
        )

    def test_us_technical_shell_is_valid_and_submission_eligible(self) -> None:
        document = self.fixture(market="US", prompt_shell_mode="us_technical_shell")
        result = self.result(document)
        self.assertTrue(result["valid"], result)
        self.assertTrue(result["market_prompt_v7_submission_eligible"])
        prompt = document["variants"][0]["renderings"]["prompt"]
        self.assertEqual(prompt["serializer_id"], "canonical_prompt_v7")
        self.assertEqual(prompt["compiled_text_sha256"], hashlib.sha256(prompt["compiled_text"].encode("utf-8")).hexdigest())
        lines = prompt["compiled_text"].splitlines()
        self.assertTrue(lines[0].startswith("Use @Image1 only for the fixed creator"))
        self.assertTrue(lines[1].startswith("Use @Image2 only for the garment"))
        self.assertTrue(lines[2].startswith("GLOBAL NON-NEGOTIABLES:"))
        self.assertTrue(lines[3].startswith("SUBJECT, GARMENT, SCENE, LIGHT, AND SOUND NON-NEGOTIABLES:"))
        self.assertTrue(lines[4].startswith("US creative direction:"))
        self.assertTrue(lines[5].startswith("US technical shell:"))
        self.assertEqual(sum(line.startswith("Cut ") for line in lines), 3)
        self.assertEqual(sum("Shot non-negotiables:" in line for line in lines), 6)

    def test_new_work_requires_hash_bound_zero_human_identity_audit(self) -> None:
        document = self.fixture()
        reference = document["variants"][0]["references"][0]
        reference.pop("identity_cue_audit")
        self.assert_rejects(document, "THREE_VIEW_IDENTITY_AUDIT_INVALID")

    def test_human_cue_cannot_be_hidden_behind_passed_summary(self) -> None:
        document = self.fixture()
        reference = document["variants"][0]["references"][0]
        reference["identity_cue_audit"]["hands_fingers_nails_present"] = True
        reference["identity_cue_audit_sha256"] = BATCH._canonical_sha256(reference["identity_cue_audit"])
        self.assert_rejects(document, "THREE_VIEW_IDENTITY_AUDIT_INVALID")

    def test_identity_audit_is_bound_to_exact_reference_bytes(self) -> None:
        document = self.fixture()
        reference = document["variants"][0]["references"][0]
        reference["identity_cue_audit"]["audited_sha256"] = "0" * 64
        reference["identity_cue_audit_sha256"] = BATCH._canonical_sha256(reference["identity_cue_audit"])
        self.assert_rejects(document, "THREE_VIEW_IDENTITY_AUDIT_INVALID")

    def test_us_compact_shell_retains_all_technical_settings(self) -> None:
        document = self.fixture(
            market="US", prompt_shell_mode="us_compact_storyboard",
            commerce_cta_mode="none", verdict_mode="ownership_verdict",
        )
        result = self.result(document)
        self.assertTrue(result["valid"], result)
        text = self.compiled_text(document)
        self.assertIn("Compact US storyboard: authentic phone-shot friend share", text)
        self.assertIn("Use a fixed phone camera", text)
        self.assertIn("no music", text.casefold())
        self.assertIn(r'''5'6\" / 115 lb / Size S''', text)

    def test_de_hybrid_shell_is_valid_and_keeps_review_text_outbound_clean(self) -> None:
        document = self.fixture(market="DE", delivery_mode="de_hybrid_proof")
        result = self.result(document)
        self.assertTrue(result["valid"], result)
        text = self.compiled_text(document)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("Use @Image1 only for the fixed creator"))
        self.assertTrue(lines[1].startswith("Use @Image2 only for the garment"))
        self.assertTrue(lines[2].startswith("GLOBAL NON-NEGOTIABLES:"))
        self.assertTrue(lines[3].startswith("SUBJECT, GARMENT, SCENE, LIGHT, AND SOUND NON-NEGOTIABLES:"))
        self.assertTrue(lines[4].startswith("German creative direction:"))
        self.assertTrue(lines[5].startswith("German performance shell:"))
        self.assertIn("168 cm / 52 kg / Größe S", text)
        self.assertIn("Wirkt ein Oversize-Top schnell kastenförmig?", text)
        for row in document["variants"][0]["voiceover_review"]:
            translation = row.get("zh_cn_translation")
            if translation:
                self.assertNotIn(translation, text)

    def test_model_specific_fit_stats_are_projected(self) -> None:
        cases = (
            ("美1", "US", "5'6\" / 115 lb / Size S"),
            ("美2", "US", "5'6\" / 200lb / Size 2XL"),
            ("美3", "US", "5'6\" / 200lb / Size 2XL"),
            ("德1", "DE", "168 cm / 52 kg / Größe S"),
            ("德2", "DE", "168 cm / 52 kg / Größe S"),
            ("德3", "DE", "168 cm / 90 kg / Größe 2XL"),
        )
        for model_preset, market, expected_stats in cases:
            with self.subTest(model_preset=model_preset):
                document = self.fixture(market=market)
                document["batch_key"]["model_preset"] = model_preset
                self.recompile(document)
                result = self.result(document)
                self.assertTrue(result["valid"], result)
                global_deadlines = document["variants"][0]["three_layer_deadlines"]["global_deadlines"]
                self.assertEqual(global_deadlines["allowed_screen_text"], [expected_stats])
                self.assertIn(expected_stats.replace('"', r'\"'), self.compiled_text(document))

    def test_de_live_simple_is_valid_and_all_spoken_beats_are_visible_dialogue(self) -> None:
        document = self.fixture(market="DE", delivery_mode="de_live_simple")
        result = self.result(document)
        self.assertTrue(result["valid"], result)
        beats = document["variants"][0]["canonical_timeline"]["beats"]
        for beat in beats:
            if beat.get("spoken_line"):
                self.assertEqual(beat["speech_mode"], "on_camera_dialogue")
                self.assertEqual(beat["mouth_visibility"], "visible")
                self.assertTrue(beat["lip_sync_required"])
            if beat.get("claim_proof_id"):
                self.assertIn(beat["proof_action_type"], MARKET.DE_LIVE_SIMPLE_ACTIONS)

    def test_v7_has_no_raw_metadata_or_internal_enum_labels(self) -> None:
        for document in (self.fixture(market="US"), self.fixture(market="DE")):
            text = self.compiled_text(document)
            lowered = text.casefold()
            for forbidden in (
                "claim_id", "claim_proof_id", "evidence_id", "pain_point_id",
                "research_bundle", "review_id", "https://", "raw json", "raw yaml",
                "denim_bag", "point_trace", "turn_settle", "internal beat",
            ):
                self.assertNotIn(forbidden, lowered)
            self.assertNotRegex(text, r"```|\{\s*\"")

    def test_profile_controls_and_review_hashes_are_bound_and_receipted(self) -> None:
        document = self.fixture(market="US")
        variant = document["variants"][0]
        prompt = variant["renderings"]["prompt"]
        profile = MARKET.MARKET_PROFILES[document["batch_key"]["market_prompt_profile_id"]]
        self.assertEqual(prompt["market_prompt_profile_sha256"], MARKET.canonical_sha256(profile))
        self.assertEqual(prompt["generation_controls_sha256"], MARKET.canonical_sha256(variant["generation_controls"]))
        self.assertEqual(prompt["voiceover_review_sha256"], MARKET.canonical_sha256(variant["voiceover_review"]))
        result = self.result(document)
        BATCH._attach_director_receipts(result, document, "a" * 64)
        receipt = result["director_receipts"][0]
        self.assertEqual(receipt["market_prompt_contract_id"], "zibuyu_market_prompt_v1")
        self.assertEqual(receipt["market_prompt_profile_id"], "us_champion_v1")
        self.assertEqual(receipt["market"], "US")
        self.assertEqual(receipt["voiceover_language"], "en-US")
        for field in (
            "market_prompt_profile_sha256", "generation_controls_sha256",
            "voiceover_review_sha256", "three_layer_deadlines_sha256",
            "compiled_text_sha256",
        ):
            self.assertEqual(receipt[field], prompt[field])
        self.assertEqual(receipt["deadline_contract_id"], MARKET.DEADLINE_CONTRACT_ID)

    def test_compile_is_deterministic_and_goldens_are_exact(self) -> None:
        cases = (
            ("us_v7.txt", {"market": "US"}),
            ("de_v7.txt", {"market": "DE", "delivery_mode": "de_hybrid_proof"}),
        )
        for golden_name, kwargs in cases:
            first = BATCH.compile_document(self.fixture(**kwargs))
            second = BATCH.compile_document(self.fixture(**kwargs))
            first_text = self.compiled_text(first)
            second_text = self.compiled_text(second)
            self.assertEqual(first_text, second_text)
            expected = (GOLDEN_DIR / golden_name).read_text(encoding="utf-8").removesuffix("\n")
            self.assertEqual(first_text, expected)

    def test_closed_market_tuple_rejects_cross_market_model(self) -> None:
        document = self.fixture(market="US")
        document["batch_key"]["model_preset"] = "德1"
        self.assert_rejects(document, "MODEL_PRESET_MARKET_MISMATCH")

    def test_closed_market_tuple_rejects_language_profile_drift(self) -> None:
        document = self.fixture(market="US")
        document["batch_key"]["voiceover_language"] = "de-DE"
        self.assert_rejects(document, "MARKET_LANGUAGE_MISMATCH")

    def test_generation_controls_are_required_and_profile_scoped(self) -> None:
        controls = copy.deepcopy(self.fixture(market="US")["variants"][0]["generation_controls"])
        del controls["camera_mode"]
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertIn("MARKET_PROFILE_INVALID", {item["code"] for item in issues})
        controls = copy.deepcopy(self.fixture(market="US")["variants"][0]["generation_controls"])
        controls["prompt_shell_mode"] = "de_performance_script"
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertIn("MARKET_PROFILE_INVALID", {item["code"] for item in issues})

    def test_scene_strategy_is_required_and_compiled_as_an_occasion_answer(self) -> None:
        document = self.fixture(market="US")
        controls = copy.deepcopy(document["variants"][0]["generation_controls"])
        del controls["scene_strategy"]
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertIn("SCENE_STRATEGY_INVALID", {item["code"] for item in issues})

        text = self.compiled_text(document)
        self.assertIn("Scene and occasion sale:", text)
        self.assertIn("weekend shopping followed by a casual lunch", text)
        self.assertIn("shopping and lunch", text)
        self.assertIn("high-rise straight jeans", text)
        self.assertNotIn(" in bright bedroom", text.casefold())

    def test_outward_wear_cannot_default_to_a_private_primary_scene(self) -> None:
        controls = copy.deepcopy(self.fixture(market="US")["variants"][0]["generation_controls"])
        strategy = controls["scene_strategy"]
        strategy.update({
            "primary_scene_id": "bedroom_mirror",
            "primary_scene_description": "a bright bedroom mirror corner",
            "bedroom_policy": "excluded",
        })
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertIn("BEDROOM_DEFAULT_FORBIDDEN", {item["code"] for item in issues})

    def test_private_primary_scene_requires_a_specific_proof_reason(self) -> None:
        controls = copy.deepcopy(self.fixture(market="US")["variants"][0]["generation_controls"])
        strategy = controls["scene_strategy"]
        strategy.update({
            "scene_role": "fit_proof",
            "primary_scene_id": "bedroom_mirror",
            "primary_scene_description": "a clean bedroom mirror area used for exact waist and hem fit proof",
            "video_form": "fixed_phone_fit_proof",
            "bedroom_policy": "primary_justified",
            "bedroom_justification": "The fixed mirror distance is required to compare the waist, side seam, and hem endpoints in one stable fit proof.",
        })
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertNotIn("BEDROOM_DEFAULT_FORBIDDEN", {item["code"] for item in issues})

        strategy["bedroom_justification"] = "Natural light"
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertIn("BEDROOM_DEFAULT_FORBIDDEN", {item["code"] for item in issues})

        for weak_reason in (
            "Natural light is convenient to film, easy to generate, and gives a familiar UGC feel even when this sentence is padded with extra production wording.",
            "The quiet room provides a consistent background and keeps the creator centered throughout the recording.",
        ):
            strategy["bedroom_justification"] = weak_reason
            issues = MARKET.validate_generation_controls("us_champion_v1", controls)
            self.assertIn("BEDROOM_DEFAULT_FORBIDDEN", {item["code"] for item in issues})

    def test_scene_strategy_rejects_vague_occasion_and_incomplete_outfit(self) -> None:
        controls = copy.deepcopy(self.fixture(market="US")["variants"][0]["generation_controls"])
        controls["scene_strategy"]["occasion"] = "daily life"
        issues = MARKET.validate_generation_controls("us_champion_v1", controls)
        self.assertIn("SCENE_STRATEGY_INVALID", {item["code"] for item in issues})

        controls = copy.deepcopy(self.fixture(market="DE")["variants"][0]["generation_controls"])
        controls["scene_strategy"]["outfit_answer"] = "jeans"
        issues = MARKET.validate_generation_controls("de_champion_v1", controls)
        self.assertIn("SCENE_STRATEGY_INVALID", {item["code"] for item in issues})

    def test_private_primary_scene_accepts_specific_detail_and_styling_proofs(self) -> None:
        examples = (
            (
                "detail_proof",
                "The fixed mirror distance is required to compare neckline stitching, button alignment, and cuff seam detail in the same frame.",
            ),
            (
                "multi_styling",
                "The fixed camera distance is required to compare tucked, untucked, and layered outfit styling switches in the same frame.",
            ),
        )
        for scene_role, reason in examples:
            controls = copy.deepcopy(self.fixture(market="US")["variants"][0]["generation_controls"])
            controls["scene_strategy"].update({
                "scene_role": scene_role,
                "primary_scene_id": "bedroom_mirror",
                "primary_scene_description": "a clean bedroom mirror area reserved for the declared proof",
                "video_form": "fixed_phone_fit_proof" if scene_role == "detail_proof" else "multi_styling_switch",
                "bedroom_policy": "primary_justified",
                "bedroom_justification": reason,
            })
            issues = MARKET.validate_generation_controls("us_champion_v1", controls)
            self.assertNotIn("BEDROOM_DEFAULT_FORBIDDEN", {item["code"] for item in issues}, issues)

    def test_scene_strategy_is_exactly_projected_into_creative_and_continuity_fields(self) -> None:
        mutations = (
            ("quality_plan", "continuity_anchors", "scene", "bedroom_mirror"),
            ("quality_plan", "continuity_anchors", "outfit", "jeans"),
            ("creative_delta", "scene_id", None, "bedroom_mirror"),
            ("creative_delta", "styling_signature", None, "jeans"),
        )
        for first, second, third, value in mutations:
            document = self.fixture(market="US")
            if third is None:
                document["variants"][0][first][second] = value
            else:
                document["variants"][0][first][second][third] = value
            self.assert_rejects(document, "SCENE_STRATEGY_DRIFT")

    def test_private_proof_cut_must_transition_once_to_the_primary_scene(self) -> None:
        document = self.fixture(market="US")
        variant = document["variants"][0]
        strategy = variant["generation_controls"]["scene_strategy"]
        strategy.update({
            "video_form": "mirror_to_destination_match_cut",
            "location_plan": "primary_plus_proof_cut",
            "bedroom_policy": "proof_only",
            "proof_scene_id": "bedroom_mirror",
            "proof_scene_description": "a clean bedroom mirror area for the opening fit check",
        })
        for beat in variant["canonical_timeline"]["beats"][:2]:
            beat["scene_id"] = "bedroom_mirror"
            beat["scene"] = strategy["proof_scene_description"]
        self.recompile(document)
        result = self.result(document)
        self.assertTrue(result["valid"], result)
        self.assertIn("make one intentional transition", self.compiled_text(document))

        drift = copy.deepcopy(document)
        drift["variants"][0]["canonical_timeline"]["beats"][4]["scene_id"] = "bedroom_mirror"
        drift["variants"][0]["canonical_timeline"]["beats"][4]["scene"] = "a bedroom mirror"
        self.recompile(drift)
        self.assert_rejects(drift, "SCENE_LOCATION_PLAN_INVALID", "BEDROOM_DEFAULT_FORBIDDEN")

    def test_scene_id_and_readable_scene_prose_cannot_disagree(self) -> None:
        document = self.fixture(market="DE")
        beat = document["variants"][0]["canonical_timeline"]["beats"][0]
        beat["scene"] = "a bright bedroom mirror corner"
        self.recompile(document)
        self.assert_rejects(document, "SCENE_TIMELINE_MISMATCH")

    def test_spoken_language_field_must_match_batch_language(self) -> None:
        document = self.fixture(market="US")
        beat = document["variants"][0]["canonical_timeline"]["beats"][0]
        beat["spoken_language"] = "de-DE"
        self.sync_voiceover_review(document)
        self.recompile(document)
        self.assert_rejects(document, "MARKET_LANGUAGE_MISMATCH")

    def test_de_spoken_slot_rejects_chinese_review_text(self) -> None:
        document = self.fixture(market="DE")
        beat = document["variants"][0]["canonical_timeline"]["beats"][0]
        beat["spoken_line"] = "这句中文不能进入德语口播。"
        document["variants"][0]["creative_delta"]["hook_text_original"] = beat["spoken_line"]
        self.sync_voiceover_review(document)
        self.recompile(document)
        self.assert_rejects(document, "LANGUAGE_GATE_FAILED")

    def test_de_spoken_slot_rejects_high_confidence_english_clause(self) -> None:
        document = self.fixture(market="DE")
        beat = document["variants"][0]["canonical_timeline"]["beats"][0]
        beat["spoken_line"] = "Look at this top and tap the link."
        document["variants"][0]["creative_delta"]["hook_text_original"] = beat["spoken_line"]
        self.sync_voiceover_review(document)
        self.recompile(document)
        self.assert_rejects(document, "LANGUAGE_GATE_FAILED")

    def test_voiceover_review_must_be_exact_projection(self) -> None:
        document = self.fixture(market="US")
        document["variants"][0]["voiceover_review"][0]["market_line"] = "Drifted review line."
        self.assert_rejects(document, "VOICEOVER_REVIEW_DRIFT")

    def test_voiceover_review_rejects_placeholder_translation(self) -> None:
        document = self.fixture(market="DE")
        document["variants"][0]["voiceover_review"][0]["zh_cn_translation"] = "中文审核译文：1"
        self.recompile(document)
        self.assert_rejects(document, "VOICEOVER_REVIEW_DRIFT")

    def test_default_market_fixtures_have_meaningful_chinese_translations(self) -> None:
        for document in (self.fixture(market="US"), self.fixture(market="DE")):
            for row in document["variants"][0]["voiceover_review"]:
                if row["market_line"]:
                    translation = row["zh_cn_translation"]
                    self.assertRegex(translation, r"[\u3400-\u9fff]")
                    self.assertNotRegex(translation, r"^(?:中文)?(?:审核)?(?:译文|翻译)[：:]?\d*$")

    def test_proof_endpoint_must_match_plan_and_visible_endpoint(self) -> None:
        document = self.fixture(market="US")
        beat = document["variants"][0]["canonical_timeline"]["beats"][1]
        beat["proof_endpoint"] = "an invented endpoint"
        beat["visible_endpoint"] = "an invented endpoint"
        self.recompile(document)
        self.assert_rejects(document, "CLAIM_PROOF_ENDPOINT_MISMATCH")

    def test_complex_proof_requires_offscreen_voiceover(self) -> None:
        document = self.fixture(market="US")
        beat = document["variants"][0]["canonical_timeline"]["beats"][3]
        self.assertIn(beat["proof_action_type"], MARKET.COMPLEX_PROOF_ACTIONS)
        beat.update({
            "speech_mode": "on_camera_dialogue", "mouth_visibility": "visible",
            "lip_sync_required": True, "actor": "the same creator with face visible",
        })
        self.sync_voiceover_review(document)
        self.recompile(document)
        self.assert_rejects(document, "DELIVERY_MODE_CONFLICT")

    def test_cut_block_density_requires_three_or_four_runs(self) -> None:
        document = self.fixture(market="US")
        beats = document["variants"][0]["canonical_timeline"]["beats"]
        for beat, setup_id in zip(beats, ("a", "a", "a", "b", "b", "b")):
            beat["camera_setup_id"] = setup_id
        self.recompile(document)
        self.assert_rejects(document, "CUT_BLOCK_DENSITY_INVALID")

    def test_cut_block_rejects_a_b_a_reuse(self) -> None:
        document = self.fixture(market="US")
        beats = document["variants"][0]["canonical_timeline"]["beats"]
        for beat, setup_id in zip(beats, ("a", "a", "b", "b", "a", "a")):
            beat["camera_setup_id"] = setup_id
        self.recompile(document)
        self.assert_rejects(document, "CUT_BLOCK_SEQUENCE_INVALID")

    def test_cut_block_cannot_cross_macro_phase(self) -> None:
        document = self.fixture(market="US")
        beats = document["variants"][0]["canonical_timeline"]["beats"]
        for beat, setup_id in zip(beats, ("a", "a", "a", "b", "c", "c")):
            beat["camera_setup_id"] = setup_id
        self.recompile(document)
        self.assert_rejects(document, "CUT_BLOCK_SEQUENCE_INVALID")

    def test_camera_music_and_screen_controls_are_enforced(self) -> None:
        camera = self.fixture(market="US")
        camera["variants"][0]["canonical_timeline"]["beats"][0]["camera_motivation"] += " handheld"
        self.recompile(camera)
        self.assert_rejects(camera, "CAMERA_MODE_CONFLICT")

        music = self.fixture(market="US")
        music["variants"][0]["canonical_timeline"]["beats"][0]["audio"] += " with soft background music"
        self.recompile(music)
        self.assert_rejects(music, "MUSIC_MODE_CONFLICT")

        screen = self.fixture(market="US")
        screen["variants"][0]["canonical_timeline"]["beats"][0]["scene"] += " with subtitle overlay"
        self.recompile(screen)
        self.assert_rejects(screen, "SCREEN_TEXT_POLICY_INVALID")

    def test_cta_must_be_unique_final_human_only_and_evidence_bound(self) -> None:
        nonfinal = self.fixture(market="US")
        beats = nonfinal["variants"][0]["canonical_timeline"]["beats"]
        beats[-2]["purpose"] = "cta"
        beats[-1]["purpose"] = "styling"
        self.recompile(nonfinal)
        self.assert_rejects(nonfinal, "CTA_POSITION_INVALID")

        graphic = self.fixture(market="US")
        graphic["variants"][0]["canonical_timeline"]["beats"][-1]["actor"] += " beside a shopping cart icon"
        self.recompile(graphic)
        self.assert_rejects(graphic, "CTA_POLICY_INVALID")

        promo = self.fixture(market="US", commerce_cta_mode="evidence_backed_promo")
        self.assert_rejects(promo, "CTA_POLICY_INVALID")

    def test_outbound_text_rejects_urls_and_internal_metadata(self) -> None:
        document = self.fixture(market="US")
        beat = document["variants"][0]["canonical_timeline"]["beats"][0]
        beat["scene"] += "; claim_id: claim-neckline; https://example.test"
        self.recompile(document)
        self.assert_rejects(document, "PROMPT_META_LEAK")

    def test_v7_hash_and_serializer_drift_are_rejected(self) -> None:
        hash_drift = self.fixture(market="US")
        hash_drift["variants"][0]["renderings"]["prompt"]["generation_controls_sha256"] = "0" * 64
        self.assert_rejects(hash_drift, "QUALITY_PLAN_HASH_MISMATCH")

        serializer_drift = self.fixture(market="US")
        serializer_drift["variants"][0]["renderings"]["prompt"]["serializer_id"] = "canonical_prompt_v5"
        self.assert_rejects(serializer_drift, "PROMPT_SERIALIZER_MISMATCH")

    def test_three_layer_deadlines_are_complete_and_hash_bound(self) -> None:
        document = self.fixture(market="US")
        variant = document["variants"][0]
        deadlines = variant["three_layer_deadlines"]
        self.assertEqual(set(deadlines), set(MARKET.THREE_LAYER_DEADLINE_FIELDS))
        self.assertEqual(set(deadlines["global_deadlines"]), set(MARKET.GLOBAL_DEADLINE_FIELDS))
        self.assertEqual(set(deadlines["asset_deadlines"]), set(MARKET.ASSET_DEADLINE_FIELDS))
        self.assertEqual(len(deadlines["shot_deadlines"]), len(variant["canonical_timeline"]["beats"]))
        self.assertTrue(all(set(shot) == set(MARKET.SHOT_DEADLINE_FIELDS) for shot in deadlines["shot_deadlines"]))
        prompt = variant["renderings"]["prompt"]
        self.assertEqual(prompt["three_layer_deadlines_sha256"], MARKET.canonical_sha256(deadlines))

    def test_missing_deadline_layer_is_rejected(self) -> None:
        for layer in ("global_deadlines", "asset_deadlines", "shot_deadlines"):
            document = self.fixture(market="US")
            del document["variants"][0]["three_layer_deadlines"][layer]
            self.assert_rejects(document, "DEADLINE_CONTRACT_INVALID")

    def test_global_deadline_drift_is_rejected(self) -> None:
        document = self.fixture(market="US")
        document["variants"][0]["three_layer_deadlines"]["global_deadlines"]["frame_rate_fps"] = 30
        self.compile_current_deadlines(document)
        self.assert_rejects(document, "GLOBAL_DEADLINE_DRIFT")

    def test_asset_deadline_drift_is_rejected(self) -> None:
        document = self.fixture(market="DE")
        asset = document["variants"][0]["three_layer_deadlines"]["asset_deadlines"]
        asset["scene_strategy"]["primary_scene_id"] = "bedroom_mirror"
        self.compile_current_deadlines(document)
        self.assert_rejects(document, "ASSET_DEADLINE_DRIFT")

    def test_shot_deadline_drift_is_rejected(self) -> None:
        document = self.fixture(market="US")
        shot = document["variants"][0]["three_layer_deadlines"]["shot_deadlines"][0]
        shot["core_action"] = "an unrelated invented action"
        self.compile_current_deadlines(document)
        self.assert_rejects(document, "SHOT_DEADLINE_DRIFT")

    def test_no_generated_text_policy_has_no_fit_stats_or_overlay(self) -> None:
        document = self.fixture(market="US")
        document["variants"][0]["generation_controls"]["screen_text_policy"] = "no_generated_text"
        self.recompile(document)
        result = self.result(document)
        self.assertTrue(result["valid"], result)
        global_deadlines = document["variants"][0]["three_layer_deadlines"]["global_deadlines"]
        self.assertEqual(global_deadlines["allowed_screen_text"], [])
        text = self.compiled_text(document)
        self.assertNotIn("5'6\" / 115 lb / Size S", text)
        self.assertIn("Generate no text anywhere in the frame", text)

    def test_historical_v6_compile_bytes_remain_unchanged_and_read_only(self) -> None:
        legacy = BATCH._fixture_v14_legacy_v6(market="US")
        before = self.compiled_text(legacy).encode("utf-8")
        compiled = BATCH.compile_document(legacy)
        self.assertEqual(before, self.compiled_text(compiled).encode("utf-8"))
        expected = (GOLDEN_DIR / "us_v6.txt").read_text(encoding="utf-8").removesuffix("\n")
        self.assertEqual(self.compiled_text(compiled), expected)
        result = BATCH.validate(compiled)
        self.assertTrue(result["valid"], result)
        self.assertFalse(result["eligible_for_new_submission"])
        self.assertTrue(result["historical_prompt_v6_read_only"])

    def test_legacy_v5_compile_bytes_remain_unchanged(self) -> None:
        legacy = BATCH._fixture_v13()
        before = [variant["renderings"]["prompt"]["compiled_text"].encode("utf-8") for variant in legacy["variants"]]
        compiled = BATCH.compile_document(legacy)
        after = [variant["renderings"]["prompt"]["compiled_text"].encode("utf-8") for variant in compiled["variants"]]
        self.assertEqual(before, after)
        result = BATCH.validate(compiled)
        self.assertTrue(result["valid"], result)
        self.assertFalse(result["eligible_for_new_submission"])
        self.assertFalse(result["market_prompt_v6_submission_eligible"])

    def test_legacy_v5_exact_resume_is_data_only_and_hash_locked(self) -> None:
        legacy = BATCH._fixture_v13()
        prompt_hashes = {
            variant["variant_id"]: variant["renderings"]["prompt"]["compiled_text_sha256"]
            for variant in legacy["variants"]
        }
        reference_hashes = {
            reference["reference_id"]: reference["sha256"]
            for variant in legacy["variants"] for reference in variant["references"]
        }
        legacy["legacy_v5_exact_resume"] = {
            "prevalidated": True, "explicitly_approved": True, "submitted": False,
            "record_id": None, "prompt_hashes": prompt_hashes,
            "reference_hashes": reference_hashes,
        }
        result = BATCH.validate(legacy)
        state = result["legacy_v5_exact_resume"]
        self.assertTrue(result["valid"], result)
        self.assertTrue(state["eligible"])
        self.assertFalse(state["execution_authorized"])
        self.assertEqual(state["reason"], "exact_resume_only")

        drift = copy.deepcopy(legacy)
        first_key = next(iter(drift["legacy_v5_exact_resume"]["prompt_hashes"]))
        drift["legacy_v5_exact_resume"]["prompt_hashes"][first_key] = "0" * 64
        self.assertEqual(
            BATCH.validate(drift)["legacy_v5_exact_resume"]["reason"],
            "prompt_or_reference_hash_drift",
        )

        submitted = copy.deepcopy(legacy)
        submitted["legacy_v5_exact_resume"]["record_id"] = "203177"
        self.assertEqual(
            BATCH.validate(submitted)["legacy_v5_exact_resume"]["reason"],
            "submitted_or_record_id_present",
        )

    def test_legacy_self_test_matrix_stays_green(self) -> None:
        result = BATCH.run_self_test()
        self.assertTrue(result["valid"], result)
        self.assertGreaterEqual(result["passed"], 125)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
