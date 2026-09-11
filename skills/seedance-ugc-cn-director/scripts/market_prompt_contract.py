#!/usr/bin/env python3
"""Deterministic US/DE market prompt contract for schema 1.4.

This module deliberately has no third-party dependencies.  It is the shared
machine source for market tuple closure, generation-control enums, occasion-
first scene routing, canonical hashes, three-layer non-negotiable constraints,
natural-language v6/v7 prompt serialization, and small lexical gates used by
the batch and streaming validators.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


SCHEMA_VERSION = "1.4"
QUALITY_CONTRACT_ID = "zibuyu_ugc_quality_v4"
LEGACY_QUALITY_CONTRACT_ID = "zibuyu_ugc_quality_v3"
MARKET_PROMPT_CONTRACT_ID = "zibuyu_market_prompt_v1"
PROMPT_SERIALIZER_ID = "canonical_prompt_v7"
LEGACY_PROMPT_SERIALIZER_ID = "canonical_prompt_v6"
DEADLINE_CONTRACT_ID = "zibuyu_three_layer_deadlines_v1"

THREE_LAYER_DEADLINE_FIELDS = (
    "contract_id",
    "global_deadlines",
    "asset_deadlines",
    "shot_deadlines",
)
GLOBAL_DEADLINE_FIELDS = (
    "platform",
    "aspect_ratio",
    "duration_seconds",
    "resolution",
    "frame_rate_fps",
    "camera_mode",
    "screen_text_policy",
    "allowed_screen_text",
    "music_mode",
    "spoken_language",
    "capture_quality_lock",
    "camera_behavior_lock",
    "lens_depth_lock",
    "postprocessing_lock",
    "audio_capture_lock",
    "forbidden_overlays",
)
ASSET_DEADLINE_FIELDS = (
    "creator_identity",
    "subject_appearance_lock",
    "garment_identity",
    "color_name",
    "garment_signature",
    "garment_structure_lock",
    "fabric_physics_lock",
    "outfit",
    "outfit_prop_lock",
    "scene_strategy",
    "scene_environment_lock",
    "lighting",
    "lighting_lock",
    "soundscape_lock",
    "forbidden_asset_drift",
)
SHOT_DEADLINE_FIELDS = (
    "beat_id",
    "start_seconds",
    "end_seconds",
    "camera_setup_id",
    "scene_id",
    "camera_motion",
    "lighting",
    "audio",
    "core_action",
    "action_target",
    "readable_endpoint",
    "hand_plan",
    "action_physics_lock",
    "anatomy_lock",
    "garment_state_lock",
    "continuity_lock",
    "forbidden_outcomes",
)

REQUIRED_FORBIDDEN_OVERLAYS = {
    "subtitles",
    "translation",
    "price",
    "product_name",
    "link",
    "shopping_cart",
    "platform_ui",
    "sticker",
    "logo",
    "watermark",
}
REQUIRED_FORBIDDEN_ASSET_DRIFT = {
    "creator_identity",
    "body_shape",
    "garment_color",
    "garment_structure",
    "garment_texture",
    "fabric_physics",
    "undeclared_scene",
    "lighting_direction",
    "soundscape",
    "outfit_props",
}
REQUIRED_FORBIDDEN_SHOT_OUTCOMES = {
    "extra_limbs_or_fingers",
    "body_or_garment_intersection",
    "garment_structure_change",
    "unmotivated_fabric_motion",
    "continuity_break",
}

GENERATION_CONTROL_FIELDS = (
    "prompt_shell_mode",
    "delivery_mode",
    "camera_mode",
    "music_mode",
    "verdict_mode",
    "commerce_cta_mode",
    "screen_text_policy",
)

SCENE_STRATEGY_REQUIRED_FIELDS = (
    "wear_context",
    "scene_role",
    "primary_scene_id",
    "primary_scene_description",
    "occasion",
    "buyer_styling_question",
    "outfit_answer",
    "video_form",
    "location_plan",
    "bedroom_policy",
)
SCENE_STRATEGY_OPTIONAL_FIELDS = (
    "proof_scene_id",
    "proof_scene_description",
    "bedroom_justification",
)

ALLOWED_MARKET_TUPLES = {
    ("US", "美1", "en-US", "us_champion_v1"),
    ("US", "美2", "en-US", "us_champion_v1"),
    ("US", "美3", "en-US", "us_champion_v1"),
    ("DE", "德1", "de-DE", "de_champion_v1"),
    ("DE", "德2", "de-DE", "de_champion_v1"),
    ("DE", "德3", "de-DE", "de_champion_v1"),
}

MODEL_FIT_STATS_TEXT = {
    "美1": "5'6\" / 115 lb / Size S",
    "美2": "5'6\" / 200lb / Size 2XL",
    "美3": "5'6\" / 200lb / Size 2XL",
    "德1": "168 cm / 52 kg / Größe S",
    "德2": "168 cm / 52 kg / Größe S",
    "德3": "168 cm / 90 kg / Größe 2XL",
}

MARKET_PROFILES: dict[str, dict[str, Any]] = {
    "us_champion_v1": {
        "market": "US",
        "voiceover_language": "en-US",
        "model_presets": ("美1", "美2", "美3"),
        "prompt_shell_modes": ("us_technical_shell", "us_compact_storyboard"),
        "delivery_modes": ("us_hybrid_share",),
        "fit_stats_text": "5'6\" / 115 lb / Size S",
        "spoken_language_name": "natural American English",
        "profile_opening": (
            "US creative direction: native TikTok phone UGC, immediate garment visibility, "
            "friend-to-friend American delivery, and every spoken claim proved by one readable action."
        ),
        "scene_treatment": (
            "Let a credible American lifestyle destination add native occasion interest, but keep the garment and complete outfit solution dominant."
        ),
    },
    "de_champion_v1": {
        "market": "DE",
        "voiceover_language": "de-DE",
        "model_presets": ("德1", "德2", "德3"),
        "prompt_shell_modes": ("de_performance_script",),
        "delivery_modes": ("de_live_simple", "de_hybrid_proof"),
        "fit_stats_text": "168 cm / 52 kg / Größe S",
        "spoken_language_name": "natural conversational German",
        "profile_opening": (
            "German creative direction: the creator speaks only natural conversational German, "
            "starts from one concrete buyer concern, proves the exact garment part with one action, "
            "and closes with a believable first-person verdict or a light human-delivered link cue."
        ),
        "scene_treatment": (
            "Keep the German use-case location calm, plausible, and practical; never force landmarks, tourist scenery, flags, or artificial local signs."
        ),
    },
}

PROMPT_SHELL_MODES = {"us_technical_shell", "us_compact_storyboard", "de_performance_script"}
DELIVERY_MODES = {"us_hybrid_share", "de_live_simple", "de_hybrid_proof"}
CAMERA_MODES = {"fixed_phone", "creator_handheld", "friend_handheld"}
MUSIC_MODES = {"none", "low_non_lyrical"}
VERDICT_MODES = {"soft_verdict", "ownership_verdict", "none"}
COMMERCE_CTA_MODES = {"none", "light_link", "evidence_backed_promo"}
SCREEN_TEXT_POLICIES = {"no_generated_text", "fixed_model_stats_only"}
WEAR_CONTEXTS = {"outward_wear", "homewear", "mixed"}
SCENE_ROLES = {
    "occasion_outfit_solution", "movement_proof", "fit_proof", "detail_proof", "multi_styling",
}
SCENE_IDS = {
    "cafe_social", "beach_vacation", "commercial_street", "city_sidewalk", "office_commute",
    "travel_hotel", "entryway_departure", "outdoor_leisure", "home_living", "bedroom_mirror",
    "closet_try_on", "bathroom_mirror", "other_use_case",
}
VIDEO_FORMS = {
    "destination_outfit_share", "grwm_departure", "friend_filmed_walkthrough",
    "fixed_phone_fit_proof", "mirror_to_destination_match_cut", "travel_pack_and_wear",
    "multi_styling_switch", "home_try_on",
}
LOCATION_PLANS = {"single_primary_scene", "primary_plus_proof_cut"}
BEDROOM_POLICIES = {"excluded", "proof_only", "primary_justified", "homewear_primary"}
PRIVATE_INTERIOR_SCENE_IDS = {"home_living", "bedroom_mirror", "closet_try_on", "bathroom_mirror"}
PRIVATE_INTERIOR_TERMS = {
    "bedroom", "bed room", "bedroom mirror", "closet", "wardrobe room", "bathroom mirror",
    "home interior", "living room", "schlafzimmer", "kleiderschrank", "badezimmer",
    "卧室", "衣柜", "浴室", "家中客厅",
}
WEAK_BEDROOM_JUSTIFICATION_TERMS = {
    "convenient to film", "easy to film", "natural light", "ugc feel", "common setup",
    "easy to generate", "generation stability", "quiet room", "consistent background",
    "creator centered", "clean background", "simple setup", "convenience",
    "方便拍摄", "自然光", "ugc感", "常见场景", "容易生成", "安静房间", "背景稳定", "干净背景",
}
BEDROOM_PROOF_TARGET_TERMS = {
    "fit_proof": {
        "waist", "hem", "side seam", "neckline", "sleeve", "shoulder", "length", "drape",
        "silhouette", "proportion", "fit", "taille", "saum", "seitennaht", "ausschnitt",
        "ärmel", "schulter", "länge", "fall", "passform", "腰", "下摆", "侧缝", "领口",
        "袖", "肩", "衣长", "垂坠", "廓形", "比例", "版型",
    },
    "detail_proof": {
        "neckline", "collar", "cuff", "seam", "button", "texture", "fabric", "stitching", "hem",
        "ausschnitt", "kragen", "manschette", "naht", "knopf", "struktur", "stoff", "saum",
        "领口", "衣领", "袖口", "缝线", "纽扣", "纹理", "面料", "下摆",
    },
    "multi_styling": {
        "outfit", "styling", "tucked", "untucked", "layer", "bottoms", "shoes", "look",
        "outfit", "styling", "eingesteckt", "offen", "layering", "hose", "schuhe", "look",
        "穿搭", "造型", "塞衣角", "不塞", "叠穿", "下装", "鞋", "搭配",
    },
}
BEDROOM_PROOF_METHOD_TERMS = {
    "compare", "comparison", "same frame", "fixed mirror distance", "fixed camera distance",
    "side-by-side", "front side and back", "front, side, and back", "endpoint", "alignment",
    "styling switch", "fit proof", "detail proof", "vergleichen", "vergleich", "gleicher bildausschnitt",
    "fester spiegelabstand", "fester kameraabstand", "nebeneinander", "endpunkt", "ausrichtung",
    "对比", "比较", "同一画面", "固定镜距", "固定机位", "前侧后", "端点", "对齐", "换装",
}
VAGUE_OCCASIONS = {
    "daily life", "everyday", "everyday wear", "casual use", "general use", "going out",
    "alltag", "alltäglich", "alltagskleidung", "日常", "日常生活", "平时", "外出",
}
OUTFIT_BOTTOM_TERMS = {
    "jeans", "trousers", "pants", "slacks", "skirt", "shorts", "leggings", "culottes", "chinos",
    "hose", "hosen", "rock", "röcke", "牛仔裤", "长裤", "西裤", "裤", "半身裙", "短裙", "短裤", "打底裤",
}
OUTFIT_SHOE_TERMS = {
    "sneakers", "trainers", "shoes", "sandals", "loafers", "boots", "heels", "flats", "pumps",
    "sneaker", "schuhe", "sandalen", "loafer", "stiefel", "absätze", "鞋", "运动鞋", "凉鞋", "乐福鞋", "靴", "高跟鞋", "平底鞋",
}
OUTFIT_BAG_ACCESSORY_TERMS = {
    "bag", "tote", "crossbody", "handbag", "clutch", "backpack", "purse", "belt", "earrings",
    "necklace", "bracelet", "watch", "sunglasses", "scarf", "hat", "tasche", "umhängetasche",
    "rucksack", "gürtel", "ohrringe", "kette", "uhr", "sonnenbrille", "schal", "hut",
    "包", "托特", "斜挎", "手提包", "双肩包", "腰带", "耳环", "项链", "手链", "手表", "墨镜", "围巾", "帽",
}

COMPLEX_PROOF_ACTIONS = {
    "pinch_release",
    "pull_release",
    "raise_arm",
    "smooth_release",
    "turn_settle",
    "walk_settle",
    "open_close",
    "pocket_use",
    "front_tuck",
}
DE_LIVE_SIMPLE_ACTIONS = {"point_trace", "touch_release", "style_adjust"}

HAN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
URL_PATTERN = re.compile(r"(?:https?://|local://|www\.)", re.IGNORECASE)
META_PATTERN = re.compile(
    r"(?:\b(?:claim_id|claim_proof_id|evidence_id|pain_point_id|review_id|research_bundle|"
    r"human_identity_pixels_absent|spoken_claim_ids|product_part_id|proof_action_type|"
    r"visible_reference|verified_test)\b|中文(?:翻译|审稿)|(?:analysis|reasoning|internal rationale|"
    r"raw json|raw yaml)\s*[:：]|```|\{\s*\"[^\"]+\"\s*:)",
    re.IGNORECASE,
)

DE_LOANWORD_ALLOWLIST = {
    "basic", "basics", "top", "tops", "look", "looks", "oversize", "oversized",
    "style", "styling", "high", "waist", "jeans", "shirt", "t-shirt", "shirtjacke",
}
ENGLISH_FUNCTION_WORDS = {
    "the", "this", "that", "these", "those", "with", "from", "your", "you", "are",
    "is", "it", "my", "i", "love", "wear", "tap", "click", "here", "how", "when",
    "really", "just", "because", "and", "but", "looks", "feels",
}
GERMAN_FUNCTION_WORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",
    "ist", "sind", "mit", "für", "fuer", "und", "aber", "hier", "wenn", "wie",
    "schau", "sieh", "beim", "bleibt", "sitzt", "endet", "öffnet", "oeffnet",
    "ich", "du", "mein", "meine", "behalte", "unten", "links",
}
HIGH_CONFIDENCE_ENGLISH_PHRASES = (
    "look at this", "tap the link", "click the link", "you can see", "i love this",
    "this top is", "this dress is", "this fabric", "wear it with", "here is why",
)
HIGH_CONFIDENCE_GERMAN_PHRASES = (
    "schau mal", "sieh dir", "hier siehst du", "beim drehen", "der ausschnitt",
    "die ärmel", "die aermel", "das behalte ich", "unten links",
)

GRAPHIC_CTA_TERMS = {
    "arrow", "arrow emoji", "shopping cart", "cart icon", "button graphic", "sticker",
    "badge", "floating icon", "pointer graphic", "ui overlay", "product-link graphic",
    "animated text", "emoji", "箭头", "购物车", "按钮", "贴纸", "徽章", "浮动图标",
}
EXTRA_SCREEN_TEXT_TERMS = {
    "subtitle", "subtitles", "caption overlay", "price card", "discount badge", "sale badge",
    "text overlay", "on-screen text", "lower third", "untertitel", "preis-tag", "rabatt-badge",
    "字幕", "价格卡", "折扣标签", "屏幕文字",
}
MUSIC_POSITIVE_TERMS = {
    "background music", "music bed", "soundtrack", "instrumental music", "soft music",
    "background track", "bgm", "hintergrundmusik", "musik", "背景音乐", "配乐",
}
MUSIC_NEGATIVE_TERMS = {"no music", "without music", "keine musik", "无音乐", "不要音乐"}
LYRICAL_MUSIC_TERMS = {
    "lyrics", "lyrical", "vocal music", "sung vocals", "song vocals", "gesang", "liedtext",
    "歌词", "人声歌曲",
}
FIXED_CAMERA_TERMS = {"tripod", "fixed camera", "locked tripod", "static camera", "固定机位", "三脚架"}
HANDHELD_CAMERA_TERMS = {"handheld", "hand-held", "selfie phone", "mirror selfie", "手持", "自拍"}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split())


def fit_stats_text_for_batch(batch_key: Any) -> str:
    """Return the fixed upper-left stats text for a concrete model preset."""
    batch = batch_key if isinstance(batch_key, dict) else {}
    preset = str(batch.get("model_preset") or "")
    if preset in MODEL_FIT_STATS_TEXT:
        return MODEL_FIT_STATS_TEXT[preset]
    profile = MARKET_PROFILES.get(str(batch.get("market_prompt_profile_id")), {})
    fallback = profile.get("fit_stats_text")
    return fallback if isinstance(fallback, str) else ""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    """Return the stable canonical JSON SHA-256 used by all v1.4 bindings."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _issue(code: str, path: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "path": path, "message": message}
    if details:
        item["details"] = details
    return item


