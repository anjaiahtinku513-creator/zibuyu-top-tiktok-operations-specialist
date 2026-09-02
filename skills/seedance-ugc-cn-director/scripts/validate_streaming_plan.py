#!/usr/bin/env python3
"""Validate immutable multi-color plans for quality-gated streaming releases."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


try:
    import market_prompt_contract as _market_contract
except ModuleNotFoundError:
    _market_contract_path = Path(__file__).with_name("market_prompt_contract.py")
    _market_contract_spec = importlib.util.spec_from_file_location(
        "zibuyu_streaming_market_prompt_contract", _market_contract_path,
    )
    if _market_contract_spec is None or _market_contract_spec.loader is None:
        raise RuntimeError(f"cannot load market prompt contract: {_market_contract_path}")
    _market_contract = importlib.util.module_from_spec(_market_contract_spec)
    _market_contract_spec.loader.exec_module(_market_contract)


CONTRACT_ID = "zibuyu_quality_gated_streaming_v2"
SCHEMA_VERSION = "1.1"
MARKET_PROMPT_CONTRACT_ID = _market_contract.MARKET_PROMPT_CONTRACT_ID
QUALITY_CONTRACT_ID = _market_contract.QUALITY_CONTRACT_ID
PROMPT_SERIALIZER_ID = _market_contract.PROMPT_SERIALIZER_ID
LEGACY_CONTRACT_ID = "zibuyu_quality_gated_streaming_v1"
LEGACY_SCHEMA_VERSION = "1.0"
LEGACY_QUALITY_CONTRACT_ID = "zibuyu_ugc_quality_v2"
LEGACY_PROMPT_SERIALIZER_ID = "canonical_prompt_v4"
LEGACY_V5_PROMPT_SERIALIZER_ID = "canonical_prompt_v5"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
LEGACY_BRAND_HASHTAG = "#Imily Bela"
HASHTAG_RE = re.compile(r"^#[^\s#]+$")
VARIANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
GARMENT_FIELDS = (
    "category", "silhouette", "neckline", "sleeve", "length", "closure",
    "seams_panels", "texture_scale", "thickness", "opacity", "drape",
)
BATCH_KEY_FIELDS = (
    "model_preset", "market", "voiceover_language", "platform",
    "duration_seconds", "aspect_ratio", "resolution", "frame_rate_fps", "market_prompt_profile_id",
)
CREATIVE_DELTA_FIELDS = (
    "hook_angle_id", "hook_text_original", "color_terms", "opening_visual_id",
    "scene_id", "scene_palette", "styling_signature", "proof_focus_id",
    "pain_focus_id", "signature_action_id", "spoken_intent_ids",
    "caption_angle_id", "difference_summary",
)
QUALITY_GATES = {
    "all_source_colors_grouped": True,
    "shared_evidence_locked": True,
    "planned_differentiation_passed": True,
    "per_variant_three_view_qc_required": True,
    "per_variant_human_identity_pixel_qc_required": True,
    "per_variant_director_validation_required": True,
    "identity_first_reference_order_required": True,
    "schema_version_required": "1.4",
    "quality_contract_id_required": QUALITY_CONTRACT_ID,
    "market_prompt_contract_id_required": MARKET_PROMPT_CONTRACT_ID,
    "serializer_id_required": PROMPT_SERIALIZER_ID,
    "three_layer_deadlines_required": True,
    "rendered_quality_review_required": True,
}
LEGACY_V5_QUALITY_GATES = {
    "all_source_colors_grouped": True,
    "shared_evidence_locked": True,
    "planned_differentiation_passed": True,
    "per_variant_three_view_qc_required": True,
    "per_variant_human_identity_pixel_qc_required": True,
    "per_variant_director_validation_required": True,
    "identity_first_reference_order_required": True,
    "schema_version_required": "1.3",
    "quality_contract_id_required": LEGACY_QUALITY_CONTRACT_ID,
    "serializer_id_required": LEGACY_V5_PROMPT_SERIALIZER_ID,
    "rendered_quality_review_required": True,
}
LEGACY_QUALITY_GATES = {
    "all_source_colors_grouped": True,
    "shared_evidence_locked": True,
    "planned_differentiation_passed": True,
    "per_variant_three_view_qc_required": True,
    "per_variant_director_validation_required": True,
    "schema_version_required": "1.3",
    "quality_contract_id_required": LEGACY_QUALITY_CONTRACT_ID,
    "serializer_id_required": LEGACY_PROMPT_SERIALIZER_ID,
    "rendered_quality_review_required": True,
}
MAX_STREAMING_VARIANTS = 12
REQUIRED_SHARED_CORE_FIELDS = (
    "research_bundle", "pain_solution_map", "claims_allowlist", "claims_registry",
)
class DuplicateKeyError(ValueError):
    pass


def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _error(code: str, path: str, message: str, **details: Any) -> dict[str, Any]:
    item = {"code": code, "path": path, "message": message}
    if details:
        item["details"] = details
    return item


def _result(errors: list[dict[str, Any]], **summary: Any) -> dict[str, Any]:
    errors.sort(key=lambda item: (item["code"], item["path"]))
    if errors:
        return {"valid": False, "primary_error": errors[0], "errors": errors, **summary}
    return {"valid": True, "primary_error": None, "errors": [], **summary}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(" " if unicodedata.category(char)[0] in {"P", "S"} else char for char in text)
    return " ".join(text.split())


def _without_colors(value: Any, color_terms: list[Any]) -> str:
    text = _norm(value)
    terms = sorted({_norm(term) for term in color_terms if _norm(term)}, key=len, reverse=True)
    for term in terms:
        text = text.replace(term, " ")
    return " ".join(text.split())


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_hashtag(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    tag = value.strip()
    return tag == LEGACY_BRAND_HASHTAG or bool(HASHTAG_RE.fullmatch(tag))


def _required_string(obj: Any, field: str, path: str, errors: list[dict[str, Any]]) -> Any:
    value = obj.get(field) if isinstance(obj, dict) else None
    if not _nonempty(value):
        errors.append(_error("STREAM_REQUIRED_FIELD", f"{path}.{field}", "non-empty string is required"))
    return value


def _planned_payload(document: dict[str, Any]) -> list[Any]:
    fields = [
        "variant_id", "color_name", "source_images", "garment_signature",
        "creative_delta", "planned_non_cta_spoken_intent_ids",
        "planned_non_cta_visual_signatures", "caption", "hashtags",
    ]
    if document.get("contract_id") == CONTRACT_ID and document.get("schema_version") == SCHEMA_VERSION:
        fields.insert(4, "generation_controls")
        fields.insert(5, "deadline_blueprint")
    return [
        {field: variant.get(field) for field in fields}
        for variant in document.get("planned_variants", [])
        if isinstance(variant, dict)
    ]


def deadline_blueprint_from_deadlines(deadlines: Any) -> dict[str, Any]:
    """Lock the global and asset layers before a color is released."""
    source = deadlines if isinstance(deadlines, dict) else {}
    return {
        "contract_id": source.get("contract_id"),
        "global_deadlines": json.loads(json.dumps(source.get("global_deadlines"), ensure_ascii=False)),
        "asset_deadlines": json.loads(json.dumps(source.get("asset_deadlines"), ensure_ascii=False)),
        "shot_deadline_policy": {
            "required_fields": list(_market_contract.SHOT_DEADLINE_FIELDS),
            "required_forbidden_outcomes": sorted(_market_contract.REQUIRED_FORBIDDEN_SHOT_OUTCOMES),
        },
    }


def _validate_deadline_blueprint(
    blueprint: Any,
    batch_key: dict[str, Any],
    controls: Any,
    variant: dict[str, Any],
    path: str,
    errors: list[dict[str, Any]],
) -> None:
    if not isinstance(blueprint, dict):
        errors.append(_error(
            "STREAM_DEADLINE_BLUEPRINT_INVALID", path,
            "new plans require an immutable global/asset deadline blueprint",
        ))
        return
    expected_top = {"contract_id", "global_deadlines", "asset_deadlines", "shot_deadline_policy"}
    if set(blueprint) != expected_top or blueprint.get("contract_id") != _market_contract.DEADLINE_CONTRACT_ID:
        errors.append(_error(
            "STREAM_DEADLINE_BLUEPRINT_INVALID", path,
            "deadline blueprint fields and contract_id must exactly match the current contract",
        ))
    global_deadlines = blueprint.get("global_deadlines")
    asset_deadlines = blueprint.get("asset_deadlines")
    if not isinstance(global_deadlines, dict) or set(global_deadlines) != set(_market_contract.GLOBAL_DEADLINE_FIELDS):
        errors.append(_error(
            "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".global_deadlines",
            "global deadline fields are incomplete or unsupported",
        ))
    else:
        profile = _market_contract.MARKET_PROFILES.get(str(batch_key.get("market_prompt_profile_id")), {})
        policy = controls.get("screen_text_policy") if isinstance(controls, dict) else None
        expected_allowed = [profile.get("fit_stats_text")] if policy == "fixed_model_stats_only" else []
        expected_global = {
            "platform": batch_key.get("platform"),
            "aspect_ratio": batch_key.get("aspect_ratio"),
            "duration_seconds": batch_key.get("duration_seconds"),
            "resolution": batch_key.get("resolution"),
            "frame_rate_fps": batch_key.get("frame_rate_fps"),
            "camera_mode": controls.get("camera_mode") if isinstance(controls, dict) else None,
            "screen_text_policy": policy,
            "allowed_screen_text": expected_allowed,
            "music_mode": controls.get("music_mode") if isinstance(controls, dict) else None,
            "spoken_language": batch_key.get("voiceover_language"),
        }
        for field, expected in expected_global.items():
            if global_deadlines.get(field) != expected:
                errors.append(_error(
                    "STREAM_DEADLINE_BLUEPRINT_DRIFT", path + ".global_deadlines." + field,
                    "global deadline must exactly project the immutable batch and generation controls",
                ))
        for field in (
            "capture_quality_lock", "camera_behavior_lock", "lens_depth_lock",
            "postprocessing_lock", "audio_capture_lock",
        ):
            if not _nonempty(global_deadlines.get(field)):
                errors.append(_error(
                    "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".global_deadlines." + field,
                    "concrete global deadline prose is required",
                ))
        overlays = global_deadlines.get("forbidden_overlays")
        if not isinstance(overlays, list) or not _market_contract.REQUIRED_FORBIDDEN_OVERLAYS.issubset(set(overlays)):
            errors.append(_error(
                "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".global_deadlines.forbidden_overlays",
                "mandatory overlay exclusions are missing",
            ))
    if not isinstance(asset_deadlines, dict) or set(asset_deadlines) != set(_market_contract.ASSET_DEADLINE_FIELDS):
        errors.append(_error(
            "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".asset_deadlines",
            "asset deadline fields are incomplete or unsupported",
        ))
    else:
        expected_asset = {
            "color_name": variant.get("color_name"),
            "garment_signature": variant.get("garment_signature"),
            "scene_strategy": controls.get("scene_strategy") if isinstance(controls, dict) else None,
        }
        for field, expected in expected_asset.items():
            if asset_deadlines.get(field) != expected:
                errors.append(_error(
                    "STREAM_DEADLINE_BLUEPRINT_DRIFT", path + ".asset_deadlines." + field,
                    "asset deadline must exactly project the planned color, garment, and scene strategy",
                ))
        for field in (
            "creator_identity", "subject_appearance_lock", "garment_identity",
            "garment_structure_lock", "fabric_physics_lock", "outfit", "outfit_prop_lock",
            "scene_environment_lock", "lighting", "lighting_lock", "soundscape_lock",
        ):
            if not _nonempty(asset_deadlines.get(field)):
                errors.append(_error(
                    "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".asset_deadlines." + field,
                    "concrete asset deadline prose is required",
                ))
        drift = asset_deadlines.get("forbidden_asset_drift")
        if not isinstance(drift, list) or not _market_contract.REQUIRED_FORBIDDEN_ASSET_DRIFT.issubset(set(drift)):
            errors.append(_error(
                "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".asset_deadlines.forbidden_asset_drift",
                "mandatory asset-drift exclusions are missing",
            ))
    expected_policy = {
        "required_fields": list(_market_contract.SHOT_DEADLINE_FIELDS),
        "required_forbidden_outcomes": sorted(_market_contract.REQUIRED_FORBIDDEN_SHOT_OUTCOMES),
    }
    if blueprint.get("shot_deadline_policy") != expected_policy:
        errors.append(_error(
            "STREAM_DEADLINE_BLUEPRINT_INVALID", path + ".shot_deadline_policy",
            "shot deadline policy must exactly lock the current per-beat contract",
        ))


def validate_plan(document: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    legacy_v4_plan = False
    legacy_v5_plan = False
    if not isinstance(document, dict):
        return _result([_error("STREAM_ROOT_INVALID", "$", "top-level JSON must be an object")])
    historical_plan = (
        document.get("schema_version") == LEGACY_SCHEMA_VERSION
        and document.get("contract_id") == LEGACY_CONTRACT_ID
    )
    if not historical_plan:
        if document.get("schema_version") != SCHEMA_VERSION:
            errors.append(_error("STREAM_SCHEMA_INVALID", "$.schema_version", f"new plans must equal {SCHEMA_VERSION}"))
        if document.get("contract_id") != CONTRACT_ID:
            errors.append(_error("STREAM_CONTRACT_INVALID", "$.contract_id", f"new plans must equal {CONTRACT_ID}"))
        if document.get("market_prompt_contract_id") != MARKET_PROMPT_CONTRACT_ID:
            errors.append(_error(
                "STREAM_MARKET_CONTRACT_INVALID", "$.market_prompt_contract_id",
                f"new plans must equal {MARKET_PROMPT_CONTRACT_ID}",
            ))
    for field in ("batch_run_id", "batch_compile_id", "sku_family_id"):
        _required_string(document, field, "$", errors)
    if document.get("plan_revision") != 0 or document.get("plan_locked") is not True:
        errors.append(_error(
            "STREAM_PLAN_NOT_IMMUTABLE", "$",
            "quality-gated streaming requires immutable plan_revision 0 with plan_locked true",
        ))

    batch_key = document.get("batch_key")
    if not isinstance(batch_key, dict):
        errors.append(_error("STREAM_BATCH_KEY_INVALID", "$.batch_key", "batch_key must be an object"))
        batch_key = {}
    required_batch_fields = BATCH_KEY_FIELDS if not historical_plan else tuple(
        field for field in BATCH_KEY_FIELDS if field != "market_prompt_profile_id"
    )
    for field in required_batch_fields:
        value = batch_key.get(field)
        if field == "duration_seconds":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                errors.append(_error("STREAM_BATCH_KEY_INVALID", f"$.batch_key.{field}", "positive duration is required"))
        elif field == "frame_rate_fps":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(_error("STREAM_BATCH_KEY_INVALID", f"$.batch_key.{field}", "positive integer frame rate is required"))
        elif not _nonempty(value):
            errors.append(_error("STREAM_BATCH_KEY_INVALID", f"$.batch_key.{field}", "non-empty value is required"))
    if not historical_plan:
        for issue in _market_contract.validate_market_tuple(batch_key):
            errors.append(_error(
                "STREAM_MARKET_PROFILE_INVALID", "$.batch_key",
                str(issue.get("message") or "market tuple violates the market prompt contract"),
                market_error_code=issue.get("code"), market_error_path=issue.get("path"),
            ))

    shared_core = document.get("shared_core")
    if not isinstance(shared_core, dict) or not shared_core:
        errors.append(_error("STREAM_SHARED_CORE_INVALID", "$.shared_core", "locked shared_core is required"))
        shared_core = {}
    expected_shared_hash = canonical_sha256(shared_core)
    if document.get("shared_core_sha256") != expected_shared_hash:
        errors.append(_error("STREAM_SHARED_CORE_HASH_MISMATCH", "$.shared_core_sha256", "must hash shared_core canonically"))
    for field in REQUIRED_SHARED_CORE_FIELDS:
        value = shared_core.get(field)
        if field == "research_bundle":
            if not isinstance(value, dict) or value.get("contract_id") != "amazon_product_review_evidence_v1":
                errors.append(_error(
                    "STREAM_SHARED_EVIDENCE_INCOMPLETE", f"$.shared_core.{field}",
                    "locked research_bundle with amazon_product_review_evidence_v1 is required",
                ))
        elif not isinstance(value, list) or not value:
            errors.append(_error(
                "STREAM_SHARED_EVIDENCE_INCOMPLETE", f"$.shared_core.{field}",
                "locked non-empty shared evidence list is required",
            ))

    gates = document.get("quality_gates")
    if not isinstance(gates, dict):
        errors.append(_error("STREAM_QUALITY_GATES_INVALID", "$.quality_gates", "quality_gates must be an object"))
        gates = {}
    serializer_required = gates.get("serializer_id_required")
    if historical_plan and serializer_required == LEGACY_PROMPT_SERIALIZER_ID:
        legacy_v4_plan = True
        expected_gates = LEGACY_QUALITY_GATES
    elif historical_plan and serializer_required == LEGACY_V5_PROMPT_SERIALIZER_ID:
        legacy_v5_plan = True
        expected_gates = LEGACY_V5_QUALITY_GATES
    else:
        expected_gates = QUALITY_GATES
    for field, expected in expected_gates.items():
        if gates.get(field) != expected:
            errors.append(_error("STREAM_QUALITY_GATE_MISSING", f"$.quality_gates.{field}", f"must equal {expected!r}"))
    allowed_serializers = (
        {LEGACY_PROMPT_SERIALIZER_ID, LEGACY_V5_PROMPT_SERIALIZER_ID}
        if historical_plan else {PROMPT_SERIALIZER_ID}
    )
    if serializer_required not in allowed_serializers:
        errors.append(_error(
            "STREAM_QUALITY_GATE_MISSING", "$.quality_gates.serializer_id_required",
            f"must equal {PROMPT_SERIALIZER_ID} for new plans; historical v1 serializers are read-only",
        ))

    variants = document.get("planned_variants")
    if not isinstance(variants, list) or len(variants) < 2:
        errors.append(_error("STREAM_VARIANTS_INVALID", "$.planned_variants", "at least two planned color variants are required"))
        variants = []
    elif len(variants) > MAX_STREAMING_VARIANTS:
        errors.append(_error(
            "STREAM_VARIANT_LIMIT_EXCEEDED", "$.planned_variants",
            f"quality-gated streaming supports at most {MAX_STREAMING_VARIANTS} colors; use barrier waves above this limit",
        ))
    if document.get("planned_variant_count") != len(variants):
        errors.append(_error("STREAM_VARIANT_COUNT_MISMATCH", "$.planned_variant_count", "must equal planned_variants length"))

    contexts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_colors: set[str] = set()
    garment_baseline: str | None = None
    for index, variant in enumerate(variants):
        path = f"$.planned_variants[{index}]"
        if not isinstance(variant, dict):
            errors.append(_error("STREAM_VARIANT_INVALID", path, "variant must be an object"))
            continue
        variant_id = _required_string(variant, "variant_id", path, errors)
        color_name = _required_string(variant, "color_name", path, errors)
        if isinstance(variant_id, str):
            if VARIANT_ID_RE.fullmatch(variant_id) is None:
                errors.append(_error(
                    "STREAM_VARIANT_ID_INVALID", path + ".variant_id",
                    "variant_id must be a filesystem-safe 1-64 character token",
                ))
            if variant_id in seen_ids:
                errors.append(_error("STREAM_VARIANT_DUPLICATE", path + ".variant_id", "variant_id must be unique"))
            seen_ids.add(variant_id)
        normalized_color = _norm(color_name)
        if normalized_color:
            if normalized_color in seen_colors:
                errors.append(_error("STREAM_COLOR_DUPLICATE", path + ".color_name", "color names must be unique"))
            seen_colors.add(normalized_color)

        source_images = variant.get("source_images")
        if not isinstance(source_images, list) or not source_images or any(not _nonempty(item) for item in source_images):
            errors.append(_error("STREAM_SOURCE_IMAGES_INVALID", path + ".source_images", "grouped source image paths are required"))

        signature = variant.get("garment_signature")
        if not isinstance(signature, dict):
            errors.append(_error("STREAM_GARMENT_SIGNATURE_INVALID", path + ".garment_signature", "garment_signature must be an object"))
            signature = {}
        for field in GARMENT_FIELDS:
            if not _nonempty(signature.get(field)):
                errors.append(_error("STREAM_GARMENT_SIGNATURE_INVALID", f"{path}.garment_signature.{field}", "non-empty field is required"))
        signature_hash = canonical_sha256({field: signature.get(field) for field in GARMENT_FIELDS})
        if garment_baseline is None:
            garment_baseline = signature_hash
        elif signature_hash != garment_baseline:
            errors.append(_error("STREAM_NOT_SAME_SKU", path + ".garment_signature", "all planned variants must have one color-only garment signature"))

        controls = variant.get("generation_controls")
        if not historical_plan:
            profile_id = batch_key.get("market_prompt_profile_id")
            control_issues = _market_contract.validate_generation_controls(
                profile_id, controls, path + ".generation_controls",
            )
            for issue in control_issues:
                errors.append(_error(
                    "STREAM_GENERATION_CONTROLS_INVALID",
                    str(issue.get("path") or path + ".generation_controls"),
                    str(issue.get("message") or "generation controls violate the market contract"),
                    market_error_code=issue.get("code"),
                ))
            _validate_deadline_blueprint(
                variant.get("deadline_blueprint"), batch_key, controls, variant,
                path + ".deadline_blueprint", errors,
            )

        delta = variant.get("creative_delta")
        if not isinstance(delta, dict):
            errors.append(_error("STREAM_CREATIVE_DELTA_INVALID", path + ".creative_delta", "creative_delta must be an object"))
            delta = {}
        for field in CREATIVE_DELTA_FIELDS:
            value = delta.get(field)
            if field in {"color_terms", "scene_palette", "spoken_intent_ids"}:
                if not isinstance(value, list) or not value or any(not _nonempty(item) for item in value):
                    errors.append(_error("STREAM_CREATIVE_DELTA_INVALID", f"{path}.creative_delta.{field}", "non-empty string array is required"))
            elif not _nonempty(value):
                errors.append(_error("STREAM_CREATIVE_DELTA_INVALID", f"{path}.creative_delta.{field}", "non-empty value is required"))
        if not historical_plan:
            for issue in _market_contract.validate_scene_projection(
                controls.get("scene_strategy") if isinstance(controls, dict) else None,
                creative_delta=delta,
                path=path,
            ):
                errors.append(_error(
                    "STREAM_SCENE_STRATEGY_DRIFT",
                    str(issue.get("path") or path + ".creative_delta"),
                    str(issue.get("message") or "creative scene/outfit projection violates the market contract"),
                    market_error_code=issue.get("code"),
                ))

        intents = variant.get("planned_non_cta_spoken_intent_ids")
        signatures = variant.get("planned_non_cta_visual_signatures")
        for field, value in (("planned_non_cta_spoken_intent_ids", intents), ("planned_non_cta_visual_signatures", signatures)):
            if not isinstance(value, list) or len(value) < 4 or any(not _nonempty(item) for item in value):
                errors.append(_error("STREAM_TIMELINE_PLAN_INVALID", f"{path}.{field}", "at least four non-CTA string entries are required"))
        if isinstance(signatures, list):
            for signature_index, visual_signature in enumerate(signatures):
                parts = str(visual_signature).split("|")
                canonical = "|".join(_norm(part) for part in parts)
                if len(parts) != 3 or not all(_nonempty(part) for part in parts) or visual_signature != canonical:
                    errors.append(_error(
                        "STREAM_VISUAL_SIGNATURE_INVALID",
                        f"{path}.planned_non_cta_visual_signatures[{signature_index}]",
                        "must be canonical normalized framing|core_action|product_point",
                    ))
        if isinstance(intents, list) and delta.get("spoken_intent_ids") != intents:
            errors.append(_error(
                "STREAM_INTENT_PLAN_DRIFT", f"{path}.creative_delta.spoken_intent_ids",
                "must exactly equal planned_non_cta_spoken_intent_ids",
            ))
        caption = variant.get("caption")
        if not _nonempty(caption) or "#" in str(caption):
            errors.append(_error("STREAM_CAPTION_INVALID", path + ".caption", "non-empty caption without hashtags is required"))
        hashtags = variant.get("hashtags")
        if not isinstance(hashtags, list) or len(hashtags) != 5 or any(not _valid_hashtag(tag) for tag in hashtags):
            errors.append(_error("STREAM_HASHTAGS_INVALID", path + ".hashtags", "exactly five valid #tags relevant to the product, market, occasion, or buyer search intent are required"))
            hashtags = [] if not isinstance(hashtags, list) else hashtags
        if len({_norm(tag) for tag in hashtags}) != len(hashtags):
            errors.append(_error("STREAM_HASHTAGS_DUPLICATE", path + ".hashtags", "hashtags must be unique"))
        contexts.append({
            "variant": variant,
            "generation_controls": controls if isinstance(controls, dict) else {},
            "delta": delta,
            "intents": intents if isinstance(intents, list) else [],
            "visual_signatures": signatures if isinstance(signatures, list) else [],
            "caption": caption,
            "hashtags": hashtags,
        })

    for left, right in itertools.combinations(contexts, 2):
        lv, rv = left["variant"], right["variant"]
        ld, rd = left["delta"], right["delta"]
        pair = f"$.planned_variants[{lv.get('variant_id')}~{rv.get('variant_id')}]"
        dimensions = {
            "hook_angle_id": ld.get("hook_angle_id") != rd.get("hook_angle_id"),
            "opening_visual_id": ld.get("opening_visual_id") != rd.get("opening_visual_id"),
            "scene": (ld.get("scene_id"), ld.get("scene_palette")) != (rd.get("scene_id"), rd.get("scene_palette")),
            "styling_signature": ld.get("styling_signature") != rd.get("styling_signature"),
            "proof_focus_id": ld.get("proof_focus_id") != rd.get("proof_focus_id"),
            "signature_action_id": ld.get("signature_action_id") != rd.get("signature_action_id"),
        }
        different = [name for name, changed in dimensions.items() if changed]
        attention = dimensions["hook_angle_id"] or dimensions["opening_visual_id"]
        visual_world = dimensions["scene"] or dimensions["styling_signature"]
        proof_action = dimensions["proof_focus_id"] or dimensions["signature_action_id"]
        intent_diffs = sum(a != b for a, b in itertools.zip_longest(left["intents"], right["intents"], fillvalue=object()))
        visual_diffs = sum(a != b for a, b in itertools.zip_longest(left["visual_signatures"], right["visual_signatures"], fillvalue=object()))
        if len(different) < 3 or not (attention and visual_world and proof_action) or intent_diffs < 2 or visual_diffs < 2:
            errors.append(_error(
                "STREAM_DIFFERENTIATION_FAILED", pair,
                "each color pair must differ across attention, visual world, proof/action, and at least two non-CTA intents/shots",
                dimensions=different, intent_differences=intent_diffs, visual_signature_differences=visual_diffs,
            ))
        color_terms = list(ld.get("color_terms") or []) + list(rd.get("color_terms") or [])
        if _without_colors(ld.get("hook_text_original"), color_terms) == _without_colors(rd.get("hook_text_original"), color_terms):
            errors.append(_error("STREAM_HOOK_COLOR_ONLY", pair, "planned hooks collide after color-term normalization"))
        if _without_colors(left["caption"], color_terms) == _without_colors(right["caption"], color_terms):
            errors.append(_error("STREAM_CAPTION_COLOR_ONLY", pair, "planned captions collide after color-term normalization"))
        left_tags = {_norm(tag) for tag in left["hashtags"]}
        right_tags = {_norm(tag) for tag in right["hashtags"]}
        if min(len(left_tags), len(right_tags)) - len(left_tags & right_tags) < 2:
            errors.append(_error("STREAM_HASHTAG_COLLISION", pair, "each color must contribute at least two pairwise-unique hashtags"))

    expected_diff_hash = canonical_sha256(_planned_payload(document))
    if document.get("differentiation_plan_sha256") != expected_diff_hash:
        errors.append(_error("STREAM_DIFFERENTIATION_HASH_MISMATCH", "$.differentiation_plan_sha256", "must hash the immutable planned variant payload"))
    return _result(
        errors,
        contract_id=CONTRACT_ID,
        planned_variant_count=len(variants),
        pair_count=len(contexts) * (len(contexts) - 1) // 2,
        shared_core_sha256=expected_shared_hash,
        differentiation_plan_sha256=expected_diff_hash,
        planned_variant_ids=[item.get("variant_id") for item in variants if isinstance(item, dict)],
        eligible_for_new_release=not errors and not historical_plan,
        historical_prompt_v4_read_only=legacy_v4_plan,
        historical_prompt_v5_read_only=legacy_v5_plan,
    )


def validate_release_binding(plan: Any, release: Any, plan_sha256: str) -> list[dict[str, Any]]:
    """Return errors that bind one complete release to its immutable streaming plan."""
    errors: list[dict[str, Any]] = []
    plan_result = validate_plan(plan)
    if plan_result.get("valid") is not True:
        errors.append(_error("STREAM_PLAN_VALIDATION_FAILED", "streaming_release.plan_path", "the immutable streaming plan is invalid"))
        return errors
    if not isinstance(release, dict):
        return [_error("STREAM_RELEASE_INVALID", "streaming_release", "streaming_release must be an object")]
    binding = release.get("streaming_release")
    if not isinstance(binding, dict):
        return [_error("STREAM_RELEASE_INVALID", "streaming_release", "streaming_release binding is required")]
    plan_contract = plan.get("contract_id")
    if binding.get("contract_id") != plan_contract:
        errors.append(_error(
            "STREAM_CONTRACT_INVALID", "streaming_release.contract_id",
            "must equal the immutable plan contract_id",
        ))
    if binding.get("plan_sha256") != plan_sha256 or not HASH_RE.fullmatch(str(binding.get("plan_sha256", ""))):
        errors.append(_error("STREAM_PLAN_HASH_MISMATCH", "streaming_release.plan_sha256", "must hash the exact immutable plan file bytes"))
    variant_id = binding.get("variant_id")
    planned = [item for item in plan.get("planned_variants", []) if isinstance(item, dict) and item.get("variant_id") == variant_id]
    if len(planned) != 1:
        errors.append(_error("STREAM_RELEASE_OWNER_MISMATCH", "streaming_release.variant_id", "variant must resolve exactly once in the immutable plan"))
        return errors
    planned_variant = planned[0]
    expected_child_run_id = f"{plan.get('batch_run_id')}:{variant_id}"
    if binding.get("child_run_id") != expected_child_run_id:
        errors.append(_error(
            "STREAM_CHILD_RUN_ID_MISMATCH", "streaming_release.child_run_id",
            f"must equal {expected_child_run_id}",
        ))
    gates = plan.get("quality_gates", {}) if isinstance(plan.get("quality_gates"), dict) else {}
    expected_schema = gates.get("schema_version_required")
    expected_quality = gates.get("quality_contract_id_required")
    if release.get("schema_version") != expected_schema or release.get("quality_contract_id") != expected_quality:
        errors.append(_error(
            "STREAM_RELEASE_QUALITY_DOWNGRADE", "$",
            "release schema and quality contract must exactly match the immutable plan",
        ))
    if plan_contract == CONTRACT_ID:
        if release.get("market_prompt_contract_id") != MARKET_PROMPT_CONTRACT_ID:
            errors.append(_error(
                "STREAM_MARKET_CONTRACT_INVALID", "market_prompt_contract_id",
                f"new release must equal {MARKET_PROMPT_CONTRACT_ID}",
            ))
    if release.get("compile_mode") != "single_compile":
        errors.append(_error("STREAM_RELEASE_MODE_INVALID", "compile_mode", "each streaming release must be one immutable single_compile"))
    for field in ("batch_compile_id", "sku_family_id", "batch_key", "shared_core"):
        if release.get(field) != plan.get(field):
            errors.append(_error("STREAM_RELEASE_PLAN_DRIFT", field, "release must exactly reuse the immutable batch plan field"))
    variants = release.get("variants")
    if not isinstance(variants, list) or len(variants) != 1 or not isinstance(variants[0], dict):
        errors.append(_error("STREAM_RELEASE_VARIANT_INVALID", "variants", "release must contain exactly one full variant"))
        return errors
    actual = variants[0]
    if actual.get("variant_id") != variant_id:
        errors.append(_error("STREAM_RELEASE_OWNER_MISMATCH", "variants[0].variant_id", "release variant must match streaming_release.variant_id"))
    for field in ("color_name", "source_images", "garment_signature", "creative_delta", "caption", "hashtags"):
        if actual.get(field) != planned_variant.get(field):
            errors.append(_error("STREAM_RELEASE_PLAN_DRIFT", f"variants[0].{field}", "released variant must equal the immutable planned value"))
    if plan_contract == CONTRACT_ID and actual.get("generation_controls") != planned_variant.get("generation_controls"):
        errors.append(_error(
            "STREAM_RELEASE_PLAN_DRIFT", "variants[0].generation_controls",
            "released generation controls must equal the immutable market plan",
        ))
    if plan_contract == CONTRACT_ID:
        blueprint = planned_variant.get("deadline_blueprint")
        deadlines = actual.get("three_layer_deadlines")
        if not isinstance(blueprint, dict) or not isinstance(deadlines, dict):
            errors.append(_error(
                "STREAM_RELEASE_DEADLINE_DRIFT", "variants[0].three_layer_deadlines",
                "current releases require the complete three-layer deadline contract",
            ))
        else:
            if deadlines.get("contract_id") != blueprint.get("contract_id"):
                errors.append(_error(
                    "STREAM_RELEASE_DEADLINE_DRIFT", "variants[0].three_layer_deadlines.contract_id",
                    "release deadline contract must equal the immutable plan",
                ))
            for field in ("global_deadlines", "asset_deadlines"):
                if deadlines.get(field) != blueprint.get(field):
                    errors.append(_error(
                        "STREAM_RELEASE_DEADLINE_DRIFT", f"variants[0].three_layer_deadlines.{field}",
                        "release deadline layer must exactly equal the immutable plan",
                    ))
            shot_deadlines = deadlines.get("shot_deadlines")
            policy = blueprint.get("shot_deadline_policy")
            expected_fields = set(policy.get("required_fields", [])) if isinstance(policy, dict) else set()
            expected_outcomes = set(policy.get("required_forbidden_outcomes", [])) if isinstance(policy, dict) else set()
            if not isinstance(shot_deadlines, list) or not shot_deadlines:
                errors.append(_error(
                    "STREAM_RELEASE_DEADLINE_DRIFT", "variants[0].three_layer_deadlines.shot_deadlines",
                    "release requires one complete shot deadline for every canonical beat",
                ))
            else:
                for shot_index, shot in enumerate(shot_deadlines):
                    shot_path = f"variants[0].three_layer_deadlines.shot_deadlines[{shot_index}]"
                    if not isinstance(shot, dict) or set(shot) != expected_fields:
                        errors.append(_error(
                            "STREAM_RELEASE_DEADLINE_DRIFT", shot_path,
                            "shot deadline fields must exactly match the immutable policy",
                        ))
                        continue
                    outcomes = shot.get("forbidden_outcomes")
                    if not isinstance(outcomes, list) or not expected_outcomes.issubset(set(outcomes)):
                        errors.append(_error(
                            "STREAM_RELEASE_DEADLINE_DRIFT", shot_path + ".forbidden_outcomes",
                            "shot deadline is missing mandatory failure exclusions",
                        ))
    if actual.get("three_view_qc") != "passed":
        errors.append(_error("STREAM_RELEASE_REFERENCE_NOT_PASSED", "variants[0].three_view_qc", "current color three-view must pass QC before release"))
    plan_serializer = plan.get("quality_gates", {}).get("serializer_id_required")
    prompt = actual.get("renderings", {}).get("prompt", {}) if isinstance(actual.get("renderings"), dict) else {}
    if not isinstance(prompt, dict) or prompt.get("serializer_id") != plan_serializer:
        errors.append(_error(
            "STREAM_RELEASE_QUALITY_DOWNGRADE", "variants[0].renderings.prompt.serializer_id",
            "release prompt serializer must exactly match the immutable streaming plan",
        ))
    references = actual.get("references")
    if plan_serializer in {PROMPT_SERIALIZER_ID, LEGACY_V5_PROMPT_SERIALIZER_ID}:
        if not isinstance(references, list) or not references:
            errors.append(_error(
                "STREAM_RELEASE_REFERENCE_NOT_PASSED", "variants[0].references",
                "identity-safe release requires apparel references after fixed-model @Image1",
            ))
            references = []
        tag_indices: list[int] = []
        for reference_index, reference in enumerate(references):
            reference_path = f"variants[0].references[{reference_index}]"
            if not isinstance(reference, dict):
                errors.append(_error("STREAM_RELEASE_REFERENCE_NOT_PASSED", reference_path, "reference must be an object"))
                continue
            match = re.fullmatch(r"@Image([2-9]|[1-9][0-9]+)", str(reference.get("interface_tag", "")))
            if reference.get("role") != "apparel_three_view" or match is None:
                errors.append(_error(
                    "STREAM_RELEASE_REFERENCE_NOT_PASSED", reference_path,
                    "garment references must be role apparel_three_view and ordered at @Image2 or higher; @Image1 is fixed-model identity",
                ))
            else:
                tag_indices.append(int(match.group(1)))
            if reference.get("human_identity_pixels_absent") is not True:
                errors.append(_error(
                    "STREAM_RELEASE_REFERENCE_NOT_PASSED", reference_path + ".human_identity_pixels_absent",
                    "zero-human-identity-pixel audit must pass before streaming release",
                ))
        if tag_indices != list(range(2, 2 + len(references))):
            errors.append(_error(
                "STREAM_RELEASE_REFERENCE_NOT_PASSED", "variants[0].references",
                "garment references must be contiguous in request order as @Image2, @Image3, and so on",
            ))
    three_view_path = actual.get("three_view_path")
    three_view_sha256 = actual.get("three_view_sha256")
    if not isinstance(three_view_path, str) or not Path(three_view_path).is_absolute():
        errors.append(_error(
            "STREAM_THREE_VIEW_FILE_INVALID", "variants[0].three_view_path",
            "streaming release requires an absolute local three-view path",
        ))
    else:
        try:
            actual_three_view_sha256 = hashlib.sha256(Path(three_view_path).read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(_error("STREAM_THREE_VIEW_FILE_INVALID", "variants[0].three_view_path", str(exc)))
        else:
            if three_view_sha256 != actual_three_view_sha256:
                errors.append(_error(
                    "STREAM_THREE_VIEW_HASH_MISMATCH", "variants[0].three_view_sha256",
                    "must equal the SHA-256 of the exact readable local three-view file",
                ))
    timeline = actual.get("canonical_timeline")
    beats = timeline.get("beats") if isinstance(timeline, dict) else None
    if not isinstance(beats, list):
        errors.append(_error("STREAM_RELEASE_TIMELINE_INVALID", "variants[0].canonical_timeline.beats", "full canonical timeline is required"))
    else:
        non_cta = [beat for beat in beats if isinstance(beat, dict) and beat.get("purpose") != "cta"]
        intents = [beat.get("spoken_intent_id") for beat in non_cta]
        signatures = [
            "|".join(_norm(beat.get(field)) for field in ("framing", "core_action", "product_point"))
            for beat in non_cta
        ]
        if intents != planned_variant.get("planned_non_cta_spoken_intent_ids"):
            errors.append(_error("STREAM_RELEASE_TIMELINE_DRIFT", "variants[0].canonical_timeline", "non-CTA spoken intents differ from the immutable plan"))
        if signatures != planned_variant.get("planned_non_cta_visual_signatures"):
            errors.append(_error("STREAM_RELEASE_TIMELINE_DRIFT", "variants[0].canonical_timeline", "non-CTA framing/action/product signatures differ from the immutable plan"))
    return errors


def _fixture() -> dict[str, Any]:
    shared = {
        "research_digest_id": "research-1",
        "template_id": "template-1",
        "research_bundle": {
            "contract_id": "amazon_product_review_evidence_v1",
            "status": "not_provided",
        },
        "pain_solution_map": [{"pain_point_id": "pain-1"}],
        "claims_allowlist": ["claim-visible-1"],
        "claims_registry": [{"claim_id": "claim-visible-1"}],
    }
    garment = {field: f"same-{field}" for field in GARMENT_FIELDS}
    variants = []
    for index, (variant_id, color) in enumerate((("black", "Black"), ("blue", "Blue"))):
        intents = [f"{variant_id}-intent-{item}" for item in range(4)]
        signatures = [
            "|".join(_norm(value) for value in (
                f"{variant_id}-frame-{item}", f"{variant_id}-action-{item}", f"{variant_id}-proof-{item}",
            ))
            for item in range(4)
        ]
        scene_id = "cafe_social" if index == 0 else "commercial_street"
        scene_description = (
            "a calm neighborhood cafe terrace beside a pedestrian street"
            if index == 0 else "a quiet pedestrian shopping street with clean neutral storefronts"
        )
        variants.append({
            "variant_id": variant_id,
            "color_name": color,
            "source_images": [f"C:/source/{variant_id}.png"],
            "garment_signature": garment,
            "generation_controls": {
                "prompt_shell_mode": "de_performance_script",
                "delivery_mode": "de_hybrid_proof",
                "camera_mode": "fixed_phone",
                "music_mode": "none",
                "verdict_mode": "ownership_verdict",
                "commerce_cta_mode": "light_link",
                "screen_text_policy": "fixed_model_stats_only",
                "scene_strategy": {
                    "wear_context": "outward_wear",
                    "scene_role": "occasion_outfit_solution",
                    "primary_scene_id": scene_id,
                    "primary_scene_description": scene_description,
                    "occasion": "an everyday cafe stop" if index == 0 else "a relaxed city shopping errand",
                    "buyer_styling_question": (
                        "What can I wear for a cafe stop that looks put together without feeling formal?"
                        if index == 0 else
                        "What can I wear for city shopping that feels polished but remains practical?"
                    ),
                    "outfit_answer": (
                        "straight jeans, clean low-profile sneakers, a small crossbody bag, and simple earrings"
                        if index == 0 else
                        "tailored trousers, clean loafers, a structured crossbody bag, and a simple watch"
                    ),
                    "video_form": "destination_outfit_share",
                    "location_plan": "single_primary_scene",
                    "bedroom_policy": "excluded",
                },
            },
            "creative_delta": {
                "hook_angle_id": f"hook-{index}",
                "hook_text_original": f"Why does this {color} look work for {index + 1}?",
                "color_terms": [color],
                "opening_visual_id": f"open-{index}",
                "scene_id": scene_id,
                "scene_palette": [f"palette-{index}"],
                "styling_signature": (
                    "straight jeans, clean low-profile sneakers, a small crossbody bag, and simple earrings"
                    if index == 0 else
                    "tailored trousers, clean loafers, a structured crossbody bag, and a simple watch"
                ),
                "proof_focus_id": f"proof-{index}",
                "pain_focus_id": f"pain-{index}",
                "signature_action_id": f"action-{index}",
                "spoken_intent_ids": intents,
                "caption_angle_id": f"caption-{index}",
                "difference_summary": f"distinct plan {index}",
            },
            "planned_non_cta_spoken_intent_ids": intents,
            "planned_non_cta_visual_signatures": signatures,
            "caption": f"A distinct outfit idea number {index + 1}.",
            "hashtags": [f"#tag{index}{item}" for item in range(5)],
        })
    document = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "market_prompt_contract_id": MARKET_PROMPT_CONTRACT_ID,
        "batch_run_id": "stream-self-test",
        "batch_compile_id": "stream-compile",
        "sku_family_id": "stream-sku",
        "plan_revision": 0,
        "plan_locked": True,
        "batch_key": {
            "model_preset": "德1", "market": "DE", "voiceover_language": "de-DE",
            "market_prompt_profile_id": "de_champion_v1",
            "platform": "TikTok", "duration_seconds": 15, "aspect_ratio": "9:16", "resolution": "720p",
            "frame_rate_fps": 60,
        },
        "shared_core": shared,
        "shared_core_sha256": canonical_sha256(shared),
        "quality_gates": dict(QUALITY_GATES),
        "planned_variant_count": len(variants),
        "planned_variants": variants,
    }
    for variant in document["planned_variants"]:
        controls = variant["generation_controls"]
        deadlines = {
            "contract_id": _market_contract.DEADLINE_CONTRACT_ID,
            "global_deadlines": {
                "platform": document["batch_key"]["platform"],
                "aspect_ratio": document["batch_key"]["aspect_ratio"],
                "duration_seconds": document["batch_key"]["duration_seconds"],
                "resolution": document["batch_key"]["resolution"],
                "frame_rate_fps": document["batch_key"]["frame_rate_fps"],
                "camera_mode": controls["camera_mode"],
                "screen_text_policy": controls["screen_text_policy"],
                "allowed_screen_text": ["168 cm / 52 kg / Größe S"],
                "music_mode": controls["music_mode"],
                "spoken_language": document["batch_key"]["voiceover_language"],
                "capture_quality_lock": "Ordinary smartphone native capture with real exposure limits and natural sensor noise.",
                "camera_behavior_lock": "Fixed phone behavior with only physically motivated vibration and no automatic reframing.",
                "lens_depth_lock": "Natural standard-lens perspective and full depth of field keep subject and environment readable.",
                "postprocessing_lock": "No beauty filter, body reshaping, cinematic grade, synthetic blur, or plastic skin.",
                "audio_capture_lock": "Phone microphone direct sound preserves room reflection, clothing friction, breaths, and distance changes.",
                "forbidden_overlays": sorted(_market_contract.REQUIRED_FORBIDDEN_OVERLAYS),
            },
            "asset_deadlines": {
                "creator_identity": "The same adult German woman remains visually and anatomically consistent in every shot.",
                "subject_appearance_lock": "Natural body proportions, skin texture, hair, jewelry, and styling remain unchanged.",
                "garment_identity": f"The same {variant['color_name']} apparel SKU is worn and demonstrated throughout.",
                "color_name": variant["color_name"],
                "garment_signature": variant["garment_signature"],
                "garment_structure_lock": "All neckline, sleeve, seam, panel, closure, hem, front, side, and back structures stay exact.",
                "fabric_physics_lock": "The fabric bends, stretches, drapes, wrinkles, and settles only under hands, body motion, and gravity.",
                "outfit": controls["scene_strategy"]["outfit_answer"],
                "outfit_prop_lock": "Outfit pieces and handled props stay distinct, stable, and continuous across every hard cut.",
                "scene_strategy": controls["scene_strategy"],
                "scene_environment_lock": "The declared primary location and its practical background objects remain coherent and readable.",
                "lighting": "Consistent natural daylight from one declared direction with stable color temperature and shadows.",
                "lighting_lock": "Key direction, exposure logic, shadow direction, and garment-detail visibility do not jump between shots.",
                "soundscape_lock": "Only direct German speech and plausible location sounds are audible with stable acoustic character.",
                "forbidden_asset_drift": sorted(_market_contract.REQUIRED_FORBIDDEN_ASSET_DRIFT),
            },
            "shot_deadlines": [],
        }
        variant["deadline_blueprint"] = deadline_blueprint_from_deadlines(deadlines)
    document["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(document))
    return document


def _legacy_v4_fixture() -> dict[str, Any]:
    document = _legacy_v5_fixture()
    document["quality_gates"] = dict(LEGACY_QUALITY_GATES)
    return document


def _legacy_v5_fixture() -> dict[str, Any]:
    document = _fixture()
    document["schema_version"] = LEGACY_SCHEMA_VERSION
    document["contract_id"] = LEGACY_CONTRACT_ID
    document.pop("market_prompt_contract_id", None)
    document["batch_key"].pop("market_prompt_profile_id", None)
    document["quality_gates"] = dict(LEGACY_V5_QUALITY_GATES)
    for variant in document["planned_variants"]:
        variant.pop("generation_controls", None)
    document["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(document))
    return document


def _release_fixture(plan: dict[str, Any], variant_id: str = "black") -> dict[str, Any]:
    planned = next(item for item in plan["planned_variants"] if item["variant_id"] == variant_id)
    beats = []
    for index, (intent, signature) in enumerate(zip(
        planned["planned_non_cta_spoken_intent_ids"],
        planned["planned_non_cta_visual_signatures"],
    )):
        framing, core_action, product_point = signature.split("|", 2)
        beats.append({
            "beat_id": f"{variant_id}-beat-{index}",
            "purpose": "hook" if index == 0 else "proof",
            "spoken_intent_id": intent,
            "framing": framing,
            "core_action": core_action,
            "product_point": product_point,
        })
    release = {
        "schema_version": plan["quality_gates"]["schema_version_required"],
        "quality_contract_id": plan["quality_gates"]["quality_contract_id_required"],
        "compile_mode": "single_compile",
        "batch_compile_id": plan["batch_compile_id"],
        "sku_family_id": plan["sku_family_id"],
        "batch_key": json.loads(json.dumps(plan["batch_key"])),
        "shared_core": json.loads(json.dumps(plan["shared_core"])),
        "streaming_release": {
            "contract_id": plan["contract_id"],
            "plan_sha256": "a" * 64,
            "variant_id": variant_id,
            "child_run_id": f"{plan['batch_run_id']}:{variant_id}",
        },
        "variants": [{
            **json.loads(json.dumps(planned)),
            "three_view_qc": "passed",
            "three_view_path": str(Path(__file__).resolve()),
            "three_view_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "references": [{
                "reference_id": f"ref-{variant_id}",
                "variant_id": variant_id,
                "role": "apparel_three_view",
                "interface_tag": "@Image2",
                "human_identity_pixels_absent": True,
            }],
            "canonical_timeline": {"beats": beats},
            "three_layer_deadlines": {
                "contract_id": planned.get("deadline_blueprint", {}).get("contract_id"),
                "global_deadlines": json.loads(json.dumps(planned.get("deadline_blueprint", {}).get("global_deadlines"))),
                "asset_deadlines": json.loads(json.dumps(planned.get("deadline_blueprint", {}).get("asset_deadlines"))),
                "shot_deadlines": [{
                    "beat_id": beat["beat_id"],
                    "start_seconds": index * 3,
                    "end_seconds": (index + 1) * 3,
                    "camera_setup_id": f"camera-{index}",
                    "scene_id": planned["creative_delta"]["scene_id"],
                    "camera_motion": "fixed phone with no tracking or digital zoom",
                    "lighting": "same natural daylight direction and exposure logic",
                    "audio": "direct German speech with plausible room and clothing sound",
                    "core_action": beat["core_action"],
                    "action_target": "the declared garment proof area",
                    "readable_endpoint": "the proof action ends fully visible before the hard cut",
                    "hand_plan": "left and right hands keep distinct anatomically plausible roles",
                    "action_physics_lock": "hands contact real fabric and the fabric responds locally before gravity settles it",
                    "anatomy_lock": "two arms, two hands, and natural fingers remain correctly attached without intersections",
                    "garment_state_lock": "garment color, construction, fit, and wear state remain unchanged through the shot",
                    "continuity_lock": "entry state matches the previous endpoint and the exit state is readable for the next hard cut",
                    "forbidden_outcomes": sorted(_market_contract.REQUIRED_FORBIDDEN_SHOT_OUTCOMES),
                } for index, beat in enumerate(beats)],
            },
            "renderings": {"prompt": {
                "serializer_id": plan["quality_gates"]["serializer_id_required"],
            }},
        }],
    }
    if plan.get("contract_id") == CONTRACT_ID:
        release["market_prompt_contract_id"] = MARKET_PROMPT_CONTRACT_ID
    return release


def self_test() -> dict[str, Any]:
    good = _fixture()
    cases = [("valid_plan", good, True, None)]
    cases.append(("legacy_v4_plan_readable", _legacy_v4_fixture(), True, None))
    cases.append(("legacy_v5_plan_readable", _legacy_v5_fixture(), True, None))
    bad_diff = json.loads(json.dumps(good))
    bad_diff["planned_variants"][1]["creative_delta"] = json.loads(json.dumps(bad_diff["planned_variants"][0]["creative_delta"]))
    bad_diff["planned_variants"][1]["creative_delta"]["color_terms"] = ["Blue"]
    bad_diff["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(bad_diff))
    cases.append(("color_only_collision", bad_diff, False, "STREAM_DIFFERENTIATION_FAILED"))
    bad_gate = json.loads(json.dumps(good))
    bad_gate["quality_gates"]["per_variant_director_validation_required"] = False
    cases.append(("quality_gate_removed", bad_gate, False, "STREAM_QUALITY_GATE_MISSING"))
    bad_market = json.loads(json.dumps(good))
    bad_market["batch_key"]["model_preset"] = "美1"
    cases.append(("market_tuple_drift", bad_market, False, "STREAM_MARKET_PROFILE_INVALID"))
    bad_controls = json.loads(json.dumps(good))
    bad_controls["planned_variants"][0]["generation_controls"]["delivery_mode"] = "us_hybrid_share"
    bad_controls["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(bad_controls))
    cases.append(("generation_controls_profile_drift", bad_controls, False, "STREAM_GENERATION_CONTROLS_INVALID"))
    bad_hash = json.loads(json.dumps(good))
    bad_hash["shared_core_sha256"] = "0" * 64
    cases.append(("shared_hash_drift", bad_hash, False, "STREAM_SHARED_CORE_HASH_MISMATCH"))
    bad_shared = json.loads(json.dumps(good))
    del bad_shared["shared_core"]["claims_registry"]
    bad_shared["shared_core_sha256"] = canonical_sha256(bad_shared["shared_core"])
    cases.append(("shared_evidence_incomplete", bad_shared, False, "STREAM_SHARED_EVIDENCE_INCOMPLETE"))
    bad_intent = json.loads(json.dumps(good))
    bad_intent["planned_variants"][0]["creative_delta"]["spoken_intent_ids"][0] = "unplanned-intent"
    bad_intent["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(bad_intent))
    cases.append(("spoken_intent_plan_drift", bad_intent, False, "STREAM_INTENT_PLAN_DRIFT"))
    bad_scene_projection = json.loads(json.dumps(good))
    bad_scene_projection["planned_variants"][0]["creative_delta"]["scene_id"] = "bedroom_mirror"
    bad_scene_projection["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(bad_scene_projection))
    cases.append((
        "scene_strategy_projection_drift", bad_scene_projection, False, "STREAM_SCENE_STRATEGY_DRIFT",
    ))
    missing_deadline_blueprint = json.loads(json.dumps(good))
    missing_deadline_blueprint["planned_variants"][0].pop("deadline_blueprint", None)
    missing_deadline_blueprint["differentiation_plan_sha256"] = canonical_sha256(
        _planned_payload(missing_deadline_blueprint)
    )
    cases.append((
        "deadline_blueprint_required", missing_deadline_blueprint, False,
        "STREAM_DEADLINE_BLUEPRINT_INVALID",
    ))
    global_deadline_drift = json.loads(json.dumps(good))
    global_deadline_drift["planned_variants"][0]["deadline_blueprint"]["global_deadlines"]["resolution"] = "4K"
    global_deadline_drift["differentiation_plan_sha256"] = canonical_sha256(
        _planned_payload(global_deadline_drift)
    )
    cases.append((
        "global_deadline_projection_drift", global_deadline_drift, False,
        "STREAM_DEADLINE_BLUEPRINT_DRIFT",
    ))
    too_many = json.loads(json.dumps(good))
    while len(too_many["planned_variants"]) <= MAX_STREAMING_VARIANTS:
        clone = json.loads(json.dumps(too_many["planned_variants"][1]))
        suffix = len(too_many["planned_variants"])
        clone["variant_id"] = f"extra-{suffix}"
        clone["color_name"] = f"Extra {suffix}"
        too_many["planned_variants"].append(clone)
    too_many["planned_variant_count"] = len(too_many["planned_variants"])
    too_many["differentiation_plan_sha256"] = canonical_sha256(_planned_payload(too_many))
    cases.append(("streaming_color_limit", too_many, False, "STREAM_VARIANT_LIMIT_EXCEEDED"))
    details = []
    for name, value, should_pass, expected in cases:
        result = validate_plan(value)
        codes = {item["code"] for item in result.get("errors", [])}
        passed = result.get("valid") is should_pass and (expected is None or expected in codes)
        details.append({"name": name, "passed": passed})

    release = _release_fixture(good)
    release_errors = validate_release_binding(good, release, "a" * 64)
    details.append({"name": "valid_release_binding", "passed": not release_errors})

    release_drift = json.loads(json.dumps(release))
    release_drift["variants"][0]["caption"] = "Unplanned replacement caption."
    drift_codes = {item["code"] for item in validate_release_binding(good, release_drift, "a" * 64)}
    details.append({"name": "release_plan_drift", "passed": "STREAM_RELEASE_PLAN_DRIFT" in drift_codes})

    release_controls = json.loads(json.dumps(release))
    release_controls["variants"][0]["generation_controls"]["music_mode"] = "low_non_lyrical"
    control_codes = {item["code"] for item in validate_release_binding(good, release_controls, "a" * 64)}
    details.append({"name": "release_generation_controls_drift", "passed": "STREAM_RELEASE_PLAN_DRIFT" in control_codes})

    release_market_contract = json.loads(json.dumps(release))
    release_market_contract["market_prompt_contract_id"] = "wrong"
    market_contract_codes = {item["code"] for item in validate_release_binding(good, release_market_contract, "a" * 64)}
    details.append({"name": "release_market_contract_drift", "passed": "STREAM_MARKET_CONTRACT_INVALID" in market_contract_codes})

    release_qc = json.loads(json.dumps(release))
    release_qc["variants"][0]["three_view_qc"] = "pending"
    qc_codes = {item["code"] for item in validate_release_binding(good, release_qc, "a" * 64)}
    details.append({"name": "release_qc_block", "passed": "STREAM_RELEASE_REFERENCE_NOT_PASSED" in qc_codes})

    release_identity_pixels = json.loads(json.dumps(release))
    release_identity_pixels["variants"][0]["references"][0]["human_identity_pixels_absent"] = False
    identity_pixel_codes = {item["code"] for item in validate_release_binding(good, release_identity_pixels, "a" * 64)}
    details.append({"name": "release_identity_pixel_qc_block", "passed": "STREAM_RELEASE_REFERENCE_NOT_PASSED" in identity_pixel_codes})

    release_reference_order = json.loads(json.dumps(release))
    release_reference_order["variants"][0]["references"][0]["interface_tag"] = "@Image1"
    reference_order_codes = {item["code"] for item in validate_release_binding(good, release_reference_order, "a" * 64)}
    details.append({"name": "release_identity_first_order_block", "passed": "STREAM_RELEASE_REFERENCE_NOT_PASSED" in reference_order_codes})

    release_serializer = json.loads(json.dumps(release))
    release_serializer["variants"][0]["renderings"]["prompt"]["serializer_id"] = LEGACY_PROMPT_SERIALIZER_ID
    serializer_codes = {item["code"] for item in validate_release_binding(good, release_serializer, "a" * 64)}
    details.append({"name": "release_serializer_downgrade_block", "passed": "STREAM_RELEASE_QUALITY_DOWNGRADE" in serializer_codes})

    release_global_deadline = json.loads(json.dumps(release))
    release_global_deadline["variants"][0]["three_layer_deadlines"]["global_deadlines"]["capture_quality_lock"] += " altered"
    release_global_deadline_codes = {
        item["code"] for item in validate_release_binding(good, release_global_deadline, "a" * 64)
    }
    details.append({
        "name": "release_global_deadline_drift",
        "passed": "STREAM_RELEASE_DEADLINE_DRIFT" in release_global_deadline_codes,
    })

    release_asset_deadline = json.loads(json.dumps(release))
    release_asset_deadline["variants"][0]["three_layer_deadlines"]["asset_deadlines"]["garment_structure_lock"] += " altered"
    release_asset_deadline_codes = {
        item["code"] for item in validate_release_binding(good, release_asset_deadline, "a" * 64)
    }
    details.append({
        "name": "release_asset_deadline_drift",
        "passed": "STREAM_RELEASE_DEADLINE_DRIFT" in release_asset_deadline_codes,
    })

    release_shot_deadline = json.loads(json.dumps(release))
    release_shot_deadline["variants"][0]["three_layer_deadlines"]["shot_deadlines"][0]["forbidden_outcomes"].pop()
    release_shot_deadline_codes = {
        item["code"] for item in validate_release_binding(good, release_shot_deadline, "a" * 64)
    }
    details.append({
        "name": "release_shot_deadline_policy_drift",
        "passed": "STREAM_RELEASE_DEADLINE_DRIFT" in release_shot_deadline_codes,
    })

    legacy_plan = _legacy_v4_fixture()
    legacy_result = validate_plan(legacy_plan)
    details.append({
        "name": "legacy_v4_plan_not_new_release_eligible",
        "passed": legacy_result.get("valid") is True and legacy_result.get("eligible_for_new_release") is False,
    })
    legacy_v5_plan = _legacy_v5_fixture()
    legacy_v5_result = validate_plan(legacy_v5_plan)
    details.append({
        "name": "legacy_v5_plan_not_new_release_eligible",
        "passed": legacy_v5_result.get("valid") is True
        and legacy_v5_result.get("eligible_for_new_release") is False
        and legacy_v5_result.get("historical_prompt_v5_read_only") is True,
    })
    legacy_release = _release_fixture(legacy_plan)
    legacy_release["variants"][0]["references"][0]["interface_tag"] = "@Image1"
    legacy_release["variants"][0]["references"][0].pop("human_identity_pixels_absent", None)
    details.append({
        "name": "legacy_v4_release_binding_readable",
        "passed": not validate_release_binding(legacy_plan, legacy_release, "a" * 64),
    })

    release_timeline = json.loads(json.dumps(release))
    release_timeline["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "unplanned action"
    timeline_codes = {item["code"] for item in validate_release_binding(good, release_timeline, "a" * 64)}
    details.append({"name": "release_timeline_drift", "passed": "STREAM_RELEASE_TIMELINE_DRIFT" in timeline_codes})

    release_hash = json.loads(json.dumps(release))
    release_hash["streaming_release"]["plan_sha256"] = "b" * 64
    hash_codes = {item["code"] for item in validate_release_binding(good, release_hash, "a" * 64)}
    details.append({"name": "release_plan_hash_drift", "passed": "STREAM_PLAN_HASH_MISMATCH" in hash_codes})

    release_run = json.loads(json.dumps(release))
    release_run["streaming_release"]["child_run_id"] = "unbound-child"
    run_codes = {item["code"] for item in validate_release_binding(good, release_run, "a" * 64)}
    details.append({"name": "release_child_run_drift", "passed": "STREAM_CHILD_RUN_ID_MISMATCH" in run_codes})

    release_file_hash = json.loads(json.dumps(release))
    release_file_hash["variants"][0]["three_view_sha256"] = "0" * 64
    file_hash_codes = {item["code"] for item in validate_release_binding(good, release_file_hash, "a" * 64)}
    details.append({"name": "release_three_view_hash_drift", "passed": "STREAM_THREE_VIEW_HASH_MISMATCH" in file_hash_codes})
    failures = [item for item in details if not item["passed"]]
    return _result(
        [] if not failures else [_error("STREAM_SELF_TEST_FAILED", "--self-test", "one or more self-tests failed")],
        mode="self-test", passed=len(details) - len(failures), failed=len(failures), tests=details,
    )


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("json_source", nargs="?", help="immutable streaming plan JSON path, inline JSON, or -")
    parser.add_argument("--self-test", action="store_true")
    try:
        args = parser.parse_args(argv)
        if args.self_test:
            if args.json_source is not None:
                raise ValueError("--self-test does not accept json_source")
            result = self_test()
        else:
            if args.json_source is None:
                raise ValueError("json_source is required")
            source = args.json_source
            if source == "-":
                raw = sys.stdin.buffer.read()
            elif source.lstrip().startswith(("{", "[")):
                raw = source.encode("utf-8")
            else:
                raw = Path(source).read_bytes()
            document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object_no_duplicates)
            result = validate_plan(document)
            result["streaming_plan_sha256"] = hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        result = _result([_error("STREAM_INPUT_INVALID", "$", str(exc))])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("valid") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