def _private_scene(scene_id: Any, description: Any = None) -> bool:
    if scene_id in PRIVATE_INTERIOR_SCENE_IDS:
        return True
    normalized = _norm(description)
    return any(_norm(term) in normalized for term in PRIVATE_INTERIOR_TERMS)


def _mentions_any_term(value: Any, terms: set[str]) -> bool:
    normalized = _norm(value)
    for term in terms:
        normalized_term = _norm(term)
        if HAN_PATTERN.search(normalized_term):
            if normalized_term in normalized:
                return True
        elif re.search(r"(?<!\w)" + re.escape(normalized_term) + r"(?!\w)", normalized):
            return True
    return False


def _specific_bedroom_justification(value: Any, scene_role: Any) -> bool:
    text = str(value or "").strip()
    normalized = _norm(text)
    if not text or any(_norm(term) in normalized for term in WEAK_BEDROOM_JUSTIFICATION_TERMS):
        return False
    target_terms = BEDROOM_PROOF_TARGET_TERMS.get(str(scene_role), set())
    if not target_terms or not _mentions_any_term(text, target_terms):
        return False
    if not _mentions_any_term(text, BEDROOM_PROOF_METHOD_TERMS):
        return False
    if HAN_PATTERN.search(text):
        return len(text) >= 16
    return len(text) >= 28 and len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9'-]+", text)) >= 6


def validate_scene_strategy(strategy: Any, path: str = "generation_controls.scene_strategy") -> list[dict[str, Any]]:
    """Validate the occasion, outfit answer, video form, and private-interior gate."""
    if not isinstance(strategy, dict):
        return [_issue(
            "SCENE_STRATEGY_INVALID", path,
            "schema 1.4 requires an occasion-first scene_strategy object",
        )]
    issues: list[dict[str, Any]] = []
    allowed_fields = set(SCENE_STRATEGY_REQUIRED_FIELDS) | set(SCENE_STRATEGY_OPTIONAL_FIELDS)
    unknown = sorted(set(strategy) - allowed_fields)
    if unknown:
        issues.append(_issue(
            "SCENE_STRATEGY_INVALID", path,
            "scene_strategy contains unsupported fields", fields=unknown,
        ))
    missing = [
        field for field in SCENE_STRATEGY_REQUIRED_FIELDS
        if not isinstance(strategy.get(field), str) or not str(strategy[field]).strip()
    ]
    if missing:
        issues.append(_issue(
            "SCENE_STRATEGY_INVALID", path,
            "scene_strategy is missing required non-empty string fields", fields=missing,
        ))
        return issues

    enum_checks = (
        ("wear_context", WEAR_CONTEXTS),
        ("scene_role", SCENE_ROLES),
        ("primary_scene_id", SCENE_IDS),
        ("video_form", VIDEO_FORMS),
        ("location_plan", LOCATION_PLANS),
        ("bedroom_policy", BEDROOM_POLICIES),
    )
    for field, allowed in enum_checks:
        if strategy.get(field) not in allowed:
            issues.append(_issue(
                "SCENE_STRATEGY_INVALID", path + "." + field,
                "unsupported occasion-scene control", actual=strategy.get(field),
            ))

    location_plan = strategy.get("location_plan")
    primary_id = strategy.get("primary_scene_id")
    primary_description = strategy.get("primary_scene_description")
    proof_id = strategy.get("proof_scene_id")
    proof_description = strategy.get("proof_scene_description")
    bedroom_policy = strategy.get("bedroom_policy")
    wear_context = strategy.get("wear_context")
    primary_private = _private_scene(primary_id, primary_description)

    if _norm(strategy.get("occasion")) in VAGUE_OCCASIONS:
        issues.append(_issue(
            "SCENE_STRATEGY_INVALID", path + ".occasion",
            "occasion must name a concrete destination or use moment, not a generic daily-life label",
        ))
    outfit_answer = strategy.get("outfit_answer")
    missing_outfit_parts = [
        label for label, terms in (
            ("bottoms", OUTFIT_BOTTOM_TERMS),
            ("shoes", OUTFIT_SHOE_TERMS),
            ("bag_or_accessory", OUTFIT_BAG_ACCESSORY_TERMS),
        )
        if not _mentions_any_term(outfit_answer, terms)
    ]
    if missing_outfit_parts:
        issues.append(_issue(
            "SCENE_STRATEGY_INVALID", path + ".outfit_answer",
            "outfit_answer must include concrete bottoms, shoes, and a bag or accessory decision",
            missing=missing_outfit_parts,
        ))

    if location_plan == "single_primary_scene":
        if proof_id is not None or proof_description is not None:
            issues.append(_issue(
                "SCENE_LOCATION_PLAN_INVALID", path,
                "single_primary_scene must omit proof_scene_id and proof_scene_description",
            ))
    elif location_plan == "primary_plus_proof_cut":
        if proof_id not in SCENE_IDS or not isinstance(proof_description, str) or not proof_description.strip():
            issues.append(_issue(
                "SCENE_LOCATION_PLAN_INVALID", path,
                "primary_plus_proof_cut requires an approved proof_scene_id and concrete proof_scene_description",
            ))
        elif proof_id == primary_id:
            issues.append(_issue(
                "SCENE_LOCATION_PLAN_INVALID", path + ".proof_scene_id",
                "proof scene must differ from the primary use-case scene",
            ))

    proof_private = _private_scene(proof_id, proof_description) if proof_id is not None else False
    if bedroom_policy == "excluded" and (primary_private or proof_private):
        issues.append(_issue(
            "BEDROOM_DEFAULT_FORBIDDEN", path + ".bedroom_policy",
            "excluded private-interior policy conflicts with the selected primary or proof scene",
        ))
    elif bedroom_policy == "proof_only":
        if location_plan != "primary_plus_proof_cut" or primary_private or not proof_private:
            issues.append(_issue(
                "BEDROOM_DEFAULT_FORBIDDEN", path + ".bedroom_policy",
                "proof_only requires a non-private primary scene plus one private proof scene",
            ))
    elif bedroom_policy == "primary_justified":
        if (
            not primary_private
            or strategy.get("scene_role") not in {"fit_proof", "detail_proof", "multi_styling"}
            or not _specific_bedroom_justification(
                strategy.get("bedroom_justification"), strategy.get("scene_role"),
            )
        ):
            issues.append(_issue(
                "BEDROOM_DEFAULT_FORBIDDEN", path + ".bedroom_justification",
                "a private primary scene for outward or mixed wear requires a specific fit, detail, or multi-styling justification",
            ))
    elif bedroom_policy == "homewear_primary":
        if wear_context != "homewear" or not primary_private:
            issues.append(_issue(
                "BEDROOM_DEFAULT_FORBIDDEN", path + ".bedroom_policy",
                "homewear_primary is allowed only for genuine homewear in a private primary scene",
            ))

    if wear_context in {"outward_wear", "mixed"} and primary_private and bedroom_policy != "primary_justified":
        issues.append(_issue(
            "BEDROOM_DEFAULT_FORBIDDEN", path + ".primary_scene_id",
            "outward or mixed wear may not default to a private primary scene",
        ))
    if strategy.get("video_form") == "mirror_to_destination_match_cut" and not (
        location_plan == "primary_plus_proof_cut" and proof_private and not primary_private
    ):
        issues.append(_issue(
            "SCENE_LOCATION_PLAN_INVALID", path + ".video_form",
            "mirror_to_destination_match_cut requires one private proof scene followed by a non-private primary scene",
        ))
    if strategy.get("video_form") == "home_try_on" and not (
        wear_context == "homewear" and primary_private
    ):
        issues.append(_issue(
            "SCENE_LOCATION_PLAN_INVALID", path + ".video_form",
            "home_try_on is reserved for genuine homewear in a private primary scene",
        ))
    return issues


def validate_scene_projection(
    strategy: Any,
    continuity_anchors: Any = None,
    creative_delta: Any = None,
    path: str = "variant",
) -> list[dict[str, Any]]:
    """Lock scene and outfit decisions into continuity and creative projections."""
    if not isinstance(strategy, dict):
        return []
    expected_scene = strategy.get("primary_scene_id")
    expected_outfit = strategy.get("outfit_answer")
    issues: list[dict[str, Any]] = []
    projections = (
        (continuity_anchors, "scene", expected_scene, path + ".quality_plan.continuity_anchors.scene"),
        (continuity_anchors, "outfit", expected_outfit, path + ".quality_plan.continuity_anchors.outfit"),
        (creative_delta, "scene_id", expected_scene, path + ".creative_delta.scene_id"),
        (creative_delta, "styling_signature", expected_outfit, path + ".creative_delta.styling_signature"),
    )
    for container, field, expected, field_path in projections:
        if isinstance(container, dict) and container.get(field) != expected:
            issues.append(_issue(
                "SCENE_STRATEGY_DRIFT", field_path,
                "scene/outfit projection must exactly match the locked scene_strategy",
                expected=expected, actual=container.get(field),
            ))
    return issues


def validate_scene_alignment(
    strategy: Any, beats: Any, path: str = "canonical_timeline.beats",
) -> list[dict[str, Any]]:
    """Bind every beat to the locked primary/proof location without scene hopping."""
    if not isinstance(strategy, dict) or not isinstance(beats, list) or not beats:
        return []
    primary_id = strategy.get("primary_scene_id")
    proof_id = strategy.get("proof_scene_id")
    location_plan = strategy.get("location_plan")
    scene_ids: list[Any] = []
    issues: list[dict[str, Any]] = []
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        scene_id = beat.get("scene_id")
        scene_ids.append(scene_id)
        if scene_id not in SCENE_IDS:
            issues.append(_issue(
                "SCENE_TIMELINE_MISMATCH", f"{path}[{index}].scene_id",
                "every v6 beat requires one approved machine scene_id", actual=scene_id,
            ))
        if _private_scene(None, beat.get("scene")) and scene_id not in PRIVATE_INTERIOR_SCENE_IDS:
            issues.append(_issue(
                "SCENE_TIMELINE_MISMATCH", f"{path}[{index}].scene",
                "readable scene prose names a private interior but scene_id claims a non-private location",
            ))
    if any(scene_id not in SCENE_IDS for scene_id in scene_ids):
        return issues
    if location_plan == "single_primary_scene":
        drift = [index for index, scene_id in enumerate(scene_ids) if scene_id != primary_id]
        if drift:
            issues.append(_issue(
                "SCENE_TIMELINE_MISMATCH", path,
                "single_primary_scene requires every beat to use the primary scene_id", beat_indices=drift,
            ))
        return issues
    if location_plan != "primary_plus_proof_cut" or proof_id not in SCENE_IDS:
        return issues

    allowed = {primary_id, proof_id}
    drift = [index for index, scene_id in enumerate(scene_ids) if scene_id not in allowed]
    if drift:
        issues.append(_issue(
            "SCENE_TIMELINE_MISMATCH", path,
            "two-location plan contains an undeclared scene_id", beat_indices=drift,
        ))
    if primary_id not in scene_ids or proof_id not in scene_ids:
        issues.append(_issue(
            "SCENE_LOCATION_PLAN_INVALID", path,
            "primary_plus_proof_cut must use both declared locations",
        ))
    transitions = sum(left != right for left, right in zip(scene_ids, scene_ids[1:]))
    if transitions > 1:
        issues.append(_issue(
            "SCENE_LOCATION_PLAN_INVALID", path,
            "a 15-second two-location plan allows only one intentional scene transition", transitions=transitions,
        ))
    if scene_ids[-1] != primary_id or scene_ids.count(primary_id) <= len(scene_ids) // 2:
        issues.append(_issue(
            "SCENE_LOCATION_PLAN_INVALID", path,
            "the primary use-case scene must hold the majority of beats and the final result",
        ))
    if _private_scene(proof_id) and any(
        scene_id == proof_id and index > 1 for index, scene_id in enumerate(scene_ids)
    ):
        issues.append(_issue(
            "BEDROOM_DEFAULT_FORBIDDEN", path,
            "a private proof scene may occupy only the first two beats before the outward primary scene",
        ))
    return issues


def validate_market_tuple(batch_key: Any) -> list[dict[str, Any]]:
    """Validate the closed market/model/language/profile tuple for schema 1.4."""
    if not isinstance(batch_key, dict):
        return [_issue("MARKET_PROFILE_INVALID", "batch_key", "batch_key must be an object")]
    market = batch_key.get("market")
    preset = batch_key.get("model_preset")
    language = batch_key.get("voiceover_language")
    profile_id = batch_key.get("market_prompt_profile_id")
    actual = (market, preset, language, profile_id)
    if actual in ALLOWED_MARKET_TUPLES:
        return []
    issues: list[dict[str, Any]] = []
    profile = MARKET_PROFILES.get(str(profile_id))
    if profile is None:
        issues.append(_issue(
            "MARKET_PROFILE_INVALID", "batch_key.market_prompt_profile_id",
            "schema 1.4 requires us_champion_v1 or de_champion_v1",
            actual=profile_id,
        ))
        return issues
    if market != profile["market"] or language != profile["voiceover_language"]:
        issues.append(_issue(
            "MARKET_LANGUAGE_MISMATCH", "batch_key.voiceover_language",
            "market, language, and market prompt profile must form one approved tuple",
            market=market, language=language, profile_id=profile_id,
        ))
    if preset not in profile["model_presets"]:
        issues.append(_issue(
            "MODEL_PRESET_MARKET_MISMATCH", "batch_key.model_preset",
            "fixed-model preset does not belong to the selected market profile",
            model_preset=preset, profile_id=profile_id,
        ))
    if not issues:
        issues.append(_issue(
            "MARKET_PROFILE_INVALID", "batch_key",
            "market tuple is not in the closed schema 1.4 allowlist", actual=list(actual),
        ))
    return issues


def validate_generation_controls(
    profile_id: Any, controls: Any, path: str = "generation_controls",
) -> list[dict[str, Any]]:
    if not isinstance(controls, dict):
        return [_issue("MARKET_PROFILE_INVALID", path, "generation_controls must be an object")]
    issues: list[dict[str, Any]] = []
    missing = [field for field in GENERATION_CONTROL_FIELDS if not isinstance(controls.get(field), str) or not controls[field]]
    if missing:
        issues.append(_issue(
            "MARKET_PROFILE_INVALID", path,
            "generation_controls is missing required string fields", fields=missing,
        ))
        return issues
    unknown = sorted(set(controls) - set(GENERATION_CONTROL_FIELDS) - {"promotion_evidence_ids", "scene_strategy"})
    if unknown:
        issues.append(_issue(
            "MARKET_PROFILE_INVALID", path,
            "generation_controls contains unsupported fields", fields=unknown,
        ))
    profile = MARKET_PROFILES.get(str(profile_id), {})
    shell = controls.get("prompt_shell_mode")
    delivery = controls.get("delivery_mode")
    if shell not in PROMPT_SHELL_MODES or shell not in profile.get("prompt_shell_modes", ()):
        issues.append(_issue(
            "MARKET_PROFILE_INVALID", path + ".prompt_shell_mode",
            "prompt shell is not permitted by the selected market profile", actual=shell,
        ))
    if delivery not in DELIVERY_MODES or delivery not in profile.get("delivery_modes", ()):
        issues.append(_issue(
            "DELIVERY_MODE_CONFLICT", path + ".delivery_mode",
            "delivery mode is not permitted by the selected market profile", actual=delivery,
        ))
    enum_checks = (
        ("camera_mode", CAMERA_MODES, "CAMERA_MODE_CONFLICT"),
        ("music_mode", MUSIC_MODES, "MUSIC_MODE_CONFLICT"),
        ("verdict_mode", VERDICT_MODES, "CTA_POLICY_INVALID"),
        ("commerce_cta_mode", COMMERCE_CTA_MODES, "CTA_POLICY_INVALID"),
        ("screen_text_policy", SCREEN_TEXT_POLICIES, "SCREEN_TEXT_POLICY_INVALID"),
    )
    for field, allowed, code in enum_checks:
        if controls.get(field) not in allowed:
            issues.append(_issue(code, path + "." + field, "unsupported generation control", actual=controls.get(field)))
    issues.extend(validate_scene_strategy(controls.get("scene_strategy"), path + ".scene_strategy"))
    if controls.get("verdict_mode") == "none" and controls.get("commerce_cta_mode") == "none":
        issues.append(_issue(
            "CTA_POLICY_INVALID", path,
            "the closing beat requires a verdict, a commerce CTA, or both",
        ))
    evidence_ids = controls.get("promotion_evidence_ids")
    if evidence_ids is not None and (
        not isinstance(evidence_ids, list) or any(not isinstance(item, str) or not item for item in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        issues.append(_issue(
            "CTA_POLICY_INVALID", path + ".promotion_evidence_ids",
            "promotion_evidence_ids must be a unique non-empty string array when provided",
        ))
    return issues


def _deadline_string(
    container: Any,
    field: str,
    path: str,
    issues: list[dict[str, Any]],
    *,
    minimum_length: int = 16,
) -> str:
    value = container.get(field) if isinstance(container, dict) else None
    if not isinstance(value, str) or len(value.strip()) < minimum_length:
        issues.append(_issue(
            "DEADLINE_LOCK_MISSING",
            path + "." + field,
            "three-layer deadline prose must be concrete and non-empty",
            minimum_characters=minimum_length,
        ))
        return ""
    return value.strip()


def _deadline_string_set(
    value: Any,
    path: str,
    issues: list[dict[str, Any]],
    required: set[str],
    code: str,
) -> set[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        issues.append(_issue(
            code,
            path,
            "deadline exclusions must be a unique non-empty string array",
        ))
        return set()
    actual = set(value)
    missing = sorted(required - actual)
    if missing:
        issues.append(_issue(
            code,
            path,
            "deadline exclusions omit mandatory failure outcomes",
            missing=missing,
        ))
    return actual


def _deadline_projection(
    container: Any,
    expected: dict[str, Any],
    path: str,
    issues: list[dict[str, Any]],
    code: str,
) -> None:
    if not isinstance(container, dict):
        issues.append(_issue(code, path, "deadline layer must be an object"))
        return
    for field, expected_value in expected.items():
        if container.get(field) != expected_value:
            issues.append(_issue(
                code,
                path + "." + field,
                "deadline projection must exactly match its canonical source",
                expected=expected_value,
                actual=container.get(field),
            ))


def validate_three_layer_deadlines(
    deadlines: Any,
    batch_key: Any,
    generation_controls: Any,
    variant: Any,
    beats: Any,
    quality_plan: Any,
    path: str = "three_layer_deadlines",
) -> list[dict[str, Any]]:
    """Validate global, asset, and per-shot non-negotiables as exact projections."""
    if not isinstance(deadlines, dict):
        return [_issue(
            "DEADLINE_CONTRACT_INVALID",
            path,
            "current schema 1.4 work requires a three_layer_deadlines object",
        )]
    issues: list[dict[str, Any]] = []
    unknown_top = sorted(set(deadlines) - set(THREE_LAYER_DEADLINE_FIELDS))
    missing_top = sorted(set(THREE_LAYER_DEADLINE_FIELDS) - set(deadlines))
    if unknown_top or missing_top or deadlines.get("contract_id") != DEADLINE_CONTRACT_ID:
        issues.append(_issue(
            "DEADLINE_CONTRACT_INVALID",
            path,
            "three-layer deadline contract fields or contract_id are invalid",
            missing=missing_top,
            unsupported=unknown_top,
            expected_contract_id=DEADLINE_CONTRACT_ID,
            actual_contract_id=deadlines.get("contract_id"),
        ))

    batch = batch_key if isinstance(batch_key, dict) else {}
    controls = generation_controls if isinstance(generation_controls, dict) else {}
    item = variant if isinstance(variant, dict) else {}
    plan = quality_plan if isinstance(quality_plan, dict) else {}
    anchors = plan.get("continuity_anchors") if isinstance(plan.get("continuity_anchors"), dict) else {}

    global_deadlines = deadlines.get("global_deadlines")
    if not isinstance(global_deadlines, dict):
        issues.append(_issue(
            "GLOBAL_DEADLINE_INVALID", path + ".global_deadlines",
            "global_deadlines must be an object",
        ))
    else:
        unknown = sorted(set(global_deadlines) - set(GLOBAL_DEADLINE_FIELDS))
        missing = sorted(set(GLOBAL_DEADLINE_FIELDS) - set(global_deadlines))
        if unknown or missing:
            issues.append(_issue(
                "GLOBAL_DEADLINE_INVALID", path + ".global_deadlines",
                "global_deadlines must contain exactly the supported fields",
                missing=missing, unsupported=unknown,
            ))
        policy = controls.get("screen_text_policy")
        fit_stats_text = fit_stats_text_for_batch(batch)
        expected_screen_text = (
            [fit_stats_text]
            if policy == "fixed_model_stats_only" and fit_stats_text
            else []
        )
        _deadline_projection(
            global_deadlines,
            {
                "platform": batch.get("platform"),
                "aspect_ratio": batch.get("aspect_ratio"),
                "duration_seconds": batch.get("duration_seconds"),
                "resolution": batch.get("resolution"),
                "frame_rate_fps": batch.get("frame_rate_fps"),
                "camera_mode": controls.get("camera_mode"),
                "screen_text_policy": policy,
                "allowed_screen_text": expected_screen_text,
                "music_mode": controls.get("music_mode"),
                "spoken_language": batch.get("voiceover_language"),
            },
            path + ".global_deadlines",
            issues,
            "GLOBAL_DEADLINE_DRIFT",
        )
        frame_rate = global_deadlines.get("frame_rate_fps")
        if not isinstance(frame_rate, int) or isinstance(frame_rate, bool) or frame_rate <= 0:
            issues.append(_issue(
                "GLOBAL_DEADLINE_INVALID",
                path + ".global_deadlines.frame_rate_fps",
                "frame_rate_fps must be a positive integer locked in batch_key",
            ))
        for field in (
            "capture_quality_lock", "camera_behavior_lock", "lens_depth_lock",
            "postprocessing_lock", "audio_capture_lock",
        ):
            _deadline_string(global_deadlines, field, path + ".global_deadlines", issues, minimum_length=24)
        _deadline_string_set(
            global_deadlines.get("forbidden_overlays"),
            path + ".global_deadlines.forbidden_overlays",
            issues,
            REQUIRED_FORBIDDEN_OVERLAYS,
            "GLOBAL_DEADLINE_INVALID",
        )

    asset_deadlines = deadlines.get("asset_deadlines")
    if not isinstance(asset_deadlines, dict):
        issues.append(_issue(
            "ASSET_DEADLINE_INVALID", path + ".asset_deadlines",
            "asset_deadlines must be an object",
        ))
    else:
        unknown = sorted(set(asset_deadlines) - set(ASSET_DEADLINE_FIELDS))
        missing = sorted(set(ASSET_DEADLINE_FIELDS) - set(asset_deadlines))
        if unknown or missing:
            issues.append(_issue(
                "ASSET_DEADLINE_INVALID", path + ".asset_deadlines",
                "asset_deadlines must contain exactly the supported fields",
                missing=missing, unsupported=unknown,
            ))
        _deadline_projection(
            asset_deadlines,
            {
                "creator_identity": anchors.get("creator_identity"),
                "garment_identity": anchors.get("garment_identity"),
                "color_name": item.get("color_name"),
                "garment_signature": item.get("garment_signature"),
                "outfit": anchors.get("outfit"),
                "scene_strategy": controls.get("scene_strategy"),
                "lighting": anchors.get("lighting"),
            },
            path + ".asset_deadlines",
            issues,
            "ASSET_DEADLINE_DRIFT",
        )
        for field in (
            "subject_appearance_lock", "garment_structure_lock", "fabric_physics_lock",
            "outfit_prop_lock", "scene_environment_lock", "lighting_lock", "soundscape_lock",
        ):
            _deadline_string(asset_deadlines, field, path + ".asset_deadlines", issues, minimum_length=24)
        _deadline_string_set(
            asset_deadlines.get("forbidden_asset_drift"),
            path + ".asset_deadlines.forbidden_asset_drift",
            issues,
            REQUIRED_FORBIDDEN_ASSET_DRIFT,
            "ASSET_DEADLINE_INVALID",
        )

    shot_deadlines = deadlines.get("shot_deadlines")
    canonical_beats = beats if isinstance(beats, list) else []
    if not isinstance(shot_deadlines, list) or len(shot_deadlines) != len(canonical_beats):
        issues.append(_issue(
            "SHOT_DEADLINE_INVALID",
            path + ".shot_deadlines",
            "shot_deadlines must contain exactly one ordered entry per canonical beat",
            expected_count=len(canonical_beats),
            actual_count=len(shot_deadlines) if isinstance(shot_deadlines, list) else None,
        ))
        return issues
    for index, (shot, beat) in enumerate(zip(shot_deadlines, canonical_beats)):
        shot_path = f"{path}.shot_deadlines[{index}]"
        if not isinstance(shot, dict) or not isinstance(beat, dict):
            issues.append(_issue("SHOT_DEADLINE_INVALID", shot_path, "shot deadline and beat must be objects"))
            continue
        unknown = sorted(set(shot) - set(SHOT_DEADLINE_FIELDS))
        missing = sorted(set(SHOT_DEADLINE_FIELDS) - set(shot))
        if unknown or missing:
            issues.append(_issue(
                "SHOT_DEADLINE_INVALID", shot_path,
                "shot deadline must contain exactly the supported fields",
                missing=missing, unsupported=unknown,
            ))
        readable_endpoint = beat.get("proof_endpoint") or beat.get("visible_endpoint")
        _deadline_projection(
            shot,
            {
                "beat_id": beat.get("beat_id"),
                "start_seconds": beat.get("start_seconds"),
                "end_seconds": beat.get("end_seconds"),
                "camera_setup_id": beat.get("camera_setup_id"),
                "scene_id": beat.get("scene_id"),
                "camera_motion": beat.get("camera_motion"),
                "lighting": beat.get("lighting"),
                "audio": beat.get("audio"),
                "core_action": beat.get("core_action"),
                "action_target": beat.get("action_target"),
                "readable_endpoint": readable_endpoint,
                "hand_plan": beat.get("hand_plan"),
            },
            shot_path,
            issues,
            "SHOT_DEADLINE_DRIFT",
        )
        prose_locks = [
            _deadline_string(shot, field, shot_path, issues, minimum_length=20)
            for field in (
                "action_physics_lock", "anatomy_lock", "garment_state_lock", "continuity_lock",
            )
        ]
        normalized_locks = [_norm(value) for value in prose_locks if value]
        if len(normalized_locks) != len(set(normalized_locks)):
            issues.append(_issue(
                "SHOT_DEADLINE_INVALID",
                shot_path,
                "action, anatomy, garment-state, and continuity locks must be distinct instructions",
            ))
        _deadline_string_set(
            shot.get("forbidden_outcomes"),
            shot_path + ".forbidden_outcomes",
            issues,
            REQUIRED_FORBIDDEN_SHOT_OUTCOMES,
            "SHOT_DEADLINE_INVALID",
        )
    return issues


def contains_han(value: Any) -> bool:
    return bool(HAN_PATTERN.search(str(value or "")))


def _latin_tokens(value: Any) -> list[str]:
    return re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ'-]+", _norm(value))


def language_issue(value: Any, market: str) -> str | None:
    """Return a deterministic high-confidence language problem, not a guessed language."""
    text = str(value or "").strip()
    if not text:
        return None
    if contains_han(text):
        return "spoken market-language slots may not contain Chinese review text"
    normalized = _norm(text)
    tokens = _latin_tokens(text)
    if market == "DE":
        if any(phrase in normalized for phrase in HIGH_CONFIDENCE_ENGLISH_PHRASES):
            return "German spoken slot contains a high-confidence English template phrase"
        english_hits = [token for token in tokens if token in ENGLISH_FUNCTION_WORDS and token not in DE_LOANWORD_ALLOWLIST]
        german_hits = [token for token in tokens if token in GERMAN_FUNCTION_WORDS]
        if len(english_hits) >= 3 and not german_hits:
            return "German spoken slot is an English clause rather than German with permitted commerce loanwords"
    elif market == "US":
        if any(phrase in normalized for phrase in HIGH_CONFIDENCE_GERMAN_PHRASES):
            return "American-English spoken slot contains a high-confidence German phrase"
        german_hits = [token for token in tokens if token in GERMAN_FUNCTION_WORDS]
        english_hits = [token for token in tokens if token in ENGLISH_FUNCTION_WORDS]
        if len(german_hits) >= 3 and not english_hits:
            return "American-English spoken slot is a German clause"
    return None


def setup_groups(beats: Iterable[Any]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        setup_id = beat.get("camera_setup_id")
        if not groups or groups[-1][-1].get("camera_setup_id") != setup_id:
            groups.append([beat])
        else:
            groups[-1].append(beat)
    return groups


def macro_phase(beat: dict[str, Any]) -> str:
    purpose = beat.get("purpose")
    try:
        start = Decimal(str(beat.get("start_seconds")))
    except (InvalidOperation, TypeError, ValueError):
        start = Decimal("0")
    if purpose == "cta" or start >= Decimal("10"):
        return "close/detail conviction"
    if start >= Decimal("4"):
        return "animated proof"
    return "recognition/result hook"


def cut_block_issues(beats: Any, duration_seconds: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(beats, list):
        return []
    try:
        is_default = Decimal(str(duration_seconds)) == Decimal("15")
    except (InvalidOperation, TypeError, ValueError):
        is_default = False
    if not is_default:
        return []
    issues: list[dict[str, Any]] = []
    groups = setup_groups(beats)
    if not 3 <= len(groups) <= 4:
        issues.append(_issue(
            "CUT_BLOCK_DENSITY_INVALID", path,
            "a default 15-second schema 1.4 timeline requires three or four contiguous camera-setup cut blocks",
            minimum=3, maximum=4, actual=len(groups),
        ))
    run_ids = [str(group[0].get("camera_setup_id")) for group in groups if group]
    repeated = sorted({setup_id for setup_id in run_ids if run_ids.count(setup_id) > 1})
    if repeated:
        issues.append(_issue(
            "CUT_BLOCK_SEQUENCE_INVALID", path,
            "a camera_setup_id may form only one contiguous cut block; A-B-A reuse is invalid",
            reused_setup_ids=repeated,
        ))
    crossing = [
        str(group[0].get("camera_setup_id"))
        for group in groups if len({macro_phase(beat) for beat in group}) > 1
    ]
    if crossing:
        issues.append(_issue(
            "CUT_BLOCK_SEQUENCE_INVALID", path,
            "one camera-setup run may not cross macro performance phases",
            crossing_setup_ids=crossing,
        ))
    return issues


def text_contains_any(value: Any, terms: Iterable[str]) -> bool:
    normalized = _norm(value)
    return any(_norm(term) in normalized for term in terms)


def meta_leaks(values: Iterable[tuple[str, Any]], forbidden_tokens: Iterable[Any] = ()) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    forbidden = [str(token) for token in forbidden_tokens if token is not None and str(token)]
    for path, value in values:
        if not isinstance(value, str) or not value:
            continue
        matches: list[str] = []
        if URL_PATTERN.search(value):
            matches.append("URL")
        if META_PATTERN.search(value):
            matches.append("internal metadata")
        matches.extend(token for token in forbidden if token in value)
        if matches:
            issues.append(_issue(
                "PROMPT_META_LEAK", path,
                "outbound generation text contains internal analysis, IDs, URLs, review text, or structured metadata",
                matches=sorted(set(matches)),
            ))
    return issues


def reference_contract_v6(reference_ids: list[str], reference_map: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "Use @Image1 only for the fixed creator's face, skin tone, ethnicity, hair, age presentation, and body identity; never use it as garment evidence."
    ]
    for reference_id in reference_ids:
        matches = reference_map.get(reference_id, [])
        if len(matches) != 1:
            continue
        tag = str(matches[0].get("interface_tag") or "<missing garment reference>")
        lines.append(
            f"Use {tag} only for the garment's exact color, silhouette, neckline, sleeve construction, hem, seams, "
            "texture scale, thickness, opacity, and drape. It contains no usable human identity. Do not copy any "
            "person, anatomy, jewelry, manicure, tattoo, pose, white triptych layout, divider, repeated body, camera, "
            "environment, text, or watermark from it. Only the @Image1 creator wears this garment in one coherent real scene."
        )
    return "\n".join(lines)


def _inline(value: Any) -> str:
    # Canonical fields may use snake_case internally, but the outbound prompt
    # must read as direction rather than expose implementation enum labels.
    return " ".join(
        str(value or "").replace("_", " ").replace("\r", " ").replace("\n", " ").split()
    )


def _time_text(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return _inline(value)
    if number == number.to_integral():
        return str(int(number))
    return format(number.normalize(), "f")


def _spoken_instruction(beat: dict[str, Any], profile: dict[str, Any]) -> str:
    line = json.dumps(beat.get("spoken_line"), ensure_ascii=False)
    language = profile.get("spoken_language_name", "the selected market language")
    if beat.get("speech_mode") == "on_camera_dialogue":
        timing = beat.get("sequential_live_proof")
        if isinstance(timing, dict):
            def window_text(field: str) -> str:
                value = timing.get(field)
                if not isinstance(value, list) or len(value) != 2:
                    return "invalid timing"
                try:
                    if any(not Decimal(str(number)).is_finite() for number in value):
                        return "invalid timing"
                except (InvalidOperation, TypeError, ValueError):
                    return "invalid timing"
                return f"{_time_text(value[0])}-{_time_text(value[1])}s"

            anchors = timing.get("speech_hand_anchors") if isinstance(timing.get("speech_hand_anchors"), dict) else {}
            instruction = (
                f"Timed action before live speech: {window_text('action_window_seconds')} perform only the declared single proof action in silence, with no speaking lip motion. "
                f"{window_text('settle_window_seconds')} stop the action and let the garment settle in silence. "
                f"Only during {window_text('speech_window_seconds')} the same creator says {line} in {language}; "
                "the mouth is visible and matches every word exactly, the stable camera is unchanged, the torso is still apart from natural micro-expression, and neither hand moves. "
                f"Keep the left hand {_inline(anchors.get('left'))} and the right hand {_inline(anchors.get('right'))}. "
                f"Throughout speech keep this settled proof endpoint readable and unobstructed: {_inline(timing.get('speech_endpoint'))}. "
                "Do not repeat the action while speaking and do not add off-screen narration."
            )
            if timing.get("claim_scope") == "visible_loose_allowance":
                instruction += (
                    f" Move only existing loose garment allowance by at most {_inline(timing.get('allowance_displacement_cm'))} cm, then fully release it before settling. "
                    "Preserve yarn length and stripe spacing; this demonstrates visible fit space only, never fabric stretch, elasticity, or performance."
                )
            return instruction
        return f"The creator says {line} in {language}; the mouth is visible and matches every word exactly."
    if beat.get("speech_mode") == "offscreen_voiceover":
        return f"Off-screen {language} voiceover says {line}; keep the mouth out of frame and do not animate lip-sync."
    return "No speech in this beat."


def _camera_instruction(mode: str) -> str:
    return {
        "fixed_phone": (
            "Use a fixed phone camera within each cut; reframe only at an intentional cut, and keep dialogue, detail proof, and the close locked or subtly moving."
        ),
        "creator_handheld": (
            "The creator operates the phone throughout each continuous handheld cut; the filming hand keeps ownership of it while the free hand proves one garment target at a time."
        ),
        "friend_handheld": (
            "A friend operates the handheld phone with restrained natural breathing; the creator never holds the filming phone, and detail or dialogue shots settle before the proof."
        ),
    }.get(mode, "Use one coherent phone-camera treatment.")


def _music_instruction(mode: str) -> str:
    if mode == "low_non_lyrical":
        return "Use quiet non-lyrical background music below the speech, plus natural location ambience and action sounds; no sung vocals or lyrics."
    return "Use natural location ambience and action sounds only; no music."


def _close_instruction(controls: dict[str, Any]) -> str:
    verdict = controls.get("verdict_mode")
    commerce = controls.get("commerce_cta_mode")
    parts: list[str] = []
    if verdict == "soft_verdict":
        parts.append("Finish with a sincere low-pressure personal judgment")
    elif verdict == "ownership_verdict":
        parts.append("Finish with a believable first-person keep-or-wear verdict")
    if commerce == "light_link":
        parts.append("add one brief human-delivered link cue with no graphic aid")
    elif commerce == "evidence_backed_promo":
        parts.append("mention only the separately verified promotion and use no graphic aid")
    return "; ".join(parts) + "."


def _screen_instruction(profile: dict[str, Any], duration: Any) -> str:
    stats = profile.get("fit_stats_text", "")
    return (
        f"Keep exactly one persistent upper-left fit-stats line, {json.dumps(stats, ensure_ascii=False)}, from 0-{_time_text(duration)}s. "
        "Do not add a Model label. Keep it clear of the face, hands, body, garment, and every proof action. "
        "Use clean white type with a subtle dark outline or translucent dark strip. The screen stays clean otherwise: "
        "no subtitles, watermark, arrows, stickers, icons, badges, price cards, buttons, or interface graphics."
    )


def _screen_instruction_v7(
    profile: dict[str, Any], duration: Any, global_deadlines: dict[str, Any],
) -> str:
    policy = global_deadlines.get("screen_text_policy")
    if policy == "no_generated_text":
        return (
            "Generate no text anywhere in the frame: no subtitles, translations, letters, numbers, prices, product names, links, "
            "shopping-cart graphics, platform interface, stickers, logos, labels, barcodes, or watermarks."
        )
    allowed = global_deadlines.get("allowed_screen_text")
    stats = allowed[0] if isinstance(allowed, list) and len(allowed) == 1 else profile.get("fit_stats_text", "")
    return (
        f"Keep exactly one persistent upper-left fit-stats line, {json.dumps(stats, ensure_ascii=False)}, from 0-{_time_text(duration)}s. "
        "Do not add a Model label. Keep it clear of the face, hands, body, garment, and every proof action. "
        "Use clean white type with a subtle dark outline or translucent dark strip. The screen stays clean otherwise: "
        "no subtitles, translations, watermark, arrows, stickers, icons, badges, price cards, product names, links, buttons, or interface graphics."
    )


def _deadline_list_text(values: Any) -> str:
    if not isinstance(values, list):
        return "none beyond the stated rules"
    return ", ".join(_inline(value) for value in values if _inline(value))


def _global_deadline_instruction(
    profile: dict[str, Any], global_deadlines: Any,
) -> str:
    layer = global_deadlines if isinstance(global_deadlines, dict) else {}
    return (
        "GLOBAL NON-NEGOTIABLES: "
        f"deliver {_inline(layer.get('platform'))} in {_inline(layer.get('aspect_ratio'))}, "
        f"exactly {_time_text(layer.get('duration_seconds'))} seconds at {_inline(layer.get('resolution'))} and "
        f"{_inline(layer.get('frame_rate_fps'))} fps. "
        f"Capture quality: {_inline(layer.get('capture_quality_lock'))}. "
        f"Camera behavior: {_inline(layer.get('camera_behavior_lock'))}. "
        f"Lens and depth: {_inline(layer.get('lens_depth_lock'))}. "
        f"Post-processing: {_inline(layer.get('postprocessing_lock'))}. "
        f"Audio capture: {_inline(layer.get('audio_capture_lock'))}. "
        + _camera_instruction(str(layer.get("camera_mode"))) + " "
        + _music_instruction(str(layer.get("music_mode"))) + " "
        + _screen_instruction_v7(profile, layer.get("duration_seconds"), layer)
    )


def _garment_signature_instruction(signature: Any) -> str:
    if not isinstance(signature, dict):
        return "the exact declared garment construction"
    ordered = (
        "category", "silhouette", "neckline", "sleeve", "length", "closure",
        "seams_panels", "texture_scale", "thickness", "opacity", "drape",
    )
    return "; ".join(
        f"{_inline(field)} {_inline(signature.get(field))}" for field in ordered
    )


def _asset_deadline_instruction(asset_deadlines: Any) -> str:
    layer = asset_deadlines if isinstance(asset_deadlines, dict) else {}
    strategy = layer.get("scene_strategy") if isinstance(layer.get("scene_strategy"), dict) else {}
    return (
        "SUBJECT, GARMENT, SCENE, LIGHT, AND SOUND NON-NEGOTIABLES: "
        f"Creator identity stays {_inline(layer.get('creator_identity'))}. "
        f"Subject appearance: {_inline(layer.get('subject_appearance_lock'))}. "
        f"Garment identity stays {_inline(layer.get('garment_identity'))}, in exact {_inline(layer.get('color_name'))}; "
        f"construction signature: {_garment_signature_instruction(layer.get('garment_signature'))}. "
        f"Garment structure: {_inline(layer.get('garment_structure_lock'))}. "
        f"Fabric physics: {_inline(layer.get('fabric_physics_lock'))}. "
        f"Outfit stays {_inline(layer.get('outfit'))}. Props and layering: {_inline(layer.get('outfit_prop_lock'))}. "
        f"Declared primary location is {_inline(strategy.get('primary_scene_description'))}. "
        f"Environment: {_inline(layer.get('scene_environment_lock'))}. "
        f"Lighting anchor stays {_inline(layer.get('lighting'))}; {_inline(layer.get('lighting_lock'))}. "
        f"Soundscape: {_inline(layer.get('soundscape_lock'))}. "
        f"Never allow {_deadline_list_text(layer.get('forbidden_asset_drift'))}."
    )


def _shot_deadline_instruction(shot_deadline: Any) -> str:
    layer = shot_deadline if isinstance(shot_deadline, dict) else {}
    return (
        "Shot non-negotiables: "
        f"action physics: {_inline(layer.get('action_physics_lock'))}; "
        f"anatomy: {_inline(layer.get('anatomy_lock'))}; "
        f"garment state: {_inline(layer.get('garment_state_lock'))}; "
        f"continuity handoff: {_inline(layer.get('continuity_lock'))}. "
        f"Forbidden outcomes: {_deadline_list_text(layer.get('forbidden_outcomes'))}."
    )


def _scene_instruction(profile: dict[str, Any], strategy: Any) -> str:
    if not isinstance(strategy, dict):
        return "Scene and occasion: use one evidence-appropriate real wearing location and keep it coherent."
    primary = json.dumps(str(strategy.get("primary_scene_description") or "the selected real-use location"), ensure_ascii=False)
    occasion = json.dumps(str(strategy.get("occasion") or "the selected wearing occasion"), ensure_ascii=False)
    question = json.dumps(str(strategy.get("buyer_styling_question") or "the buyer's outfit question"), ensure_ascii=False)
    outfit = json.dumps(str(strategy.get("outfit_answer") or "the selected complete outfit"), ensure_ascii=False)
    scene_role = _inline(strategy.get("scene_role"))
    video_form = _inline(strategy.get("video_form"))
    treatment = str(profile.get("scene_treatment") or "Keep the location credible and garment-led.")
    if strategy.get("location_plan") == "primary_plus_proof_cut":
        proof = json.dumps(str(strategy.get("proof_scene_description") or "the declared proof location"), ensure_ascii=False)
        continuity = (
            f"Use {proof} only for the opening proof, make one intentional transition to {primary}, then stay in the primary location through the final result."
        )
    else:
        continuity = f"Keep every cut inside {primary} as one physically coherent location."
    bedroom_policy = strategy.get("bedroom_policy")
    if bedroom_policy == "excluded":
        private_control = "Do not substitute a bedroom, closet, bathroom mirror, or generic home interior."
    elif bedroom_policy == "proof_only":
        private_control = "The private interior is proof-only and may occupy no more than the first two timed moments."
    elif bedroom_policy == "primary_justified":
        private_control = "The private primary scene serves only the declared fit, detail, or multi-styling proof; never treat it as a generic default."
    else:
        private_control = "The private primary scene is the truthful homewear use case."
    return (
        f"Scene and occasion sale: use {primary} for {occasion}. Its sales job is {scene_role}; answer the buyer's styling question {question} "
        f"with this complete outfit formula: {outfit}. Shoot it as a {video_form}. {continuity} {private_control} {treatment}"
    )


def serialize_prompt_v6(
    reference_contract: str,
    beats: list[Any],
    reference_map: dict[str, list[dict[str, Any]]],
    batch_key: Any,
    generation_controls: Any,
) -> str:
    """Serialize validated schema 1.4 data into deterministic natural v6 text."""
    batch_key = batch_key if isinstance(batch_key, dict) else {}
    controls = generation_controls if isinstance(generation_controls, dict) else {}
    profile = MARKET_PROFILES.get(str(batch_key.get("market_prompt_profile_id")), {})
    shell = controls.get("prompt_shell_mode")

    # Identity and garment role locks must lead the prompt so later market and
    # performance language cannot blur what each input image is allowed to do.
    lines = list(reference_contract.splitlines())
    lines.append(str(profile.get("profile_opening") or "Market-specific creator direction."))

    technical = (
        f"Format and control: {_inline(batch_key.get('platform'))}, {_inline(batch_key.get('aspect_ratio'))}, "
        f"{_time_text(batch_key.get('duration_seconds'))} seconds, {_inline(batch_key.get('resolution'))}. "
        + _camera_instruction(str(controls.get("camera_mode"))) + " "
        + _music_instruction(str(controls.get("music_mode"))) + " "
        + _screen_instruction(profile, batch_key.get("duration_seconds"))
    )
    if shell == "us_compact_storyboard":
        lines.append(
            f"Compact US storyboard: {_inline(batch_key.get('platform'))}, {_inline(batch_key.get('aspect_ratio'))}, "
            f"{_time_text(batch_key.get('duration_seconds'))} seconds, {_inline(batch_key.get('resolution'))}; "
            "authentic phone-shot friend share; show the garment immediately, let each camera setup prove one buyer-relevant result, and keep one coherent creator, outfit, scene, and physical light. "
            + _camera_instruction(str(controls.get("camera_mode"))) + " "
            + _music_instruction(str(controls.get("music_mode"))) + " "
            + _screen_instruction(profile, batch_key.get("duration_seconds"))
        )
    elif shell == "de_performance_script":
        lines.append(
            "German performance shell: keep every spoken word German and naturally synchronized when the mouth is visible; use a stable simple action for live speech and move detail, walking, turning, or two-hand proof to off-screen voiceover. "
            + technical
        )
    else:
        lines.append(technical)

    lines.append(_scene_instruction(profile, controls.get("scene_strategy")))

    lines.append(
        "Performance flow: recognition, animated proof, then close-detail conviction. Use three or four contiguous camera setup blocks and five to seven sequential timed moments. "
        "Each claim names one garment part or effect, performs one matching action, reaches one readable proof endpoint, and hands that physical state into the next beat. "
        "Use exactly two naturally connected arms and hands; default to one active demonstration hand. "
        + _close_instruction(controls)
    )

    for cut_number, group in enumerate(setup_groups(beats), start=1):
        if not group:
            continue
        start = _time_text(group[0].get("start_seconds"))
        end = _time_text(group[-1].get("end_seconds"))
        phase = macro_phase(group[0])
        lines.append(f"Cut {cut_number} ({start}-{end}s, {phase}):")
        for beat in group:
            beat_start = _time_text(beat.get("start_seconds"))
            beat_end = _time_text(beat.get("end_seconds"))
            matches = reference_map.get(beat.get("reference_binding"), [])
            tag = str(matches[0].get("interface_tag")) if len(matches) == 1 else "the garment reference"
            endpoint = _inline(beat.get("proof_endpoint")) or _inline(beat.get("visible_endpoint"))
            styling = _inline(beat.get("styling_point"))
            expression = _inline(beat.get("micro_expression"))
            hand_plan = beat.get("hand_plan") if isinstance(beat.get("hand_plan"), dict) else {}
            left = hand_plan.get("left") if isinstance(hand_plan.get("left"), dict) else {}
            right = hand_plan.get("right") if isinstance(hand_plan.get("right"), dict) else {}
            details = (
                f"{beat_start}-{beat_end}s: {_inline(beat.get('actor'))} wears {tag} in {_inline(beat.get('scene'))}. "
                f"Frame {_inline(beat.get('framing'))}. {_inline(beat.get('core_action'))}; the readable proof resolves as {endpoint}. "
                f"The left hand starts {_inline(left.get('start_anchor'))}, {_inline(left.get('action'))}, and ends {_inline(left.get('end_anchor'))}; "
                f"the right hand starts {_inline(right.get('start_anchor'))}, {_inline(right.get('action'))}, and ends {_inline(right.get('end_anchor'))}. "
                f"Carry gaze, weight, hand occupancy, props, and garment state forward. Camera: {_inline(beat.get('camera_motion'))}. "
                f"Light: {_inline(beat.get('lighting'))}."
            )
            if styling:
                details += f" Styling result: {styling}."
            if expression:
                details += f" Expression and delivery: {expression}."
            details += " " + _spoken_instruction(beat, profile)
            sound = _inline(beat.get("audio")) or "natural room tone"
            details += f" Local sound: {sound}."
            lines.append(details)
    return "\n".join(lines)


def serialize_prompt_v7(
    reference_contract: str,
    beats: list[Any],
    reference_map: dict[str, list[dict[str, Any]]],
    batch_key: Any,
    generation_controls: Any,
    three_layer_deadlines: Any,
) -> str:
    """Serialize current schema 1.4 data with three explicit deadline layers."""
    batch_key = batch_key if isinstance(batch_key, dict) else {}
    controls = generation_controls if isinstance(generation_controls, dict) else {}
    deadlines = three_layer_deadlines if isinstance(three_layer_deadlines, dict) else {}
    profile = MARKET_PROFILES.get(str(batch_key.get("market_prompt_profile_id")), {})
    shell = controls.get("prompt_shell_mode")
    global_deadlines = deadlines.get("global_deadlines") if isinstance(deadlines.get("global_deadlines"), dict) else {}
    asset_deadlines = deadlines.get("asset_deadlines") if isinstance(deadlines.get("asset_deadlines"), dict) else {}
    shot_deadlines = deadlines.get("shot_deadlines") if isinstance(deadlines.get("shot_deadlines"), list) else []
    shot_by_beat = {
        str(item.get("beat_id")): item for item in shot_deadlines
        if isinstance(item, dict) and isinstance(item.get("beat_id"), str)
    }

    lines = list(reference_contract.splitlines())
    lines.append(_global_deadline_instruction(profile, global_deadlines))
    lines.append(_asset_deadline_instruction(asset_deadlines))
    lines.append(str(profile.get("profile_opening") or "Market-specific creator direction."))

    if shell == "us_compact_storyboard":
        lines.append(
            "Compact US storyboard: authentic phone-shot friend share; show the garment immediately, let each camera setup prove one buyer-relevant result, "
            "and keep one coherent creator, outfit, declared scene plan, physical light direction, and live sound world."
        )
    elif shell == "de_performance_script":
        if any(isinstance(beat, dict) and "sequential_live_proof" in beat for beat in beats):
            lines.append(
                "German performance shell: keep every spoken word German and naturally synchronized with the visible creator. "
                "For explicitly timed sequential proofs, complete the declared silent action and silent settling window first, then speak with still hands and a readable settled endpoint; never combine complex action with live speech. "
                "Other live proof remains a stable simple gesture."
            )
        else:
            lines.append(
                "German performance shell: keep every spoken word German and naturally synchronized when the mouth is visible; use a stable simple action for live speech "
                "and move detail, walking, turning, or two-hand proof to off-screen voiceover."
            )
    else:
        lines.append(
            "US technical shell: authentic phone-shot UGC with immediate garment visibility, readable physical proof, and restrained natural performance."
        )

    lines.append(_scene_instruction(profile, controls.get("scene_strategy")))
    lines.append(
        "Performance flow: recognition, animated proof, then close-detail conviction. Use three or four contiguous camera setup blocks and five to seven sequential timed moments. "
        "Each claim names one garment part or effect, performs one matching action, reaches one readable proof endpoint, and hands that physical state into the next beat. "
        "Use exactly two naturally connected arms and hands; default to one active demonstration hand. "
        + _close_instruction(controls)
    )

    for cut_number, group in enumerate(setup_groups(beats), start=1):
        if not group:
            continue
        start = _time_text(group[0].get("start_seconds"))
        end = _time_text(group[-1].get("end_seconds"))
        phase = macro_phase(group[0])
        lines.append(f"Cut {cut_number} ({start}-{end}s, {phase}):")
        for beat in group:
            beat_start = _time_text(beat.get("start_seconds"))
            beat_end = _time_text(beat.get("end_seconds"))
            matches = reference_map.get(beat.get("reference_binding"), [])
            tag = str(matches[0].get("interface_tag")) if len(matches) == 1 else "the garment reference"
            endpoint = _inline(beat.get("proof_endpoint")) or _inline(beat.get("visible_endpoint"))
            styling = _inline(beat.get("styling_point"))
            expression = _inline(beat.get("micro_expression"))
            hand_plan = beat.get("hand_plan") if isinstance(beat.get("hand_plan"), dict) else {}
            left = hand_plan.get("left") if isinstance(hand_plan.get("left"), dict) else {}
            right = hand_plan.get("right") if isinstance(hand_plan.get("right"), dict) else {}
            details = (
                f"{beat_start}-{beat_end}s: {_inline(beat.get('actor'))} wears {tag} in {_inline(beat.get('scene'))}. "
                f"Frame {_inline(beat.get('framing'))}. {_inline(beat.get('core_action'))}; the readable proof resolves as {endpoint}. "
                f"The left hand starts {_inline(left.get('start_anchor'))}, {_inline(left.get('action'))}, and ends {_inline(left.get('end_anchor'))}; "
                f"the right hand starts {_inline(right.get('start_anchor'))}, {_inline(right.get('action'))}, and ends {_inline(right.get('end_anchor'))}. "
                f"Carry gaze, weight, hand occupancy, props, and garment state forward. Camera: {_inline(beat.get('camera_motion'))}. "
                f"Light: {_inline(beat.get('lighting'))}."
            )
            if styling:
                details += f" Styling result: {styling}."
            if expression:
                details += f" Expression and delivery: {expression}."
            details += " " + _spoken_instruction(beat, profile)
            sound = _inline(beat.get("audio")) or "natural room tone"
            details += f" Local sound: {sound}."
            details += " " + _shot_deadline_instruction(shot_by_beat.get(str(beat.get("beat_id"))))
            lines.append(details)
    return "\n".join(lines)


__all__ = [
    "ALLOWED_MARKET_TUPLES",
    "CAMERA_MODES",
    "COMMERCE_CTA_MODES",
    "COMPLEX_PROOF_ACTIONS",
    "DEADLINE_CONTRACT_ID",
    "DE_LIVE_SIMPLE_ACTIONS",
    "DELIVERY_MODES",
    "EXTRA_SCREEN_TEXT_TERMS",
    "FIXED_CAMERA_TERMS",
    "GENERATION_CONTROL_FIELDS",
    "GLOBAL_DEADLINE_FIELDS",
    "GRAPHIC_CTA_TERMS",
    "HANDHELD_CAMERA_TERMS",
    "LYRICAL_MUSIC_TERMS",
    "MARKET_PROFILES",
    "MARKET_PROMPT_CONTRACT_ID",
    "MODEL_FIT_STATS_TEXT",
    "MUSIC_NEGATIVE_TERMS",
    "MUSIC_POSITIVE_TERMS",
    "PRIVATE_INTERIOR_SCENE_IDS",
    "PROMPT_SERIALIZER_ID",
    "QUALITY_CONTRACT_ID",
    "LEGACY_PROMPT_SERIALIZER_ID",
    "LEGACY_QUALITY_CONTRACT_ID",
    "ASSET_DEADLINE_FIELDS",
    "SHOT_DEADLINE_FIELDS",
    "THREE_LAYER_DEADLINE_FIELDS",
    "REQUIRED_FORBIDDEN_OVERLAYS",
    "REQUIRED_FORBIDDEN_ASSET_DRIFT",
    "REQUIRED_FORBIDDEN_SHOT_OUTCOMES",
    "SCHEMA_VERSION",
    "SCENE_IDS",
    "SCENE_STRATEGY_OPTIONAL_FIELDS",
    "SCENE_STRATEGY_REQUIRED_FIELDS",
    "VIDEO_FORMS",
    "canonical_sha256",
    "contains_han",
    "cut_block_issues",
    "fit_stats_text_for_batch",
    "language_issue",
    "meta_leaks",
    "reference_contract_v6",
    "serialize_prompt_v6",
    "serialize_prompt_v7",
    "setup_groups",
    "text_contains_any",
    "validate_generation_controls",
    "validate_three_layer_deadlines",
    "validate_market_tuple",
    "validate_scene_alignment",
    "validate_scene_projection",
    "validate_scene_strategy",
]
