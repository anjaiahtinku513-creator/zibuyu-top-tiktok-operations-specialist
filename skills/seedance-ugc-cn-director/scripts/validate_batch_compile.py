#!/usr/bin/env python3
"""Deterministic validator for same-SKU creative batch compilations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import itertools
import json
import math
import re
import sys
import tempfile
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


try:
    import market_prompt_contract as _market_contract
except ModuleNotFoundError:
    _market_contract_path = Path(__file__).with_name("market_prompt_contract.py")
    _market_contract_spec = importlib.util.spec_from_file_location(
        "zibuyu_market_prompt_contract", _market_contract_path,
    )
    if _market_contract_spec is None or _market_contract_spec.loader is None:
        raise RuntimeError(f"cannot load market prompt contract: {_market_contract_path}")
    _market_contract = importlib.util.module_from_spec(_market_contract_spec)
    _market_contract_spec.loader.exec_module(_market_contract)


VISUAL_FIELDS = (
    "purpose", "framing", "camera_motion", "scene", "lighting", "actor",
    "core_action", "action_target", "left_hand_state", "right_hand_state",
    "product_point", "styling_point", "product_visibility", "reference_binding", "micro_expression",
)
QUALITY_VISUAL_FIELDS = (
    "camera_setup_id", "camera_motivation", "light_motivation", "visible_endpoint",
    "lip_sync_required", "speech_mode", "mouth_visibility", "motion_budget",
)
MOTION_RICH_VISUAL_FIELDS = (
    "beat_role", "claim_proof_id", "proof_target", "proof_action_type",
    "product_part_id", "evidence_basis", "spoken_claim_ids", "hook_semantics", "posture_id", "hand_plan",
)
EVIDENCE_RICH_VISUAL_FIELDS = ("evidence_ids", "pain_point_id")
MARKET_RICH_VISUAL_FIELDS = ("spoken_language", "proof_endpoint", "scene_id")
REQUIRED_BASE_BEAT_FIELDS = (
    "framing", "camera_motion", "scene", "lighting", "actor", "core_action",
    "action_target", "left_hand_state", "right_hand_state", "product_point",
)
BEAT_PURPOSES = {"hook", "pain", "proof", "styling", "cta"}
PRODUCT_VISIBILITIES = {"full", "detail", "partial", "none"}
SCRIPT_FIELDS = ("beat_id", "spoken_line")
PROMPT_FIELDS = ("beat_id",) + VISUAL_FIELDS + ("spoken_line", "silence_reason", "spoken_intent_id", "audio")
QUALITY_PROMPT_FIELDS = (
    ("beat_id",) + VISUAL_FIELDS + QUALITY_VISUAL_FIELDS
    + ("spoken_line", "silence_reason", "spoken_intent_id", "audio")
)
MOTION_RICH_PROMPT_FIELDS = (
    ("beat_id",) + VISUAL_FIELDS + QUALITY_VISUAL_FIELDS + MOTION_RICH_VISUAL_FIELDS
    + ("spoken_line", "silence_reason", "spoken_intent_id", "audio")
)
EVIDENCE_RICH_PROMPT_FIELDS = (
    ("beat_id",) + VISUAL_FIELDS + QUALITY_VISUAL_FIELDS + MOTION_RICH_VISUAL_FIELDS
    + EVIDENCE_RICH_VISUAL_FIELDS + ("spoken_line", "silence_reason", "spoken_intent_id", "audio")
)
MARKET_RICH_PROMPT_FIELDS = EVIDENCE_RICH_PROMPT_FIELDS + MARKET_RICH_VISUAL_FIELDS
GARMENT_FIELDS = (
    "category", "silhouette", "neckline", "sleeve", "length", "closure",
    "seams_panels", "texture_scale", "thickness", "opacity", "drape",
)
COLOR_WORDS = {
    "black", "white", "blue", "red", "green", "pink", "beige", "brown",
    "gray", "grey", "purple", "yellow", "orange", "navy", "cream", "ivory",
    "khaki", "burgundy", "charcoal", "tan", "schwarz", "weiß", "weiss",
    "blau", "rot", "grün", "gruen", "rosa", "braun", "grau", "lila",
    "gelb", "marine", "creme", "黑色", "白色", "蓝色", "紅色", "红色",
    "绿色", "綠色", "粉色", "米色", "棕色", "咖色", "灰色", "紫色",
    "黄色", "黃色", "橙色", "藏青", "藏青色", "酒红", "酒紅", "卡其",
}
QUALITY_CONTRACT_ID_V11 = "zibuyu_ugc_quality_v1"
QUALITY_CONTRACT_ID_V12 = "zibuyu_ugc_quality_v2"
QUALITY_CONTRACT_ID_V14 = _market_contract.QUALITY_CONTRACT_ID
QUALITY_CONTRACT_ID_V14_LEGACY = _market_contract.LEGACY_QUALITY_CONTRACT_ID
PROMPT_V2_SERIALIZER_ID = "canonical_prompt_v2"
PROMPT_V3_SERIALIZER_ID = "canonical_prompt_v3"
PROMPT_V4_SERIALIZER_ID = "canonical_prompt_v4"
PROMPT_V5_SERIALIZER_ID = "canonical_prompt_v5"
PROMPT_V6_SERIALIZER_ID = _market_contract.LEGACY_PROMPT_SERIALIZER_ID
PROMPT_V7_SERIALIZER_ID = _market_contract.PROMPT_SERIALIZER_ID
INTERFACE_TAG_PATTERN = re.compile(r"@(?:图片|Image)[1-9][0-9]*")
V5_GARMENT_TAG_PATTERN = re.compile(r"@Image([2-9]|[1-9][0-9]+)")
LEGACY_BRAND_HASHTAG = "#Imily Bela"
HASHTAG_PATTERN = re.compile(r"#[^\s#]+")
POSITIVE_REPLACEMENT = "one_creator_same_garment_single_real_scene"
REQUIRED_TRANSFER = {
    "garment_identity", "exact_color", "silhouette", "neckline", "sleeve_construction",
    "hem", "seams", "texture_scale", "thickness", "opacity", "drape",
}
LEGACY_REQUIRED_NON_TRANSFER = {
    "white_background", "three_panel_layout", "panel_dividers", "repeated_bodies",
    "multiple_models", "reference_face_hair", "reference_pose", "reference_camera",
    "reference_environment", "embedded_text", "watermark",
}
REQUIRED_NON_TRANSFER = LEGACY_REQUIRED_NON_TRANSFER | {
    "reference_skin_tone", "reference_ethnicity", "reference_neck",
    "reference_chest_collarbone", "reference_shoulders", "reference_arms",
    "reference_hands_fingers", "reference_nails", "reference_tattoos",
    "reference_jewelry", "reference_body_shape",
}
ZERO_HUMAN_IDENTITY_AUDIT_VERSION = "zero_human_identity_pixels_v1"
ZERO_HUMAN_IDENTITY_INSPECTION_METHOD = "full_resolution_visual_inspection"
ZERO_HUMAN_IDENTITY_CUE_FIELDS = (
    "skin_present", "face_present", "hair_present", "neck_chest_collarbone_present",
    "shoulders_arms_wrists_present", "hands_fingers_nails_present",
    "tattoos_jewelry_present", "person_specific_body_shape_present",
)
FIXED_MODEL_IDENTITY_LINE = (
    "Reference 1 / @Image1 = fixed-model identity only: preserve the selected fixed creator's exact face, "
    "skin tone, ethnicity, hair, age presentation and body identity; do not use it as garment evidence."
)
DIRECTORIAL_VOICES = {"observational_naturalist", "intimate_minimalist", "graphic_formalist"}
SECONDARY_FIDELITY_SPENDS = {"visible_proof", "lip_sync", "natural_motion", "scene_readability"}
CONTINUITY_ANCHORS = ("creator_identity", "garment_identity", "outfit", "scene", "lighting")
SPEECH_MODES = {"on_camera_dialogue", "offscreen_voiceover", "none"}
MOUTH_VISIBILITIES = {"visible", "not_visible", "partial"}
MOTION_BUDGET_VALUES = {
    "camera": {"locked", "subtle", "active"},
    "performer": {"still", "micro", "simple", "active"},
    "active_hands": {"zero", "one", "two"},
}
BEAT_ROLES = {"context_hook", "reaction", "product_claim", "styling", "cta"}
EVIDENCE_BASES = {"visible_reference", "user_provided", "product_page", "verified_test"}
EVIDENCE_BASIS_SOURCE_KINDS = {
    "visible_reference": {"visible_reference", "amazon_customer_image"},
    "user_provided": {"user_provided", "garment_label"},
    "product_page": {
        "amazon_catalog_attribute", "amazon_seller_copy", "amazon_customer_review",
        "amazon_customer_image", "amazon_qa", "amazon_review_summary", "third_party_summary",
    },
    "verified_test": {"verified_test"},
}
INDEPENDENT_SOLUTION_SOURCE_KINDS = {
    "amazon_catalog_attribute", "amazon_customer_image", "visible_reference",
    "garment_label", "user_provided", "verified_test",
}
AMAZON_BASE_HOSTS = {
    "amazon.com", "amazon.ca", "amazon.com.mx", "amazon.com.br", "amazon.co.uk",
    "amazon.de", "amazon.fr", "amazon.it", "amazon.es", "amazon.nl", "amazon.se",
    "amazon.pl", "amazon.com.be", "amazon.com.tr", "amazon.in", "amazon.co.jp",
    "amazon.sg", "amazon.com.au", "amazon.ae", "amazon.sa",
}
ASSERTION_LEVELS = {"visible", "qualitative", "numeric"}
RESEARCH_CONTRACT_ID = "amazon_product_review_evidence_v1"
RESEARCH_STATUSES = {"complete", "partial", "blocked", "not_provided"}
COLLECTION_ROUTES = {"chrome", "public_web", "user_capture", "not_applicable"}
SOURCE_KINDS = {
    "amazon_catalog_attribute", "amazon_seller_copy", "amazon_customer_review",
    "amazon_customer_image", "amazon_qa", "amazon_review_summary", "third_party_summary",
    "visible_reference", "garment_label", "user_provided", "verified_test",
}
SOURCE_ROLES = {
    "product_fact", "seller_marketing", "buyer_experience", "visual_observation",
    "discovery_only", "verified_measurement",
}
SOURCE_KIND_ROLES = {
    "amazon_catalog_attribute": {"product_fact"},
    "amazon_seller_copy": {"seller_marketing"},
    "amazon_customer_review": {"buyer_experience"},
    "amazon_customer_image": {"visual_observation"},
    "amazon_qa": {"discovery_only"},
    "amazon_review_summary": {"discovery_only"},
    "third_party_summary": {"discovery_only"},
    "visible_reference": {"visual_observation"},
    "garment_label": {"product_fact"},
    "user_provided": {"product_fact", "buyer_experience", "visual_observation"},
    "verified_test": {"verified_measurement"},
}
VARIANT_SCOPES = {"exact_child", "parent_family", "sibling_child", "unknown", "not_applicable"}
ASSERTION_KINDS = {
    "visible_feature", "exact_composition", "fit_attribute", "stretch_attribute",
    "weight_attribute", "care_attribute", "seller_qualitative_claim", "buyer_experience",
    "buyer_visual_observation", "verified_performance",
}
CLAIM_MODES = {"direct", "qualified", "visual_only"}
EVIDENCE_USE_VALUES = {
    "claim_direct", "claim_qualified", "pain_context", "visual_only", "discovery_only", "blocked",
}
CONFLICT_STATUSES = {"clear", "conflicted", "mixed", "unverified"}
PAIN_BASES = {"review_theme", "direct_review", "user_provided", "visible_reference", "category_inference"}
FRAMING_CLASSES = {"detail_closeup", "chest_to_hem", "side_back", "full_fit", "styling"}
PROOF_ACTION_TYPES = {
    "point_trace", "touch_release", "pinch_release", "pull_release", "raise_arm",
    "smooth_release", "turn_settle", "walk_settle", "open_close", "pocket_use", "front_tuck", "style_adjust",
}
HANDS_REQUIRED = {"one", "two", "body"}
HAND_ACTIVITY = {"none", "left", "right", "both"}
POSTURE_IDS = {
    "relaxed_three_quarter", "weight_shift_left", "weight_shift_right",
    "side_relaxed", "seated_relaxed", "hands_only_detail",
    "mirror_selfie_relaxed", "mirror_step_back", "mirror_weight_shift", "mirror_close_detail",
}
HOOK_SEMANTICS = {"pain_question", "evidence_backed_contrast", "context"}
NEGATIVE_CONTRAST_TERMS = {
    "not", "isn't", "isnt", "aren't", "arent", "no ordinary", "no basic",
    "kein", "keine", "keinen", "keinem", "keiner", "nicht",
    "\u4e0d\u662f", "\u53ef\u4e0d\u662f", "\u5e76\u4e0d\u662f", "\u5e76\u975e", "\u522b\u628a",
}
FORMAL_VOICEOVER_TERMS = {
    "this garment features", "this garment offers", "this product features", "this product offers",
    "the garment is characterized by", "the product is characterized by", "it can be observed",
    "it is evident", "furthermore", "moreover", "in addition", "therefore",
    "dieses kleidungsst\u00fcck verf\u00fcgt \u00fcber", "dieses kleidungsstueck verfuegt ueber",
    "dieses kleidungsst\u00fcck bietet", "dieses kleidungsstueck bietet", "dar\u00fcber hinaus",
    "darueber hinaus", "des weiteren", "ferner", "somit", "es ist zu erkennen",
    "zeichnet sich durch", "\u672c\u4ea7\u54c1\u91c7\u7528", "\u8be5\u670d\u88c5\u5177\u5907", "\u6b64\u5916",
    "\u7efc\u4e0a", "\u7531\u6b64\u53ef\u89c1", "\u503c\u5f97\u4e00\u63d0\u7684\u662f",
}
GENERIC_DETAIL_TERMS = {
    "looks nice", "looks good", "is nice", "is great", "so cute", "really cute",
    "very beautiful", "easy to style", "easy to pair", "sieht gut aus", "ist sehr sch\u00f6n",
    "ist sehr schoen", "mega sch\u00f6n", "mega schoen", "leicht zu kombinieren",
    "\u5f88\u597d\u770b", "\u5f88\u6f02\u4eae", "\u5f88\u4e0d\u9519", "\u5f88\u767e\u642d", "\u5f88\u597d\u642d",
}
VOICEOVER_OPENER_PREFIXES = {
    "look at", "take a look", "here you can see", "now look", "now check", "you can see",
    "schau mal", "sieh dir", "guck mal", "hier siehst du", "jetzt schau",
    "\u4f60\u770b", "\u770b\u770b", "\u6765\u770b", "\u518d\u770b", "\u63a5\u7740\u770b",
}
PRODUCT_PART_IDS = {
    "neckline", "sleeve", "hem", "slit", "texture", "material_performance",
    "drape", "fit", "pocket", "closure", "styling",
}
PERFORMANCE_TERMS = {
    "stretch", "stretchy", "elastic", "elasticity", "dehnbar", "elastisch",
    "弹力", "弹性", "breathable", "breathability", "透气", "cooling", "凉感",
    "waterproof", "water resistant", "防水", "opaque", "opacity", "不透",
    "anti wrinkle", "wrinkle resistant", "抗皱",
}
STRETCH_TERMS = {
    "stretch", "stretchy", "elastic", "elasticity", "dehnbar", "elastisch", "弹力", "弹性",
}
PERFORMANCE_ACTION_TERMS = {
    "pull", "pulls", "pulled", "tug", "tugs", "stretch", "stretches", "extend", "extends",
    "拉伸", "拉扯", "扯开", "回弹",
}
EXTRA_LIMB_TERMS = {
    "third hand", "third arm", "extra hand", "extra arm", "additional hand", "additional arm",
    "another hand", "another arm", "helper hand", "helper arm", "free hand", "free arm",
    "supporting hand", "supporting arm", "other hand", "other arm", "spare hand", "spare arm",
    "three hands", "three arms", "第三只手", "第三条手臂", "辅助的手", "另一只手", "空闲手", "三只手", "三条手臂",
}
NEGATED_ACTION_TEXT_TERMS = {
    "does not", "do not", "did not", "not touch", "not pull", "not smooth", "not turn",
    "never", "without touching", "without pulling", "without moving", "intends to", "plans to", "prepares to",
    "appears to", "pretends to", "about to", "will touch", "will pull", "will smooth", "will turn",
    "untouched", "unpulled", "unmoved", "stays near", "leaves it untouched",
    "打算", "计划", "准备要", "假装", "将要", "不触摸", "不拉", "不抚平", "不转身", "没有动作",
}
MULTI_TARGET_SEPARATORS = re.compile(r"(?:\b(?:and|plus|und|et)\b|[&,/、，]|(?:与|和|及))", re.IGNORECASE)
PERFORMANCE_CLAIM_GROUPS = {
    "stretch": STRETCH_TERMS,
    "breathability": {"breathable", "breathability", "atmungsaktiv", "透气"},
    "cooling": {"cooling", "cool touch", "kühlend", "凉感"},
    "waterproof": {"waterproof", "water resistant", "wasserfest", "防水"},
    "opacity": {"opaque", "opacity", "blickdicht", "不透"},
    "wrinkle": {"anti wrinkle", "wrinkle resistant", "knitterarm", "抗皱"},
}
UNBOUND_GARMENT_CLAIM_TERMS = {
    "neckline", "collar", "v-neck", "sleeve", "sleeves", "cuff", "cuffs", "hem", "slit", "slits", "pocket", "pockets", "button", "buttons", "zipper", "closure",
    "texture", "fabric", "material", "drape", "coverage", "stitching", "seam", "领口", "衣领", "袖口", "袖子",
    "下摆", "开衩", "口袋", "纽扣", "拉链", "纹理", "面料", "材质", "垂坠", "遮盖", "走线",
    "ausschnitt", "kragen", "ärmel", "aermel", "bündchen", "buendchen", "saum", "schlitz", "schlitze",
    "tasche", "taschen", "knopf", "knöpfe", "knoepfe", "reißverschluss", "reissverschluss", "naht", "nähte", "naehte",
    "stoff", "material", "faltenwurf",
}
TARGET_ACTION_RULES = (
    ({"stretch", "elastic", "dehnbar", "elastisch", "弹力", "弹性"}, {"pull_release"}),
    ({"pocket", "口袋"}, {"pocket_use"}),
    ({"button", "zipper", "zip", "closure", "纽扣", "拉链", "开合"}, {"open_close"}),
    ({"neckline", "collar", "v-neck", "领口", "衣领"}, {"point_trace", "touch_release"}),
    ({"sleeve", "cuff", "arm coverage", "袖", "袖口", "手臂遮盖"}, {"point_trace", "touch_release", "raise_arm"}),
    ({"hem", "slit", "下摆", "开衩"}, {"point_trace", "touch_release", "smooth_release", "turn_settle"}),
    ({"texture", "fabric", "material", "soft", "纹理", "面料", "材质", "柔软"}, {"touch_release", "pinch_release", "smooth_release"}),
    ({"drape", "fit", "coverage", "side", "back", "垂坠", "版型", "遮盖", "侧面", "后摆"}, {"smooth_release", "turn_settle", "walk_settle", "raise_arm"}),
    ({"styling", "outfit", "proportion", "穿搭", "造型", "比例"}, {"front_tuck", "style_adjust", "turn_settle", "walk_settle"}),
)
TARGET_FRAMING_RULES = (
    ({"stretch", "elastic", "dehnbar", "elastisch", "弹力", "弹性"}, {"detail_closeup"}),
    ({"texture", "fabric", "material", "soft", "纹理", "面料", "材质", "柔软"}, {"detail_closeup"}),
    ({"pocket", "button", "zipper", "zip", "closure", "口袋", "纽扣", "拉链", "开合"}, {"detail_closeup"}),
    ({"neckline", "collar", "v-neck", "领口", "衣领"}, {"detail_closeup"}),
    ({"sleeve", "cuff", "arm coverage", "袖", "袖口", "手臂遮盖"}, {"detail_closeup"}),
    ({"hem", "slit", "下摆", "开衩"}, {"detail_closeup", "chest_to_hem", "side_back"}),
    ({"drape", "fit", "coverage", "side", "back", "垂坠", "版型", "遮盖", "侧面", "后摆"}, {"chest_to_hem", "side_back", "full_fit"}),
    ({"styling", "outfit", "proportion", "穿搭", "造型", "比例"}, {"styling", "full_fit"}),
)
FRAMING_TEXT_TERMS = {
    "detail_closeup": {"close-up", "closeup", "detail", "macro", "特写", "近景"},
    "chest_to_hem": {"chest", "waist", "torso", "hem", "胸", "腰", "躯干", "下摆"},
    "side_back": {"side", "back", "profile", "侧", "背", "后"},
    "full_fit": {"full", "full-body", "head-to-toe", "全身", "整体"},
    "styling": {"styling", "outfit", "look", "穿搭", "造型", "全身"},
}
ACTION_TEXT_TERMS = {
    "point_trace": {"trace", "traces", "point", "points", "indicate", "指向", "沿着", "描过"},
    "touch_release": {"touch", "touches", "tap", "taps", "轻触", "触摸", "点触"},
    "pinch_release": {"pinch", "pinches", "rub", "rubs", "捏", "揉", "摩擦"},
    "pull_release": PERFORMANCE_ACTION_TERMS,
    "raise_arm": {"raise arm", "raises arm", "lift arm", "lifts arm", "抬臂", "抬起手臂"},
    "smooth_release": {"smooth", "smooths", "flatten", "flattens", "顺平", "抚平"},
    "turn_settle": {"quarter-turn", "half-turn", "slow turn", "turns the body", "rotate", "rotates", "pivot", "转身", "旋转"},
    "open_close": {"opens button", "closes button", "opens zipper", "closes zipper", "zips", "unzips", "buttons", "unbuttons", "打开纽扣", "合上纽扣", "拉开拉链", "拉合拉链", "扣上"},
    "pocket_use": {"pocket", "inserts hand", "slides hand", "口袋", "插入口袋"},
    "front_tuck": {"front tuck", "tucks", "tuck", "塞衣角", "前塞"},
    "style_adjust": {"adjust", "adjusts", "adds accessory", "整理配饰", "调整配饰"},
    "walk_settle": {"walks", "takes one step", "steps forward", "steps back", "walks closer", "walks back", "迈步", "走动", "走近", "后退"},
    "prop_move": {"waves a bag", "lifts a bag", "holds a bag", "carries a bag", "holds a prop", "carries a prop", "picks up", "puts down", "挥动包", "拿着包", "携带包", "拿起道具", "放下道具"},
    "body_bend": {"bends", "leans down", "crouches", "弯腰", "下蹲"},
    "head_nod": {"nods", "shakes head", "点头", "摇头"},
}
STATIC_ACTION_TERMS = {
    "hold still", "holds still", "holding still", "stand still", "stands still",
    "standing still", "completely still", "completely motionless", "continues pose",
    "maintains pose", "remains posed", "keeps pose", "holds pose", "at attention", "military stance", "feet together arms straight",
    "保持不动", "静止站立", "原地站立", "立正", "双脚并拢双手贴腿",
}
SELF_TEST_BATCH_KEY = {
    "model_preset": "美1", "market": "US", "voiceover_language": "en-US",
    "platform": "TikTok", "duration_seconds": 15, "aspect_ratio": "9:16",
    "resolution": "1080x1920", "frame_rate_fps": 60,
}
PRIMARY_ORDER = (
    "INPUT_READ_ERROR", "INVALID_JSON", "ROOT_TYPE_INVALID", "SCHEMA_VERSION_INVALID",
    "QUALITY_CONTRACT_INVALID", "VARIANTS_INVALID",
    "COMPILE_MODE_INVALID", "COMPILE_MODE_VARIANT_COUNT", "BATCH_DURATION_INVALID", "BATCH_NOT_SAME_SKU",
    "MARKET_PROFILE_INVALID", "MARKET_LANGUAGE_MISMATCH", "MODEL_PRESET_MARKET_MISMATCH",
    "VARIANT_TYPE_INVALID", "VARIANT_ID_MISSING", "DUPLICATE_VARIANT_ID",
    "TIMELINE_INVALID", "TIMELINE_ID_MISSING", "DUPLICATE_TIMELINE_ID",
    "THREE_VIEW_QC_FAILED", "MISSING_VARIANT_REFERENCE", "REFERENCE_TAG_INVALID",
    "REFERENCE_TRANSFER_CONTRACT_INVALID", "QUALITY_PLAN_MISSING",
    "RESEARCH_BUNDLE_INVALID", "REVIEW_EVIDENCE_INVALID", "BUYER_PAIN_MAP_INVALID",
    "CLAIMS_REGISTRY_INVALID", "CONTRAST_HOOK_PROOF_INVALID", "PROOF_PLAN_MISSING",
    "CLAIM_EVIDENCE_INVALID", "PAIN_PROOF_BINDING_INVALID",
    "UNSUPPORTED_PERFORMANCE_DEMO", "DIRECTING_INTENT_INVALID",
    "DIRECTORIAL_VOICE_INVALID", "FIDELITY_ALLOCATION_INVALID", "DIRECTING_INTENT_DRIFT",
    "QUALITY_PLAN_HASH_MISMATCH", "DEADLINE_CONTRACT_INVALID", "DEADLINE_LOCK_MISSING",
    "GLOBAL_DEADLINE_INVALID", "GLOBAL_DEADLINE_DRIFT", "ASSET_DEADLINE_INVALID",
    "ASSET_DEADLINE_DRIFT", "SHOT_DEADLINE_INVALID", "SHOT_DEADLINE_DRIFT",
    "DEADLINE_HASH_MISMATCH", "COLOR_VARIANT_INVARIANT_DRIFT", "TIMELINE_INTERVAL_INVALID",
    "TIMELINE_GAP", "TIMELINE_OVERLAP", "DURATION_MISMATCH", "DUPLICATE_BEAT_ID",
    "SUBMISSION_FIELD_MISSING", "STALE_TIMELINE_VERSION", "VARIANT_REFERENCE_MISMATCH", "TIMELINE_MISSING_BEAT",
    "TIMELINE_EXTRA_BEAT", "TIMELINE_ORDER_DRIFT", "TIMELINE_TIME_DRIFT",
    "BEAT_QUALITY_FIELD_MISSING", "BEAT_ENUM_INVALID", "SPEECH_MODE_INVALID", "SHOT_DENSITY_EXCEEDED",
    "LANGUAGE_GATE_FAILED", "VOICEOVER_REVIEW_DRIFT", "DELIVERY_MODE_CONFLICT",
    "CAMERA_MODE_CONFLICT", "MUSIC_MODE_CONFLICT", "CUT_BLOCK_DENSITY_INVALID", "CUT_BLOCK_SEQUENCE_INVALID",
    "MOTION_BUDGET_INVALID", "CLAIM_PROOF_FRAMING_MISMATCH", "STATIC_SELLING_BEAT",
    "CLAIM_PROOF_ACTION_MISMATCH", "CLAIM_PROOF_ENDPOINT_MISMATCH",
    "ACTION_COVERAGE_INSUFFICIENT", "PERFORMANCE_ARC_INVALID", "VOICEOVER_STYLE_TOO_FORMAL", "VOICEOVER_DETAIL_TOO_GENERIC",
    "VOICEOVER_STYLE_TOO_REPETITIVE", "VOICEOVER_PACING_INVALID", "FLAT_PERFORMANCE_RISK", "HAND_PLAN_INVALID",
    "HAND_ACTIVITY_MISMATCH", "ANATOMY_RISK_OVERLOAD", "STIFF_POSTURE_RISK",
    "ACTION_SEQUENCE_OVERLOAD", "ACTION_TRANSITION_INVALID",
    "LIPSYNC_COMPLEXITY_OVERLOAD", "DETAIL_SHOT_COMPLEXITY_OVERLOAD", "CTA_COMPLEXITY_OVERLOAD",
    "CTA_POLICY_INVALID", "CTA_POSITION_INVALID", "SCREEN_TEXT_POLICY_INVALID", "PROMPT_META_LEAK",
    "TIMELINE_VO_DRIFT", "TIMELINE_ACTION_DRIFT", "PROJECTION_INVALID",
    "PROJECTION_FIELD_MISSING", "PROMPT_TEXT_MISSING", "PROMPT_QUALITY_BLOCK_MISSING",
    "CANONICAL_BEATS_HASH_MISMATCH", "PROMPT_V2_FULL_JSON_LEAK", "PROMPT_SERIALIZER_MISMATCH",
    "PROMPT_HASH_MISMATCH",
    "CAPTION_INVALID", "CAPTION_HASHTAG_COUNT", "CAPTION_HASHTAG_INVALID",
    "DIFF_COLOR_ONLY", "DIFF_THRESHOLD_NOT_MET", "DIFF_PAIR_COLLISION",
    "CAPTION_COLOR_ONLY", "HASHTAG_PAIR_COLLISION", "PROMPT_REUSED_ACROSS_VARIANTS",
    "CREATIVE_DELTA_INVALID", "CREATIVE_DELTA_FIELD_MISSING", "HOOK_MISSING",
)
RANK = {code: index for index, code in enumerate(PRIMARY_ORDER)}


def _error(code: str, path: str, message: str, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "path": path, "message": message}
    if details:
        item["details"] = details
    return item


_STREAMING_PLAN_MODULE: Any = None


def _load_streaming_plan_module() -> Any:
    global _STREAMING_PLAN_MODULE
    if _STREAMING_PLAN_MODULE is not None:
        return _STREAMING_PLAN_MODULE
    script_path = Path(__file__).resolve().with_name("validate_streaming_plan.py")
    spec = importlib.util.spec_from_file_location("zibuyu_validate_streaming_plan", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load streaming plan validator: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _STREAMING_PLAN_MODULE = module
    return module


def _streaming_release_errors(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind an optional single-color release to one immutable all-color quality plan."""
    binding = document.get("streaming_release")
    if binding is None:
        return []
    if not isinstance(binding, dict):
        return [_error("STREAM_RELEASE_INVALID", "streaming_release", "streaming_release must be an object")]
    raw_path = binding.get("plan_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return [_error("STREAM_PLAN_PATH_INVALID", "streaming_release.plan_path", "absolute immutable plan path is required")]
    plan_path = Path(raw_path).expanduser()
    if not plan_path.is_absolute():
        return [_error("STREAM_PLAN_PATH_INVALID", "streaming_release.plan_path", "plan path must be absolute")]
    try:
        raw = plan_path.read_bytes()
        module = _load_streaming_plan_module()
        plan = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=module._object_no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        return [_error("STREAM_PLAN_READ_ERROR", "streaming_release.plan_path", str(exc))]
    plan_sha256 = hashlib.sha256(raw).hexdigest()
    return module.validate_release_binding(plan, document, plan_sha256)


def _result(errors: list[dict[str, Any]], **summary: Any) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for item in errors:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        unique[key] = item
    ordered = sorted(
        unique.values(),
        key=lambda e: (RANK.get(e["code"], 10_000), e["path"], e["code"], e["message"]),
    )
    valid = not ordered
    return {
        "valid": valid,
        "primary_error": None if valid else ordered[0]["code"],
        "errors": ordered,
        **summary,
    }


def _number(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and math.isfinite(value))


def _decimal(value: Any) -> Decimal | None:
    if not _number(value):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _same(a: Any, b: Any) -> bool:
    da, db = _decimal(a), _decimal(b)
    if da is not None or db is not None:
        return da is not None and db is not None and da == db
    return a == b


def _norm(value: Any) -> str:
    if isinstance(value, list):
        value = "|".join(str(part) for part in value)
    return "".join(
        ch for ch in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if ch.isalnum()
    )


def _valid_hashtag(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    tag = value.strip()
    return tag == LEGACY_BRAND_HASHTAG or bool(HASHTAG_PATTERN.fullmatch(tag))


def _without_colors(text: Any, color_terms: list[Any]) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    terms = {
        unicodedata.normalize("NFKC", str(term)).casefold()
        for term in color_terms if isinstance(term, str) and term
    }
    for term in sorted(terms, key=len, reverse=True):
        value = value.replace(term, " ")
    value = "".join(" " if unicodedata.category(ch)[0] in {"P", "S"} else ch for ch in value)
    return " ".join(value.split())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_text(_canonical_json(value))


def _zero_human_identity_audit(image_sha256: str) -> dict[str, Any]:
    audit = {
        "audit_version": ZERO_HUMAN_IDENTITY_AUDIT_VERSION,
        "audited_sha256": image_sha256,
        "inspection_method": ZERO_HUMAN_IDENTITY_INSPECTION_METHOD,
        "reviewed_at": "2026-07-30T08:10:00Z",
        "passed": True,
    }
    audit.update({field: False for field in ZERO_HUMAN_IDENTITY_CUE_FIELDS})
    return audit


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _meaningful_zh_translation(value: Any) -> bool:
    """Reject empty or obvious placeholder text in the Chinese review layer."""
    if not _nonempty(value) or not _market_contract.contains_han(value):
        return False
    compact = re.sub(r"[\s\[\]【】()（）<>《》]", "", str(value)).casefold()
    if compact in {"待翻译", "待补", "待补充", "待定", "占位", "占位符", "tbd", "todo"}:
        return False
    return re.fullmatch(
        r"(?:中文)?(?:审核)?(?:译文|翻译)(?:[:：#-]?(?:\d+|待补|待定|占位符?|tbd|todo))?",
        compact,
        flags=re.IGNORECASE,
    ) is None


def _https_host(value: Any) -> str | None:
    if not _nonempty(value):
        return None
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return None
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return None
    return parsed.hostname.casefold().rstrip(".")


def _is_amazon_host(host: str | None) -> bool:
    return bool(host) and any(host == base or host.endswith("." + base) for base in AMAZON_BASE_HOSTS)


def _is_amazon_image_host(host: str | None) -> bool:
    return bool(host) and (
        host == "media-amazon.com" or host.endswith(".media-amazon.com")
        or host == "ssl-images-amazon.com" or host.endswith(".ssl-images-amazon.com")
    )


def _url_path_contains_identifier(value: Any, identifier: Any) -> bool:
    if not _nonempty(value) or not _nonempty(identifier):
        return False
    try:
        path = urlparse(str(value)).path.casefold()
    except ValueError:
        return False
    token = re.escape(str(identifier).casefold())
    return re.search(r"(?:^|/)" + token + r"(?:/|$)", path) is not None


def _amazon_product_url_matches(value: Any, child_product_id: Any) -> bool:
    if not _is_amazon_host(_https_host(value)) or not _nonempty(child_product_id):
        return False
    try:
        path = urlparse(str(value)).path.casefold()
    except ValueError:
        return False
    child = re.escape(str(child_product_id).casefold())
    return re.search(r"/(?:dp|gp/product)/" + child + r"(?:/|$)", path) is not None


def _inline(value: Any) -> str:
    return " ".join(str(value or "").split())


def _time_text(value: Any) -> str:
    decimal = _decimal(value)
    if decimal is None:
        return "?"
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _string_set(value: Any) -> set[str] | None:
    if not isinstance(value, list) or any(not _nonempty(item) for item in value):
        return None
    normalized = [str(item).strip() for item in value]
    return set(normalized) if len(normalized) == len(set(normalized)) else None


def _legacy_v5_exact_resume_state(document: Any, *, validator_valid: bool) -> dict[str, Any]:
    """Report strict legacy-v5 resume capability without authorizing execution."""
    state = {
        "requested": False,
        "eligible": False,
        "execution_authorized": False,
        "reason": "not_requested",
    }
    if not isinstance(document, dict):
        return state
    resume = document.get("legacy_v5_exact_resume")
    if not isinstance(resume, dict):
        return state
    state["requested"] = True
    if document.get("schema_version") != "1.3" or not validator_valid:
        state["reason"] = "requires_valid_schema_1_3"
        return state
    if resume.get("prevalidated") is not True or resume.get("explicitly_approved") is not True:
        state["reason"] = "requires_prevalidation_and_explicit_approval"
        return state
    if resume.get("submitted") is not False or _nonempty(resume.get("record_id")):
        state["reason"] = "submitted_or_record_id_present"
        return state
    variants = [variant for variant in document.get("variants", []) if isinstance(variant, dict)]
    if not variants or any(_nonempty(variant.get("record_id")) for variant in variants):
        state["reason"] = "variant_record_id_present"
        return state
    prompt_hashes = resume.get("prompt_hashes")
    reference_hashes = resume.get("reference_hashes")
    if not isinstance(prompt_hashes, dict) or not isinstance(reference_hashes, dict):
        state["reason"] = "exact_prompt_and_reference_hash_maps_required"
        return state
    actual_prompt_hashes: dict[str, Any] = {}
    actual_reference_hashes: dict[str, Any] = {}
    for variant in variants:
        variant_id = str(variant.get("variant_id") or "")
        renderings = variant.get("renderings") if isinstance(variant.get("renderings"), dict) else {}
        prompt = renderings.get("prompt") if isinstance(renderings.get("prompt"), dict) else {}
        if prompt.get("serializer_id") != PROMPT_V5_SERIALIZER_ID:
            state["reason"] = "canonical_prompt_v5_required"
            return state
        actual_prompt_hashes[variant_id] = prompt.get("compiled_text_sha256")
        for reference in variant.get("references", []):
            if isinstance(reference, dict) and _nonempty(reference.get("reference_id")):
                actual_reference_hashes[str(reference["reference_id"])] = reference.get("sha256")
    if prompt_hashes != actual_prompt_hashes or reference_hashes != actual_reference_hashes:
        state["reason"] = "prompt_or_reference_hash_drift"
        return state
    state.update({"eligible": True, "reason": "exact_resume_only"})
    return state


def _validate_research_bundle(
    shared_core: dict[str, Any], errors: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    bundle = shared_core.get("research_bundle")
    if not isinstance(bundle, dict):
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle",
            "schema 1.3 requires an amazon_product_review_evidence_v1 research bundle",
        ))
        bundle = {}
    if bundle.get("contract_id") != RESEARCH_CONTRACT_ID:
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle.contract_id",
            f"contract_id must be {RESEARCH_CONTRACT_ID}",
        ))
    status = bundle.get("status")
    route = bundle.get("collection_route")
    if status not in RESEARCH_STATUSES or route not in COLLECTION_ROUTES:
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle",
            "research status or collection route is invalid",
        ))
    limitations = bundle.get("limitations")
    if not isinstance(limitations, list) or any(not _nonempty(item) for item in limitations):
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle.limitations",
            "limitations must be a string array, empty when no limitation exists",
        ))
    selected_variant = bundle.get("selected_variant")
    if not isinstance(selected_variant, dict) or any(
        selected_variant.get(field) is not None and not _nonempty(selected_variant.get(field))
        for field in ("color", "size")
    ):
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle.selected_variant",
            "selected_variant requires color and size string-or-null fields",
        ))
    requested_url = bundle.get("requested_url")
    is_amazon = _nonempty(requested_url)
    if is_amazon and not _is_amazon_host(_https_host(requested_url)):
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle.requested_url",
            "requested_url must be an HTTPS URL on an approved Amazon marketplace host",
        ))
    if is_amazon and (
        not _amazon_product_url_matches(bundle.get("canonical_url"), bundle.get("child_product_id"))
        or not _nonempty(bundle.get("child_product_id"))
        or not _nonempty(bundle.get("captured_at"))
        or status == "not_provided"
        or route == "not_applicable"
    ):
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle",
            "an Amazon request requires a matching HTTPS Amazon /dp/{child} canonical URL, child product ID, capture time, route, and non-not_provided status",
        ))

    source_map: dict[str, dict[str, Any]] = {}
    review_ids: set[str] = set()
    image_parent_ids: list[tuple[str, str]] = []
    sources = bundle.get("evidence_sources")
    if not isinstance(sources, list) or not sources:
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle.evidence_sources",
            "schema 1.3 requires at least one evidence source, including visible-reference sources when no page is provided",
        ))
        sources = []
    forbidden_review_keys = {"body", "author", "profile_name", "review_text", "raw_review", "reviewer_name"}
    for index, source in enumerate(sources):
        path = f"shared_core.research_bundle.evidence_sources[{index}]"
        if not isinstance(source, dict):
            errors.append(_error("RESEARCH_BUNDLE_INVALID", path, "evidence source must be an object"))
            continue
        source_id = source.get("source_id")
        if not _nonempty(source_id) or str(source_id) in source_map:
            errors.append(_error("RESEARCH_BUNDLE_INVALID", path + ".source_id", "source_id must be non-empty and unique"))
            continue
        source_kind = source.get("source_kind")
        source_role = source.get("source_role")
        if source_kind not in SOURCE_KINDS or source_role not in SOURCE_ROLES or source_role not in SOURCE_KIND_ROLES.get(source_kind, set()):
            errors.append(_error(
                "RESEARCH_BUNDLE_INVALID", path,
                "source_kind and source_role are invalid or semantically incompatible",
            ))
        if (
            not _nonempty(source.get("url")) or not _nonempty(source.get("locator"))
            or not _nonempty(source.get("captured_at"))
            or not isinstance(source.get("content_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", source.get("content_sha256", ""))
            or source.get("variant_scope") not in VARIANT_SCOPES
        ):
            errors.append(_error(
                "RESEARCH_BUNDLE_INVALID", path,
                "source requires URL, locator, capture time, variant scope, and lowercase content SHA-256",
            ))
        if forbidden_review_keys & set(source):
            errors.append(_error(
                "REVIEW_EVIDENCE_INVALID", path,
                "raw review bodies and reviewer identity must stay outside the batch object and paid prompt",
            ))
        source_url = source.get("url")
        if source_kind in {
            "amazon_catalog_attribute", "amazon_seller_copy", "amazon_customer_review",
            "amazon_qa", "amazon_review_summary",
        } and not _is_amazon_host(_https_host(source_url)):
            errors.append(_error(
                "RESEARCH_BUNDLE_INVALID", path + ".url",
                "Amazon page evidence must use HTTPS on an approved Amazon marketplace host",
            ))
        if source_kind == "amazon_customer_image" and not _is_amazon_image_host(_https_host(source_url)):
            errors.append(_error(
                "RESEARCH_BUNDLE_INVALID", path + ".url",
                "Amazon customer-image evidence must use an approved HTTPS Amazon image host",
            ))
        if (
            source_kind == "amazon_catalog_attribute"
            and source.get("variant_scope") == "exact_child"
            and not _amazon_product_url_matches(source_url, source.get("child_product_id"))
        ):
            errors.append(_error(
                "RESEARCH_BUNDLE_INVALID", path + ".url",
                "exact-child catalog evidence URL must contain its child product ID in /dp/ or /gp/product/ path",
            ))
        if source_kind == "amazon_customer_review":
            review_id = source.get("review_id")
            rating = source.get("rating")
            review_date_status = source.get("review_date_status")
            review_date = source.get("review_date")
            verified_purchase = source.get("verified_purchase")
            review_date_valid = (
                review_date_status == "captured" and _nonempty(review_date)
                or review_date_status == "not_captured" and review_date is None
            )
            if (
                not _nonempty(review_id) or str(review_id) in review_ids
                or not _number(rating) or not 1 <= float(rating) <= 5
                or not review_date_valid
                or verified_purchase not in {True, False, "unknown"}
            ):
                errors.append(_error(
                    "REVIEW_EVIDENCE_INVALID", path,
                    "direct reviews require unique review_id, 1-5 rating, explicit date capture status, and verified-purchase true/false/unknown",
                ))
            elif not _url_path_contains_identifier(source_url, review_id):
                errors.append(_error(
                    "REVIEW_EVIDENCE_INVALID", path + ".url",
                    "direct review URL path must contain the exact review_id",
                ))
            elif _nonempty(review_id):
                review_ids.add(str(review_id))
        elif source_kind == "amazon_customer_image":
            parent_review_id = source.get("parent_review_id")
            if not _nonempty(parent_review_id):
                errors.append(_error(
                    "REVIEW_EVIDENCE_INVALID", path + ".parent_review_id",
                    "a customer image requires its parent review ID",
                ))
            else:
                image_parent_ids.append((path, str(parent_review_id)))
        source_map[str(source_id)] = source
    for path, parent_review_id in image_parent_ids:
        if parent_review_id not in review_ids:
            errors.append(_error(
                "REVIEW_EVIDENCE_INVALID", path + ".parent_review_id",
                "customer-image parent_review_id must resolve to a direct review source in this bundle",
            ))

    evidence_map: dict[str, dict[str, Any]] = {}
    items = bundle.get("evidence_items")
    if not isinstance(items, list) or not items:
        errors.append(_error(
            "RESEARCH_BUNDLE_INVALID", "shared_core.research_bundle.evidence_items",
            "schema 1.3 requires normalized evidence items",
        ))
        items = []
    for index, item in enumerate(items):
        path = f"shared_core.research_bundle.evidence_items[{index}]"
        if not isinstance(item, dict):
            errors.append(_error("RESEARCH_BUNDLE_INVALID", path, "evidence item must be an object"))
            continue
        evidence_id = item.get("evidence_id")
        source = source_map.get(str(item.get("source_id")))
        uses = _string_set(item.get("permitted_uses"))
        if not _nonempty(evidence_id) or str(evidence_id) in evidence_map:
            errors.append(_error("RESEARCH_BUNDLE_INVALID", path + ".evidence_id", "evidence_id must be non-empty and unique"))
            continue
        if (
            source is None or item.get("assertion_kind") not in ASSERTION_KINDS
            or not _nonempty(item.get("statement")) or len(str(item.get("statement"))) > 240
            or not isinstance(item.get("exact_product_match"), bool)
            or item.get("exact_variant_match") not in {True, False, "unknown"}
            or item.get("conflict_status") not in CONFLICT_STATUSES
            or uses is None or not uses or not uses.issubset(EVIDENCE_USE_VALUES)
            or not isinstance(item.get("performance_demo_allowed"), bool)
        ):
            errors.append(_error(
                "RESEARCH_BUNDLE_INVALID", path,
                "evidence item provenance, assertion, scope, conflict, use, or performance fields are invalid",
            ))
            evidence_map[str(evidence_id)] = item
            continue
        kind = source.get("source_kind")
        if kind in {"amazon_review_summary", "third_party_summary", "amazon_qa"} and not uses.issubset({"discovery_only", "blocked"}):
            errors.append(_error("CLAIM_EVIDENCE_INVALID", path, "summary/Q&A evidence is discovery-only"))
        if kind == "amazon_seller_copy" and "pain_context" in uses:
            errors.append(_error("REVIEW_EVIDENCE_INVALID", path, "seller copy cannot be buyer-pain evidence"))
        if kind == "amazon_customer_review" and (
            item.get("assertion_kind") != "buyer_experience"
            or not uses.issubset({"claim_qualified", "pain_context", "blocked"})
            or item.get("performance_demo_allowed") is not False
        ):
            errors.append(_error("REVIEW_EVIDENCE_INVALID", path, "direct reviews support qualified buyer experience only"))
        if kind == "amazon_customer_image" and (
            item.get("assertion_kind") not in {"buyer_visual_observation", "visible_feature"}
            or not uses.issubset({"visual_only", "pain_context", "blocked"})
            or item.get("performance_demo_allowed") is not False
        ):
            errors.append(_error("CLAIM_EVIDENCE_INVALID", path, "customer images support inspected visible observations only"))
        if item.get("exact_product_match") is False and not uses.issubset({"discovery_only", "blocked"}):
            errors.append(_error("CLAIM_EVIDENCE_INVALID", path, "mismatched-product evidence cannot enter claims or buyer pains"))
        if item.get("conflict_status") in {"conflicted", "unverified"} and uses & {"claim_direct", "claim_qualified", "visual_only"}:
            errors.append(_error("CLAIM_EVIDENCE_INVALID", path, "conflicted or unverified evidence cannot authorize a script claim"))
        evidence_map[str(evidence_id)] = item

    analysis = bundle.get("review_analysis")
    theme_map: dict[str, dict[str, Any]] = {}
    if not isinstance(analysis, dict):
        errors.append(_error("REVIEW_EVIDENCE_INVALID", "shared_core.research_bundle.review_analysis", "review_analysis must be an object"))
        analysis = {}
    review_status = analysis.get("status")
    if review_status not in {"collected", "partial", "blocked", "no_reviews", "not_provided"}:
        errors.append(_error("REVIEW_EVIDENCE_INVALID", "shared_core.research_bundle.review_analysis.status", "invalid review status"))
    sampled = _string_set(analysis.get("sampled_review_ids"))
    if sampled is None or not sampled.issubset(review_ids):
        errors.append(_error(
            "REVIEW_EVIDENCE_INVALID", "shared_core.research_bundle.review_analysis.sampled_review_ids",
            "sampled review IDs must be unique and resolve to direct review sources",
        ))
        sampled = set()
    image_count = analysis.get("buyer_image_count")
    actual_image_count = sum(1 for source in source_map.values() if source.get("source_kind") == "amazon_customer_image")
    if not isinstance(image_count, int) or isinstance(image_count, bool) or image_count < 0 or image_count != actual_image_count:
        errors.append(_error(
            "REVIEW_EVIDENCE_INVALID", "shared_core.research_bundle.review_analysis.buyer_image_count",
            "buyer_image_count must equal the number of registered customer-image sources",
            expected=actual_image_count, actual=image_count,
        ))
    themes = analysis.get("themes")
    if not isinstance(themes, list):
        errors.append(_error("REVIEW_EVIDENCE_INVALID", "shared_core.research_bundle.review_analysis.themes", "themes must be an array"))
        themes = []
    for index, theme in enumerate(themes):
        path = f"shared_core.research_bundle.review_analysis.themes[{index}]"
        if not isinstance(theme, dict):
            errors.append(_error("REVIEW_EVIDENCE_INVALID", path, "review theme must be an object"))
            continue
        theme_id = theme.get("theme_id")
        evidence_ids = _string_set(theme.get("review_evidence_ids"))
        if not _nonempty(theme_id) or str(theme_id) in theme_map:
            errors.append(_error("REVIEW_EVIDENCE_INVALID", path + ".theme_id", "theme_id must be non-empty and unique"))
            continue
        review_evidence_valid = evidence_ids is not None and bool(evidence_ids) and all(
            evidence_id in evidence_map
            and source_map.get(str(evidence_map[evidence_id].get("source_id")), {}).get("source_kind") == "amazon_customer_review"
            for evidence_id in evidence_ids
        )
        theme_review_ids = [
            str(source_map.get(str(evidence_map.get(evidence_id, {}).get("source_id")), {}).get("review_id"))
            for evidence_id in (evidence_ids or set())
            if _nonempty(source_map.get(str(evidence_map.get(evidence_id, {}).get("source_id")), {}).get("review_id"))
        ]
        unique_theme_review_ids = set(theme_review_ids)
        if (
            not _nonempty(theme.get("normalized_theme"))
            or theme.get("sentiment") not in {"praise", "complaint", "mixed"}
            or not review_evidence_valid
            or len(theme_review_ids) != len(evidence_ids or set())
            or len(unique_theme_review_ids) != len(theme_review_ids)
            or not isinstance(theme.get("mention_count"), int)
            or theme.get("mention_count") != len(unique_theme_review_ids)
            or theme.get("claim_strength") not in {"single_attribution", "sample_theme", "risk_only"}
            or theme.get("permitted_script_use") not in {"pain_context", "claim_qualified", "blocked"}
        ):
            errors.append(_error("REVIEW_EVIDENCE_INVALID", path, "review theme fields or direct-review bindings are invalid"))
        if theme.get("claim_strength") == "sample_theme" and len(unique_theme_review_ids) < 3:
            errors.append(_error(
                "REVIEW_EVIDENCE_INVALID", path + ".claim_strength",
                "sample-theme language requires at least three unique direct review source IDs",
            ))
        theme_map[str(theme_id)] = theme
    for field in ("conflicts", "excluded_sources"):
        if not isinstance(analysis.get(field), list):
            errors.append(_error("REVIEW_EVIDENCE_INVALID", f"shared_core.research_bundle.review_analysis.{field}", f"{field} must be an array"))

    pain_map: dict[str, dict[str, Any]] = {}
    pain_entries = shared_core.get("pain_solution_map")
    if not isinstance(pain_entries, list) or not pain_entries:
        errors.append(_error("BUYER_PAIN_MAP_INVALID", "shared_core.pain_solution_map", "schema 1.3 requires a non-empty pain-solution map"))
        pain_entries = []
    for index, pain in enumerate(pain_entries):
        path = f"shared_core.pain_solution_map[{index}]"
        if not isinstance(pain, dict):
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path, "pain entry must be an object"))
            continue
        pain_id = pain.get("pain_point_id")
        evidence_ids = _string_set(pain.get("evidence_ids"))
        solution_claim_ids = _string_set(pain.get("solution_claim_ids"))
        target_language_terms = _string_set(pain.get("target_language_terms"))
        if not _nonempty(pain_id) or str(pain_id) in pain_map:
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path + ".pain_point_id", "pain_point_id must be non-empty and unique"))
            continue
        base_valid = (
            _nonempty(pain.get("pain_statement"))
            and pain.get("basis") in PAIN_BASES
            and evidence_ids is not None
            and solution_claim_ids is not None
            and target_language_terms is not None
            and isinstance(pain.get("selected_for_script"), bool)
            and pain.get("status") in {"script_eligible", "risk_only"}
            and all(evidence_id in evidence_map for evidence_id in evidence_ids)
        )
        if not base_valid:
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path, "pain provenance, selection, status, or solution fields are invalid"))
        if pain.get("basis") != "category_inference" and not evidence_ids:
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path + ".evidence_ids", "non-inferred pain requires evidence IDs"))
        if pain.get("basis") == "review_theme":
            theme = theme_map.get(str(pain.get("review_theme_id")))
            if theme is None or evidence_ids != (_string_set(theme.get("review_evidence_ids")) or set()):
                errors.append(_error("BUYER_PAIN_MAP_INVALID", path, "review-theme pain must exactly bind one validated review theme"))
        elif pain.get("basis") == "direct_review" and not all(
            source_map.get(str(evidence_map.get(evidence_id, {}).get("source_id")), {}).get("source_kind") == "amazon_customer_review"
            for evidence_id in evidence_ids
        ):
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path, "direct-review pain must bind only direct customer reviews"))
        if pain.get("status") == "script_eligible" and not solution_claim_ids:
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path + ".solution_claim_ids", "script-eligible pain requires an independently evidenced solution claim"))
        if pain.get("selected_for_script") is True and not target_language_terms:
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path + ".target_language_terms", "a selected pain requires registered target-language hook/caption terms"))
        if pain.get("status") == "risk_only" and pain.get("selected_for_script") is True:
            errors.append(_error("BUYER_PAIN_MAP_INVALID", path, "risk-only pain cannot be selected for a conversion script"))
        pain_map[str(pain_id)] = pain
    if pain_map and not any(
        pain.get("selected_for_script") is True and pain.get("status") == "script_eligible"
        for pain in pain_map.values()
    ):
        errors.append(_error("BUYER_PAIN_MAP_INVALID", "shared_core.pain_solution_map", "at least one script-eligible pain must be selected"))
    return bundle, source_map, evidence_map, theme_map, pain_map


def _v13_performance_evidence_ok(
    claim: dict[str, Any] | None,
    evidence_map: dict[str, dict[str, Any]],
    source_map: dict[str, dict[str, Any]],
) -> bool:
    if not isinstance(claim, dict):
        return False
    evidence_ids = _string_set(claim.get("evidence_ids")) or set()
    allowed_kinds = {"amazon_catalog_attribute", "amazon_seller_copy", "garment_label", "user_provided", "verified_test"}
    return any(
        evidence_id in evidence_map
        and evidence_map[evidence_id].get("performance_demo_allowed") is True
        and evidence_map[evidence_id].get("conflict_status") == "clear"
        and evidence_map[evidence_id].get("exact_product_match") is True
        and evidence_map[evidence_id].get("exact_variant_match") is not False
        and source_map.get(str(evidence_map[evidence_id].get("source_id")), {}).get("source_kind") in allowed_kinds
        for evidence_id in evidence_ids
    )


def _v13_numeric_claim_ok(
    claim: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]],
    source_map: dict[str, dict[str, Any]],
) -> bool:
    evidence_ids = _string_set(claim.get("evidence_ids")) or set()
    if claim.get("assertion_kind") == "exact_composition":
        allowed_kinds = {"amazon_catalog_attribute", "garment_label", "user_provided", "verified_test"}
        return bool(evidence_ids) and all(
            evidence_id in evidence_map
            and evidence_map[evidence_id].get("assertion_kind") == "exact_composition"
            and evidence_map[evidence_id].get("exact_product_match") is True
            and evidence_map[evidence_id].get("exact_variant_match") is not False
            and evidence_map[evidence_id].get("conflict_status") == "clear"
            and source_map.get(str(evidence_map[evidence_id].get("source_id")), {}).get("source_kind") in allowed_kinds
            for evidence_id in evidence_ids
        )
    return claim.get("evidence_basis") == "verified_test" and bool(evidence_ids) and all(
        source_map.get(str(evidence_map.get(evidence_id, {}).get("source_id")), {}).get("source_kind") == "verified_test"
        for evidence_id in evidence_ids
    )


def _interface_tag_index(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"@(?:图片|Image)([1-9][0-9]*)", value)
    return int(match.group(1)) if match else None


def _ordered_reference_ids(
    reference_ids: set[str] | list[str],
    reference_map: dict[str, list[dict[str, Any]]],
    *,
    identity_safe: bool,
) -> list[str]:
    ids = list(reference_ids)
    if not identity_safe:
        return sorted(ids)
    return sorted(
        ids,
        key=lambda reference_id: (
            _interface_tag_index(reference_map.get(reference_id, [{}])[0].get("interface_tag")) or 10**9,
            reference_id,
        ),
    )


def _reference_contract_line(reference: dict[str, Any], *, identity_safe: bool = False) -> str:
    raw_tag = reference.get("interface_tag")
    tag = str(raw_tag) if _nonempty(raw_tag) else "<missing-interface-tag>"
    if identity_safe:
        reference_position = _interface_tag_index(tag)
        position = str(reference_position) if reference_position is not None else "<missing-position>"
        return (
            f"Reference {position} / {tag} = garment identity only: preserve exact color, silhouette, neckline, "
            "sleeve construction, hem, seams, texture scale, thickness, opacity and drape. The apparel reference "
            "passed human_identity_pixels_absent: true. Do not transfer its skin tone, ethnicity, neck, chest, "
            "collarbone, shoulders, arms, hands, fingers, nails, tattoos, jewelry, body shape, face, hair, white "
            "triptych, dividers, repeated or multiple bodies, pose, camera, environment, text or watermark. "
            "Only the @Image1 fixed creator wears the same garment in one continuous real-world scene."
        )
    return (
        f"{tag} = garment identity only: preserve exact color, silhouette, neckline, sleeve construction, "
        "hem, seams, texture scale, thickness, opacity and drape. Replace its white triptych, dividers, "
        "repeated or multiple bodies, reference face or hair, pose, camera, environment, text and watermark "
        "with one creator wearing the same garment in one continuous real-world scene."
    )


def _reference_contract_text(
    reference_ids: list[str],
    reference_map: dict[str, list[dict[str, Any]]],
    *,
    identity_safe: bool = False,
) -> str:
    lines: list[str] = [FIXED_MODEL_IDENTITY_LINE] if identity_safe else []
    ordered_ids = _ordered_reference_ids(reference_ids, reference_map, identity_safe=identity_safe)
    for reference_id in ordered_ids:
        matches = reference_map.get(reference_id, [])
        if len(matches) == 1:
            lines.append(_reference_contract_line(matches[0], identity_safe=identity_safe))
    return "\n".join(lines)


def _setup_groups(beats: list[Any]) -> list[list[dict[str, Any]]]:
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


def _macro_phase_groups(beats: list[Any]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """Project internal beats into the three viewer-perceived creator-performance phases."""
    phase_specs = (
        ("recognition/result Hook", "warm recognition; direct eye contact; friendly certainty"),
        ("animated proof", "energy rises; brighter certainty; emphasize proof pivots"),
        ("close/detail conviction", "pleased conviction or relief; sincere verdict; warm close"),
    )
    grouped: list[list[dict[str, Any]]] = [[], [], []]
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        start = _decimal(beat.get("start_seconds"))
        purpose = beat.get("purpose")
        if index == 0 or purpose == "hook":
            phase_index = 0
        elif purpose == "cta" or (start is not None and start >= Decimal("10")):
            phase_index = 2
        else:
            phase_index = 1
        grouped[phase_index].append(beat)
    return [
        (phase_specs[index][0], phase_specs[index][1], group)
        for index, group in enumerate(grouped)
    ]


def _speech_text(beat: dict[str, Any]) -> str:
    mode = beat.get("speech_mode")
    line = beat.get("spoken_line")
    if mode == "on_camera_dialogue":
        return f"On-camera dialogue: {_canonical_json(line)}; mouth visible with exact lip-sync."
    if mode == "offscreen_voiceover":
        return f"Off-screen voiceover: {_canonical_json(line)}; mouth not visible, no lip-sync."
    return "No speech."


def _model_stats_overlay_instruction(batch_key: dict[str, Any]) -> str:
    text = _market_contract.fit_stats_text_for_batch(batch_key)
    if not text:
        return ""
    duration = _time_text(batch_key.get("duration_seconds"))
    return (
        f"Persistent upper-left fit-stats overlay: exact text {_canonical_json(text)} from 0-{duration}s, "
        "with no 'Model' label; place it in the upper-left safe area, clearly visible but not covering the face, "
        "hands, body, garment, or product proof; use clean white text with a subtle dark outline or translucent dark strip. "
    )


def _serialize_prompt_v2(
    reference_contract: str,
    beats: list[Any],
    reference_map: dict[str, list[dict[str, Any]]],
    batch_key: dict[str, Any] | None,
) -> str:
    batch_key = batch_key if isinstance(batch_key, dict) else {}
    overlay_instruction = _model_stats_overlay_instruction(batch_key)
    clean_screen_instruction = (
        overlay_instruction +
        "Clean screen otherwise: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays."
        if overlay_instruction
        else "Clean screen: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays."
    )
    global_line = (
        f"Global: {_inline(batch_key.get('platform'))}; {_inline(batch_key.get('aspect_ratio'))}; "
        f"{_time_text(batch_key.get('duration_seconds'))}s; {_inline(batch_key.get('resolution'))}; "
        f"authentic UGC phone video. One creator wears the same garment throughout. {clean_screen_instruction} Keep the same creator, "
        "outfit, scene and light across shots; two natural connected arms and hands, at most one active hand."
    )
    lines = [reference_contract, global_line]
    for shot_number, group in enumerate(_setup_groups(beats), start=1):
        start = _time_text(group[0].get("start_seconds"))
        end = _time_text(group[-1].get("end_seconds"))
        lines.append(f"Shot {shot_number} ({start}-{end}s):")
        for beat in group:
            beat_start = _time_text(beat.get("start_seconds"))
            beat_end = _time_text(beat.get("end_seconds"))
            matches = reference_map.get(beat.get("reference_binding"), [])
            tag = str(matches[0].get("interface_tag")) if len(matches) == 1 else "<unbound-garment-reference>"
            styling = _inline(beat.get("styling_point"))
            styling_text = f" Styling: {styling}." if styling else ""
            expression = _inline(beat.get("micro_expression"))
            expression_text = f" Expression: {expression}." if expression else ""
            sound = _inline(beat.get("audio")) or "natural room tone"
            lines.append(
                f"{beat_start}-{beat_end}s: {_inline(beat.get('actor'))} in {_inline(beat.get('scene'))}, "
                f"wearing {tag}. Frame: {_inline(beat.get('framing'))}. Action: {_inline(beat.get('core_action'))} "
                f"toward {_inline(beat.get('action_target'))}; left hand {_inline(beat.get('left_hand_state'))}; "
                f"right hand {_inline(beat.get('right_hand_state'))}; end with {_inline(beat.get('visible_endpoint'))}. "
                f"Product proof: {_inline(beat.get('product_point'))}; garment visibility "
                f"{_inline(beat.get('product_visibility'))}.{styling_text} Camera: {_inline(beat.get('camera_motion'))}. "
                f"Light: {_inline(beat.get('lighting'))}.{expression_text} {_speech_text(beat)} Sound: {sound}."
            )
    return "\n".join(lines)


def _hand_plan_text(beat: dict[str, Any]) -> str:
    hand_plan = beat.get("hand_plan") if isinstance(beat.get("hand_plan"), dict) else {}
    left = hand_plan.get("left") if isinstance(hand_plan.get("left"), dict) else {}
    right = hand_plan.get("right") if isinstance(hand_plan.get("right"), dict) else {}
    return (
        f"Hand plan: left starts {_inline(left.get('start_anchor'))}, does {_inline(left.get('action'))}, "
        f"ends {_inline(left.get('end_anchor'))}; right starts {_inline(right.get('start_anchor'))}, "
        f"does {_inline(right.get('action'))}, ends {_inline(right.get('end_anchor'))}."
    )


def _serialize_prompt_v3(
    reference_contract: str,
    beats: list[Any],
    reference_map: dict[str, list[dict[str, Any]]],
    batch_key: dict[str, Any] | None,
) -> str:
    batch_key = batch_key if isinstance(batch_key, dict) else {}
    overlay_instruction = _model_stats_overlay_instruction(batch_key)
    clean_screen_instruction = (
        overlay_instruction +
        "Clean screen otherwise: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays."
        if overlay_instruction
        else "Clean screen: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays."
    )
    global_line = (
        f"Global: {_inline(batch_key.get('platform'))}; {_inline(batch_key.get('aspect_ratio'))}; "
        f"{_time_text(batch_key.get('duration_seconds'))}s; {_inline(batch_key.get('resolution'))}; "
        f"authentic motion-rich UGC phone video. Use native direct-response commerce cadence: complete a new product-proof or styling action "
        "about every 2-3 seconds; no slow-motion fashion posing or idle explanatory beats. Start from declared posture and hand anchors, ease "
        "through one action, briefly hold the endpoint, then reset or cut. Use natural acceleration/deceleration, shoulder-elbow-wrist follow-through, "
        "small breathing/blink/weight shifts, and realistic garment lag/settling; micro-movements never replace proof. "
        f"One creator wears the same garment throughout. {clean_screen_instruction} Keep the same creator, "
        "outfit, scene and light across shots. The creator has exactly two natural arms connected shoulder-to-wrist-to-hand "
        "and exactly two hands. Actions occur sequentially, one garment target at a time, and each reaches a visible endpoint "
        "before the next action. Default to one active hand; use both only for one declared evidence-backed coordinated proof."
    )
    lines = [reference_contract, global_line]
    for shot_number, group in enumerate(_setup_groups(beats), start=1):
        start = _time_text(group[0].get("start_seconds"))
        end = _time_text(group[-1].get("end_seconds"))
        lines.append(f"Shot {shot_number} ({start}-{end}s):")
        for beat in group:
            beat_start = _time_text(beat.get("start_seconds"))
            beat_end = _time_text(beat.get("end_seconds"))
            matches = reference_map.get(beat.get("reference_binding"), [])
            tag = str(matches[0].get("interface_tag")) if len(matches) == 1 else "<unbound-garment-reference>"
            styling = _inline(beat.get("styling_point"))
            styling_text = f" Styling: {styling}." if styling else ""
            expression = _inline(beat.get("micro_expression"))
            expression_text = f" Expression: {expression}." if expression else ""
            sound = _inline(beat.get("audio")) or "natural room tone"
            proof_target = _inline(beat.get("proof_target"))
            proof_text = ""
            if proof_target:
                proof_text = (
                    f" Focus: {proof_target}; action type {_inline(beat.get('proof_action_type'))}; "
                    f"evidence {_inline(beat.get('evidence_basis'))}."
                )
            lines.append(
                f"{beat_start}-{beat_end}s: {_inline(beat.get('actor'))} in {_inline(beat.get('scene'))}, "
                f"wearing {tag}. Posture: {_inline(beat.get('posture_id'))}. Frame: {_inline(beat.get('framing'))}.{proof_text} "
                f"Action: {_inline(beat.get('core_action'))} toward {_inline(beat.get('action_target'))}; "
                f"end with {_inline(beat.get('visible_endpoint'))}. {_hand_plan_text(beat)} "
                f"Product proof: {_inline(beat.get('product_point'))}; garment visibility "
                f"{_inline(beat.get('product_visibility'))}.{styling_text} Camera: {_inline(beat.get('camera_motion'))}. "
                f"Light: {_inline(beat.get('lighting'))}.{expression_text} {_speech_text(beat)} Sound: {sound}."
            )
    return "\n".join(lines)


def _serialize_prompt_v4(
    reference_contract: str,
    beats: list[Any],
    reference_map: dict[str, list[dict[str, Any]]],
    batch_key: dict[str, Any] | None,
) -> str:
    """Serialize schema 1.3 as a continuous creator performance around internal proof beats."""
    batch_key = batch_key if isinstance(batch_key, dict) else {}
    overlay_instruction = _model_stats_overlay_instruction(batch_key)
    clean_screen_instruction = (
        overlay_instruction
        + "Clean screen otherwise: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays."
        if overlay_instruction
        else "Clean screen: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays."
    )
    global_line = (
        f"Global: {_inline(batch_key.get('platform'))}; {_inline(batch_key.get('aspect_ratio'))}; "
        f"{_time_text(batch_key.get('duration_seconds'))}s; {_inline(batch_key.get('resolution'))}; "
        "authentic creator-led UGC phone video delivered as one continuous friend-to-friend recommendation. "
        "Use native direct-response commerce cadence inside three viewer-perceived phases: recognition/result Hook, animated proof, then close/detail conviction, while completing a new product-proof or styling action about every 2-3 seconds. "
        "Carry gaze, weight, expression, filming/task hand, props and garment state forward; each visible endpoint flows into the next motivated action, and reset or cut only when clarity requires it. "
        "Motivate distance changes by what the viewer needs to see: step back for full fit, move or turn for fabric/silhouette behavior, and walk closer for decisive detail. "
        "In mirror-selfie POV the filming hand continuously holds the phone; any alternate garment or prop stays owned until an explicit place-down, handoff or intentional cut. "
        "Do not snap to attention, symmetrically reset both hands to the hips, freeze between proofs, or perform isolated equal-energy product poses. "
        "Use natural acceleration/deceleration, shoulder-elbow-wrist follow-through, plausible weight transfer, small breathing/blink shifts and realistic garment lag/settling; micro-movements never replace proof. "
        "Shape expression and voice from warm recognition to animated certainty to delighted conviction or relief through changing emphasis and brief proof-pivot pauses, not shouting. "
        f"One creator wears the same garment throughout. {clean_screen_instruction} Keep the same creator, outfit, scene and physical light across phases. "
        "The creator has exactly two natural arms connected shoulder-to-wrist-to-hand and exactly two hands. Actions remain sequential, one garment target per internal beat, and each reaches a readable endpoint before the next action. "
        "Default to one active demonstration hand; use both only for one declared evidence-backed coordinated proof."
    )
    lines = [reference_contract, global_line]
    for shot_number, (phase_name, delivery, group) in enumerate(_macro_phase_groups(beats), start=1):
        if not group:
            continue
        start = _time_text(group[0].get("start_seconds"))
        end = _time_text(group[-1].get("end_seconds"))
        lines.append(
            f"Shot {shot_number} — Macro phase {phase_name} ({start}-{end}s; continuous creator flow). "
            f"Performance: {delivery}."
        )
        for beat in group:
            beat_start = _time_text(beat.get("start_seconds"))
            beat_end = _time_text(beat.get("end_seconds"))
            matches = reference_map.get(beat.get("reference_binding"), [])
            tag = str(matches[0].get("interface_tag")) if len(matches) == 1 else "<unbound-garment-reference>"
            styling = _inline(beat.get("styling_point"))
            styling_text = f" Styling: {styling}." if styling else ""
            expression = _inline(beat.get("micro_expression"))
            expression_text = f" Expression/delivery cue: {expression}." if expression else ""
            sound = _inline(beat.get("audio")) or "natural room tone"
            proof_target = _inline(beat.get("proof_target"))
            proof_text = ""
            if proof_target:
                proof_text = (
                    f" Focus: {proof_target}; action type {_inline(beat.get('proof_action_type'))}; "
                    f"evidence {_inline(beat.get('evidence_basis'))}."
                )
            lines.append(
                f"{beat_start}-{beat_end}s internal beat: {_inline(beat.get('actor'))} in {_inline(beat.get('scene'))}, "
                f"wearing {tag}. Posture: {_inline(beat.get('posture_id'))}. Frame: {_inline(beat.get('framing'))}.{proof_text} "
                f"Action: {_inline(beat.get('core_action'))} toward {_inline(beat.get('action_target'))}; "
                f"end with {_inline(beat.get('visible_endpoint'))}. {_hand_plan_text(beat)} "
                "Continuity: inherit the prior gaze, weight, hand occupancy, prop ownership and garment state; hand off this endpoint naturally or declare the cut. "
                f"Product proof: {_inline(beat.get('product_point'))}; garment visibility "
                f"{_inline(beat.get('product_visibility'))}.{styling_text} Camera: {_inline(beat.get('camera_motion'))}. "
                f"Light: {_inline(beat.get('lighting'))}.{expression_text} {_speech_text(beat)} Sound: {sound}."
            )
    return "\n".join(lines)


def _serialize_prompt_v5(
    reference_contract: str,
    beats: list[Any],
    reference_map: dict[str, list[dict[str, Any]]],
    batch_key: dict[str, Any] | None,
) -> str:
    """Serialize new schema 1.3 work with an identity-first deterministic role contract."""
    return _serialize_prompt_v4(reference_contract, beats, reference_map, batch_key)


def _word_count(value: Any) -> int:
    return len(re.findall(r"[^\W_]+(?:['\u2019-][^\W_]+)*", str(value or ""), flags=re.UNICODE))


def _contains_any(value: Any, terms: set[str]) -> bool:
    normalized = _norm(value)
    return any(_norm(term) in normalized for term in terms)


def _spoken_opener(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    for prefix in sorted(VOICEOVER_OPENER_PREFIXES, key=len, reverse=True):
        if text.startswith(unicodedata.normalize("NFKC", prefix).casefold()):
            return _norm(prefix)
    tokens = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    return "|".join(tokens[:2]) if len(tokens) >= 2 else ""


def _is_static_action(value: Any) -> bool:
    return not _nonempty(value) or _contains_any(value, STATIC_ACTION_TERMS)


def _target_tokens(value: Any) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("_", " ")
    return [
        token for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
        if len(token) >= 3 or any(ord(char) > 127 for char in token)
    ]


def _mentions_target(target: Any, *values: Any) -> bool:
    tokens = _target_tokens(target)
    haystack = _norm(" ".join(str(value or "") for value in values))
    return bool(tokens) and all(token in haystack for token in tokens)


def _has_multiple_targets(value: Any) -> bool:
    return bool(MULTI_TARGET_SEPARATORS.search(str(value or "")))


def _performance_group_ids(*values: Any) -> set[str]:
    return {
        group_id for group_id, terms in PERFORMANCE_CLAIM_GROUPS.items()
        if any(_contains_any(value, terms) for value in values)
    }


def _target_matches_terms(target: Any, terms: set[str]) -> bool:
    target_text = unicodedata.normalize("NFKC", str(target or "")).casefold().replace("_", " ")
    target_tokens = set(re.findall(r"[^\W_]+", target_text, flags=re.UNICODE))
    for term in terms:
        term_text = unicodedata.normalize("NFKC", str(term)).casefold().replace("_", " ")
        term_tokens = set(re.findall(r"[^\W_]+", term_text, flags=re.UNICODE))
        if term_tokens and term_tokens.issubset(target_tokens):
            return True
    return False


def _action_matches_target(target: Any, action_type: Any) -> bool:
    normalized_action = _norm(action_type)
    for terms, allowed_actions in TARGET_ACTION_RULES:
        if _target_matches_terms(target, terms):
            return any(normalized_action == _norm(allowed) for allowed in allowed_actions)
    return True


def _framing_matches_target(target: Any, framing_class: Any) -> bool:
    normalized_class = _norm(framing_class)
    for terms, allowed_classes in TARGET_FRAMING_RULES:
        if _target_matches_terms(target, terms):
            return any(normalized_class == _norm(allowed) for allowed in allowed_classes)
    return True


def _framing_text_matches_class(framing: Any, framing_class: Any) -> bool:
    terms = FRAMING_TEXT_TERMS.get(str(framing_class), set())
    return bool(terms) and _contains_any(framing, terms)


def _detected_action_types(core_action: Any) -> set[str]:
    return {
        action_type for action_type, terms in ACTION_TEXT_TERMS.items()
        if _target_matches_terms(core_action, terms)
    }


def _hand_side_markers(core_action: Any) -> set[str]:
    text = _norm(core_action)
    left_terms = {"lefthand", "leftfinger", "leftindex", "左手", "左手指"}
    right_terms = {"righthand", "rightfinger", "rightindex", "右手", "右手指"}
    markers: set[str] = set()
    if any(_norm(term) in text for term in left_terms):
        markers.add("left")
    if any(_norm(term) in text for term in right_terms):
        markers.add("right")
    if _norm("both hands") in text or _norm("双手") in text:
        markers.update({"left", "right"})
    return markers


def _minimum_product_actions(duration: Any) -> int:
    value = _decimal(duration)
    if value is None or value <= 0:
        return 1
    return max(1, math.ceil(float(value) / 4.0))


def _minimum_market_product_actions(duration: Any) -> int:
    """Reserve two seconds for the mandatory v1.4 human verdict/CTA close."""
    value = _decimal(duration)
    if value is None or value <= 0:
        return 1
    proof_seconds = max(0.0, float(value) - 2.0)
    return max(1, math.ceil(proof_seconds / 4.0))


def _interval(item: dict[str, Any]) -> tuple[Any, Any]:
    if "start_seconds" in item or "end_seconds" in item:
        return item.get("start_seconds"), item.get("end_seconds")
    interval = item.get("interval")
    return tuple(interval) if isinstance(interval, list) and len(interval) == 2 else (None, None)


def _check_projection(
    errors: list[dict[str, Any]], base: str, name: str, rendering: Any,
    list_key: str, fields: tuple[str, ...], timeline: dict[str, Any], beats: list[Any],
) -> None:
    path = f"{base}.renderings.{name}"
    if not isinstance(rendering, dict):
        errors.append(_error("PROJECTION_INVALID", path, "projection must be an object"))
        return
    if rendering.get("source_timeline_id") != timeline.get("timeline_id"):
        errors.append(_error("VARIANT_REFERENCE_MISMATCH", path + ".source_timeline_id", "projection timeline_id is not canonical"))
    if rendering.get("source_timeline_version") != timeline.get("timeline_version"):
        errors.append(_error("STALE_TIMELINE_VERSION", path + ".source_timeline_version", "projection timeline_version is stale"))
    items = rendering.get(list_key)
    if not isinstance(items, list):
        errors.append(_error("PROJECTION_INVALID", path + "." + list_key, "structured projection list is required"))
        return
    if len(items) < len(beats):
        errors.append(_error("TIMELINE_MISSING_BEAT", path, "projection omits canonical beats", expected=len(beats), actual=len(items)))
    elif len(items) > len(beats):
        errors.append(_error("TIMELINE_EXTRA_BEAT", path, "projection adds non-canonical beats", expected=len(beats), actual=len(items)))
    projected_ids = [item.get("beat_id") if isinstance(item, dict) else None for item in items]
    canonical_ids = [item.get("beat_id") if isinstance(item, dict) else None for item in beats]
    if len(items) == len(beats) and projected_ids != canonical_ids:
        errors.append(_error("TIMELINE_ORDER_DRIFT", path, "projection beat order/identity differs from canonical timeline"))
    for index, (item, source) in enumerate(zip(items, beats)):
        item_path = f"{path}.{list_key}[{index}]"
        if not isinstance(item, dict) or not isinstance(source, dict):
            errors.append(_error("PROJECTION_INVALID", item_path, "projection beat must be an object"))
            continue
        actual_start, actual_end = _interval(item)
        if not _same(actual_start, source.get("start_seconds")) or not _same(actual_end, source.get("end_seconds")):
            errors.append(_error("TIMELINE_TIME_DRIFT", item_path, "projection interval differs from canonical timeline"))
        for field in fields:
            if field not in item:
                errors.append(_error("PROJECTION_FIELD_MISSING", item_path + "." + field, "required source field is missing"))
            elif item[field] != source.get(field):
                code = "TIMELINE_VO_DRIFT" if field in {"spoken_line", "spoken_intent_id"} else "TIMELINE_ACTION_DRIFT"
                if field == "reference_binding":
                    code = "VARIANT_REFERENCE_MISMATCH"
                errors.append(_error(code, item_path + "." + field, "projection field differs from canonical timeline"))


def _check_broll(
    errors: list[dict[str, Any]], base: str, rendering: Any,
    timeline: dict[str, Any], beats: list[Any], fields: tuple[str, ...] = VISUAL_FIELDS,
) -> None:
    path = base + ".renderings.broll"
    if not isinstance(rendering, dict):
        errors.append(_error("PROJECTION_INVALID", path, "projection must be an object"))
        return
    if rendering.get("source_timeline_id") != timeline.get("timeline_id"):
        errors.append(_error("VARIANT_REFERENCE_MISMATCH", path + ".source_timeline_id", "projection timeline_id is not canonical"))
    if rendering.get("source_timeline_version") != timeline.get("timeline_version"):
        errors.append(_error("STALE_TIMELINE_VERSION", path + ".source_timeline_version", "projection timeline_version is stale"))
    shots = rendering.get("shots")
    if not isinstance(shots, list):
        errors.append(_error("PROJECTION_INVALID", path + ".shots", "structured shots list is required"))
        return
    sources = {beat.get("beat_id"): beat for beat in beats if isinstance(beat, dict)}
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {beat_id: [] for beat_id in sources}
    shot_ids: set[str] = set()
    parent_positions: list[int] = []
    source_order = {beat.get("beat_id"): i for i, beat in enumerate(beats) if isinstance(beat, dict)}
    for index, shot in enumerate(shots):
        shot_path = f"{path}.shots[{index}]"
        if not isinstance(shot, dict):
            errors.append(_error("PROJECTION_INVALID", shot_path, "B-roll shot must be an object"))
            continue
        shot_id, parent = shot.get("shot_id"), shot.get("parent_beat_id")
        if not isinstance(shot_id, str) or not shot_id or shot_id in shot_ids:
            errors.append(_error("PROJECTION_INVALID", shot_path + ".shot_id", "shot_id must be non-empty and unique"))
        else:
            shot_ids.add(shot_id)
        if parent not in sources:
            errors.append(_error("TIMELINE_EXTRA_BEAT", shot_path + ".parent_beat_id", "B-roll shot has no canonical parent"))
            continue
        groups[parent].append((index, shot))
        parent_positions.append(source_order[parent])
        source = sources[parent]
        for field in fields:
            if field not in shot:
                errors.append(_error("PROJECTION_FIELD_MISSING", shot_path + "." + field, "required source field is missing"))
            elif shot[field] != source.get(field):
                code = "VARIANT_REFERENCE_MISMATCH" if field == "reference_binding" else "TIMELINE_ACTION_DRIFT"
                errors.append(_error(code, shot_path + "." + field, "B-roll field differs from canonical timeline"))
    if parent_positions != sorted(parent_positions):
        errors.append(_error("TIMELINE_ORDER_DRIFT", path + ".shots", "B-roll parent order differs from canonical timeline"))
    for beat_id, source in sources.items():
        children = groups[beat_id]
        if not children:
            errors.append(_error("TIMELINE_MISSING_BEAT", path + ".shots", "B-roll omits a canonical parent", beat_id=beat_id))
            continue
        previous = _decimal(source.get("start_seconds"))
        for index, shot in children:
            start, end = map(_decimal, _interval(shot))
            if start is None or end is None or start >= end or start != previous:
                errors.append(_error("TIMELINE_TIME_DRIFT", f"{path}.shots[{index}]", "B-roll child intervals must contiguously cover the parent"))
                break
            previous = end
        if previous != _decimal(source.get("end_seconds")):
            errors.append(_error("TIMELINE_TIME_DRIFT", path + ".shots", "B-roll children do not exactly cover the parent", beat_id=beat_id))


def validate(document: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(document, dict):
        return _result([_error("ROOT_TYPE_INVALID", "$", "top-level JSON must be an object")])
    schema_version = document.get("schema_version")
    if schema_version not in {"1.0", "1.1", "1.2", "1.3", "1.4"}:
        errors.append(_error("SCHEMA_VERSION_INVALID", "schema_version", "schema_version must be 1.0, 1.1, 1.2, 1.3, or 1.4"))
    market_v14 = schema_version == "1.4"
    market_v14_current = market_v14 and document.get("quality_contract_id") == QUALITY_CONTRACT_ID_V14
    market_v14_legacy_v6 = market_v14 and document.get("quality_contract_id") == QUALITY_CONTRACT_ID_V14_LEGACY
    quality_v11 = schema_version in {"1.1", "1.2", "1.3", "1.4"}
    motion_v12 = schema_version in {"1.2", "1.3", "1.4"}
    evidence_v13 = schema_version in {"1.3", "1.4"}
    expected_quality_contract = QUALITY_CONTRACT_ID_V12 if motion_v12 else QUALITY_CONTRACT_ID_V11
    if market_v14:
        if not (market_v14_current or market_v14_legacy_v6):
            errors.append(_error(
                "QUALITY_CONTRACT_INVALID", "quality_contract_id",
                (
                    f"schema 1.4 requires {QUALITY_CONTRACT_ID_V14} for new work; "
                    f"{QUALITY_CONTRACT_ID_V14_LEGACY} is accepted only as historical v6"
                ),
            ))
    elif quality_v11 and document.get("quality_contract_id") != expected_quality_contract:
        errors.append(_error(
            "QUALITY_CONTRACT_INVALID", "quality_contract_id",
            f"schema {schema_version} requires {expected_quality_contract}",
        ))
    variants = document.get("variants")
    if not isinstance(variants, list):
        return _result([_error("VARIANTS_INVALID", "variants", "variants must be an array")])
    if document.get("normalization_id") != "nfkc_casefold_ws_punct_v1":
        errors.append(_error("SUBMISSION_FIELD_MISSING", "normalization_id", "submission requires nfkc_casefold_ws_punct_v1"))
    mode = document.get("compile_mode")
    if mode not in {"single_compile", "sku_batch_compile", "repair"}:
        errors.append(_error("COMPILE_MODE_INVALID", "compile_mode", "compile_mode must be single_compile, sku_batch_compile, or repair"))
    elif (mode in {"single_compile", "repair"} and len(variants) != 1) or (mode == "sku_batch_compile" and len(variants) < 2):
        errors.append(_error("COMPILE_MODE_VARIANT_COUNT", "variants", "variant count does not match compile_mode", compile_mode=mode, count=len(variants)))
    batch_key = document.get("batch_key")
    duration = batch_key.get("duration_seconds") if isinstance(batch_key, dict) else None
    if _decimal(duration) is None or Decimal(str(duration)) <= 0:
        errors.append(_error("BATCH_DURATION_INVALID", "batch_key.duration_seconds", "positive numeric duration_seconds is required"))
    if quality_v11:
        for field in ("model_preset", "market", "voiceover_language", "platform", "aspect_ratio", "resolution"):
            if not isinstance(batch_key, dict) or not _nonempty(batch_key.get(field)):
                errors.append(_error(
                    "SUBMISSION_FIELD_MISSING", "batch_key." + field,
                    "schema 1.1 compact prompt requires this batch setting",
                ))
    if market_v14:
        if market_v14_current:
            frame_rate = batch_key.get("frame_rate_fps") if isinstance(batch_key, dict) else None
            if not isinstance(frame_rate, int) or isinstance(frame_rate, bool) or frame_rate <= 0:
                errors.append(_error(
                    "GLOBAL_DEADLINE_INVALID", "batch_key.frame_rate_fps",
                    "current schema 1.4 work requires a positive integer frame_rate_fps",
                ))
        if document.get("market_prompt_contract_id") != _market_contract.MARKET_PROMPT_CONTRACT_ID:
            errors.append(_error(
                "MARKET_PROFILE_INVALID", "market_prompt_contract_id",
                f"schema 1.4 requires {_market_contract.MARKET_PROMPT_CONTRACT_ID}",
            ))
        errors.extend(_market_contract.validate_market_tuple(batch_key))
    claims_registry: dict[str, dict[str, Any]] = {}
    claims_allowlist_ids: set[str] = set()
    research_bundle: dict[str, Any] = {}
    source_map: dict[str, dict[str, Any]] = {}
    evidence_map: dict[str, dict[str, Any]] = {}
    review_theme_map: dict[str, dict[str, Any]] = {}
    pain_map: dict[str, dict[str, Any]] = {}
    if motion_v12:
        shared_core = document.get("shared_core")
        if not isinstance(shared_core, dict):
            errors.append(_error("CLAIMS_REGISTRY_INVALID", "shared_core", "schema 1.2+ requires shared_core claims evidence"))
            shared_core = {}
        if evidence_v13:
            research_bundle, source_map, evidence_map, review_theme_map, pain_map = _validate_research_bundle(shared_core, errors)
        allowlist = shared_core.get("claims_allowlist")
        if (
            not isinstance(allowlist, list) or not allowlist
            or any(not _nonempty(item) for item in allowlist)
            or len({_norm(item) for item in allowlist}) != len(allowlist)
        ):
            errors.append(_error("CLAIMS_REGISTRY_INVALID", "shared_core.claims_allowlist", "schema 1.2+ requires a non-empty unique claim_id allowlist"))
            allowlist = []
        claims_allowlist_ids = {str(item) for item in allowlist}
        registry = shared_core.get("claims_registry")
        if not isinstance(registry, list) or not registry:
            errors.append(_error("CLAIMS_REGISTRY_INVALID", "shared_core.claims_registry", "schema 1.2+ requires a non-empty claims_registry"))
            registry = []
        for claim_index, claim in enumerate(registry):
            claim_path = f"shared_core.claims_registry[{claim_index}]"
            if not isinstance(claim, dict):
                errors.append(_error("CLAIMS_REGISTRY_INVALID", claim_path, "claim entry must be an object"))
                continue
            claim_id = claim.get("claim_id")
            if not _nonempty(claim_id) or claim_id in claims_registry:
                errors.append(_error("CLAIMS_REGISTRY_INVALID", claim_path + ".claim_id", "claim_id must be non-empty and unique"))
                continue
            if (
                not _nonempty(claim.get("feature_id"))
                or claim.get("product_part_id") not in PRODUCT_PART_IDS
                or claim.get("evidence_basis") not in EVIDENCE_BASES
                or not _nonempty(claim.get("evidence_ref"))
                or claim.get("assertion_level") not in ASSERTION_LEVELS
            ):
                errors.append(_error("CLAIMS_REGISTRY_INVALID", claim_path, "claim evidence fields are missing or invalid"))
            if evidence_v13:
                evidence_ids = _string_set(claim.get("evidence_ids"))
                claim_mode = claim.get("claim_mode")
                assertion_kind = claim.get("assertion_kind")
                if evidence_ids is None or not evidence_ids or claim_mode not in CLAIM_MODES or assertion_kind not in ASSERTION_KINDS:
                    errors.append(_error(
                        "CLAIM_EVIDENCE_INVALID", claim_path,
                        "schema 1.3 claims require non-empty evidence_ids, assertion_kind, and claim_mode",
                    ))
                    evidence_ids = set()
                unresolved = sorted(evidence_ids - set(evidence_map))
                if unresolved:
                    errors.append(_error(
                        "CLAIM_EVIDENCE_INVALID", claim_path + ".evidence_ids",
                        "every claim evidence ID must resolve in the research bundle",
                        unresolved=unresolved,
                    ))
                required_use = {
                    "direct": "claim_direct", "qualified": "claim_qualified", "visual_only": "visual_only",
                }.get(claim_mode)
                for evidence_id in sorted(evidence_ids & set(evidence_map)):
                    item = evidence_map[evidence_id]
                    source = source_map.get(str(item.get("source_id")), {})
                    uses = _string_set(item.get("permitted_uses")) or set()
                    if required_use not in uses:
                        errors.append(_error(
                            "CLAIM_EVIDENCE_INVALID", claim_path + ".evidence_ids",
                            "claim mode is not permitted by its linked evidence item",
                            evidence_id=evidence_id, claim_mode=claim_mode,
                        ))
                    if item.get("exact_product_match") is not True or item.get("conflict_status") != "clear":
                        errors.append(_error(
                            "CLAIM_EVIDENCE_INVALID", claim_path + ".evidence_ids",
                            "script claims require clear exact-product evidence",
                            evidence_id=evidence_id,
                        ))
                    allowed_source_kinds = EVIDENCE_BASIS_SOURCE_KINDS.get(str(claim.get("evidence_basis")), set())
                    if source.get("source_kind") not in allowed_source_kinds:
                        errors.append(_error(
                            "CLAIM_EVIDENCE_INVALID", claim_path + ".evidence_ids",
                            "claim evidence_basis is incompatible with its evidence source kind",
                            evidence_id=evidence_id, evidence_basis=claim.get("evidence_basis"),
                            source_kind=source.get("source_kind"),
                        ))
                    if claim_mode in {"direct", "visual_only"} and (
                        item.get("exact_variant_match") is not True
                        or source.get("variant_scope") != "exact_child"
                    ):
                        errors.append(_error(
                            "CLAIM_EVIDENCE_INVALID", claim_path + ".evidence_ids",
                            "direct and visual-only selected-variant claims require exact-child evidence with exact_variant_match=true",
                            evidence_id=evidence_id, exact_variant_match=item.get("exact_variant_match"),
                            variant_scope=source.get("variant_scope"),
                        ))
                    if source.get("source_kind") in {"amazon_review_summary", "third_party_summary", "amazon_qa"}:
                        errors.append(_error("CLAIM_EVIDENCE_INVALID", claim_path, "discovery-only evidence cannot authorize a claim"))
                    if source.get("source_kind") == "amazon_customer_review" and claim_mode != "qualified":
                        errors.append(_error("CLAIM_EVIDENCE_INVALID", claim_path, "customer-review claims must remain qualified buyer experience"))
                    if source.get("source_kind") == "amazon_customer_image" and assertion_kind not in {"visible_feature", "buyer_visual_observation"}:
                        errors.append(_error("CLAIM_EVIDENCE_INVALID", claim_path, "customer images can support visible observations only"))
            spoken_claim_terms = claim.get("spoken_claim_terms")
            if (
                not isinstance(spoken_claim_terms, list) or not spoken_claim_terms
                or any(not _nonempty(term) for term in spoken_claim_terms)
                or len({_norm(term) for term in spoken_claim_terms}) != len(spoken_claim_terms)
            ):
                errors.append(_error("CLAIMS_REGISTRY_INVALID", claim_path + ".spoken_claim_terms", "each claim needs non-empty unique target-language spoken terms"))
            if _has_multiple_targets(claim.get("feature_id")):
                errors.append(_error("CLAIMS_REGISTRY_INVALID", claim_path + ".feature_id", "one claim entry may describe only one garment part or effect"))
            if claim.get("assertion_level") == "numeric" and (
                not evidence_v13 and claim.get("evidence_basis") != "verified_test"
                or evidence_v13 and not _v13_numeric_claim_ok(claim, evidence_map, source_map)
            ):
                errors.append(_error("CLAIM_EVIDENCE_INVALID", claim_path, "numeric claims require verified test, except exact composition from eligible exact-product catalog/label evidence"))
            claims_registry[str(claim_id)] = claim
        unknown_allowlist_ids = sorted(claims_allowlist_ids - set(claims_registry))
        if unknown_allowlist_ids:
            errors.append(_error(
                "CLAIMS_REGISTRY_INVALID", "shared_core.claims_allowlist",
                "every allowlisted claim_id must resolve in claims_registry",
                unknown_claim_ids=unknown_allowlist_ids,
            ))
        if evidence_v13:
            for pain_id, pain in pain_map.items():
                solution_claim_ids = _string_set(pain.get("solution_claim_ids")) or set()
                unresolved = sorted(solution_claim_ids - claims_allowlist_ids)
                if unresolved:
                    errors.append(_error(
                        "BUYER_PAIN_MAP_INVALID", "shared_core.pain_solution_map",
                        "every pain solution claim must resolve in the claims allowlist",
                        pain_point_id=pain_id, unresolved_claim_ids=unresolved,
                    ))
                if pain.get("status") == "script_eligible":
                    unsupported_solutions: list[str] = []
                    for solution_claim_id in sorted(solution_claim_ids & set(claims_registry)):
                        solution_claim = claims_registry[solution_claim_id]
                        solution_evidence_ids = _string_set(solution_claim.get("evidence_ids")) or set()
                        has_independent_exact_variant_evidence = any(
                            evidence_id in evidence_map
                            and evidence_map[evidence_id].get("exact_product_match") is True
                            and evidence_map[evidence_id].get("exact_variant_match") is True
                            and evidence_map[evidence_id].get("conflict_status") == "clear"
                            and source_map.get(str(evidence_map[evidence_id].get("source_id")), {}).get("variant_scope") == "exact_child"
                            and source_map.get(str(evidence_map[evidence_id].get("source_id")), {}).get("source_kind") in INDEPENDENT_SOLUTION_SOURCE_KINDS
                            for evidence_id in solution_evidence_ids
                        )
                        if not has_independent_exact_variant_evidence:
                            unsupported_solutions.append(solution_claim_id)
                    if unsupported_solutions:
                        errors.append(_error(
                            "BUYER_PAIN_MAP_INVALID", "shared_core.pain_solution_map",
                            "script-eligible pains require an independently evidenced exact-variant product solution, not review evidence alone",
                            pain_point_id=pain_id, unsupported_solution_claim_ids=unsupported_solutions,
                        ))
    variant_ids: set[str] = set()
    timeline_ids: set[str] = set()
    contexts: list[dict[str, Any]] = []
    prompt_hashes: dict[str, list[tuple[int, str]]] = {}
    legacy_v4_seen = False
    for index, variant in enumerate(variants):
        base = f"variants[{index}]"
        if not isinstance(variant, dict):
            errors.append(_error("VARIANT_TYPE_INVALID", base, "variant must be an object"))
            continue
        variant_id = variant.get("variant_id")
        if not isinstance(variant_id, str) or not variant_id.strip():
            errors.append(_error("VARIANT_ID_MISSING", base + ".variant_id", "non-empty variant_id is required"))
        elif variant_id in variant_ids:
            errors.append(_error("DUPLICATE_VARIANT_ID", base + ".variant_id", "variant_id must be unique", variant_id=variant_id))
        else:
            variant_ids.add(variant_id)
        existing_renderings = variant.get("renderings") if isinstance(variant.get("renderings"), dict) else {}
        existing_prompt = existing_renderings.get("prompt") if isinstance(existing_renderings.get("prompt"), dict) else {}
        existing_serializer = existing_prompt.get("serializer_id")
        legacy_v13_prompt = schema_version == "1.3" and existing_serializer == PROMPT_V4_SERIALIZER_ID
        if legacy_v13_prompt:
            legacy_v4_seen = True
        identity_safe_v13 = evidence_v13 and not legacy_v13_prompt
        generation_controls: dict[str, Any] = {}
        if market_v14:
            raw_controls = variant.get("generation_controls")
            errors.extend(_market_contract.validate_generation_controls(
                batch_key.get("market_prompt_profile_id") if isinstance(batch_key, dict) else None,
                raw_controls, base + ".generation_controls",
            ))
            if isinstance(raw_controls, dict):
                generation_controls = raw_controls
        if not isinstance(variant.get("three_view_path"), str) or not variant["three_view_path"].strip() or not isinstance(variant.get("source_images"), list) or not variant["source_images"]:
            errors.append(_error("MISSING_VARIANT_REFERENCE", base, "source_images and three_view_path are required"))
        if variant.get("three_view_qc") not in {"passed", True}:
            errors.append(_error("THREE_VIEW_QC_FAILED", base + ".three_view_qc", "three-view QC must be passed"))
        signature = variant.get("garment_signature")
        if not isinstance(signature, dict):
            errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".garment_signature", "submission requires garment_signature"))
            signature = {}
        for field in GARMENT_FIELDS:
            if not isinstance(signature.get(field), str) or not signature[field].strip():
                errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".garment_signature." + field, "garment signature field is required"))
        evidence_hash = variant.get("three_view_sha256")
        if not isinstance(evidence_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_hash):
            errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".three_view_sha256", "submission requires lowercase SHA-256 evidence hash"))
        references = variant.get("references")
        if not isinstance(references, list) or not references:
            errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".references", "submission requires a references array"))
            references = []
        reference_map: dict[str, list[dict[str, Any]]] = {}
        interface_tags: set[str] = set()
        identity_safe_tag_indices: list[int] = []
        for ref_index, reference in enumerate(references):
            ref_path = f"{base}.references[{ref_index}]"
            if not isinstance(reference, dict) or not isinstance(reference.get("reference_id"), str) or not reference["reference_id"]:
                errors.append(_error("MISSING_VARIANT_REFERENCE", ref_path, "reference_id is required"))
                continue
            reference_map.setdefault(reference["reference_id"], []).append(reference)
            if reference.get("variant_id") != variant_id or reference.get("role") != "apparel_three_view":
                errors.append(_error("VARIANT_REFERENCE_MISMATCH", ref_path, "reference owner/role does not match variant"))
            if reference.get("local_path") != variant.get("three_view_path") or reference.get("sha256") != evidence_hash:
                errors.append(_error("VARIANT_REFERENCE_MISMATCH", ref_path, "reference path/hash does not match three-view evidence"))
            if quality_v11:
                interface_tag = reference.get("interface_tag")
                if (
                    not _nonempty(interface_tag) or str(interface_tag) != str(interface_tag).strip()
                    or INTERFACE_TAG_PATTERN.fullmatch(str(interface_tag)) is None
                    or (identity_safe_v13 and V5_GARMENT_TAG_PATTERN.fullmatch(str(interface_tag)) is None)
                    or any(control in str(interface_tag) for control in ("\r", "\n", "\t"))
                    or str(interface_tag) in interface_tags
                ):
                    errors.append(_error(
                        "REFERENCE_TAG_INVALID", ref_path + ".interface_tag",
                        (
                            "canonical_prompt_v5 garment interface_tag must be an exact, unique @ImageN token with N >= 2; @Image1 is reserved for fixed-model identity"
                            if identity_safe_v13 else
                            "interface_tag must be an exact, unique interface token in @图片N or @ImageN form"
                        ),
                    ))
                else:
                    interface_tags.add(str(interface_tag))
                    if identity_safe_v13:
                        tag_index = _interface_tag_index(interface_tag)
                        if tag_index is not None:
                            identity_safe_tag_indices.append(tag_index)
                must_transfer = _string_set(reference.get("must_transfer"))
                must_not_transfer = _string_set(reference.get("must_not_transfer"))
                missing_transfer = sorted(REQUIRED_TRANSFER - (must_transfer or set()))
                required_non_transfer = REQUIRED_NON_TRANSFER if identity_safe_v13 else LEGACY_REQUIRED_NON_TRANSFER
                missing_non_transfer = sorted(required_non_transfer - (must_not_transfer or set()))
                overlap = sorted((must_transfer or set()) & (must_not_transfer or set()))
                if (
                    must_transfer is None or must_not_transfer is None
                    or missing_transfer or missing_non_transfer or overlap
                    or reference.get("positive_replacement") != POSITIVE_REPLACEMENT
                ):
                    errors.append(_error(
                        "REFERENCE_TRANSFER_CONTRACT_INVALID", ref_path,
                        "schema 1.1 apparel reference transfer contract is incomplete or contradictory",
                        missing_must_transfer=missing_transfer,
                        missing_must_not_transfer=missing_non_transfer,
                        overlap=overlap,
                        positive_replacement=reference.get("positive_replacement"),
                    ))
                if identity_safe_v13 and reference.get("human_identity_pixels_absent") is not True:
                    errors.append(_error(
                        "THREE_VIEW_QC_FAILED", ref_path + ".human_identity_pixels_absent",
                        "canonical_prompt_v5 requires a passed zero-human-identity-pixel visual audit; prompt exclusions cannot waive it",
                    ))
                if market_v14_current:
                    audit = reference.get("identity_cue_audit")
                    audit_sha256 = reference.get("identity_cue_audit_sha256")
                    expected_keys = {
                        "audit_version", "audited_sha256", "inspection_method", "reviewed_at", "passed",
                        *ZERO_HUMAN_IDENTITY_CUE_FIELDS,
                    }
                    audit_valid = isinstance(audit, dict) and set(audit) == expected_keys
                    if audit_valid:
                        audit_valid = (
                            audit.get("audit_version") == ZERO_HUMAN_IDENTITY_AUDIT_VERSION
                            and audit.get("audited_sha256") == reference.get("sha256")
                            and audit.get("inspection_method") == ZERO_HUMAN_IDENTITY_INSPECTION_METHOD
                            and _nonempty(audit.get("reviewed_at"))
                            and audit.get("passed") is True
                            and all(audit.get(field) is False for field in ZERO_HUMAN_IDENTITY_CUE_FIELDS)
                            and audit_sha256 == _canonical_sha256(audit)
                        )
                    if not audit_valid:
                        errors.append(_error(
                            "THREE_VIEW_IDENTITY_AUDIT_INVALID", ref_path + ".identity_cue_audit",
                            "new schema-1.4 work requires a hash-bound full-resolution cue audit with every human-identity cue false",
                        ))
        if identity_safe_v13 and identity_safe_tag_indices != list(range(2, 2 + len(references))):
            errors.append(_error(
                "REFERENCE_TAG_INVALID", base + ".references",
                "canonical_prompt_v5 garment references must be ordered contiguously as @Image2, @Image3, and so on after fixed-model @Image1",
                actual_indices=identity_safe_tag_indices,
                expected_indices=list(range(2, 2 + len(references))),
            ))
        quality_plan = variant.get("quality_plan")
        lip_sync_priority = (
            isinstance(quality_plan, dict)
            and quality_plan.get("secondary_fidelity_spend") == "lip_sync"
        )
        claim_proof_map: dict[str, dict[str, Any]] = {}
        if quality_v11:
            if not isinstance(quality_plan, dict):
                errors.append(_error("QUALITY_PLAN_MISSING", base + ".quality_plan", "schema 1.1 requires a quality_plan object"))
                quality_plan = {}
            directing_intent = quality_plan.get("directing_intent")
            if (
                not _nonempty(directing_intent)
                or _norm(directing_intent) in {"cinematic", "premium", "beautiful", "viral", "highend"}
            ):
                errors.append(_error(
                    "DIRECTING_INTENT_INVALID", base + ".quality_plan.directing_intent",
                    "directing_intent must name one concrete buyer response, not a generic look word",
                ))
            if quality_plan.get("directorial_voice") not in DIRECTORIAL_VOICES:
                errors.append(_error(
                    "DIRECTORIAL_VOICE_INVALID", base + ".quality_plan.directorial_voice",
                    "directorial_voice must be a supported functional voice",
                ))
            economized = _string_set(quality_plan.get("economized_elements"))
            if (
                quality_plan.get("primary_fidelity_spend") != "garment_identity"
                or quality_plan.get("secondary_fidelity_spend") not in SECONDARY_FIDELITY_SPENDS
                or not economized
            ):
                errors.append(_error(
                    "FIDELITY_ALLOCATION_INVALID", base + ".quality_plan",
                    "quality plan must prioritize garment identity, choose one secondary spend, and economize at least one element",
                ))
            anchors = quality_plan.get("continuity_anchors")
            if not isinstance(anchors, dict) or any(not _nonempty(anchors.get(field)) for field in CONTINUITY_ANCHORS):
                errors.append(_error(
                    "QUALITY_PLAN_MISSING", base + ".quality_plan.continuity_anchors",
                    "all continuity anchors must be non-empty",
                ))
            if motion_v12:
                claim_proof_plan = quality_plan.get("claim_proof_plan")
                if not isinstance(claim_proof_plan, list):
                    errors.append(_error("PROOF_PLAN_MISSING", base + ".quality_plan.claim_proof_plan", "schema 1.2+ requires claim_proof_plan"))
                    claim_proof_plan = []
                required_actions = (
                    _minimum_market_product_actions(duration)
                    if market_v14 else _minimum_product_actions(duration)
                )
                if len(claim_proof_plan) < required_actions:
                    errors.append(_error(
                        "ACTION_COVERAGE_INSUFFICIENT", base + ".quality_plan.claim_proof_plan",
                        "claim-proof plan does not meet the minimum sequential product-action coverage",
                        required=required_actions, actual=len(claim_proof_plan),
                    ))
                detail_targets: set[str] = set()
                planned_beat_ids: set[str] = set()
                for proof_index, proof in enumerate(claim_proof_plan):
                    proof_path = f"{base}.quality_plan.claim_proof_plan[{proof_index}]"
                    if not isinstance(proof, dict):
                        errors.append(_error("PROOF_PLAN_MISSING", proof_path, "claim-proof entry must be an object"))
                        continue
                    proof_id = proof.get("claim_proof_id")
                    claim_id = proof.get("claim_id")
                    beat_id = proof.get("beat_id")
                    if not _nonempty(proof_id) or proof_id in claim_proof_map:
                        errors.append(_error("PROOF_PLAN_MISSING", proof_path + ".claim_proof_id", "claim_proof_id must be non-empty and unique"))
                        continue
                    if not _nonempty(beat_id) or beat_id in planned_beat_ids:
                        errors.append(_error("PROOF_PLAN_MISSING", proof_path + ".beat_id", "each claim-proof entry must target one unique beat"))
                    else:
                        planned_beat_ids.add(str(beat_id))
                    claim = claims_registry.get(str(claim_id))
                    if claim is None:
                        errors.append(_error("CLAIM_EVIDENCE_INVALID", proof_path + ".claim_id", "claim_id must resolve in shared_core.claims_registry"))
                    elif str(claim_id) not in claims_allowlist_ids:
                        errors.append(_error("CLAIM_EVIDENCE_INVALID", proof_path + ".claim_id", "every used claim_id must be explicitly allowlisted"))
                    valid_plan = (
                        _nonempty(proof.get("spoken_intent_id"))
                        and _nonempty(proof.get("proof_target"))
                        and proof.get("product_part_id") in PRODUCT_PART_IDS
                        and proof.get("evidence_basis") in EVIDENCE_BASES
                        and proof.get("framing_class") in FRAMING_CLASSES
                        and proof.get("action_type") in PROOF_ACTION_TYPES
                        and proof.get("hands_required") in HANDS_REQUIRED
                        and _nonempty(proof.get("expected_visible_change"))
                    )
                    if not valid_plan:
                        errors.append(_error("PROOF_PLAN_MISSING", proof_path, "claim-proof action, framing, hand, or endpoint fields are invalid"))
                    elif not _action_matches_target(proof.get("proof_target"), proof.get("action_type")):
                        errors.append(_error(
                            "CLAIM_PROOF_ACTION_MISMATCH", proof_path + ".action_type",
                            "proof action type does not demonstrate the named garment target",
                            proof_target=proof.get("proof_target"), action_type=proof.get("action_type"),
                        ))
                    if _has_multiple_targets(proof.get("proof_target")):
                        errors.append(_error("PROOF_PLAN_MISSING", proof_path + ".proof_target", "one proof beat may target only one garment part or effect"))
                    if claim is not None and not _mentions_target(proof.get("proof_target"), claim.get("feature_id")):
                        errors.append(_error("CLAIM_PROOF_ACTION_MISMATCH", proof_path + ".proof_target", "proof target must match the registered claim feature"))
                    if claim is not None and proof.get("product_part_id") != claim.get("product_part_id"):
                        errors.append(_error("CLAIM_PROOF_ACTION_MISMATCH", proof_path + ".product_part_id", "proof product_part_id must equal the registered claim group"))
                    if valid_plan and not _framing_matches_target(proof.get("proof_target"), proof.get("framing_class")):
                        errors.append(_error(
                            "CLAIM_PROOF_FRAMING_MISMATCH", proof_path + ".framing_class",
                            "framing class is too wide or otherwise unsuitable for the named garment target",
                            proof_target=proof.get("proof_target"), framing_class=proof.get("framing_class"),
                        ))
                    if proof.get("framing_class") in {"detail_closeup", "chest_to_hem", "side_back"}:
                        detail_targets.add(str(proof.get("product_part_id")))
                    if claim is not None and proof.get("evidence_basis") != claim.get("evidence_basis"):
                        errors.append(_error("CLAIM_EVIDENCE_INVALID", proof_path + ".evidence_basis", "proof evidence basis must match its registered claim"))
                    if evidence_v13 and claim is not None:
                        proof_evidence_ids = _string_set(proof.get("evidence_ids"))
                        claim_evidence_ids = _string_set(claim.get("evidence_ids"))
                        if proof_evidence_ids is None or proof_evidence_ids != claim_evidence_ids:
                            errors.append(_error(
                                "CLAIM_EVIDENCE_INVALID", proof_path + ".evidence_ids",
                                "proof evidence IDs must exactly equal its registered claim evidence IDs",
                                expected=sorted(claim_evidence_ids or set()), actual=sorted(proof_evidence_ids or set()),
                            ))
                    performance_demo = (
                        proof.get("action_type") == "pull_release"
                        or _contains_any(proof.get("proof_target"), PERFORMANCE_TERMS)
                        or (claim is not None and _contains_any(claim.get("feature_id"), PERFORMANCE_TERMS))
                    )
                    required_performance_groups = _performance_group_ids(
                        proof.get("proof_target"), claim.get("feature_id") if isinstance(claim, dict) else None,
                    )
                    if proof.get("action_type") == "pull_release":
                        required_performance_groups.add("stretch")
                    claim_performance_groups = _performance_group_ids(claim.get("feature_id")) if isinstance(claim, dict) else set()
                    stretch_claim = (
                        _contains_any(proof.get("proof_target"), STRETCH_TERMS)
                        or (claim is not None and _contains_any(claim.get("feature_id"), STRETCH_TERMS))
                    )
                    if stretch_claim and (
                        proof.get("action_type") != "pull_release" or proof.get("hands_required") != "two"
                    ):
                        errors.append(_error(
                            "CLAIM_PROOF_ACTION_MISMATCH", proof_path,
                            "a stretch/elasticity claim requires one coordinated two-hand pull-release proof",
                        ))
                    performance_evidence_ok = (
                        _v13_performance_evidence_ok(claim, evidence_map, source_map)
                        if evidence_v13 else isinstance(claim, dict) and claim.get("evidence_basis") in {"user_provided", "product_page", "verified_test"}
                    )
                    if performance_demo and (
                        claim is None
                        or str(claim_id) not in claims_allowlist_ids
                        or not performance_evidence_ok
                        or not required_performance_groups.issubset(claim_performance_groups)
                    ):
                        errors.append(_error(
                            "UNSUPPORTED_PERFORMANCE_DEMO", proof_path,
                            "performance demonstrations require explicit non-visual evidence and an allowlisted claim",
                        ))
                    claim_proof_map[str(proof_id)] = proof
                required_detail_count = min(3, required_actions)
                if len(detail_targets) < required_detail_count:
                    errors.append(_error(
                        "ACTION_COVERAGE_INSUFFICIENT", base + ".quality_plan.claim_proof_plan",
                        "insufficient distinct garment part/effect proofs",
                        required=required_detail_count, actual=len(detail_targets), targets=sorted(detail_targets),
                    ))
        timeline = variant.get("canonical_timeline")
        if not isinstance(timeline, dict):
            errors.append(_error("TIMELINE_INVALID", base + ".canonical_timeline", "canonical_timeline must be an object"))
            continue
        timeline_id = timeline.get("timeline_id")
        if not isinstance(timeline_id, str) or not timeline_id.strip():
            errors.append(_error("TIMELINE_ID_MISSING", base + ".canonical_timeline.timeline_id", "non-empty timeline_id is required"))
        elif timeline_id in timeline_ids:
            errors.append(_error("DUPLICATE_TIMELINE_ID", base + ".canonical_timeline.timeline_id", "timeline_id must be unique", timeline_id=timeline_id))
        else:
            timeline_ids.add(timeline_id)
        timeline_duration = timeline.get("duration_seconds")
        if _decimal(timeline_duration) is None or _decimal(duration) is None or not _same(timeline_duration, duration):
            errors.append(_error("DURATION_MISMATCH", base + ".canonical_timeline.duration_seconds", "timeline duration must equal batch duration"))
        version = timeline.get("timeline_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            errors.append(_error("TIMELINE_INVALID", base + ".canonical_timeline.timeline_version", "positive integer timeline_version is required"))
        beats = timeline.get("beats")
        if not isinstance(beats, list) or not beats:
            errors.append(_error("TIMELINE_INVALID", base + ".canonical_timeline.beats", "non-empty beats array is required"))
            continue
        if market_v14:
            errors.extend(_market_contract.validate_scene_alignment(
                generation_controls.get("scene_strategy"), beats,
                base + ".canonical_timeline.beats",
            ))
        if market_v14_current:
            errors.extend(_market_contract.validate_three_layer_deadlines(
                variant.get("three_layer_deadlines"),
                batch_key,
                generation_controls,
                variant,
                beats,
                quality_plan,
                base + ".three_layer_deadlines",
            ))
        beat_ids: set[str] = set()
        previous_end = Decimal("0")
        setup_runs: list[str] = []
        visible_lipsync_words = 0
        visible_lipsync_beats = 0
        total_spoken_words = 0
        on_camera_expression_cues: list[str] = []
        symmetric_hip_reset_beats: list[str] = []
        bound_claim_proofs: set[str] = set()
        product_action_beat_ids: list[str] = []
        voiceover_openers: dict[str, list[str]] = {}
        outbound_text_values: list[tuple[str, Any]] = []
        cta_beat_indices: list[int] = []
        camera_text_values: list[tuple[str, Any]] = []
        audio_text_values: list[tuple[str, Any]] = []
        voiceover_language = str(batch_key.get("voiceover_language", "")).casefold() if isinstance(batch_key, dict) else ""
        enforce_word_budget = voiceover_language.startswith(("en", "de"))
        for beat_index, beat in enumerate(beats):
            beat_path = f"{base}.canonical_timeline.beats[{beat_index}]"
            if not isinstance(beat, dict):
                errors.append(_error("TIMELINE_INVALID", beat_path, "beat must be an object"))
                continue
            beat_id = beat.get("beat_id")
            if not isinstance(beat_id, str) or not beat_id.strip():
                errors.append(_error("TIMELINE_INVALID", beat_path + ".beat_id", "non-empty beat_id is required"))
            elif beat_id in beat_ids:
                errors.append(_error("DUPLICATE_BEAT_ID", beat_path + ".beat_id", "beat_id must be unique within its timeline", beat_id=beat_id))
            else:
                beat_ids.add(beat_id)
            if market_v14:
                if beat.get("purpose") == "cta":
                    cta_beat_indices.append(beat_index)
                outbound_fields = (
                    "framing", "camera_motion", "camera_motivation", "scene", "lighting",
                    "light_motivation", "actor", "core_action", "action_target",
                    "left_hand_state", "right_hand_state", "spoken_line", "silence_reason",
                    "product_point", "styling_point", "micro_expression", "audio",
                    "visible_endpoint", "proof_endpoint",
                )
                for field in outbound_fields:
                    outbound_text_values.append((beat_path + "." + field, beat.get(field)))
                camera_text_values.extend((
                    (beat_path + ".camera_motion", beat.get("camera_motion")),
                    (beat_path + ".camera_motivation", beat.get("camera_motivation")),
                ))
                audio_text_values.append((beat_path + ".audio", beat.get("audio")))
            binding = beat.get("reference_binding")
            if not isinstance(binding, str) or len(reference_map.get(binding, [])) != 1:
                errors.append(_error("VARIANT_REFERENCE_MISMATCH", beat_path + ".reference_binding", "canonical beat binding must resolve exactly once in this variant"))
            if quality_v11:
                missing_base_fields = [field for field in REQUIRED_BASE_BEAT_FIELDS if not _nonempty(beat.get(field))]
                if missing_base_fields:
                    errors.append(_error(
                        "BEAT_QUALITY_FIELD_MISSING", beat_path,
                        "schema 1.1 beat is missing concrete visible generation fields",
                        fields=missing_base_fields,
                    ))
                invalid_enums: dict[str, Any] = {}
                if beat.get("purpose") not in BEAT_PURPOSES:
                    invalid_enums["purpose"] = beat.get("purpose")
                if beat.get("product_visibility") not in PRODUCT_VISIBILITIES:
                    invalid_enums["product_visibility"] = beat.get("product_visibility")
                if invalid_enums:
                    errors.append(_error(
                        "BEAT_ENUM_INVALID", beat_path,
                        "purpose and product_visibility must use schema 1.1 enum values",
                        invalid=invalid_enums,
                    ))
                missing_quality_fields = [
                    field for field in ("camera_setup_id", "camera_motivation", "light_motivation", "visible_endpoint")
                    if not _nonempty(beat.get(field))
                ]
                if missing_quality_fields:
                    errors.append(_error(
                        "BEAT_QUALITY_FIELD_MISSING", beat_path,
                        "schema 1.1 beat is missing a motivated setup or visible endpoint",
                        fields=missing_quality_fields,
                    ))
                setup_id = beat.get("camera_setup_id")
                if _nonempty(setup_id) and (not setup_runs or setup_runs[-1] != setup_id):
                    setup_runs.append(str(setup_id))
                speech_mode = beat.get("speech_mode")
                mouth_visibility = beat.get("mouth_visibility")
                lip_sync_required = beat.get("lip_sync_required")
                spoken_line = beat.get("spoken_line")
                line_present = _nonempty(spoken_line)
                if line_present:
                    total_spoken_words += _word_count(spoken_line)
                speech_valid = speech_mode in SPEECH_MODES and mouth_visibility in MOUTH_VISIBILITIES and isinstance(lip_sync_required, bool)
                if speech_mode == "on_camera_dialogue":
                    speech_valid = speech_valid and line_present and mouth_visibility == "visible" and lip_sync_required is True
                elif speech_mode == "offscreen_voiceover":
                    speech_valid = speech_valid and line_present and mouth_visibility == "not_visible" and lip_sync_required is False
                elif speech_mode == "none":
                    speech_valid = speech_valid and not line_present and lip_sync_required is False
                if not speech_valid:
                    errors.append(_error(
                        "SPEECH_MODE_INVALID", beat_path,
                        "speech_mode, spoken_line, mouth_visibility, and lip_sync_required are inconsistent",
                    ))
                if market_v14:
                    expected_spoken_language = batch_key.get("voiceover_language") if isinstance(batch_key, dict) else None
                    spoken_language = beat.get("spoken_language")
                    if line_present:
                        if spoken_language != expected_spoken_language:
                            errors.append(_error(
                                "MARKET_LANGUAGE_MISMATCH", beat_path + ".spoken_language",
                                "every spoken beat must declare the exact batch voiceover language",
                                expected=expected_spoken_language, actual=spoken_language,
                            ))
                        language_problem = _market_contract.language_issue(
                            spoken_line, str(batch_key.get("market") if isinstance(batch_key, dict) else ""),
                        )
                        if language_problem:
                            errors.append(_error(
                                "LANGUAGE_GATE_FAILED", beat_path + ".spoken_line", language_problem,
                            ))
                    elif spoken_language is not None:
                        errors.append(_error(
                            "MARKET_LANGUAGE_MISMATCH", beat_path + ".spoken_language",
                            "silent beats require spoken_language=null",
                        ))
                if motion_v12 and line_present and beat.get("purpose") != "cta" and _contains_any(spoken_line, FORMAL_VOICEOVER_TERMS):
                    errors.append(_error(
                        "VOICEOVER_STYLE_TOO_FORMAL", beat_path + ".spoken_line",
                        "voiceover must sound like everyday speech, not a brochure, official transition, or seller-page sentence",
                    ))
                motion_budget = beat.get("motion_budget")
                motion_valid = isinstance(motion_budget, dict) and all(
                    motion_budget.get(axis) in allowed for axis, allowed in MOTION_BUDGET_VALUES.items()
                )
                if not motion_valid:
                    errors.append(_error(
                        "MOTION_BUDGET_INVALID", beat_path + ".motion_budget",
                        "motion_budget must declare valid camera, performer, and active_hands loads",
                    ))
                    motion_budget = {}
                else:
                    active_systems = sum((
                        motion_budget.get("camera") == "active",
                        motion_budget.get("performer") == "active",
                        motion_budget.get("active_hands") in {"one", "two"},
                    ))
                    if active_systems > 1:
                        errors.append(_error(
                            "MOTION_BUDGET_INVALID", beat_path + ".motion_budget",
                            "a beat may use at most one active camera, performer, or hand-motion system",
                            active_systems=active_systems,
                        ))
                if motion_v12:
                    beat_role = beat.get("beat_role")
                    posture_id = beat.get("posture_id")
                    if beat_role not in BEAT_ROLES:
                        errors.append(_error("BEAT_ENUM_INVALID", beat_path + ".beat_role", "schema 1.2+ requires a valid beat_role"))
                    if posture_id not in POSTURE_IDS:
                        errors.append(_error("STIFF_POSTURE_RISK", beat_path + ".posture_id", "use an explicit relaxed or proof-specific posture; attention stance is invalid"))
                    hook_semantics = beat.get("hook_semantics")
                    contrast_hook_ready = False
                    if beat_role == "context_hook":
                        if hook_semantics not in HOOK_SEMANTICS:
                            errors.append(_error(
                                "BEAT_ENUM_INVALID", beat_path + ".hook_semantics",
                                "context hooks require pain_question, evidence_backed_contrast, or context semantics",
                            ))
                        elif hook_semantics == "pain_question" and not str(beat.get("spoken_line") or "").strip().endswith("?"):
                            errors.append(_error("PROOF_PLAN_MISSING", beat_path + ".spoken_line", "pain_question hooks must be genuine questions, not product assertions"))
                        elif hook_semantics == "evidence_backed_contrast":
                            contrast_line = str(beat.get("spoken_line") or "").strip()
                            if not evidence_v13:
                                errors.append(_error(
                                    "CONTRAST_HOOK_PROOF_INVALID", beat_path + ".hook_semantics",
                                    "evidence-backed contrast hooks require schema 1.3 review and claim provenance",
                                ))
                            elif contrast_line.endswith("?") or not _contains_any(contrast_line, NEGATIVE_CONTRAST_TERMS):
                                errors.append(_error(
                                    "CONTRAST_HOOK_PROOF_INVALID", beat_path + ".spoken_line",
                                    "evidence-backed contrast hooks must be negative declarative statements, not questions",
                                ))
                    elif hook_semantics is not None:
                        errors.append(_error("BEAT_ENUM_INVALID", beat_path + ".hook_semantics", "non-hook beats require hook_semantics=null"))
                    if evidence_v13:
                        beat_evidence_ids = _string_set(beat.get("evidence_ids"))
                        if beat_evidence_ids is None:
                            errors.append(_error(
                                "CLAIM_EVIDENCE_INVALID", beat_path + ".evidence_ids",
                                "schema 1.3 beats require a unique evidence_ids array",
                            ))
                            beat_evidence_ids = set()
                        if beat_role == "context_hook":
                            pain_id = beat.get("pain_point_id")
                            pain = pain_map.get(str(pain_id))
                            if (
                                pain is None or pain.get("status") != "script_eligible"
                                or pain.get("selected_for_script") is not True
                            ):
                                errors.append(_error(
                                    "PAIN_PROOF_BINDING_INVALID", beat_path + ".pain_point_id",
                                    "schema 1.3 hook must bind one selected script-eligible pain point",
                                ))
                            else:
                                expected_pain_evidence = _string_set(pain.get("evidence_ids")) or set()
                                if beat_evidence_ids != expected_pain_evidence:
                                    errors.append(_error(
                                        "PAIN_PROOF_BINDING_INVALID", beat_path + ".evidence_ids",
                                        "hook evidence IDs must exactly equal the selected pain evidence",
                                        expected=sorted(expected_pain_evidence), actual=sorted(beat_evidence_ids),
                                    ))
                                pain_terms = _string_set(pain.get("target_language_terms")) or set()
                                hook_text = _norm(beat.get("spoken_line") or "")
                                if not any(_norm(term) in hook_text for term in pain_terms):
                                    errors.append(_error(
                                        "PAIN_PROOF_BINDING_INVALID", beat_path + ".spoken_line",
                                        "the hook must contain one registered target-language term for its selected pain",
                                        expected_terms=sorted(pain_terms),
                                    ))
                                if hook_semantics == "evidence_backed_contrast":
                                    next_beat = beats[beat_index + 1] if beat_index + 1 < len(beats) else None
                                    next_claim_ids = (
                                        _string_set(next_beat.get("spoken_claim_ids"))
                                        if isinstance(next_beat, dict) else None
                                    )
                                    solution_claim_ids = _string_set(pain.get("solution_claim_ids")) or set()
                                    next_claim_id = (
                                        next(iter(next_claim_ids))
                                        if isinstance(next_claim_ids, set) and len(next_claim_ids) == 1 else None
                                    )
                                    next_claim = claims_registry.get(str(next_claim_id))
                                    contrast_hook_ready = (
                                        pain.get("basis") in {"review_theme", "direct_review"}
                                        and isinstance(next_beat, dict)
                                        and next_beat.get("beat_role") == "product_claim"
                                        and next_beat.get("purpose") == "proof"
                                        and next_claim_id in solution_claim_ids
                                        and isinstance(next_claim, dict)
                                        and next_claim.get("product_part_id") == "texture"
                                        and next_beat.get("product_part_id") == "texture"
                                        and next_beat.get("proof_action_type") == "pinch_release"
                                        and next_beat.get("speech_mode") == "offscreen_voiceover"
                                        and next_beat.get("mouth_visibility") == "not_visible"
                                    )
                                    if not contrast_hook_ready:
                                        errors.append(_error(
                                            "CONTRAST_HOOK_PROOF_INVALID", beat_path,
                                            "a review-derived material-expectation contrast hook must be followed immediately by its mapped texture close-up with one pinch-release proof and off-screen voiceover",
                                            expected_solution_claim_ids=sorted(solution_claim_ids),
                                            actual_next_claim_ids=sorted(next_claim_ids or set()),
                                        ))
                        elif beat.get("pain_point_id") is not None:
                            errors.append(_error(
                                "PAIN_PROOF_BINDING_INVALID", beat_path + ".pain_point_id",
                                "only the context hook may carry pain_point_id",
                            ))
                    hand_plan = beat.get("hand_plan")
                    hand_valid = isinstance(hand_plan, dict) and hand_plan.get("active_hands") in HAND_ACTIVITY
                    for side in ("left", "right"):
                        side_plan = hand_plan.get(side) if isinstance(hand_plan, dict) else None
                        if not isinstance(side_plan, dict) or any(
                            not _nonempty(side_plan.get(field)) for field in ("start_anchor", "action", "end_anchor")
                        ):
                            hand_valid = False
                    if not hand_valid:
                        errors.append(_error("HAND_PLAN_INVALID", beat_path + ".hand_plan", "both hands need explicit start, action, and end anchors"))
                        hand_plan = {}
                    elif evidence_v13:
                        left_plan = hand_plan.get("left") if isinstance(hand_plan.get("left"), dict) else {}
                        right_plan = hand_plan.get("right") if isinstance(hand_plan.get("right"), dict) else {}
                        hip_anchors = (
                            left_plan.get("start_anchor"), left_plan.get("end_anchor"),
                            right_plan.get("start_anchor"), right_plan.get("end_anchor"),
                        )
                        if all("hip" in _norm(anchor) for anchor in hip_anchors):
                            symmetric_hip_reset_beats.append(str(beat.get("beat_id", "")))
                    expected_hand_activity = {
                        "zero": {"none"}, "one": {"left", "right"}, "two": {"both"},
                    }.get(motion_budget.get("active_hands"), set())
                    if hand_plan.get("active_hands") not in expected_hand_activity:
                        errors.append(_error("HAND_ACTIVITY_MISMATCH", beat_path + ".hand_plan.active_hands", "hand_plan activity must match motion_budget.active_hands"))
                    hand_markers = _hand_side_markers(beat.get("core_action"))
                    required_hand_markers = {
                        "none": set(), "left": {"left"}, "right": {"right"}, "both": {"left", "right"},
                    }.get(hand_plan.get("active_hands"), set())
                    if hand_markers != required_hand_markers:
                        errors.append(_error(
                            "HAND_ACTIVITY_MISMATCH", beat_path + ".core_action",
                            "core action hand side must exactly match the declared active hand plan",
                            expected=sorted(required_hand_markers), actual=sorted(hand_markers),
                        ))
                    spoken_claim_ids = beat.get("spoken_claim_ids")
                    if (
                        not isinstance(spoken_claim_ids, list)
                        or any(not _nonempty(claim_id) for claim_id in spoken_claim_ids)
                        or len({_norm(claim_id) for claim_id in spoken_claim_ids}) != len(spoken_claim_ids)
                    ):
                        errors.append(_error("PROOF_PLAN_MISSING", beat_path + ".spoken_claim_ids", "spoken_claim_ids must be a unique string array"))
                        spoken_claim_ids = []
                    needs_proof = beat.get("purpose") in {"pain", "proof", "styling"} or beat_role in {"product_claim", "styling"}
                    if needs_proof and line_present:
                        if _contains_any(spoken_line, GENERIC_DETAIL_TERMS):
                            errors.append(_error(
                                "VOICEOVER_DETAIL_TOO_GENERIC", beat_path + ".spoken_line",
                                "proof/styling voiceover must name a concrete visible behavior, position, construction fact, or pairing instead of generic praise",
                            ))
                        opener = _spoken_opener(spoken_line)
                        if opener:
                            voiceover_openers.setdefault(opener, []).append(str(beat.get("beat_id", "")))
                    proof_id = beat.get("claim_proof_id")
                    if needs_proof:
                        if market_v14 and not _nonempty(beat.get("proof_endpoint")):
                            errors.append(_error(
                                "CLAIM_PROOF_ENDPOINT_MISMATCH", beat_path + ".proof_endpoint",
                                "schema 1.4 claim-proof beats require one explicit proof_endpoint",
                            ))
                        if beat.get("product_part_id") not in PRODUCT_PART_IDS:
                            errors.append(_error("BEAT_ENUM_INVALID", beat_path + ".product_part_id", "claim-proof beats require one canonical product_part_id"))
                        if beat.get("speech_mode") == "none" or not _nonempty(beat.get("spoken_line")):
                            errors.append(_error(
                                "SPEECH_MODE_INVALID", beat_path,
                                "every claim-proof action requires one matching concise spoken line, normally as off-screen voiceover",
                            ))
                        proof = claim_proof_map.get(str(proof_id))
                        if proof is None:
                            errors.append(_error("PROOF_PLAN_MISSING", beat_path + ".claim_proof_id", "selling beat must bind exactly one claim-proof plan entry"))
                        else:
                            expected_spoken_claim_ids = [str(proof.get("claim_id"))]
                            if spoken_claim_ids != expected_spoken_claim_ids:
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".spoken_claim_ids",
                                    "spoken_claim_ids must exactly equal the single claim bound by claim_proof_id",
                                    expected=expected_spoken_claim_ids, actual=spoken_claim_ids,
                                ))
                            if proof_id in bound_claim_proofs:
                                errors.append(_error("PROOF_PLAN_MISSING", beat_path + ".claim_proof_id", "claim-proof plan entry may bind only one beat"))
                            bound_claim_proofs.add(str(proof_id))
                            binding_matches = (
                                proof.get("beat_id") == beat.get("beat_id")
                                and proof.get("spoken_intent_id") == beat.get("spoken_intent_id")
                                and proof.get("proof_target") == beat.get("proof_target")
                                and proof.get("action_type") == beat.get("proof_action_type")
                                and proof.get("product_part_id") == beat.get("product_part_id")
                                and proof.get("evidence_basis") == beat.get("evidence_basis")
                            )
                            if evidence_v13:
                                binding_matches = binding_matches and (
                                    _string_set(proof.get("evidence_ids")) == _string_set(beat.get("evidence_ids"))
                                )
                            if not binding_matches:
                                errors.append(_error("CLAIM_PROOF_ACTION_MISMATCH", beat_path, "beat claim/action fields do not match the pre-script claim-proof plan"))
                            if market_v14 and (
                                beat.get("proof_endpoint") != proof.get("expected_visible_change")
                                or _norm(beat.get("proof_endpoint")) not in _norm(beat.get("visible_endpoint"))
                            ):
                                errors.append(_error(
                                    "CLAIM_PROOF_ENDPOINT_MISMATCH", beat_path + ".proof_endpoint",
                                    "proof_endpoint must exactly equal the planned visible change and appear in visible_endpoint",
                                    expected=proof.get("expected_visible_change"), actual=beat.get("proof_endpoint"),
                                ))
                            if not _mentions_target(beat.get("proof_target"), beat.get("framing")):
                                errors.append(_error("CLAIM_PROOF_FRAMING_MISMATCH", beat_path + ".framing", "frame must name and center the claimed garment target"))
                            if not _framing_text_matches_class(beat.get("framing"), proof.get("framing_class")):
                                errors.append(_error("CLAIM_PROOF_FRAMING_MISMATCH", beat_path + ".framing", "frame text must visibly implement the planned close-up/shot class"))
                            if not _mentions_target(
                                beat.get("proof_target"), beat.get("action_target"), beat.get("product_point")
                            ):
                                errors.append(_error("CLAIM_PROOF_ACTION_MISMATCH", beat_path, "action target/product point must match the claimed garment target"))
                            if not _action_matches_target(beat.get("proof_target"), beat.get("proof_action_type")):
                                errors.append(_error("CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".proof_action_type", "beat action type does not demonstrate the named garment target"))
                            detected_action_types = _detected_action_types(beat.get("core_action"))
                            declared_action_type = str(beat.get("proof_action_type") or "")
                            if _contains_any(beat.get("core_action"), NEGATED_ACTION_TEXT_TERMS):
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".core_action",
                                    "proof actions must be written as positive performed actions, not negated non-actions",
                                ))
                            if declared_action_type not in detected_action_types:
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".core_action",
                                    "core action text must visibly implement the single declared proof action type",
                                    declared=declared_action_type, detected=sorted(detected_action_types),
                                ))
                            if not _mentions_target(beat.get("proof_target"), beat.get("core_action")):
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".core_action",
                                    "core action text must name the full garment target it physically demonstrates",
                                ))
                            if len(detected_action_types) > 1:
                                errors.append(_error(
                                    "ACTION_SEQUENCE_OVERLOAD", beat_path + ".core_action",
                                    "one beat may contain only one main garment action; split sequential actions into separate beats",
                                    detected=sorted(detected_action_types),
                                ))
                            bound_claim = claims_registry.get(str(proof.get("claim_id")))
                            bound_spoken_terms = bound_claim.get("spoken_claim_terms") if isinstance(bound_claim, dict) else []
                            if not isinstance(bound_spoken_terms, list) or not any(
                                _norm(term) in _norm(beat.get("spoken_line")) for term in bound_spoken_terms
                            ):
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".spoken_line",
                                    "spoken line must contain a registered target-language term for the bound claim",
                                ))
                            mentioned_claim_ids = [
                                claim_id for claim_id, registered_claim in claims_registry.items()
                                if _target_matches_terms(beat.get("spoken_line"), {str(registered_claim.get("feature_id", ""))})
                            ]
                            if len(mentioned_claim_ids) > 1:
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".spoken_line",
                                    "one claim-proof beat may speak about only one registered garment target",
                                    mentioned_claim_ids=mentioned_claim_ids,
                                ))
                            elif mentioned_claim_ids and str(proof.get("claim_id")) not in mentioned_claim_ids:
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".spoken_line",
                                    "spoken garment target must match the claim bound to this proof beat",
                                    expected_claim_id=proof.get("claim_id"), mentioned_claim_ids=mentioned_claim_ids,
                                ))
                            performance_language = any(_contains_any(value, PERFORMANCE_TERMS) for value in (
                                beat.get("spoken_line"), beat.get("product_point"), beat.get("proof_target"), beat.get("core_action"),
                            ))
                            performance_action = _target_matches_terms(beat.get("core_action"), PERFORMANCE_ACTION_TERMS)
                            requested_performance_groups = _performance_group_ids(
                                beat.get("spoken_line"), beat.get("product_point"), beat.get("proof_target"), beat.get("core_action"),
                            )
                            if performance_action or beat.get("proof_action_type") == "pull_release":
                                requested_performance_groups.add("stretch")
                            bound_performance_groups = _performance_group_ids(bound_claim.get("feature_id")) if isinstance(bound_claim, dict) else set()
                            performance_evidence_ok = (
                                isinstance(bound_claim, dict)
                                and str(proof.get("claim_id")) in claims_allowlist_ids
                                and (
                                    _v13_performance_evidence_ok(bound_claim, evidence_map, source_map)
                                    if evidence_v13 else bound_claim.get("evidence_basis") in {"user_provided", "product_page", "verified_test"}
                                )
                                and requested_performance_groups.issubset(bound_performance_groups)
                            )
                            if (performance_language or performance_action) and not performance_evidence_ok:
                                errors.append(_error(
                                    "UNSUPPORTED_PERFORMANCE_DEMO", beat_path,
                                    "spoken or demonstrated performance claims require explicit non-visual evidence and an allowlisted claim",
                                ))
                            stretch_language = any(
                                _contains_any(value, STRETCH_TERMS)
                                for value in (beat.get("spoken_line"), beat.get("product_point"), beat.get("proof_target"))
                            )
                            if stretch_language and (
                                beat.get("proof_action_type") != "pull_release" or proof.get("hands_required") != "two"
                            ):
                                errors.append(_error(
                                    "CLAIM_PROOF_ACTION_MISMATCH", beat_path,
                                    "stretch/elasticity language must be paired with one coordinated two-hand pull-release action",
                                ))
                            anatomy_text = " ".join(str(value or "") for value in (
                                beat.get("actor"), beat.get("core_action"), beat.get("left_hand_state"),
                                beat.get("right_hand_state"), _canonical_json(beat.get("hand_plan")),
                            ))
                            if _contains_any(anatomy_text, EXTRA_LIMB_TERMS):
                                errors.append(_error("ANATOMY_RISK_OVERLOAD", beat_path, "beat text implies an extra or third arm/hand"))
                            if _norm(proof.get("expected_visible_change")) not in _norm(beat.get("visible_endpoint")):
                                errors.append(_error("CLAIM_PROOF_ENDPOINT_MISMATCH", beat_path + ".visible_endpoint", "visible endpoint must contain the planned visible change"))
                            if proof.get("framing_class") == "detail_closeup" and beat.get("product_visibility") != "detail":
                                errors.append(_error("CLAIM_PROOF_FRAMING_MISMATCH", beat_path + ".product_visibility", "detail claim requires product_visibility=detail"))
                            if _is_static_action(beat.get("core_action")):
                                errors.append(_error("STATIC_SELLING_BEAT", beat_path + ".core_action", "selling beat must demonstrate the garment; a full-beat hold is invalid"))
                            action_motion = (
                                motion_budget.get("performer") in {"simple", "active"}
                                or motion_budget.get("active_hands") in {"one", "two"}
                            )
                            if not action_motion:
                                errors.append(_error("STATIC_SELLING_BEAT", beat_path + ".motion_budget", "camera motion or facial reaction alone does not count as product demonstration"))
                            hands_required = proof.get("hands_required")
                            active_hands = motion_budget.get("active_hands")
                            if hands_required == "one" and active_hands != "one":
                                errors.append(_error("HAND_ACTIVITY_MISMATCH", beat_path, "one-hand proof must declare exactly one active hand"))
                            elif hands_required == "two":
                                if (
                                    active_hands != "two" or hand_plan.get("active_hands") != "both"
                                    or motion_budget.get("camera") not in {"locked", "subtle"}
                                    or motion_budget.get("performer") not in {"still", "micro"}
                                    or beat.get("speech_mode") != "offscreen_voiceover"
                                ):
                                    errors.append(_error("ANATOMY_RISK_OVERLOAD", beat_path, "two-hand proof must be one coordinated evidence-backed action with stable torso/camera and mouth out of frame"))
                            elif hands_required == "body" and motion_budget.get("performer") not in {"simple", "active"}:
                                errors.append(_error("HAND_ACTIVITY_MISMATCH", beat_path, "body proof must declare simple/active performer motion"))
                            product_action_beat_ids.append(str(beat.get("beat_id", "")))
                    else:
                        if market_v14 and beat.get("proof_endpoint") is not None:
                            errors.append(_error(
                                "CLAIM_PROOF_ENDPOINT_MISMATCH", beat_path + ".proof_endpoint",
                                "non-proof beats require proof_endpoint=null",
                            ))
                        if beat.get("product_part_id") is not None:
                            errors.append(_error("BEAT_ENUM_INVALID", beat_path + ".product_part_id", "non-proof beats require product_part_id=null"))
                        if spoken_claim_ids:
                            errors.append(_error("CLAIM_PROOF_ACTION_MISMATCH", beat_path + ".spoken_claim_ids", "non-proof beats may not declare product claim IDs"))
                        registered_spoken_terms = [
                            term for registered_claim in claims_registry.values()
                            for term in registered_claim.get("spoken_claim_terms", [])
                            if _nonempty(term)
                        ]
                        unbound_product_claim = _target_matches_terms(beat.get("spoken_line"), UNBOUND_GARMENT_CLAIM_TERMS) or any(
                            _norm(term) in _norm(beat.get("spoken_line")) for term in registered_spoken_terms
                        )
                        pain_question_allowed = (
                            beat_role == "context_hook" and hook_semantics == "pain_question"
                            and str(beat.get("spoken_line") or "").strip().endswith("?")
                        )
                        contrast_hook_allowed = (
                            beat_role == "context_hook"
                            and hook_semantics == "evidence_backed_contrast"
                            and contrast_hook_ready
                        )
                        if unbound_product_claim and not (pain_question_allowed or contrast_hook_allowed):
                            errors.append(_error(
                                "PROOF_PLAN_MISSING", beat_path + ".spoken_line",
                                "a hook/reaction/CTA may not introduce an unbound garment claim; use a validated evidence-backed contrast or move it to one claim-proof beat",
                            ))
                        if evidence_v13 and beat_role != "context_hook" and (_string_set(beat.get("evidence_ids")) or set()):
                            errors.append(_error(
                                "CLAIM_EVIDENCE_INVALID", beat_path + ".evidence_ids",
                                "non-proof, non-hook beats require empty evidence_ids",
                            ))
                        unbound_performance = any(_contains_any(value, PERFORMANCE_TERMS) for value in (
                            beat.get("spoken_line"), beat.get("product_point"), beat.get("proof_target"), beat.get("core_action"),
                        )) or _target_matches_terms(beat.get("core_action"), PERFORMANCE_ACTION_TERMS)
                        if unbound_performance:
                            errors.append(_error("UNSUPPORTED_PERFORMANCE_DEMO", beat_path, "performance language or demonstration requires a bound evidence-backed claim-proof beat"))
                        if beat_role == "context_hook" and _is_static_action(beat.get("core_action")):
                            errors.append(_error("STIFF_POSTURE_RISK", beat_path, "hook must use a relaxed natural reveal action, not an attention-like static pose"))
            start, end = _decimal(beat.get("start_seconds")), _decimal(beat.get("end_seconds"))
            if start is None or end is None or start >= end:
                errors.append(_error("TIMELINE_INTERVAL_INVALID", beat_path, "beat must use a non-empty half-open [start,end) interval"))
                continue
            if start.as_tuple().exponent < -3 or end.as_tuple().exponent < -3:
                errors.append(_error("TIMELINE_INTERVAL_INVALID", beat_path, "timestamps may use at most millisecond precision"))
            if start > previous_end:
                errors.append(_error("TIMELINE_GAP", beat_path + ".start_seconds", "timeline has a gap", expected=float(previous_end), actual=float(start)))
            elif start < previous_end:
                errors.append(_error("TIMELINE_OVERLAP", beat_path + ".start_seconds", "timeline overlaps or is out of order", expected=float(previous_end), actual=float(start)))
            if quality_v11:
                beat_duration = end - start
                if beat_duration < Decimal("1.5"):
                    errors.append(_error(
                        "SHOT_DENSITY_EXCEEDED", beat_path,
                        "schema 1.1 beats must be at least 1.5 seconds",
                        duration_seconds=float(beat_duration),
                    ))
                if beat.get("speech_mode") == "on_camera_dialogue":
                    words = _word_count(beat.get("spoken_line"))
                    visible_lipsync_beats += 1
                    if enforce_word_budget:
                        visible_lipsync_words += words
                    if evidence_v13:
                        expression_cue = _norm(beat.get("micro_expression"))
                        if expression_cue:
                            on_camera_expression_cues.append(expression_cue)
                        else:
                            errors.append(_error(
                                "FLAT_PERFORMANCE_RISK", beat_path + ".micro_expression",
                                "schema 1.3 visible dialogue requires a line-matched expression/delivery cue",
                            ))
                    motion_budget = beat.get("motion_budget") if isinstance(beat.get("motion_budget"), dict) else {}
                    per_line_word_budget = 14 if evidence_v13 and lip_sync_priority else 10
                    minimum_visible_dialogue_seconds = (
                        Decimal("1.5")
                        if market_v14 and (_decimal(duration) or Decimal("0")) <= Decimal("5")
                        else Decimal("2")
                    )
                    if (
                        beat_duration < minimum_visible_dialogue_seconds or motion_budget.get("camera") not in {"locked", "subtle"}
                        or motion_budget.get("performer") == "active" or motion_budget.get("active_hands") == "two"
                        or (enforce_word_budget and words > per_line_word_budget)
                    ):
                        errors.append(_error(
                            "LIPSYNC_COMPLEXITY_OVERLOAD", beat_path,
                            "visible lip-sync exceeds its duration, dialogue, camera, performer, or hand budget",
                            duration_seconds=float(beat_duration), words=words, word_budget=per_line_word_budget,
                        ))
                motion_budget = beat.get("motion_budget") if isinstance(beat.get("motion_budget"), dict) else {}
                detail_proof = beat.get("product_visibility") == "detail"
                bound_proof = claim_proof_map.get(str(beat.get("claim_proof_id"))) if motion_v12 else None
                verified_two_hand_detail = (
                    motion_v12 and isinstance(bound_proof, dict)
                    and bound_proof.get("hands_required") == "two"
                    and bound_proof.get("evidence_basis") in {"user_provided", "product_page", "verified_test"}
                    and beat.get("speech_mode") == "offscreen_voiceover"
                )
                if detail_proof and (
                    motion_budget.get("camera") not in {"locked", "subtle"}
                    or motion_budget.get("performer") == "active"
                    or (motion_budget.get("active_hands") == "two" and not verified_two_hand_detail)
                ):
                    errors.append(_error(
                        "DETAIL_SHOT_COMPLEXITY_OVERLOAD", beat_path,
                        "detail proof requires stable camera/non-active torso and normally one active hand; two require a verified coordinated exception",
                    ))
                if beat.get("purpose") == "cta" and (
                    motion_budget.get("camera") not in {"locked", "subtle"}
                    or motion_budget.get("performer") not in {"still", "micro"}
                    or motion_budget.get("active_hands") == "two"
                ):
                    errors.append(_error(
                        "CTA_COMPLEXITY_OVERLOAD", beat_path,
                        "CTA requires locked/subtle camera, still/micro performer motion, and at most one active hand",
                    ))
                if market_v14:
                    delivery_mode = generation_controls.get("delivery_mode")
                    proof_action_type = beat.get("proof_action_type")
                    complex_proof = (
                        proof_action_type in _market_contract.COMPLEX_PROOF_ACTIONS
                        or motion_budget.get("active_hands") == "two"
                        or motion_budget.get("performer") == "active"
                    )
                    if complex_proof and beat.get("speech_mode") != "offscreen_voiceover":
                        errors.append(_error(
                            "DELIVERY_MODE_CONFLICT", beat_path,
                            "complex garment proof requires off-screen voiceover with the mouth out of frame",
                            proof_action_type=proof_action_type,
                        ))
                    if delivery_mode == "de_live_simple" and line_present:
                        if beat.get("speech_mode") != "on_camera_dialogue":
                            errors.append(_error(
                                "DELIVERY_MODE_CONFLICT", beat_path + ".speech_mode",
                                "de_live_simple requires every spoken beat to remain simple visible German dialogue",
                            ))
                        if needs_proof and proof_action_type not in _market_contract.DE_LIVE_SIMPLE_ACTIONS:
                            errors.append(_error(
                                "DELIVERY_MODE_CONFLICT", beat_path + ".proof_action_type",
                                "de_live_simple permits only a simple point, touch, or styling adjustment proof",
                                actual=proof_action_type,
                            ))
                    elif delivery_mode in {"us_hybrid_share", "de_hybrid_proof"} and line_present:
                        expected_mode = (
                            "offscreen_voiceover" if needs_proof else "on_camera_dialogue"
                        )
                        if beat.get("speech_mode") != expected_mode:
                            errors.append(_error(
                                "DELIVERY_MODE_CONFLICT", beat_path + ".speech_mode",
                                "hybrid delivery keeps hook/close live and carries product or styling proof as off-screen voiceover",
                                expected=expected_mode, actual=beat.get("speech_mode"),
                            ))
            previous_end = end
        if market_v14:
            errors.extend(_market_contract.cut_block_issues(
                beats, duration, base + ".canonical_timeline.beats",
            ))

            if len(cta_beat_indices) != 1:
                errors.append(_error(
                    "CTA_POLICY_INVALID", base + ".canonical_timeline.beats",
                    "schema 1.4 requires exactly one human closing CTA/verdict beat",
                    cta_beat_indices=cta_beat_indices,
                ))
            elif cta_beat_indices[0] != len(beats) - 1:
                errors.append(_error(
                    "CTA_POSITION_INVALID", f"{base}.canonical_timeline.beats[{cta_beat_indices[0]}]",
                    "the one CTA/verdict beat must be the final internal beat",
                ))

            camera_mode = generation_controls.get("camera_mode")
            camera_has_fixed = any(
                _market_contract.text_contains_any(value, _market_contract.FIXED_CAMERA_TERMS)
                for _, value in camera_text_values
            )
            camera_has_handheld = any(
                _market_contract.text_contains_any(value, _market_contract.HANDHELD_CAMERA_TERMS)
                for _, value in camera_text_values
            )
            if camera_mode == "fixed_phone" and camera_has_handheld:
                errors.append(_error(
                    "CAMERA_MODE_CONFLICT", base + ".generation_controls.camera_mode",
                    "fixed_phone cannot contain handheld, selfie-phone, or mirror-selfie camera instructions",
                ))
            elif camera_mode in {"creator_handheld", "friend_handheld"} and camera_has_fixed:
                errors.append(_error(
                    "CAMERA_MODE_CONFLICT", base + ".generation_controls.camera_mode",
                    "a handheld camera mode cannot also request a tripod or fixed-camera treatment",
                ))
            if camera_mode == "fixed_phone" and any(
                isinstance(beat, dict) and isinstance(beat.get("motion_budget"), dict)
                and beat["motion_budget"].get("camera") == "active"
                for beat in beats
            ):
                errors.append(_error(
                    "CAMERA_MODE_CONFLICT", base + ".canonical_timeline.beats",
                    "fixed_phone may use only locked or subtle camera load",
                ))
            if camera_mode == "creator_handheld":
                creator_phone_ready = camera_has_handheld or any(
                    isinstance(beat, dict) and str(beat.get("posture_id") or "").startswith("mirror_")
                    for beat in beats
                )
                if not creator_phone_ready:
                    errors.append(_error(
                        "CAMERA_MODE_CONFLICT", base + ".generation_controls.camera_mode",
                        "creator_handheld requires a declared handheld/selfie treatment or mirror posture",
                    ))
            if camera_mode == "friend_handheld":
                friend_conflicts = [
                    beat.get("beat_id") for beat in beats if isinstance(beat, dict) and (
                        str(beat.get("posture_id") or "").startswith("mirror_")
                        or _contains_any(
                            " ".join(str(beat.get(field) or "") for field in (
                                "actor", "left_hand_state", "right_hand_state", "core_action",
                            )),
                            {"creator holds the phone", "filming hand", "mirror selfie", "模特手持手机", "拍摄手"},
                        )
                    )
                ]
                if friend_conflicts:
                    errors.append(_error(
                        "CAMERA_MODE_CONFLICT", base + ".canonical_timeline.beats",
                        "friend_handheld forbids mirror-selfie posture or creator ownership of the filming phone",
                        beat_ids=friend_conflicts,
                    ))

            music_mode = generation_controls.get("music_mode")
            music_positive = [path for path, value in audio_text_values if _market_contract.text_contains_any(value, _market_contract.MUSIC_POSITIVE_TERMS)]
            music_negative = [path for path, value in audio_text_values if _market_contract.text_contains_any(value, _market_contract.MUSIC_NEGATIVE_TERMS)]
            lyrical_music = [path for path, value in audio_text_values if _market_contract.text_contains_any(value, _market_contract.LYRICAL_MUSIC_TERMS)]
            if (music_mode == "none" and music_positive) or (music_mode == "low_non_lyrical" and music_negative) or lyrical_music:
                errors.append(_error(
                    "MUSIC_MODE_CONFLICT", base + ".generation_controls.music_mode",
                    "structured music mode conflicts with beat audio or requests lyrics/vocals",
                    positive_music_paths=music_positive, no_music_paths=music_negative, lyrical_paths=lyrical_music,
                ))

            screen_conflicts = [
                path for path, value in outbound_text_values
                if _market_contract.text_contains_any(value, _market_contract.EXTRA_SCREEN_TEXT_TERMS)
            ]
            if screen_conflicts:
                errors.append(_error(
                    "SCREEN_TEXT_POLICY_INVALID", base + ".canonical_timeline.beats",
                    "the fixed-model fit-stats line is the only generated screen text allowed",
                    paths=screen_conflicts,
                ))

            graphic_cta_paths = [
                path for path, value in outbound_text_values
                if _market_contract.text_contains_any(value, _market_contract.GRAPHIC_CTA_TERMS)
            ]
            if graphic_cta_paths:
                errors.append(_error(
                    "CTA_POLICY_INVALID", base + ".canonical_timeline.beats",
                    "CTA must be human-only with no arrow, cart, button, badge, sticker, icon, or interface graphic",
                    paths=graphic_cta_paths,
                ))

            if len(cta_beat_indices) == 1:
                cta_beat = beats[cta_beat_indices[0]] if isinstance(beats[cta_beat_indices[0]], dict) else {}
                cta_line = _norm(cta_beat.get("spoken_line"))
                commerce_cta_mode = generation_controls.get("commerce_cta_mode")
                verdict_mode = generation_controls.get("verdict_mode")
                link_terms = {"link", "tap", "click", "unten links", "antippen", "klicken", "链接", "点击"}
                has_link_cue = _contains_any(cta_line, link_terms)
                if commerce_cta_mode == "light_link" and not has_link_cue:
                    errors.append(_error(
                        "CTA_POLICY_INVALID", base + ".generation_controls.commerce_cta_mode",
                        "light_link requires one brief target-language human link cue in the closing line",
                    ))
                elif commerce_cta_mode == "none" and has_link_cue:
                    errors.append(_error(
                        "CTA_POLICY_INVALID", base + ".generation_controls.commerce_cta_mode",
                        "commerce_cta_mode=none forbids link/click/tap language",
                    ))
                if verdict_mode == "ownership_verdict":
                    market = batch_key.get("market") if isinstance(batch_key, dict) else None
                    ownership_terms = (
                        {"keep", "keeping", "mine", "rotation", "behalte", "bleibt bei mir", "kommt mit"}
                        if market == "DE" else {"keep", "keeping", "mine", "rotation"}
                    )
                    if not _contains_any(cta_line, ownership_terms):
                        errors.append(_error(
                            "CTA_POLICY_INVALID", base + ".generation_controls.verdict_mode",
                            "ownership_verdict requires a believable first-person keep-or-wear phrase",
                        ))
                if commerce_cta_mode == "evidence_backed_promo":
                    promotion_ids = generation_controls.get("promotion_evidence_ids")
                    promotion_ids = set(promotion_ids) if isinstance(promotion_ids, list) else set()
                    eligible_promotion_ids = {
                        evidence_id for evidence_id in promotion_ids if evidence_id in evidence_map
                        and evidence_map[evidence_id].get("exact_product_match") is True
                        and evidence_map[evidence_id].get("exact_variant_match") is True
                        and evidence_map[evidence_id].get("conflict_status") == "clear"
                        and "claim_direct" in (_string_set(evidence_map[evidence_id].get("permitted_uses")) or set())
                        and source_map.get(str(evidence_map[evidence_id].get("source_id")), {}).get("source_kind") == "user_provided"
                    }
                    if not promotion_ids or eligible_promotion_ids != promotion_ids:
                        errors.append(_error(
                            "CTA_POLICY_INVALID", base + ".generation_controls.promotion_evidence_ids",
                            "evidence_backed_promo requires explicit clear exact-variant user-provided promotion evidence",
                            provided=sorted(promotion_ids), eligible=sorted(eligible_promotion_ids),
                        ))

            voiceover_review = variant.get("voiceover_review")
            review_valid = isinstance(voiceover_review, list) and len(voiceover_review) == len(beats)
            expected_review_fields = {
                "beat_id", "speech_mode", "spoken_language", "market_line",
                "zh_cn_translation", "silence_reason",
            }
            if review_valid:
                for review_index, (row, beat) in enumerate(zip(voiceover_review, beats)):
                    row_path = f"{base}.voiceover_review[{review_index}]"
                    if not isinstance(row, dict) or set(row) != expected_review_fields or not isinstance(beat, dict):
                        review_valid = False
                        break
                    line_present = _nonempty(beat.get("spoken_line"))
                    row_valid = (
                        row.get("beat_id") == beat.get("beat_id")
                        and row.get("speech_mode") == beat.get("speech_mode")
                        and row.get("spoken_language") == beat.get("spoken_language")
                        and row.get("market_line") == beat.get("spoken_line")
                        and row.get("silence_reason") == beat.get("silence_reason")
                    )
                    if line_present:
                        row_valid = row_valid and _meaningful_zh_translation(row.get("zh_cn_translation"))
                    else:
                        row_valid = row_valid and not _nonempty(row.get("zh_cn_translation"))
                    if not row_valid:
                        review_valid = False
                        break
            if not review_valid:
                errors.append(_error(
                    "VOICEOVER_REVIEW_DRIFT", base + ".voiceover_review",
                    "voiceover_review must exactly project every beat and keep a meaningful, non-placeholder Chinese translation only for spoken units",
                ))

            forbidden_meta_tokens = {
                str(source.get("review_id")) for source in source_map.values()
                if _nonempty(source.get("review_id"))
            }
            forbidden_meta_tokens.update({
                str(value) for value in (research_bundle.get("requested_url"), research_bundle.get("canonical_url"))
                if _nonempty(value)
            })
            errors.extend(_market_contract.meta_leaks(outbound_text_values, forbidden_meta_tokens))
        if quality_v11 and not motion_v12 and len(setup_runs) > 3:
            errors.append(_error(
                "SHOT_DENSITY_EXCEEDED", base + ".canonical_timeline.beats",
                "schema 1.1 allows at most three contiguous camera-setup runs",
                setup_runs=setup_runs, run_count=len(setup_runs),
            ))
        if quality_v11 and not motion_v12:
            for run_index, group in enumerate(_setup_groups(beats)):
                action_beat_ids: list[str] = []
                for beat in group:
                    motion_budget = beat.get("motion_budget")
                    if not isinstance(motion_budget, dict):
                        continue
                    action_bearing = (
                        motion_budget.get("camera") == "active"
                        or motion_budget.get("performer") in {"simple", "active"}
                        or motion_budget.get("active_hands") in {"one", "two"}
                    )
                    if action_bearing:
                        action_beat_ids.append(str(beat.get("beat_id", "")))
                if len(action_beat_ids) > 1:
                    errors.append(_error(
                        "SHOT_ACTION_DENSITY_EXCEEDED", base + ".canonical_timeline.beats",
                        "one contiguous camera-setup run may contain at most one action-bearing beat",
                        setup_run_index=run_index, camera_setup_id=group[0].get("camera_setup_id"),
                        action_beat_ids=action_beat_ids,
                    ))
        if motion_v12:
            for opener, opener_beat_ids in voiceover_openers.items():
                if len(opener_beat_ids) >= 3:
                    errors.append(_error(
                        "VOICEOVER_STYLE_TOO_REPETITIVE", base + ".canonical_timeline.beats",
                        "three or more non-CTA proof/styling lines reuse the same opener; vary the natural sentence shape",
                        opener=opener, beat_ids=opener_beat_ids,
                    ))
            max_setup_runs = max(1, math.ceil(float(_decimal(duration) or Decimal("15")) / 3.0))
            min_setup_runs = min(3, max_setup_runs)
            if _same(duration, 15) and not 5 <= len(beats) <= 7:
                errors.append(_error(
                    "SHOT_DENSITY_EXCEEDED", base + ".canonical_timeline.beats",
                    "a default 15-second motion-rich timeline requires five to seven sequential beats",
                    minimum=5, maximum=7, actual=len(beats),
                ))
            if len(setup_runs) < min_setup_runs or len(setup_runs) > max_setup_runs:
                errors.append(_error(
                    "SHOT_DENSITY_EXCEEDED", base + ".canonical_timeline.beats",
                    "schema 1.2+ requires enough claim-specific framing without overcutting",
                    minimum=min_setup_runs, maximum=max_setup_runs, actual=len(setup_runs), setup_runs=setup_runs,
                ))
            required_actions = (
                _minimum_market_product_actions(duration)
                if market_v14 else _minimum_product_actions(duration)
            )
            if len(product_action_beat_ids) < required_actions:
                errors.append(_error(
                    "ACTION_COVERAGE_INSUFFICIENT", base + ".canonical_timeline.beats",
                    "timeline has too few non-CTA garment/styling demonstration actions",
                    required=required_actions, actual=len(product_action_beat_ids), beat_ids=product_action_beat_ids,
                ))
            unused_proofs = sorted(set(claim_proof_map) - bound_claim_proofs)
            if unused_proofs:
                errors.append(_error(
                    "PROOF_PLAN_MISSING", base + ".quality_plan.claim_proof_plan",
                    "every planned claim proof must bind exactly one timeline beat",
                    unused_claim_proof_ids=unused_proofs,
                ))
            for run_index, group in enumerate(_setup_groups(beats)):
                action_group = [
                    beat for beat in group
                    if str(beat.get("claim_proof_id")) in bound_claim_proofs
                ]
                if len(action_group) > 2:
                    errors.append(_error(
                        "ACTION_SEQUENCE_OVERLOAD", base + ".canonical_timeline.beats",
                        "one camera setup may contain at most two sequential claim-proof actions",
                        setup_run_index=run_index, camera_setup_id=group[0].get("camera_setup_id"),
                        beat_ids=[beat.get("beat_id") for beat in action_group],
                    ))
                for previous, current in zip(action_group, action_group[1:]):
                    if not evidence_v13 and previous.get("proof_target") != current.get("proof_target"):
                        errors.append(_error(
                            "CLAIM_PROOF_FRAMING_MISMATCH", base + ".canonical_timeline.beats",
                            "a changed garment target requires a new claim-specific camera setup",
                            previous_beat_id=previous.get("beat_id"), current_beat_id=current.get("beat_id"),
                        ))
                    previous_hands = previous.get("hand_plan") if isinstance(previous.get("hand_plan"), dict) else {}
                    current_hands = current.get("hand_plan") if isinstance(current.get("hand_plan"), dict) else {}
                    for side in ("left", "right"):
                        previous_side = previous_hands.get(side) if isinstance(previous_hands.get(side), dict) else {}
                        current_side = current_hands.get(side) if isinstance(current_hands.get(side), dict) else {}
                        if _norm(previous_side.get("end_anchor")) != _norm(current_side.get("start_anchor")):
                            errors.append(_error(
                                "ACTION_TRANSITION_INVALID", base + ".canonical_timeline.beats",
                                "sequential actions in one setup require continuous hand anchors",
                                side=side, previous_beat_id=previous.get("beat_id"), current_beat_id=current.get("beat_id"),
                            ))
        if evidence_v13 and _same(duration, 15):
            phase_groups = _macro_phase_groups(beats)
            phase_beats = [group for _, _, group in phase_groups]
            phase_valid = (
                len(phase_beats) == 3
                and all(phase_beats)
                and any(beat.get("purpose") == "hook" for beat in phase_beats[0])
                and sum(beat.get("purpose") in {"proof", "styling"} for beat in phase_beats[1]) >= 2
                and any(beat.get("purpose") in {"proof", "styling"} for beat in phase_beats[2])
                and any(beat.get("purpose") == "cta" for beat in phase_beats[2])
            )
            if not phase_valid:
                errors.append(_error(
                    "PERFORMANCE_ARC_INVALID", base + ".canonical_timeline.beats",
                    "schema 1.3 15-second creator performance must contain recognition Hook, animated proof, and close/detail conviction with proof before CTA",
                    phase_beat_ids=[[beat.get("beat_id") for beat in group] for group in phase_beats],
                ))
            if len(symmetric_hip_reset_beats) >= 3:
                errors.append(_error(
                    "STIFF_POSTURE_RISK", base + ".canonical_timeline.beats",
                    "three or more beats symmetrically reset both hands to hip anchors; carry task/prop/prior-endpoint state instead",
                    beat_ids=symmetric_hip_reset_beats,
                ))
            if visible_lipsync_beats >= 2 and len(set(on_camera_expression_cues)) < 2:
                errors.append(_error(
                    "FLAT_PERFORMANCE_RISK", base + ".canonical_timeline.beats",
                    "multiple visible-dialogue beats require at least two meaningfully distinct expression cues",
                    visible_dialogue_beats=visible_lipsync_beats,
                    expression_cues=on_camera_expression_cues,
                ))
            if voiceover_language.startswith("en") and not 40 <= total_spoken_words <= 62:
                errors.append(_error(
                    "VOICEOVER_PACING_INVALID", base + ".canonical_timeline.beats",
                    "15-second American-English creator voiceover must stay within the safe conversational commerce range",
                    words=total_spoken_words, minimum=40, maximum=62,
                    approximate_wpm=total_spoken_words * 4,
                ))
        if quality_v11 and enforce_word_budget and _decimal(duration) is not None:
            continuous_de_live = market_v14 and generation_controls.get("delivery_mode") == "de_live_simple"
            visible_words_per_15s = 54 if continuous_de_live else 36 if evidence_v13 and lip_sync_priority else 20
            total_budget = max(1, math.ceil(float(_decimal(duration)) * visible_words_per_15s / 15))
            visible_beat_budget = 7 if continuous_de_live else 3 if evidence_v13 and lip_sync_priority else 2
            if visible_lipsync_words > total_budget:
                errors.append(_error(
                    "LIPSYNC_COMPLEXITY_OVERLOAD", base + ".canonical_timeline.beats",
                    "visible lip-sync dialogue exceeds the clip-wide reliable-sync budget",
                    words=visible_lipsync_words, budget=total_budget,
                ))
            if evidence_v13 and visible_lipsync_beats > visible_beat_budget:
                errors.append(_error(
                    "LIPSYNC_COMPLEXITY_OVERLOAD", base + ".canonical_timeline.beats",
                    "visible lip-sync uses too many anchor beats for the selected fidelity allocation",
                    visible_dialogue_beats=visible_lipsync_beats, beat_budget=visible_beat_budget,
                ))
        if _decimal(duration) is not None and previous_end != _decimal(duration):
            if previous_end < _decimal(duration):
                errors.append(_error("TIMELINE_GAP", base + ".canonical_timeline.beats", "timeline does not cover the trailing duration", expected=float(_decimal(duration)), actual=float(previous_end)))
            errors.append(_error("DURATION_MISMATCH", base + ".canonical_timeline.beats", "final beat must end at requested duration"))
        renderings = variant.get("renderings")
        if not isinstance(renderings, dict):
            errors.append(_error("PROJECTION_INVALID", base + ".renderings", "renderings must be an object"))
            renderings = {}
        _check_projection(errors, base, "script", renderings.get("script"), "beats", SCRIPT_FIELDS, timeline, beats)
        broll_fields = (
            VISUAL_FIELDS + QUALITY_VISUAL_FIELDS + MOTION_RICH_VISUAL_FIELDS + EVIDENCE_RICH_VISUAL_FIELDS + MARKET_RICH_VISUAL_FIELDS
            if market_v14 else VISUAL_FIELDS + QUALITY_VISUAL_FIELDS + MOTION_RICH_VISUAL_FIELDS + EVIDENCE_RICH_VISUAL_FIELDS
            if evidence_v13 else VISUAL_FIELDS + QUALITY_VISUAL_FIELDS + MOTION_RICH_VISUAL_FIELDS
            if motion_v12 else VISUAL_FIELDS + QUALITY_VISUAL_FIELDS if quality_v11 else VISUAL_FIELDS
        )
        _check_broll(errors, base, renderings.get("broll"), timeline, beats, broll_fields)
        prompt = renderings.get("prompt")
        prompt_fields = MARKET_RICH_PROMPT_FIELDS if market_v14 else EVIDENCE_RICH_PROMPT_FIELDS if evidence_v13 else MOTION_RICH_PROMPT_FIELDS if motion_v12 else QUALITY_PROMPT_FIELDS if quality_v11 else PROMPT_FIELDS
        _check_projection(errors, base, "prompt", prompt, "beats", prompt_fields, timeline, beats)
        prompt_text = prompt.get("compiled_text") if isinstance(prompt, dict) else None
        prompt_hash = None
        if isinstance(prompt, dict):
            if prompt.get("variant_id") != variant_id:
                errors.append(_error("VARIANT_REFERENCE_MISMATCH", base + ".renderings.prompt.variant_id", "prompt variant_id does not match its owner"))
            expected_serializer = (
                PROMPT_V7_SERIALIZER_ID if market_v14_current else PROMPT_V6_SERIALIZER_ID if market_v14 else PROMPT_V4_SERIALIZER_ID if legacy_v13_prompt else PROMPT_V5_SERIALIZER_ID if evidence_v13
                else PROMPT_V3_SERIALIZER_ID if motion_v12 else PROMPT_V2_SERIALIZER_ID
                if quality_v11 else "canonical_prompt_v1"
            )
            allowed_serializers = (
                {PROMPT_V7_SERIALIZER_ID} if market_v14_current else
                {PROMPT_V6_SERIALIZER_ID} if market_v14 else
                {PROMPT_V4_SERIALIZER_ID, PROMPT_V5_SERIALIZER_ID}
                if evidence_v13 else {expected_serializer}
            )
            if prompt.get("serializer_id") not in allowed_serializers:
                errors.append(_error(
                    "PROMPT_SERIALIZER_MISMATCH" if market_v14 else "SUBMISSION_FIELD_MISSING",
                    base + ".renderings.prompt.serializer_id",
                    f"serializer_id must be {expected_serializer} for schema {schema_version} compilation",
                ))
            prompt_reference_ids = prompt.get("reference_ids")
            used_reference_ids = {
                beat.get("reference_binding") for beat in prompt.get("beats", [])
                if isinstance(beat, dict) and isinstance(beat.get("reference_binding"), str)
            }
            if not isinstance(prompt_reference_ids, list) or any(not isinstance(ref_id, str) for ref_id in prompt_reference_ids):
                references_valid = False
            else:
                references_valid = len(prompt_reference_ids) == len(set(prompt_reference_ids)) and set(prompt_reference_ids) == used_reference_ids and all(ref_id in reference_map for ref_id in prompt_reference_ids)
                if quality_v11:
                    expected_reference_ids = _ordered_reference_ids(
                        used_reference_ids, reference_map, identity_safe=identity_safe_v13,
                    )
                    references_valid = references_valid and prompt_reference_ids == expected_reference_ids
                    if identity_safe_v13:
                        references_valid = references_valid and set(prompt_reference_ids) == set(reference_map)
            if not references_valid:
                errors.append(_error(
                    "VARIANT_REFERENCE_MISMATCH", base + ".renderings.prompt.reference_ids",
                    "prompt references must exactly equal its beat bindings and resolve within the variant; v5 also forbids unused outbound apparel references",
                ))
            if isinstance(prompt.get("beats"), list):
                serialized = _canonical_json(prompt["beats"])
                if quality_v11:
                    if _canonical_json(prompt["beats"]) != _canonical_json(beats):
                        errors.append(_error(
                            "TIMELINE_ACTION_DRIFT", base + ".renderings.prompt.beats",
                            "schema 1.1 prompt beats must be exact canonical deep copies",
                        ))
                    expected_quality_hash = _canonical_sha256(quality_plan)
                    if prompt.get("quality_plan_sha256") != expected_quality_hash:
                        errors.append(_error(
                            "QUALITY_PLAN_HASH_MISMATCH", base + ".renderings.prompt.quality_plan_sha256",
                            "quality_plan_sha256 does not match canonical quality_plan",
                            expected=expected_quality_hash, actual=prompt.get("quality_plan_sha256"),
                        ))
                    if evidence_v13:
                        expected_research_hash = _canonical_sha256(research_bundle)
                        if prompt.get("research_bundle_sha256") != expected_research_hash:
                            errors.append(_error(
                                "QUALITY_PLAN_HASH_MISMATCH", base + ".renderings.prompt.research_bundle_sha256",
                                "research_bundle_sha256 does not match canonical research bundle",
                                expected=expected_research_hash, actual=prompt.get("research_bundle_sha256"),
                            ))
                    if market_v14:
                        profile_id = batch_key.get("market_prompt_profile_id") if isinstance(batch_key, dict) else None
                        expected_profile_hash = _market_contract.canonical_sha256(
                            _market_contract.MARKET_PROFILES.get(str(profile_id)),
                        )
                        expected_controls_hash = _market_contract.canonical_sha256(generation_controls)
                        expected_review_hash = _market_contract.canonical_sha256(variant.get("voiceover_review"))
                        for field, expected_hash in (
                            ("market_prompt_profile_sha256", expected_profile_hash),
                            ("generation_controls_sha256", expected_controls_hash),
                            ("voiceover_review_sha256", expected_review_hash),
                        ):
                            if prompt.get(field) != expected_hash:
                                errors.append(_error(
                                    "QUALITY_PLAN_HASH_MISMATCH", base + ".renderings.prompt." + field,
                                    f"{field} does not match its canonical schema 1.4 source",
                                    expected=expected_hash, actual=prompt.get(field),
                                ))
                        if market_v14_current:
                            expected_deadline_hash = _market_contract.canonical_sha256(
                                variant.get("three_layer_deadlines"),
                            )
                            if prompt.get("three_layer_deadlines_sha256") != expected_deadline_hash:
                                errors.append(_error(
                                    "DEADLINE_HASH_MISMATCH",
                                    base + ".renderings.prompt.three_layer_deadlines_sha256",
                                    "three_layer_deadlines_sha256 does not match the canonical three-layer deadline contract",
                                    expected=expected_deadline_hash,
                                    actual=prompt.get("three_layer_deadlines_sha256"),
                                ))
                    expected_beats_hash = _canonical_sha256(beats)
                    if prompt.get("canonical_beats_sha256") != expected_beats_hash:
                        errors.append(_error(
                            "CANONICAL_BEATS_HASH_MISMATCH", base + ".renderings.prompt.canonical_beats_sha256",
                            "canonical_beats_sha256 does not match canonical timeline beats",
                            expected=expected_beats_hash, actual=prompt.get("canonical_beats_sha256"),
                        ))
                    canonical_reference_ids = _ordered_reference_ids({
                        beat.get("reference_binding") for beat in beats
                        if isinstance(beat, dict) and isinstance(beat.get("reference_binding"), str)
                    }, reference_map, identity_safe=identity_safe_v13)
                    expected_reference_contract = (
                        _market_contract.reference_contract_v6(canonical_reference_ids, reference_map)
                        if market_v14 else _reference_contract_text(
                            canonical_reference_ids, reference_map, identity_safe=identity_safe_v13,
                        )
                    )
                    reference_contract = prompt.get("reference_contract_text")
                    if not _nonempty(reference_contract) or reference_contract != expected_reference_contract:
                        errors.append(_error(
                            "PROMPT_QUALITY_BLOCK_MISSING", base + ".renderings.prompt.reference_contract_text",
                            "reference_contract_text must be the deterministic role, exclusion, and positive-replacement lock",
                        ))
                    if isinstance(prompt_text, str):
                        if serialized and serialized in prompt_text:
                            errors.append(_error(
                                "PROMPT_V2_FULL_JSON_LEAK", base + ".renderings.prompt.compiled_text",
                                f"{expected_serializer} must not include the full internal beat JSON",
                            ))
                        expected_prompt_text = (
                            _market_contract.serialize_prompt_v7(
                                expected_reference_contract, beats, reference_map, batch_key, generation_controls,
                                variant.get("three_layer_deadlines"),
                            ) if market_v14_current else _market_contract.serialize_prompt_v6(
                                expected_reference_contract, beats, reference_map, batch_key, generation_controls,
                            ) if market_v14 else _serialize_prompt_v4(expected_reference_contract, beats, reference_map, batch_key)
                            if legacy_v13_prompt else _serialize_prompt_v5(expected_reference_contract, beats, reference_map, batch_key)
                            if evidence_v13 else _serialize_prompt_v3(expected_reference_contract, beats, reference_map, batch_key)
                            if motion_v12 else _serialize_prompt_v2(expected_reference_contract, beats, reference_map, batch_key)
                        )
                        if prompt_text != expected_prompt_text:
                            errors.append(_error(
                                "PROMPT_SERIALIZER_MISMATCH", base + ".renderings.prompt.compiled_text",
                                f"compiled_text does not exactly match {expected_serializer} deterministic shot serialization",
                            ))
                        if evidence_v13:
                            forbidden_research_tokens = {
                                str(source.get("review_id")) for source in source_map.values()
                                if _nonempty(source.get("review_id"))
                            }
                            forbidden_research_tokens.update({
                                str(value) for value in (research_bundle.get("requested_url"), research_bundle.get("canonical_url"))
                                if _nonempty(value)
                            })
                            leaked = sorted(token for token in forbidden_research_tokens if token in prompt_text)
                            if leaked:
                                errors.append(_error(
                                    "PROMPT_V2_FULL_JSON_LEAK", base + ".renderings.prompt.compiled_text",
                                    "paid generation text must not contain review IDs or research URLs",
                                    leaked_tokens=leaked,
                                ))
                        if market_v14:
                            errors.extend(_market_contract.meta_leaks(
                                [(base + ".renderings.prompt.compiled_text", prompt_text)],
                                forbidden_research_tokens,
                            ))
                elif not isinstance(prompt_text, str) or serialized not in prompt_text:
                    errors.append(_error("PROMPT_SERIALIZER_MISMATCH", base + ".renderings.prompt.compiled_text", "canonical serialized beat block must appear verbatim in compiled_text"))
        if not isinstance(prompt_text, str) or not prompt_text.strip():
            errors.append(_error("PROMPT_TEXT_MISSING", base + ".renderings.prompt.compiled_text", "non-empty compiled prompt text is required"))
        else:
            prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            prompt_hashes.setdefault(prompt_hash, []).append((index, str(variant.get("color_name", ""))))
            stored_hash = prompt.get("compiled_text_sha256") if isinstance(prompt, dict) else None
            if stored_hash is None:
                errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".renderings.prompt.compiled_text_sha256", "submission requires compiled_text_sha256"))
                stored_hash = prompt.get("prompt_hash") if isinstance(prompt, dict) else None
            if stored_hash != prompt_hash:
                errors.append(_error("PROMPT_HASH_MISMATCH", base + ".renderings.prompt.compiled_text_sha256", "stored prompt hash does not match compiled_text"))
        caption = variant.get("caption")
        if not isinstance(caption, str) or not caption.strip():
            errors.append(_error("CAPTION_INVALID", base + ".caption", "non-empty caption is required"))
        hashtags = variant.get("hashtags")
        if not isinstance(hashtags, list) or len(hashtags) != 5:
            errors.append(_error("CAPTION_HASHTAG_COUNT", base + ".hashtags", "exactly five hashtags are required"))
        elif any(not _valid_hashtag(tag) for tag in hashtags) or len({_norm(tag) for tag in hashtags}) != 5:
            errors.append(_error("CAPTION_HASHTAG_INVALID", base + ".hashtags", "hashtags must be five unique valid #tags relevant to the product, market, occasion, or buyer search intent"))
        if evidence_v13:
            caption_claim_ids = _string_set(variant.get("caption_claim_ids"))
            caption_pain_ids = _string_set(variant.get("caption_pain_point_ids"))
            if caption_claim_ids is None or not caption_claim_ids or not caption_claim_ids.issubset(claims_allowlist_ids):
                errors.append(_error(
                    "CAPTION_INVALID", base + ".caption_claim_ids",
                    "schema 1.3 caption claims must be a non-empty subset of the allowlist",
                ))
            if caption_pain_ids is None or not caption_pain_ids or any(
                pain_id not in pain_map
                or pain_map[pain_id].get("selected_for_script") is not True
                or pain_map[pain_id].get("status") != "script_eligible"
                for pain_id in caption_pain_ids
            ):
                errors.append(_error(
                    "CAPTION_INVALID", base + ".caption_pain_point_ids",
                    "schema 1.3 caption pain IDs must resolve to selected script-eligible pains",
                ))
            caption_norm = _norm(caption)
            claim_bindings = variant.get("caption_claim_bindings")
            pain_bindings = variant.get("caption_pain_bindings")
            claim_binding_map: dict[str, str] = {}
            pain_binding_map: dict[str, str] = {}
            if isinstance(claim_bindings, list):
                for binding in claim_bindings:
                    if not isinstance(binding, dict) or not _nonempty(binding.get("claim_id")) or not _nonempty(binding.get("text_anchor")):
                        claim_binding_map = {}
                        break
                    claim_id = str(binding["claim_id"])
                    if claim_id in claim_binding_map:
                        claim_binding_map = {}
                        break
                    claim_binding_map[claim_id] = str(binding["text_anchor"])
            if isinstance(pain_bindings, list):
                for binding in pain_bindings:
                    if not isinstance(binding, dict) or not _nonempty(binding.get("pain_point_id")) or not _nonempty(binding.get("text_anchor")):
                        pain_binding_map = {}
                        break
                    pain_id = str(binding["pain_point_id"])
                    if pain_id in pain_binding_map:
                        pain_binding_map = {}
                        break
                    pain_binding_map[pain_id] = str(binding["text_anchor"])
            claim_bindings_valid = (
                bool(caption_claim_ids)
                and set(claim_binding_map) == (caption_claim_ids or set())
                and all(
                    claim_id in claims_registry
                    and _norm(anchor) in caption_norm
                    and _norm(anchor) in {_norm(term) for term in claims_registry[claim_id].get("spoken_claim_terms", [])}
                    for claim_id, anchor in claim_binding_map.items()
                )
            )
            pain_bindings_valid = (
                bool(caption_pain_ids)
                and set(pain_binding_map) == (caption_pain_ids or set())
                and all(
                    pain_id in pain_map
                    and _norm(anchor) in caption_norm
                    and _norm(anchor) in {_norm(term) for term in pain_map[pain_id].get("target_language_terms", [])}
                    for pain_id, anchor in pain_binding_map.items()
                )
            )
            if not claim_bindings_valid or not pain_bindings_valid:
                errors.append(_error(
                    "CAPTION_INVALID", base + ".caption_claim_bindings",
                    "caption bindings must exactly cover declared claim/pain IDs with registered target-language anchors present in the caption",
                ))
        creative = variant.get("creative_delta")
        if not isinstance(creative, dict):
            errors.append(_error("CREATIVE_DELTA_INVALID", base + ".creative_delta", "creative_delta must be an object"))
            creative = {}
        if market_v14:
            errors.extend(_market_contract.validate_scene_projection(
                generation_controls.get("scene_strategy"),
                quality_plan.get("continuity_anchors") if isinstance(quality_plan, dict) else None,
                creative,
                base,
            ))
        dimensions = (
            creative.get("hook_angle_id"), creative.get("opening_visual_id"),
            (creative.get("scene_id"), creative.get("scene_palette")), creative.get("styling_signature"),
            creative.get("proof_focus_id"), creative.get("signature_action_id"),
        )
        creative_fields = ["hook_angle_id", "opening_visual_id", "scene_id", "styling_signature", "proof_focus_id", "signature_action_id"]
        if evidence_v13:
            creative_fields.append("pain_focus_id")
        for field in creative_fields:
            if not creative.get(field):
                errors.append(_error("CREATIVE_DELTA_FIELD_MISSING", base + ".creative_delta." + field, "required creative dimension is missing"))
        hook_lines = [beat.get("spoken_line") for beat in beats if isinstance(beat, dict) and beat.get("purpose") == "hook" and isinstance(beat.get("spoken_line"), str) and beat.get("spoken_line")]
        timeline_hook = " ".join(hook_lines)
        hook = creative.get("hook_text_original")
        if not isinstance(hook, str):
            errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".creative_delta.hook_text_original", "submission requires hook_text_original"))
            hook = creative.get("hook_text") if isinstance(creative.get("hook_text"), str) else timeline_hook
        if hook != timeline_hook:
            errors.append(_error("TIMELINE_VO_DRIFT", base + ".creative_delta.hook_text_original", "hook_text_original must equal joined canonical hook speech"))
        color_terms = creative.get("color_terms")
        if not isinstance(color_terms, list) or any(not isinstance(term, str) or not term for term in color_terms):
            errors.append(_error("SUBMISSION_FIELD_MISSING", base + ".creative_delta.color_terms", "submission requires a string color_terms array"))
            color_terms = [variant.get("color_name", "")]
        non_cta = [beat for beat in beats if isinstance(beat, dict) and beat.get("purpose") != "cta"]
        if quality_v11:
            expected_intents = [beat.get("spoken_intent_id") for beat in non_cta]
            if creative.get("spoken_intent_ids") != expected_intents:
                errors.append(_error(
                    "DIRECTING_INTENT_DRIFT", base + ".creative_delta.spoken_intent_ids",
                    "spoken_intent_ids must exactly project canonical non-CTA intent IDs in order",
                    expected=expected_intents, actual=creative.get("spoken_intent_ids"),
                ))
        if evidence_v13:
            pain_focus_id = creative.get("pain_focus_id")
            selected_pain = pain_map.get(str(pain_focus_id))
            hook_pain_ids = {
                str(beat.get("pain_point_id")) for beat in beats
                if isinstance(beat, dict) and beat.get("beat_role") == "context_hook"
            }
            used_claim_ids = {
                str(proof.get("claim_id")) for proof in claim_proof_map.values()
                if isinstance(proof, dict)
            }
            if (
                selected_pain is None or selected_pain.get("selected_for_script") is not True
                or selected_pain.get("status") != "script_eligible"
                or hook_pain_ids != {str(pain_focus_id)}
                or not ((_string_set(selected_pain.get("solution_claim_ids")) or set()) & used_claim_ids)
            ):
                errors.append(_error(
                    "PAIN_PROOF_BINDING_INVALID", base + ".creative_delta.pain_focus_id",
                    "variant pain focus must equal its hook pain and at least one mapped solution claim must appear in the proof plan",
                ))
            first_product_proof_beat = next(
                (
                    beat for beat in beats
                    if isinstance(beat, dict) and beat.get("beat_role") == "product_claim"
                    and _nonempty(beat.get("claim_proof_id"))
                ),
                None,
            )
            first_product_proof = (
                claim_proof_map.get(str(first_product_proof_beat.get("claim_proof_id")))
                if isinstance(first_product_proof_beat, dict) else None
            )
            selected_solution_ids = (
                _string_set(selected_pain.get("solution_claim_ids")) or set()
                if isinstance(selected_pain, dict) else set()
            )
            if (
                not isinstance(first_product_proof, dict)
                or str(first_product_proof.get("claim_id")) not in selected_solution_ids
            ):
                errors.append(_error(
                    "PAIN_PROOF_BINDING_INVALID", base + ".canonical_timeline.beats",
                    "the first product-proof beat after the hook must visibly answer the selected pain with one mapped solution claim",
                    expected_solution_claim_ids=sorted(selected_solution_ids),
                    actual_first_claim_id=(
                        first_product_proof.get("claim_id") if isinstance(first_product_proof, dict) else None
                    ),
                ))
        if quality_v11 and quality_plan.get("hero_proof_id") != creative.get("proof_focus_id"):
            errors.append(_error(
                "DIRECTING_INTENT_DRIFT", base + ".quality_plan.hero_proof_id",
                "hero_proof_id must equal creative_delta.proof_focus_id",
                expected=creative.get("proof_focus_id"), actual=quality_plan.get("hero_proof_id"),
            ))
        deadline_asset = (
            variant.get("three_layer_deadlines", {}).get("asset_deadlines", {})
            if market_v14_current and isinstance(variant.get("three_layer_deadlines"), dict)
            else {}
        )
        contexts.append({
            "index": index, "variant": variant, "dimensions": dimensions, "hook": hook,
            "color_terms": color_terms, "garment_signature": {field: signature.get(field) for field in GARMENT_FIELDS},
            "deadline_asset_invariants": {
                field: deadline_asset.get(field) for field in (
                    "creator_identity", "subject_appearance_lock",
                    "garment_structure_lock", "fabric_physics_lock",
                )
            },
            "reference_ids": set(reference_map),
            "intents": [beat.get("spoken_intent_id") for beat in non_cta],
            "visual_signatures": [(_norm(beat.get("framing")), _norm(beat.get("core_action")), _norm(beat.get("product_point"))) for beat in non_cta],
            "prompt_hash": prompt_hash,
        })
    if mode == "sku_batch_compile":
        for left, right in itertools.combinations(contexts, 2):
            lv, rv = left["variant"], right["variant"]
            pair = f"variants[{left['index']}]~variants[{right['index']}]"
            color_terms = left["color_terms"] + right["color_terms"]
            if left["garment_signature"] != right["garment_signature"]:
                errors.append(_error("BATCH_NOT_SAME_SKU", pair, "garment_signature differs across color variants"))
            if market_v14_current and left["deadline_asset_invariants"] != right["deadline_asset_invariants"]:
                errors.append(_error(
                    "COLOR_VARIANT_INVARIANT_DRIFT",
                    pair,
                    "same-SKU color variants must keep creator, subject, garment structure, and fabric-physics deadlines identical",
                ))
            if left["reference_ids"] & right["reference_ids"]:
                errors.append(_error("VARIANT_REFERENCE_MISMATCH", pair, "reference_id must not resolve to another variant"))
            different = [i for i, (a, b) in enumerate(zip(left["dimensions"], right["dimensions"])) if _norm(a) != _norm(b)]
            attention = any(i in {0, 1} for i in different)
            visual_world = any(i in {2, 3} for i in different)
            proof_action = any(i in {4, 5} for i in different)
            pair_failed = False
            if _without_colors(left["hook"], color_terms) == _without_colors(right["hook"], color_terms):
                errors.append(_error("DIFF_COLOR_ONLY", pair, "hooks are identical after color-word normalization"))
                pair_failed = True
            intent_diffs = sum(a != b for a, b in itertools.zip_longest(left["intents"], right["intents"], fillvalue=object()))
            visual_diffs = sum(a != b for a, b in itertools.zip_longest(left["visual_signatures"], right["visual_signatures"], fillvalue=object()))
            if len(different) < 3 or not (attention and visual_world and proof_action) or intent_diffs < 2 or visual_diffs < 2:
                errors.append(_error(
                    "DIFF_THRESHOLD_NOT_MET", pair, "pair does not satisfy all differentiation thresholds",
                    dimension_difference_count=len(different), attention=attention, visual_world=visual_world,
                    proof_action=proof_action, non_cta_intent_differences=intent_diffs,
                    non_cta_visual_signature_differences=visual_diffs,
                ))
                pair_failed = True
            if pair_failed:
                errors.append(_error("DIFF_PAIR_COLLISION", pair, "variant pair collides under the differentiation gate"))
            if _without_colors(lv.get("caption"), color_terms) == _without_colors(rv.get("caption"), color_terms):
                errors.append(_error("CAPTION_COLOR_ONLY", pair, "captions are identical after color-word normalization"))
            left_tags = {_norm(tag) for tag in lv.get("hashtags", []) if isinstance(tag, str)}
            right_tags = {_norm(tag) for tag in rv.get("hashtags", []) if isinstance(tag, str)}
            if min(len(left_tags), len(right_tags)) - len(left_tags & right_tags) < 2:
                errors.append(_error("HASHTAG_PAIR_COLLISION", pair, "variant pair must differ by at least two hashtags"))
        for digest, occurrences in sorted(prompt_hashes.items()):
            distinct_colors = {_norm(color) for _, color in occurrences}
            if len(occurrences) > 1 and len(distinct_colors) > 1:
                errors.append(_error("PROMPT_REUSED_ACROSS_VARIANTS", "variants", "compiled prompt hash is reused across colors", prompt_hash=digest, variants=[i for i, _ in occurrences]))
    errors.extend(_streaming_release_errors(document))
    legacy_v5_exact_resume = _legacy_v5_exact_resume_state(document, validator_valid=not errors)
    return _result(
        errors,
        compile_mode=mode,
        variant_count=len(variants),
        timeline_count=len(timeline_ids),
        pair_count=len(contexts) * (len(contexts) - 1) // 2 if mode == "sku_batch_compile" else 0,
        eligible_for_new_submission=market_v14_current and not errors,
        market_prompt_v7_submission_eligible=market_v14_current and not errors,
        market_prompt_v6_submission_eligible=False,
        historical_prompt_v6_read_only=market_v14_legacy_v6,
        historical_prompt_v4_read_only=legacy_v4_seen,
        legacy_v5_exact_resume=legacy_v5_exact_resume,
    )


def _beat(prefix: str, start: int, end: int, purpose: str, style: int, color: str, position: int) -> dict[str, Any]:
    styles = (
        (
            ("waist-up phone shot", "gentle hem release", "relaxed silhouette", "wardrobe_pain"),
            ("neckline close-up", "touch neckline once", "neckline shape", "neckline_proof"),
            ("full mirror shot", "add shoulder bag", "weekend styling", "outfit_idea"),
        ),
        (
            ("full-body doorway shot", "slow half-turn", "side drape", "occasion_hook"),
            ("sleeve close-up", "touch cuff once", "sleeve coverage", "sleeve_proof"),
            ("three-quarter phone shot", "make one front tuck", "balanced proportions", "layering_idea"),
        ),
    )
    if purpose == "cta":
        framing, action, point, intent = "waist-up CTA", "small down-left gesture", "purchase action", "cta"
        spoken = "If you love it, tap the link in the lower left."
    else:
        framing, action, point, intent = styles[style][position]
        spoken = f"{color} works here: {point}." if purpose == "hook" else f"Notice the {point}."
    return {
        "beat_id": f"{prefix}{position + 1}", "start_seconds": start, "end_seconds": end,
        "purpose": purpose, "framing": framing, "camera_motion": "steady phone",
        "scene": "bright bedroom" if style == 0 else "apartment doorway",
        "lighting": "window light from camera left", "actor": "one creator",
        "core_action": action, "action_target": "garment", "left_hand_state": "rests at side",
        "right_hand_state": "performs main action", "spoken_line": spoken, "silence_reason": None,
        "spoken_intent_id": intent, "product_point": point,
        "styling_point": "denim and small bag" if style == 0 else "trousers and flats",
        "product_visibility": "full" if position != 1 else "detail",
        "reference_binding": f"ref-{prefix}", "micro_expression": "small natural smile", "audio": "voiceover",
    }


def _compile_renderings_v1(timeline: dict[str, Any], color: str, variant_id: str) -> dict[str, Any]:
    beats = timeline["beats"]
    def project(fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [{"start_seconds": beat["start_seconds"], "end_seconds": beat["end_seconds"], **{field: copy.deepcopy(beat.get(field)) for field in fields}} for beat in beats]
    source = {"source_timeline_id": timeline["timeline_id"], "source_timeline_version": timeline["timeline_version"]}
    prompt_beats = copy.deepcopy(beats)
    serialized = _canonical_json(prompt_beats)
    compiled_text = color + ":" + serialized
    return {
        "script": {**source, "beats": project(SCRIPT_FIELDS)},
        "broll": {**source, "shots": [
            {"shot_id": "shot-" + beat["beat_id"], "parent_beat_id": beat["beat_id"],
             "start_seconds": beat["start_seconds"], "end_seconds": beat["end_seconds"],
             **{field: copy.deepcopy(beat.get(field)) for field in VISUAL_FIELDS}}
            for beat in beats
        ]},
        "prompt": {
            **source, "variant_id": variant_id, "serializer_id": "canonical_prompt_v1",
            "reference_ids": sorted({beat["reference_binding"] for beat in beats}),
            "beats": prompt_beats, "compiled_text": compiled_text,
            "compiled_text_sha256": _sha256_text(compiled_text),
        },
    }


def _compile_renderings_v2(variant: dict[str, Any], batch_key: dict[str, Any] | None) -> dict[str, Any]:
    timeline = variant["canonical_timeline"]
    beats = timeline["beats"]
    variant_id = variant.get("variant_id")
    references = variant.get("references") if isinstance(variant.get("references"), list) else []
    reference_map: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        if isinstance(reference, dict) and isinstance(reference.get("reference_id"), str):
            reference_map.setdefault(reference["reference_id"], []).append(reference)

    def project(fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {
                "start_seconds": beat["start_seconds"], "end_seconds": beat["end_seconds"],
                **{field: copy.deepcopy(beat.get(field)) for field in fields},
            }
            for beat in beats
        ]

    source = {"source_timeline_id": timeline["timeline_id"], "source_timeline_version": timeline["timeline_version"]}
    prompt_beats = copy.deepcopy(beats)
    reference_ids = sorted({
        beat.get("reference_binding") for beat in beats
        if isinstance(beat, dict) and isinstance(beat.get("reference_binding"), str)
    })
    reference_contract = _reference_contract_text(reference_ids, reference_map)
    compiled_text = _serialize_prompt_v2(reference_contract, prompt_beats, reference_map, batch_key)
    return {
        "script": {**source, "beats": project(SCRIPT_FIELDS)},
        "broll": {**source, "shots": [
            {
                "shot_id": "shot-" + str(beat.get("beat_id", index + 1)),
                "parent_beat_id": beat.get("beat_id"),
                "start_seconds": beat.get("start_seconds"), "end_seconds": beat.get("end_seconds"),
                **{field: copy.deepcopy(beat.get(field)) for field in VISUAL_FIELDS + QUALITY_VISUAL_FIELDS},
            }
            for index, beat in enumerate(beats)
        ]},
        "prompt": {
            **source, "variant_id": variant_id, "serializer_id": PROMPT_V2_SERIALIZER_ID,
            "reference_ids": reference_ids, "beats": prompt_beats,
            "reference_contract_text": reference_contract,
            "quality_plan_sha256": _canonical_sha256(variant.get("quality_plan")),
            "canonical_beats_sha256": _canonical_sha256(prompt_beats),
            "compiled_text": compiled_text, "compiled_text_sha256": _sha256_text(compiled_text),
        },
    }


def _compile_renderings_v3(variant: dict[str, Any], batch_key: dict[str, Any] | None) -> dict[str, Any]:
    renderings = _compile_renderings_v2(variant, batch_key)
    beats = variant["canonical_timeline"]["beats"]
    references = variant.get("references") if isinstance(variant.get("references"), list) else []
    reference_map: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        if isinstance(reference, dict) and isinstance(reference.get("reference_id"), str):
            reference_map.setdefault(reference["reference_id"], []).append(reference)
    for shot, beat in zip(renderings["broll"]["shots"], beats):
        for field in MOTION_RICH_VISUAL_FIELDS:
            shot[field] = copy.deepcopy(beat.get(field))
    prompt = renderings["prompt"]
    prompt["serializer_id"] = PROMPT_V3_SERIALIZER_ID
    compiled_text = _serialize_prompt_v3(prompt["reference_contract_text"], beats, reference_map, batch_key)
    prompt["compiled_text"] = compiled_text
    prompt["compiled_text_sha256"] = _sha256_text(compiled_text)
    return renderings


def _compile_renderings_v4(
    variant: dict[str, Any], batch_key: dict[str, Any] | None, research_bundle: Any,
) -> dict[str, Any]:
    renderings = _compile_renderings_v3(variant, batch_key)
    beats = variant["canonical_timeline"]["beats"]
    for shot, beat in zip(renderings["broll"]["shots"], beats):
        for field in EVIDENCE_RICH_VISUAL_FIELDS:
            shot[field] = copy.deepcopy(beat.get(field))
    prompt = renderings["prompt"]
    prompt["serializer_id"] = PROMPT_V4_SERIALIZER_ID
    prompt["research_bundle_sha256"] = _canonical_sha256(research_bundle)
    references = variant.get("references") if isinstance(variant.get("references"), list) else []
    reference_map: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        if isinstance(reference, dict) and isinstance(reference.get("reference_id"), str):
            reference_map.setdefault(reference["reference_id"], []).append(reference)
    compiled_text = _serialize_prompt_v4(prompt["reference_contract_text"], beats, reference_map, batch_key)
    prompt["compiled_text"] = compiled_text
    prompt["compiled_text_sha256"] = _sha256_text(compiled_text)
    return renderings


def _compile_renderings_v5(
    variant: dict[str, Any], batch_key: dict[str, Any] | None, research_bundle: Any,
) -> dict[str, Any]:
    renderings = _compile_renderings_v4(variant, batch_key, research_bundle)
    beats = variant["canonical_timeline"]["beats"]
    references = variant.get("references") if isinstance(variant.get("references"), list) else []
    reference_map: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        if isinstance(reference, dict) and isinstance(reference.get("reference_id"), str):
            reference_map.setdefault(reference["reference_id"], []).append(reference)
    prompt = renderings["prompt"]
    reference_ids = _ordered_reference_ids(
        {
            beat.get("reference_binding") for beat in beats
            if isinstance(beat, dict) and isinstance(beat.get("reference_binding"), str)
        },
        reference_map,
        identity_safe=True,
    )
    reference_contract = _reference_contract_text(reference_ids, reference_map, identity_safe=True)
    prompt["serializer_id"] = PROMPT_V5_SERIALIZER_ID
    prompt["reference_ids"] = reference_ids
    prompt["reference_contract_text"] = reference_contract
    compiled_text = _serialize_prompt_v5(reference_contract, beats, reference_map, batch_key)
    prompt["compiled_text"] = compiled_text
    prompt["compiled_text_sha256"] = _sha256_text(compiled_text)
    return renderings


def _compile_renderings_v6(
    variant: dict[str, Any], batch_key: dict[str, Any] | None, research_bundle: Any,
) -> dict[str, Any]:
    """Compile schema 1.4 through the isolated US/DE market contract."""
    renderings = _compile_renderings_v5(variant, batch_key, research_bundle)
    beats = variant["canonical_timeline"]["beats"]
    for shot, beat in zip(renderings["broll"]["shots"], beats):
        for field in MARKET_RICH_VISUAL_FIELDS:
            shot[field] = copy.deepcopy(beat.get(field))
    references = variant.get("references") if isinstance(variant.get("references"), list) else []
    reference_map: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        if isinstance(reference, dict) and isinstance(reference.get("reference_id"), str):
            reference_map.setdefault(reference["reference_id"], []).append(reference)
    reference_ids = _ordered_reference_ids(
        {
            beat.get("reference_binding") for beat in beats
            if isinstance(beat, dict) and isinstance(beat.get("reference_binding"), str)
        },
        reference_map,
        identity_safe=True,
    )
    reference_contract = _market_contract.reference_contract_v6(reference_ids, reference_map)
    controls = variant.get("generation_controls") if isinstance(variant.get("generation_controls"), dict) else {}
    profile_id = batch_key.get("market_prompt_profile_id") if isinstance(batch_key, dict) else None
    prompt = renderings["prompt"]
    prompt["serializer_id"] = PROMPT_V6_SERIALIZER_ID
    prompt["reference_ids"] = reference_ids
    prompt["reference_contract_text"] = reference_contract
    prompt["market_prompt_profile_sha256"] = _market_contract.canonical_sha256(
        _market_contract.MARKET_PROFILES.get(str(profile_id)),
    )
    prompt["generation_controls_sha256"] = _market_contract.canonical_sha256(controls)
    prompt["voiceover_review_sha256"] = _market_contract.canonical_sha256(variant.get("voiceover_review"))
    compiled_text = _market_contract.serialize_prompt_v6(
        reference_contract, beats, reference_map, batch_key, controls,
    )
    prompt["compiled_text"] = compiled_text
    prompt["compiled_text_sha256"] = _sha256_text(compiled_text)
    return renderings


def _compile_renderings_v7(
    variant: dict[str, Any], batch_key: dict[str, Any] | None, research_bundle: Any,
) -> dict[str, Any]:
    """Compile current schema 1.4 with the three-layer deadline contract."""
    renderings = _compile_renderings_v6(variant, batch_key, research_bundle)
    beats = variant["canonical_timeline"]["beats"]
    references = variant.get("references") if isinstance(variant.get("references"), list) else []
    reference_map: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        if isinstance(reference, dict) and isinstance(reference.get("reference_id"), str):
            reference_map.setdefault(reference["reference_id"], []).append(reference)
    reference_ids = _ordered_reference_ids(
        {
            beat.get("reference_binding") for beat in beats
            if isinstance(beat, dict) and isinstance(beat.get("reference_binding"), str)
        },
        reference_map,
        identity_safe=True,
    )
    reference_contract = _market_contract.reference_contract_v6(reference_ids, reference_map)
    controls = variant.get("generation_controls") if isinstance(variant.get("generation_controls"), dict) else {}
    deadlines = variant.get("three_layer_deadlines")
    prompt = renderings["prompt"]
    prompt["serializer_id"] = PROMPT_V7_SERIALIZER_ID
    prompt["three_layer_deadlines_sha256"] = _market_contract.canonical_sha256(deadlines)
    compiled_text = _market_contract.serialize_prompt_v7(
        reference_contract, beats, reference_map, batch_key, controls, deadlines,
    )
    prompt["compiled_text"] = compiled_text
    prompt["compiled_text_sha256"] = _sha256_text(compiled_text)
    return renderings


def _timeline_compilable(timeline: Any) -> bool:
    if (
        not isinstance(timeline, dict) or not _nonempty(timeline.get("timeline_id"))
        or "timeline_version" not in timeline or not isinstance(timeline.get("beats"), list)
        or not timeline["beats"]
    ):
        return False
    return all(
        isinstance(beat, dict)
        and _nonempty(beat.get("beat_id"))
        and isinstance(beat.get("reference_binding"), str)
        and "start_seconds" in beat and "end_seconds" in beat
        for beat in timeline["beats"]
    )


def compile_document(document: Any) -> Any:
    compiled = copy.deepcopy(document)
    if not isinstance(compiled, dict) or not isinstance(compiled.get("variants"), list):
        return compiled
    schema_version = compiled.get("schema_version")
    quality_v11 = schema_version in {"1.1", "1.2", "1.3", "1.4"}
    for variant in compiled["variants"]:
        if not isinstance(variant, dict):
            continue
        timeline = variant.get("canonical_timeline")
        if not _timeline_compilable(timeline):
            continue
        if schema_version == "1.4":
            shared_core = compiled.get("shared_core") if isinstance(compiled.get("shared_core"), dict) else {}
            if compiled.get("quality_contract_id") == QUALITY_CONTRACT_ID_V14:
                variant["renderings"] = _compile_renderings_v7(
                    variant, compiled.get("batch_key"), shared_core.get("research_bundle"),
                )
            else:
                variant["renderings"] = _compile_renderings_v6(
                    variant, compiled.get("batch_key"), shared_core.get("research_bundle"),
                )
        elif schema_version == "1.3":
            shared_core = compiled.get("shared_core") if isinstance(compiled.get("shared_core"), dict) else {}
            variant["renderings"] = _compile_renderings_v5(
                variant, compiled.get("batch_key"), shared_core.get("research_bundle"),
            )
        elif schema_version == "1.2":
            variant["renderings"] = _compile_renderings_v3(variant, compiled.get("batch_key"))
        elif quality_v11:
            variant["renderings"] = _compile_renderings_v2(variant, compiled.get("batch_key"))
        else:
            variant["renderings"] = _compile_renderings_v1(
                timeline, str(variant.get("color_name", "")), str(variant.get("variant_id", "")),
            )
    return compiled


def _attach_director_receipts(
    result: dict[str, Any], document: Any, batch_compile_sha256: str,
) -> None:
    result["batch_compile_sha256"] = batch_compile_sha256
    receipts: list[dict[str, Any]] = []
    if not isinstance(document, dict):
        result["director_receipts"] = receipts
        return
    for variant in document.get("variants", []):
        if not isinstance(variant, dict):
            continue
        renderings = variant.get("renderings") if isinstance(variant.get("renderings"), dict) else {}
        prompt = renderings.get("prompt") if isinstance(renderings.get("prompt"), dict) else {}
        timeline = variant.get("canonical_timeline") if isinstance(variant.get("canonical_timeline"), dict) else {}
        batch_key = document.get("batch_key") if isinstance(document.get("batch_key"), dict) else {}
        receipts.append({
            "variant_id": variant.get("variant_id"),
            "timeline_id": timeline.get("timeline_id"),
            "timeline_version": timeline.get("timeline_version"),
            "batch_compile_sha256": batch_compile_sha256,
            "schema_version": document.get("schema_version"),
            "quality_contract_id": document.get("quality_contract_id"),
            "deadline_contract_id": (
                variant.get("three_layer_deadlines", {}).get("contract_id")
                if isinstance(variant.get("three_layer_deadlines"), dict) else None
            ),
            "market_prompt_contract_id": document.get("market_prompt_contract_id"),
            "market_prompt_profile_id": batch_key.get("market_prompt_profile_id"),
            "market": batch_key.get("market"),
            "voiceover_language": batch_key.get("voiceover_language"),
            "serializer_id": prompt.get("serializer_id"),
            "quality_plan_sha256": prompt.get("quality_plan_sha256"),
            "research_bundle_sha256": prompt.get("research_bundle_sha256"),
            "canonical_beats_sha256": prompt.get("canonical_beats_sha256"),
            "market_prompt_profile_sha256": prompt.get("market_prompt_profile_sha256"),
            "generation_controls_sha256": prompt.get("generation_controls_sha256"),
            "voiceover_review_sha256": prompt.get("voiceover_review_sha256"),
            "three_layer_deadlines_sha256": prompt.get("three_layer_deadlines_sha256"),
            "compiled_text_sha256": prompt.get("compiled_text_sha256"),
            "director_valid": result.get("valid") is True,
            "eligible_for_new_submission": result.get("eligible_for_new_submission") is True,
        })
    result["director_receipts"] = receipts


def _variant(prefix: str, color: str, style: int) -> dict[str, Any]:
    grid = ((0, 4, "hook"), (4, 9, "proof"), (9, 12, "styling"), (12, 15, "cta"))
    timeline = {
        "timeline_id": f"timeline-{prefix}", "timeline_version": 1, "duration_seconds": 15,
        "beats": [_beat(prefix, start, end, purpose, style, color, i) for i, (start, end, purpose) in enumerate(grid)],
    }
    deltas = (
        ("pain_hook", "hem_release", "bedroom", ["cream", "wood"], "denim_bag", "fit", "hem_release"),
        ("occasion_hook", "doorway_turn", "entryway", ["white", "oak"], "trousers_flats", "sleeve", "half_turn"),
    )[style]
    variant_id = f"variant-{prefix}"
    evidence_hash = hashlib.sha256(f"{prefix}-three-view".encode("utf-8")).hexdigest()
    return {
        "variant_id": variant_id, "color_name": color,
        "garment_signature": {
            "category": "top", "silhouette": "relaxed", "neckline": "v-neck", "sleeve": "short",
            "length": "hip", "closure": "none", "seams_panels": "standard", "texture_scale": "fine",
            "thickness": "light", "opacity": "opaque", "drape": "soft",
        },
        "source_images": [f"{prefix}.png"], "three_view_path": f"{prefix}-three-view.png",
        "three_view_sha256": evidence_hash, "three_view_qc": "passed",
        "references": [{
            "reference_id": f"ref-{prefix}", "variant_id": variant_id, "role": "apparel_three_view",
            "local_path": f"{prefix}-three-view.png", "sha256": evidence_hash,
        }],
        "creative_delta": {
            "hook_angle_id": deltas[0], "opening_visual_id": deltas[1], "scene_id": deltas[2],
            "scene_palette": deltas[3], "styling_signature": deltas[4], "proof_focus_id": deltas[5],
            "signature_action_id": deltas[6], "hook_text_original": timeline["beats"][0]["spoken_line"],
            "color_terms": [color],
        },
        "canonical_timeline": timeline, "renderings": _compile_renderings_v1(timeline, color, variant_id),
        "caption": (
            f"{color} makes relaxed-fit weekend styling easy."
            if style == 0 else f"Use {color} to frame sleeve detail in a polished doorway look."
        ),
        "hashtags": ["#StyleIdeas", "#TryOn", "#Wardrobe", "#TikTokShop", "#OOTD"] if style == 0 else ["#SleeveDetail", "#TryOn", "#DailyLook", "#TikTokFinds", "#OutfitInspo"],
    }


def _fixture(style_b: int = 1) -> dict[str, Any]:
    return {
        "schema_version": "1.0", "normalization_id": "nfkc_casefold_ws_punct_v1",
        "compile_mode": "sku_batch_compile", "batch_compile_id": "self-test",
        "batch_revision": 1, "sku_family_id": "sku-self-test",
        "batch_key": {"model_preset": "美1", "market": "US", "voiceover_language": "en-US", "platform": "TikTok", "duration_seconds": 15, "aspect_ratio": "9:16", "resolution": "1080x1920"},
        "variants": [_variant("a", "藏蓝", 0), _variant("b", "酒红", style_b)],
    }


def _quality_variant(prefix: str, color: str, style: int) -> dict[str, Any]:
    variant = _variant(prefix, color, style)
    reference = variant["references"][0]
    reference.update({
        "interface_tag": "@Image1",
        "must_transfer": sorted(REQUIRED_TRANSFER),
        "must_not_transfer": sorted(LEGACY_REQUIRED_NON_TRANSFER),
        "positive_replacement": POSITIVE_REPLACEMENT,
    })
    creative = variant["creative_delta"]
    creative.update({
        "caption_angle_id": "weekend_formula" if style == 0 else "polished_detail",
        "difference_summary": "relaxed weekend fit proof" if style == 0 else "polished sleeve and turn proof",
    })
    beats = variant["canonical_timeline"]["beats"]
    stable_grid = ((0, 4), (4, 8), (8, 10), (10, 15))
    for beat, (start, end) in zip(beats, stable_grid):
        beat["start_seconds"] = start
        beat["end_seconds"] = end
    endpoints = (
        "garment settles fully visible in the opening frame",
        "the selected construction detail remains centered and unobstructed",
        "the complete outfit holds still for one readable moment",
        "the creator finishes one small down-left gesture with a clean screen",
    )
    for position, beat in enumerate(beats):
        beat.update({
            "camera_setup_id": f"setup-{prefix}-{(1, 2, 2, 3)[position]}",
            "camera_motivation": "keep the garment proof readable at phone-viewing size",
            "light_motivation": "real window light from camera left reveals true color and fabric folds",
            "visible_endpoint": endpoints[position],
            "speech_mode": "on_camera_dialogue" if position in {0, 3} else "offscreen_voiceover",
            "mouth_visibility": "visible" if position in {0, 3} else "not_visible",
            "lip_sync_required": position in {0, 3},
            "motion_budget": {
                "camera": "subtle" if position in {0, 2} else "locked",
                "performer": "micro" if position in {0, 1, 3} else "simple",
                "active_hands": "one",
            },
            "audio": (
                "clear dry dialogue over quiet room tone" if position in {0, 3}
                else "off-screen voiceover with one soft fabric rustle"
            ),
        })
    beats[2].update({
        "core_action": "hold completed outfit still",
        "left_hand_state": "rests naturally at side",
        "right_hand_state": "rests naturally at side",
        "motion_budget": {"camera": "subtle", "performer": "micro", "active_hands": "zero"},
    })
    beats[3]["spoken_line"] = "Love it? Tap the link in the lower left."
    creative["spoken_intent_ids"] = [beat["spoken_intent_id"] for beat in beats if beat["purpose"] != "cta"]
    creative["hook_text_original"] = beats[0]["spoken_line"]
    variant["quality_plan"] = {
        "directing_intent": (
            "make the relaxed drape feel trustworthy and easy to style"
            if style == 0 else "turn sleeve coverage concern into confidence through one clear proof"
        ),
        "directorial_voice": "observational_naturalist",
        "primary_fidelity_spend": "garment_identity",
        "secondary_fidelity_spend": "visible_proof" if style == 0 else "natural_motion",
        "economized_elements": ["crowds", "busy props", "rapid camera moves"],
        "hero_proof_id": creative["proof_focus_id"],
        "continuity_anchors": {
            "creator_identity": "fixed PopBoom model preset",
            "garment_identity": f"{variant['variant_id']} exact three-view garment",
            "outfit": creative["styling_signature"],
            "scene": creative["scene_id"],
            "lighting": "window light from camera left",
        },
    }
    variant["renderings"] = _compile_renderings_v2(variant, SELF_TEST_BATCH_KEY)
    return variant


def _fixture_v11(style_b: int = 1) -> dict[str, Any]:
    return {
        "schema_version": "1.1", "quality_contract_id": QUALITY_CONTRACT_ID_V11,
        "normalization_id": "nfkc_casefold_ws_punct_v1",
        "compile_mode": "sku_batch_compile", "batch_compile_id": "self-test-v11",
        "batch_revision": 1, "sku_family_id": "sku-self-test",
        "batch_key": copy.deepcopy(SELF_TEST_BATCH_KEY),
        "variants": [_quality_variant("qa", "藏蓝", 0), _quality_variant("qb", "酒红", style_b)],
    }


def _recompile_v11(document: dict[str, Any], variant_index: int) -> None:
    document["variants"][variant_index]["renderings"] = _compile_renderings_v2(
        document["variants"][variant_index], document.get("batch_key"),
    )


def _motion_hand_plan(active: str, left_action: str, right_action: str) -> dict[str, Any]:
    return {
        "active_hands": active,
        "left": {"start_anchor": "left relaxed side", "action": left_action, "end_anchor": "left relaxed side"},
        "right": {"start_anchor": "right relaxed side", "action": right_action, "end_anchor": "right relaxed side"},
    }


def _motion_variant(prefix: str, color: str, style: int) -> dict[str, Any]:
    variant = _quality_variant(prefix, color, style)
    reference_id = variant["references"][0]["reference_id"]
    hook_spoken = (
        "Tired of boxy oversized tees?"
        if style == 0 else "Need an easy outfit that still looks polished?"
    )
    if style == 0:
        specs = [
            ("neckline", "neckline", "detail_closeup", "point_trace", "one", "neckline edge stays centered and unobstructed"),
            ("sleeve", "sleeve", "detail_closeup", "touch_release", "one", "sleeve construction stays centered and unobstructed"),
            ("side drape", "drape", "side_back", "turn_settle", "body", "side drape settles fully visible"),
            ("outfit styling", "styling", "styling", "front_tuck", "one", "outfit styling result stays fully visible"),
        ]
    else:
        specs = [
            ("fabric texture", "texture", "detail_closeup", "pinch_release", "one", "fabric texture returns to one readable fold"),
            ("hem", "hem", "chest_to_hem", "smooth_release", "one", "hem edge settles centered and unobstructed"),
            ("side slit", "slit", "side_back", "turn_settle", "body", "side slit and back relation settle fully visible"),
            ("outfit styling", "styling", "styling", "style_adjust", "one", "outfit styling result stays fully visible"),
        ]
    intervals = ((0, 2.5), (2.5, 5), (5, 7.5), (7.5, 10.5), (10.5, 13), (13, 15))
    beats: list[dict[str, Any]] = []
    hook = {
        "beat_id": f"{prefix}-hook", "start_seconds": intervals[0][0], "end_seconds": intervals[0][1],
        "purpose": "hook", "framing": "relaxed three-quarter full-fit phone shot", "camera_motion": "locked phone frame",
        "camera_setup_id": f"{prefix}-setup-1", "camera_motivation": "show the garment immediately while the creator enters a relaxed pose",
        "scene": "bright bedroom" if style == 0 else "apartment doorway", "lighting": "window light from camera left",
        "light_motivation": "side-front light reveals true color and folds", "actor": "one creator wearing the exact reference garment",
        "core_action": "the creator shifts weight once and smooths the lower front with the right hand, then relaxes",
        "action_target": "full garment fit", "left_hand_state": "left hand rests loosely at the left side",
        "right_hand_state": "right hand smooths once and settles at the right side",
        "spoken_line": hook_spoken, "silence_reason": None,
        "spoken_intent_id": f"{prefix}-hook-intent", "product_point": "full garment fit and relaxed silhouette",
        "styling_point": variant["creative_delta"]["styling_signature"], "product_visibility": "full",
        "reference_binding": reference_id, "micro_expression": "small natural smile and one blink", "audio": "clear dialogue over room tone",
        "visible_endpoint": "the creator finishes in a relaxed three-quarter stance with the full garment readable",
        "lip_sync_required": True, "speech_mode": "on_camera_dialogue", "mouth_visibility": "visible",
        "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "one"},
        "beat_role": "context_hook", "claim_proof_id": None, "proof_target": None,
        "proof_action_type": None, "product_part_id": None, "evidence_basis": None, "spoken_claim_ids": [], "hook_semantics": "pain_question", "posture_id": "weight_shift_left",
        "hand_plan": _motion_hand_plan("right", "stays anchored", "smooths lower front once"),
    }
    beats.append(hook)
    claim_plan: list[dict[str, Any]] = []
    for index, (target, product_part_id, framing_class, action_type, hands_required, endpoint) in enumerate(specs):
        beat_id = f"{prefix}-proof-{index + 1}"
        claim_id = f"claim-{target.replace(' ', '-') }"
        proof_id = f"{prefix}-cp-{index + 1}"
        start, end = intervals[index + 1]
        body_action = hands_required == "body"
        action_text = {
            "point_trace": f"the right index finger traces the {target} once and moves clear",
            "touch_release": f"the right fingertips touch the {target} once and release",
            "pinch_release": f"the right fingertips pinch and release one small area of {target}",
            "smooth_release": f"the right hand smooths the {target} once and releases",
            "turn_settle": f"the creator makes one slow quarter-turn until the {target} settles",
            "front_tuck": f"the right hand completes one small front tuck to reveal {target}",
            "style_adjust": f"the right hand adjusts one accessory once to reveal {target}",
        }[action_type]
        framing = f"{target} close-up" if framing_class == "detail_closeup" else f"{target} {framing_class.replace('_', '-')} shot"
        hand_plan = (
            _motion_hand_plan("none", "stays anchored", "stays anchored")
            if body_action else _motion_hand_plan("right", "stays anchored", action_text)
        )
        motion_budget = {
            "camera": "locked", "performer": "simple" if body_action else "micro",
            "active_hands": "zero" if body_action else "one",
        }
        beat = {
            "beat_id": beat_id, "start_seconds": start, "end_seconds": end,
            "purpose": "styling" if framing_class == "styling" else "proof", "framing": framing,
            "camera_motion": "locked phone frame", "camera_setup_id": f"{prefix}-setup-{min(index + 2, 5)}",
            "camera_motivation": f"center the {target} at phone-viewing size", "scene": hook["scene"],
            "lighting": hook["lighting"], "light_motivation": f"side light makes the {target} readable",
            "actor": "the same creator wearing the unchanged reference garment, mouth out of frame",
            "core_action": action_text, "action_target": target,
            "left_hand_state": "left hand stays relaxed at the left side",
            "right_hand_state": "right hand performs the planned action and settles at the right side" if not body_action else "right hand stays relaxed at the right side",
            "spoken_line": {
                "neckline": "Look how the neckline sits.",
                "sleeve": "The sleeve ends right around the upper arm.",
                "drape": "Watch the side drape settle after the turn.",
                "styling": "For outfit styling, add high-rise jeans.",
                "texture": "Up close, the fabric texture shows its fine surface.",
                "hem": "The hem lands neatly over high-rise jeans.",
                "slit": "See the side slit open as she turns.",
            }[product_part_id], "silence_reason": None,
            "spoken_intent_id": f"{prefix}-intent-{index + 1}", "product_point": target,
            "styling_point": variant["creative_delta"]["styling_signature"] if framing_class == "styling" else None,
            "product_visibility": "detail" if framing_class == "detail_closeup" else "full",
            "reference_binding": reference_id, "micro_expression": None,
            "audio": "off-screen voiceover with one soft fabric sound", "visible_endpoint": endpoint,
            "lip_sync_required": False, "speech_mode": "offscreen_voiceover", "mouth_visibility": "not_visible",
            "motion_budget": motion_budget, "beat_role": "styling" if framing_class == "styling" else "product_claim",
            "claim_proof_id": proof_id, "proof_target": target, "proof_action_type": action_type,
            "product_part_id": product_part_id, "evidence_basis": "visible_reference", "spoken_claim_ids": [claim_id],
            "hook_semantics": None, "posture_id": "side_relaxed" if body_action else "hands_only_detail",
            "hand_plan": hand_plan,
        }
        beats.append(beat)
        claim_plan.append({
            "claim_proof_id": proof_id, "claim_id": claim_id, "spoken_intent_id": beat["spoken_intent_id"],
            "beat_id": beat_id, "proof_target": target, "evidence_basis": "visible_reference",
            "product_part_id": product_part_id, "framing_class": framing_class, "action_type": action_type, "hands_required": hands_required,
            "expected_visible_change": endpoint,
        })
    cta = {
        "beat_id": f"{prefix}-cta", "start_seconds": intervals[5][0], "end_seconds": intervals[5][1],
        "purpose": "cta", "framing": "relaxed three-quarter full-fit CTA shot", "camera_motion": "locked phone frame",
        "camera_setup_id": f"{prefix}-setup-5", "camera_motivation": "keep the completed outfit readable during the purchase cue",
        "scene": hook["scene"], "lighting": hook["lighting"], "light_motivation": "consistent window light preserves garment color",
        "actor": "the same creator in the unchanged outfit", "core_action": "the right hand makes one small down-left gesture and returns",
        "action_target": "human-only purchase cue", "left_hand_state": "left hand rests loosely at the left side",
        "right_hand_state": "right hand gestures once and settles at the right side",
        "spoken_line": "Love it? Tap the link in the lower left.", "silence_reason": None,
        "spoken_intent_id": "cta", "product_point": "full garment remains visible", "styling_point": variant["creative_delta"]["styling_signature"],
        "product_visibility": "full", "reference_binding": reference_id, "micro_expression": "friendly contained smile",
        "audio": "exact dialogue over quiet room tone", "visible_endpoint": "the CTA gesture finishes and both hands return to clear anchors",
        "lip_sync_required": True, "speech_mode": "on_camera_dialogue", "mouth_visibility": "visible",
        "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "one"},
        "beat_role": "cta", "claim_proof_id": None, "proof_target": None, "proof_action_type": None,
        "product_part_id": None, "evidence_basis": None, "spoken_claim_ids": [], "hook_semantics": None, "posture_id": "relaxed_three_quarter",
        "hand_plan": _motion_hand_plan("right", "stays anchored", "makes one down-left gesture"),
    }
    beats.append(cta)
    variant["canonical_timeline"] = {
        "timeline_id": f"timeline-{prefix}-v12", "timeline_version": 2, "duration_seconds": 15, "beats": beats,
    }
    variant["quality_plan"]["claim_proof_plan"] = claim_plan
    variant["creative_delta"]["spoken_intent_ids"] = [hook["spoken_intent_id"]] + [item["spoken_intent_id"] for item in claim_plan]
    variant["creative_delta"]["hook_text_original"] = hook["spoken_line"]
    variant["renderings"] = _compile_renderings_v3(variant, SELF_TEST_BATCH_KEY)
    return variant


def _fixture_v12() -> dict[str, Any]:
    feature_ids = ["neckline", "sleeve", "side drape", "outfit styling", "fabric texture", "hem", "side slit"]
    part_ids = {
        "neckline": "neckline", "sleeve": "sleeve", "side drape": "drape",
        "outfit styling": "styling", "fabric texture": "texture", "hem": "hem", "side slit": "slit",
    }
    return {
        "schema_version": "1.2", "quality_contract_id": QUALITY_CONTRACT_ID_V12,
        "normalization_id": "nfkc_casefold_ws_punct_v1", "compile_mode": "sku_batch_compile",
        "batch_compile_id": "self-test-v12", "batch_revision": 1, "sku_family_id": "sku-self-test",
        "batch_key": copy.deepcopy(SELF_TEST_BATCH_KEY),
        "shared_core": {
            "claims_allowlist": [f"claim-{feature.replace(' ', '-')}" for feature in feature_ids],
            "claims_registry": [
                {
                    "claim_id": f"claim-{feature.replace(' ', '-')}", "feature_id": feature,
                    "product_part_id": part_ids[feature],
                    "evidence_basis": "visible_reference", "evidence_ref": "apparel three-view",
                    "assertion_level": "visible", "spoken_claim_terms": [feature],
                }
                for feature in feature_ids
            ],
        },
        "variants": [_motion_variant("ma", "藏蓝", 0), _motion_variant("mb", "酒红", 1)],
    }


def _recompile_v12(document: dict[str, Any], variant_index: int) -> None:
    document["variants"][variant_index]["renderings"] = _compile_renderings_v3(
        document["variants"][variant_index], document.get("batch_key"),
    )


def _fixture_v13() -> dict[str, Any]:
    document = _fixture_v12()
    document["schema_version"] = "1.3"
    document["batch_compile_id"] = "self-test-v13"
    for variant in document["variants"]:
        for reference_index, reference in enumerate(variant["references"], start=2):
            reference["interface_tag"] = f"@Image{reference_index}"
            reference["human_identity_pixels_absent"] = True
            reference["must_not_transfer"] = sorted(REQUIRED_NON_TRANSFER)
    source_hash = hashlib.sha256(b"self-test-visible-reference-source").hexdigest()
    registry = document["shared_core"]["claims_registry"]
    evidence_items: list[dict[str, Any]] = []
    for claim in registry:
        evidence_id = "ev-" + str(claim["claim_id"])
        claim.update({
            "assertion_kind": "visible_feature",
            "claim_mode": "visual_only",
            "evidence_ids": [evidence_id],
        })
        evidence_items.append({
            "evidence_id": evidence_id,
            "source_id": "src-visible-reference",
            "assertion_kind": "visible_feature",
            "statement": f"The {claim['feature_id']} is visibly present in the apparel three-view.",
            "exact_product_match": True,
            "exact_variant_match": True,
            "conflict_status": "clear",
            "permitted_uses": ["visual_only"],
            "performance_demo_allowed": False,
        })
    document["shared_core"]["research_bundle"] = {
        "contract_id": RESEARCH_CONTRACT_ID,
        "requested_url": None,
        "canonical_url": None,
        "marketplace": None,
        "parent_product_id": None,
        "child_product_id": None,
        "selected_variant": {"color": None, "size": None},
        "captured_at": None,
        "collection_route": "not_applicable",
        "status": "not_provided",
        "limitations": ["No marketplace URL was supplied; only visible-reference evidence is used."],
        "evidence_sources": [{
            "source_id": "src-visible-reference",
            "source_kind": "visible_reference",
            "source_role": "visual_observation",
            "url": "local://self-test/apparel-three-view",
            "product_id": None,
            "child_product_id": None,
            "variant_scope": "exact_child",
            "locator": "combined apparel three-view reference",
            "captured_at": "2026-07-13T00:00:00Z",
            "content_sha256": source_hash,
            "review_id": None,
            "parent_review_id": None,
            "rating": None,
            "review_date": None,
            "review_date_status": None,
            "verified_purchase": None,
        }],
        "evidence_items": evidence_items,
        "review_analysis": {
            "status": "not_provided",
            "sampled_review_ids": [],
            "buyer_image_count": 0,
            "themes": [],
            "conflicts": [],
            "excluded_sources": [],
        },
    }
    document["shared_core"]["pain_solution_map"] = [
        {
            "pain_point_id": "pain-boxy-fit",
            "pain_statement": "The buyer worries an oversized top will look boxy.",
            "basis": "category_inference",
            "evidence_ids": [],
            "review_theme_id": None,
            "status": "script_eligible",
            "selected_for_script": True,
            "solution_claim_ids": ["claim-neckline"],
            "target_language_terms": ["boxy"],
        },
        {
            "pain_point_id": "pain-polished-outfit",
            "pain_statement": "The buyer needs an easy top that still looks polished.",
            "basis": "category_inference",
            "evidence_ids": [],
            "review_theme_id": None,
            "status": "script_eligible",
            "selected_for_script": True,
            "solution_claim_ids": ["claim-fabric-texture"],
            "target_language_terms": ["polished"],
        },
    ]
    claim_map = {str(claim["claim_id"]): claim for claim in registry}
    for index, variant in enumerate(document["variants"]):
        pain_id = "pain-boxy-fit" if index == 0 else "pain-polished-outfit"
        variant["creative_delta"]["pain_focus_id"] = pain_id
        for proof in variant["quality_plan"]["claim_proof_plan"]:
            proof["evidence_ids"] = copy.deepcopy(claim_map[str(proof["claim_id"])]["evidence_ids"])
        for beat in variant["canonical_timeline"]["beats"]:
            if beat.get("beat_role") == "context_hook":
                beat["pain_point_id"] = pain_id
                beat["evidence_ids"] = []
            elif beat.get("claim_proof_id") is not None:
                proof = next(
                    item for item in variant["quality_plan"]["claim_proof_plan"]
                    if item["claim_proof_id"] == beat["claim_proof_id"]
                )
                beat["pain_point_id"] = None
                beat["evidence_ids"] = copy.deepcopy(proof["evidence_ids"])
            else:
                beat["pain_point_id"] = None
                beat["evidence_ids"] = []
        variant["caption_claim_ids"] = [
            str(variant["quality_plan"]["claim_proof_plan"][0]["claim_id"]),
        ]
        variant["caption_pain_point_ids"] = [pain_id]
        if index == 0:
            variant["caption"] = "The neckline answers a boxy-fit concern in an easy weekend look."
            claim_anchor = "neckline"
            pain_anchor = "boxy"
        else:
            variant["caption"] = "The fabric texture keeps this polished doorway look easy."
            claim_anchor = "fabric texture"
            pain_anchor = "polished"
        variant["caption_claim_bindings"] = [{
            "claim_id": variant["caption_claim_ids"][0],
            "text_anchor": claim_anchor,
        }]
        variant["caption_pain_bindings"] = [{
            "pain_point_id": pain_id,
            "text_anchor": pain_anchor,
        }]
        variant["renderings"] = _compile_renderings_v5(
            variant, document.get("batch_key"), document["shared_core"]["research_bundle"],
        )
    return document


def _recompile_v13(document: dict[str, Any], variant_index: int) -> None:
    shared_core = document.get("shared_core") if isinstance(document.get("shared_core"), dict) else {}
    document["variants"][variant_index]["renderings"] = _compile_renderings_v5(
        document["variants"][variant_index], document.get("batch_key"), shared_core.get("research_bundle"),
    )


def _fixture_three_layer_deadlines(
    variant: dict[str, Any], batch_key: dict[str, Any],
) -> dict[str, Any]:
    """Create concrete three-layer constraints for integration fixtures."""
    controls = variant["generation_controls"]
    anchors = variant["quality_plan"]["continuity_anchors"]
    screen_policy = controls["screen_text_policy"]
    fit_stats_text = _market_contract.fit_stats_text_for_batch(batch_key)
    global_deadlines = {
        "platform": batch_key["platform"],
        "aspect_ratio": batch_key["aspect_ratio"],
        "duration_seconds": batch_key["duration_seconds"],
        "resolution": batch_key["resolution"],
        "frame_rate_fps": batch_key["frame_rate_fps"],
        "camera_mode": controls["camera_mode"],
        "screen_text_policy": screen_policy,
        "allowed_screen_text": (
            [fit_stats_text] if screen_policy == "fixed_model_stats_only" and fit_stats_text else []
        ),
        "music_mode": controls["music_mode"],
        "spoken_language": batch_key["voiceover_language"],
        "capture_quality_lock": (
            "ordinary smartphone-native UGC with mild sensor noise, limited dynamic range, normal sharpening, and natural motion blur"
        ),
        "camera_behavior_lock": (
            "the declared phone operator and support remain physically consistent inside each cut, with only motivated natural micro-movement"
        ),
        "lens_depth_lock": (
            "natural standard-lens perspective and full readable depth keep the creator, garment, props, and declared environment clear"
        ),
        "postprocessing_lock": (
            "no beauty filter, skin smoothing, body reshaping, slimming, plastic skin, cinematic grade, or artificial background blur"
        ),
        "audio_capture_lock": (
            "phone-microphone location sound with natural room or street reflection, breaths, cloth contact, footsteps, and no detached studio voice"
        ),
        "forbidden_overlays": sorted(_market_contract.REQUIRED_FORBIDDEN_OVERLAYS),
    }
    asset_deadlines = {
        "creator_identity": anchors["creator_identity"],
        "subject_appearance_lock": (
            "preserve the same adult creator, natural body proportions, visible skin texture, hair, age presentation, and unretouched consumer appearance"
        ),
        "garment_identity": anchors["garment_identity"],
        "color_name": variant["color_name"],
        "garment_signature": copy.deepcopy(variant["garment_signature"]),
        "garment_structure_lock": (
            "preserve every declared neckline, sleeve, seam, panel, closure, hem, length, and silhouette through front, side, back, and motion views"
        ),
        "fabric_physics_lock": (
            "fabric remains matte and gravity-driven with its declared weight, opacity, texture scale, softness, folds, and restrained natural settling"
        ),
        "outfit": anchors["outfit"],
        "outfit_prop_lock": (
            "keep all declared base layers, bottoms, shoes, accessories, and handled props separate, correctly worn, and physically continuous"
        ),
        "scene_strategy": copy.deepcopy(controls["scene_strategy"]),
        "scene_environment_lock": (
            "preserve the declared real-use environment, practical lived-in details, camera geography, and every intentional scene transition"
        ),
        "lighting": anchors["lighting"],
        "lighting_lock": (
            "preserve one motivated daylight direction, color temperature, exposure logic, and stable shadow direction across every cut"
        ),
        "soundscape_lock": (
            "preserve the declared location ambience, natural cloth sounds, footsteps, breaths, and distance-based voice level without unrelated speakers"
        ),
        "forbidden_asset_drift": sorted(_market_contract.REQUIRED_FORBIDDEN_ASSET_DRIFT),
    }
    shot_deadlines = []
    for index, beat in enumerate(variant["canonical_timeline"]["beats"]):
        target = str(beat.get("action_target") or "declared target")
        endpoint = beat.get("proof_endpoint") or beat.get("visible_endpoint")
        hand_plan = beat.get("hand_plan") if isinstance(beat.get("hand_plan"), dict) else {}
        left = hand_plan.get("left") if isinstance(hand_plan.get("left"), dict) else {}
        right = hand_plan.get("right") if isinstance(hand_plan.get("right"), dict) else {}
        shot_deadlines.append({
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
            "readable_endpoint": endpoint,
            "hand_plan": copy.deepcopy(beat.get("hand_plan")),
            "action_physics_lock": (
                f"shot {index + 1} performs only the declared contact and movement on {target}, then reaches the exact readable endpoint under gravity"
            ),
            "anatomy_lock": (
                f"shot {index + 1} keeps exactly two connected arms and hands with correct fingers, joints, body proportions, and declared hand anchors"
            ),
            "garment_state_lock": (
                f"shot {index + 1} keeps color, construction, coverage, side seams, and fabric behavior unchanged while only {target} responds"
            ),
            "continuity_lock": (
                f"shot {index + 1} ends with left hand {left.get('end_anchor')} and right hand {right.get('end_anchor')}, preserving props, gaze, weight, and garment state"
            ),
            "forbidden_outcomes": sorted(_market_contract.REQUIRED_FORBIDDEN_SHOT_OUTCOMES),
        })
    return {
        "contract_id": _market_contract.DEADLINE_CONTRACT_ID,
        "global_deadlines": global_deadlines,
        "asset_deadlines": asset_deadlines,
        "shot_deadlines": shot_deadlines,
    }


def _refresh_fixture_three_layer_deadlines(document: dict[str, Any], variant_index: int = 0) -> None:
    variant = document["variants"][variant_index]
    variant["three_layer_deadlines"] = _fixture_three_layer_deadlines(variant, document["batch_key"])


def _fixture_v14(
    *,
    market: str = "US",
    prompt_shell_mode: str | None = None,
    delivery_mode: str | None = None,
    camera_mode: str = "fixed_phone",
    music_mode: str = "none",
    verdict_mode: str = "soft_verdict",
    commerce_cta_mode: str = "light_link",
) -> dict[str, Any]:
    """Build one complete schema 1.4 integration fixture for tests."""
    document = _fixture_v13()
    document["schema_version"] = "1.4"
    document["quality_contract_id"] = QUALITY_CONTRACT_ID_V14
    document["market_prompt_contract_id"] = _market_contract.MARKET_PROMPT_CONTRACT_ID
    document["compile_mode"] = "single_compile"
    document["batch_compile_id"] = "self-test-v14-" + market.casefold()
    document["variants"] = [document["variants"][0]]
    variant = document["variants"][0]
    for reference in variant["references"]:
        audit = _zero_human_identity_audit(reference["sha256"])
        reference["identity_cue_audit"] = audit
        reference["identity_cue_audit_sha256"] = _canonical_sha256(audit)
    is_de = market == "DE"
    document["batch_key"].update({
        "market": market,
        "model_preset": "德1" if is_de else "美1",
        "voiceover_language": "de-DE" if is_de else "en-US",
        "market_prompt_profile_id": "de_champion_v1" if is_de else "us_champion_v1",
        "resolution": "720p",
        "frame_rate_fps": 60,
    })
    scene_strategy = {
        "wear_context": "outward_wear",
        "scene_role": "occasion_outfit_solution",
        "primary_scene_id": "cafe_social" if is_de else "commercial_street",
        "primary_scene_description": (
            "a calm neighborhood cafe terrace beside a clean pedestrian street, with no landmarks or branded signs"
            if is_de else
            "a lively but uncluttered neighborhood commercial street with cafe fronts and a shaded sidewalk"
        ),
        "occasion": (
            "an everyday cafe stop and relaxed city errand"
            if is_de else "weekend shopping followed by a casual lunch"
        ),
        "buyer_styling_question": (
            "What can I wear for a cafe stop and city errands that looks put together without feeling formal?"
            if is_de else "What can I wear for shopping and lunch that feels polished without looking overdressed?"
        ),
        "outfit_answer": (
            "high-waist straight jeans, clean low-profile sneakers, a small crossbody bag, and simple earrings"
            if is_de else "high-rise straight jeans, clean white sneakers, a small crossbody bag, and simple hoop earrings"
        ),
        "video_form": "destination_outfit_share",
        "location_plan": "single_primary_scene",
        "bedroom_policy": "excluded",
    }
    controls = {
        "prompt_shell_mode": prompt_shell_mode or ("de_performance_script" if is_de else "us_technical_shell"),
        "delivery_mode": delivery_mode or ("de_hybrid_proof" if is_de else "us_hybrid_share"),
        "camera_mode": camera_mode,
        "music_mode": music_mode,
        "verdict_mode": verdict_mode,
        "commerce_cta_mode": commerce_cta_mode,
        "screen_text_policy": "fixed_model_stats_only",
        "scene_strategy": scene_strategy,
    }
    variant["generation_controls"] = controls
    variant["creative_delta"]["scene_id"] = scene_strategy["primary_scene_id"]
    variant["creative_delta"]["scene_palette"] = (
        ["soft gray", "muted wood", "clean greenery"]
        if is_de else ["neutral storefront", "shaded pavement", "warm cafe fronts"]
    )
    variant["creative_delta"]["styling_signature"] = scene_strategy["outfit_answer"]
    variant["quality_plan"]["continuity_anchors"]["scene"] = scene_strategy["primary_scene_id"]
    variant["quality_plan"]["continuity_anchors"]["outfit"] = scene_strategy["outfit_answer"]
    beats = variant["canonical_timeline"]["beats"]
    setup_ids = ["cut-a", "cut-a", "cut-b", "cut-b", "cut-c", "cut-c"]
    for beat, setup_id in zip(beats, setup_ids):
        beat["camera_setup_id"] = setup_id
        beat["scene_id"] = scene_strategy["primary_scene_id"]
        beat["scene"] = scene_strategy["primary_scene_description"]
        beat["lighting"] = (
            "soft overcast daylight from camera left under the cafe awning"
            if is_de else "shaded late-morning daylight from camera left with soft storefront reflections"
        )
        beat["light_motivation"] = "soft side-front daylight preserves garment color and readable fabric folds"
        beat["audio"] = str(beat.get("audio") or "").replace(
            "room tone", "quiet cafe-terrace ambience" if is_de else "light pedestrian-street ambience",
        )
        if beat.get("styling_point") is not None:
            beat["styling_point"] = scene_strategy["outfit_answer"]
        beat["camera_motion"] = (
            "subtle handheld phone framing" if camera_mode in {"creator_handheld", "friend_handheld"}
            else "locked phone framing"
        )
        beat["spoken_language"] = document["batch_key"]["voiceover_language"] if _nonempty(beat.get("spoken_line")) else None
        if beat.get("claim_proof_id") is None:
            beat["proof_endpoint"] = None
        else:
            proof = next(
                item for item in variant["quality_plan"]["claim_proof_plan"]
                if item["claim_proof_id"] == beat["claim_proof_id"]
            )
            beat["proof_endpoint"] = proof["expected_visible_change"]

    if is_de:
        pain = document["shared_core"]["pain_solution_map"][0]
        pain["target_language_terms"] = ["kastenförmig"]
        beats[0]["spoken_line"] = "Wirkt ein Oversize-Top schnell kastenförmig?"
        claim_terms = {
            "claim-neckline": ["Ausschnitt"],
            "claim-sleeve": ["Ärmel"],
            "claim-side-drape": ["Seite"],
            "claim-outfit-styling": ["High-Waist-Jeans"],
        }
        spoken_lines = (
            "Schau mal, wie schön offen der Ausschnitt sitzt.",
            "Der Ärmel endet locker am Oberarm.",
            "Beim Drehen fällt die Seite ruhig zurück.",
            "Mit High-Waist-Jeans bleibt der Saum sichtbar.",
        )
        for registry_claim in document["shared_core"]["claims_registry"]:
            if registry_claim.get("claim_id") in claim_terms:
                registry_claim["spoken_claim_terms"] = claim_terms[registry_claim["claim_id"]]
        for beat, line in zip(beats[1:5], spoken_lines):
            beat["spoken_line"] = line
        if commerce_cta_mode == "light_link":
            beats[-1]["spoken_line"] = "Ich mag den Look. Den Link findest du unten links."
        elif verdict_mode == "ownership_verdict":
            beats[-1]["spoken_line"] = "Das behalte ich definitiv."
        else:
            beats[-1]["spoken_line"] = "Der Look überzeugt mich."
        variant["caption"] = "Der Ausschnitt löst die Sorge vor einem kastenförmigen Oversize-Look."
        variant["caption_claim_bindings"][0]["text_anchor"] = "Ausschnitt"
        variant["caption_pain_bindings"][0]["text_anchor"] = "kastenförmig"
    elif commerce_cta_mode == "none":
        beats[-1]["spoken_line"] = (
            "Honestly, this one definitely stays in my weekly rotation."
            if verdict_mode == "ownership_verdict" else "Honestly, this is the easy look I wanted."
        )

    if controls["delivery_mode"] == "de_live_simple":
        variant["quality_plan"]["secondary_fidelity_spend"] = "lip_sync"
        third_claim = next(
            claim for claim in document["shared_core"]["claims_registry"]
            if claim.get("claim_id") == "claim-side-drape"
        )
        third_claim.update({"feature_id": "hem", "product_part_id": "hem", "spoken_claim_terms": ["Saum"]})
        proof_specs = (
            (0, "point_trace", "one", "neckline", "neckline", "detail_closeup", "neckline edge stays centered and unobstructed"),
            (1, "touch_release", "one", "sleeve", "sleeve", "detail_closeup", "sleeve construction stays centered and unobstructed"),
            (2, "point_trace", "one", "hem", "hem", "chest_to_hem", "hem edge stays centered and unobstructed"),
            (3, "style_adjust", "one", "outfit styling", "styling", "styling", "outfit styling result stays fully visible"),
        )
        live_lines = (
            "Der Ausschnitt sitzt hier offen.",
            "Der Ärmel endet locker.",
            "Der Saum bleibt klar sichtbar.",
            "High-Waist-Jeans machen den Look komplett.",
        )
        for proof_index, action_type, hands, target, part, framing_class, endpoint in proof_specs:
            proof = variant["quality_plan"]["claim_proof_plan"][proof_index]
            beat = beats[proof_index + 1]
            proof.update({
                "proof_target": target, "product_part_id": part, "framing_class": framing_class,
                "action_type": action_type, "hands_required": hands, "expected_visible_change": endpoint,
            })
            action_text = {
                "point_trace": f"the right index finger points once to the {target} and moves clear",
                "touch_release": f"the right fingertips touch the {target} once and release",
                "style_adjust": f"the right hand adjusts one accessory once to reveal {target}",
            }[action_type]
            beat.update({
                "framing": f"{target} close-up" if framing_class == "detail_closeup" else f"{target} {framing_class.replace('_', '-')} shot",
                "actor": "the same creator wearing the unchanged reference garment with face visible",
                "core_action": action_text, "action_target": target, "product_point": target,
                "visible_endpoint": endpoint, "proof_endpoint": endpoint, "proof_target": target,
                "proof_action_type": action_type, "product_part_id": part,
                "spoken_line": live_lines[proof_index], "speech_mode": "on_camera_dialogue",
                "mouth_visibility": "visible", "lip_sync_required": True,
                "micro_expression": f"meaning-matched live German expression {proof_index + 1}",
                "audio": "exact live German dialogue over quiet room tone",
                "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "one"},
                "hand_plan": _motion_hand_plan("right", "stays anchored", action_text),
                "right_hand_state": "right hand performs the planned action and settles at the right side",
                "posture_id": "hands_only_detail" if framing_class != "styling" else "relaxed_three_quarter",
            })
        beats[0]["micro_expression"] = "warm German question with a small eyebrow lift"
        beats[-1]["micro_expression"] = "pleased German ownership smile"

    fixture_translations = {
        "Tired of boxy oversized tees?": "受够了显方的宽松T恤吗？",
        "Look how the neckline sits.": "看看这个领口的贴合位置。",
        "The sleeve ends right around the upper arm.": "袖子正好落在上臂附近。",
        "Watch the side drape settle after the turn.": "看转身后侧面的垂坠如何自然回落。",
        "For outfit styling, add high-rise jeans.": "搭配时加一条高腰牛仔裤。",
        "Love it? Tap the link in the lower left.": "喜欢吗？点击左下角的链接。",
        "Honestly, this one definitely stays in my weekly rotation.": "说真的，这件肯定会成为我每周常穿的单品。",
        "Honestly, this is the easy look I wanted.": "说真的，这正是我想要的轻松造型。",
        "Wirkt ein Oversize-Top schnell kastenförmig?": "宽松上衣是不是很容易显得方方正正？",
        "Schau mal, wie schön offen der Ausschnitt sitzt.": "看，领口敞开的程度刚刚好。",
        "Der Ärmel endet locker am Oberarm.": "袖子宽松地落在上臂处。",
        "Beim Drehen fällt die Seite ruhig zurück.": "转身时，侧面的垂坠会自然回落。",
        "Mit High-Waist-Jeans bleibt der Saum sichtbar.": "搭配高腰牛仔裤时，下摆依然清晰可见。",
        "Ich mag den Look. Den Link findest du unten links.": "我很喜欢这套造型，链接就在左下角。",
        "Das behalte ich definitiv.": "这件我肯定会留下。",
        "Der Look überzeugt mich.": "这套造型让我很满意。",
        "Der Ausschnitt sitzt hier offen.": "这里的领口敞开得很自然。",
        "Der Ärmel endet locker.": "袖子落得很宽松。",
        "Der Saum bleibt klar sichtbar.": "下摆始终清晰可见。",
        "High-Waist-Jeans machen den Look komplett.": "高腰牛仔裤让整套造型更完整。",
    }
    translations = {
        beat.get("beat_id"): fixture_translations[str(beat.get("spoken_line"))]
        for beat in beats if _nonempty(beat.get("spoken_line"))
    }
    variant["voiceover_review"] = [
        {
            "beat_id": beat.get("beat_id"),
            "speech_mode": beat.get("speech_mode"),
            "spoken_language": beat.get("spoken_language"),
            "market_line": beat.get("spoken_line"),
            "zh_cn_translation": translations.get(beat.get("beat_id")),
            "silence_reason": beat.get("silence_reason"),
        }
        for beat in beats
    ]
    variant["creative_delta"]["hook_text_original"] = beats[0]["spoken_line"]
    variant["canonical_timeline"]["timeline_id"] = "timeline-v14-" + market.casefold()
    variant["canonical_timeline"]["timeline_version"] = 1
    variant["three_layer_deadlines"] = _fixture_three_layer_deadlines(variant, document["batch_key"])
    variant["renderings"] = _compile_renderings_v7(
        variant, document.get("batch_key"), document["shared_core"].get("research_bundle"),
    )
    return document


def _fixture_v14_legacy_v6(**kwargs: Any) -> dict[str, Any]:
    """Build a historical schema 1.4/v6 document without three-layer deadlines."""
    document = _fixture_v14(**kwargs)
    document["quality_contract_id"] = QUALITY_CONTRACT_ID_V14_LEGACY
    for variant in document["variants"]:
        variant.pop("three_layer_deadlines", None)
        variant["renderings"] = _compile_renderings_v6(
            variant, document.get("batch_key"), document["shared_core"].get("research_bundle"),
        )
    return document


def _fixture_v13_legacy_v4() -> dict[str, Any]:
    """Build a valid historical schema 1.3/v4 object for read-only compatibility tests."""
    document = _fixture_v13()
    for variant in document["variants"]:
        for reference_index, reference in enumerate(variant["references"], start=1):
            reference["interface_tag"] = f"@Image{reference_index}"
            reference.pop("human_identity_pixels_absent", None)
            reference["must_not_transfer"] = sorted(LEGACY_REQUIRED_NON_TRANSFER)
        variant["renderings"] = _compile_renderings_v4(
            variant, document.get("batch_key"), document["shared_core"]["research_bundle"],
        )
    return document


def _fixture_v13_review_sample() -> dict[str, Any]:
    document = _fixture_v13()
    bundle = document["shared_core"]["research_bundle"]
    bundle.update({
        "requested_url": "https://www.amazon.com/dp/B0TEST1234?th=1&psc=1",
        "canonical_url": "https://www.amazon.com/dp/B0TEST1234?th=1&psc=1",
        "marketplace": "amazon.com",
        "parent_product_id": "B0PARENT00",
        "child_product_id": "B0TEST1234",
        "selected_variant": {"color": "Beige", "size": None},
        "captured_at": "2026-07-13T01:00:00Z",
        "collection_route": "public_web",
        "status": "partial",
        "limitations": ["Reviews are variation-family samples; they are not exact-child tests."],
    })
    review_evidence_ids: list[str] = []
    for index in range(1, 4):
        review_id = f"RTESTREVIEW{index}"
        source_id = f"src-review-{index}"
        evidence_id = f"ev-review-{index}"
        bundle["evidence_sources"].append({
            "source_id": source_id,
            "source_kind": "amazon_customer_review",
            "source_role": "buyer_experience",
            "url": f"https://www.amazon.com/portal/customer-reviews/srp/-/{review_id}",
            "product_id": "B0PARENT00",
            "child_product_id": f"B0SIBLING{index}",
            "variant_scope": "sibling_child",
            "locator": f"embedded review card {review_id}",
            "captured_at": "2026-07-13T01:00:00Z",
            "content_sha256": hashlib.sha256(review_id.encode("utf-8")).hexdigest(),
            "review_id": review_id,
            "parent_review_id": None,
            "rating": 3,
            "review_date": "2026-01-01",
            "review_date_status": "captured",
            "verified_purchase": True,
        })
        bundle["evidence_items"].append({
            "evidence_id": evidence_id,
            "source_id": source_id,
            "assertion_kind": "buyer_experience",
            "statement": "The material differed from this buyer's T-shirt expectation.",
            "exact_product_match": True,
            "exact_variant_match": False,
            "conflict_status": "clear",
            "permitted_uses": ["pain_context"],
            "performance_demo_allowed": False,
        })
        review_evidence_ids.append(evidence_id)
    bundle["review_analysis"] = {
        "status": "partial",
        "sampled_review_ids": [f"RTESTREVIEW{index}" for index in range(1, 4)],
        "buyer_image_count": 0,
        "themes": [{
            "theme_id": "theme-material-expectation",
            "normalized_theme": "material differs from expected ordinary T-shirt handfeel",
            "sentiment": "complaint",
            "review_evidence_ids": review_evidence_ids,
            "mention_count": 3,
            "claim_strength": "sample_theme",
            "permitted_script_use": "pain_context",
        }],
        "conflicts": [],
        "excluded_sources": [],
    }
    document["shared_core"]["pain_solution_map"].append({
        "pain_point_id": "pain-material-expectation",
        "pain_statement": "Some sampled buyers expected an ordinary T-shirt handfeel.",
        "basis": "review_theme",
        "evidence_ids": review_evidence_ids,
        "review_theme_id": "theme-material-expectation",
        "status": "script_eligible",
        "selected_for_script": True,
        "solution_claim_ids": ["claim-fabric-texture"],
        "target_language_terms": ["feel different", "different than expected"],
    })
    variant = document["variants"][1]
    variant["creative_delta"]["pain_focus_id"] = "pain-material-expectation"
    hook = variant["canonical_timeline"]["beats"][0]
    hook["spoken_line"] = "Worried a tee will feel different than expected?"
    hook["pain_point_id"] = "pain-material-expectation"
    hook["evidence_ids"] = review_evidence_ids
    variant["creative_delta"]["hook_text_original"] = hook["spoken_line"]
    variant["caption_pain_point_ids"] = ["pain-material-expectation"]
    variant["caption"] = "If a tee may feel different than expected, inspect the fabric texture before choosing this polished look."
    variant["caption_claim_bindings"] = [{"claim_id": "claim-fabric-texture", "text_anchor": "fabric texture"}]
    variant["caption_pain_bindings"] = [{"pain_point_id": "pain-material-expectation", "text_anchor": "different than expected"}]
    _recompile_v13(document, 0)
    _recompile_v13(document, 1)
    return document


def _fixture_v13_review_contrast() -> dict[str, Any]:
    document = _fixture_v13_review_sample()
    variant = document["variants"][1]
    hook = variant["canonical_timeline"]["beats"][0]
    hook["spoken_line"] = "Not a basic tee; fabric texture feels different than expected."
    hook["hook_semantics"] = "evidence_backed_contrast"
    variant["creative_delta"]["hook_text_original"] = hook["spoken_line"]
    _recompile_v13(document, 1)
    return document


def _streaming_plan_from_compile(document: dict[str, Any]) -> dict[str, Any]:
    stream = _load_streaming_plan_module()
    market_v14 = document.get("schema_version") == "1.4"
    market_current = market_v14 and document.get("quality_contract_id") == QUALITY_CONTRACT_ID_V14
    planned_variants = []
    for variant in document["variants"]:
        non_cta = [beat for beat in variant["canonical_timeline"]["beats"] if beat.get("purpose") != "cta"]
        planned_variant = {
            "variant_id": variant["variant_id"],
            "color_name": variant["color_name"],
            "source_images": copy.deepcopy(variant["source_images"]),
            "garment_signature": copy.deepcopy(variant["garment_signature"]),
            "creative_delta": copy.deepcopy(variant["creative_delta"]),
            "planned_non_cta_spoken_intent_ids": [beat["spoken_intent_id"] for beat in non_cta],
            "planned_non_cta_visual_signatures": [
                "|".join(stream._norm(beat[field]) for field in ("framing", "core_action", "product_point"))
                for beat in non_cta
            ],
            "caption": variant["caption"],
            "hashtags": copy.deepcopy(variant["hashtags"]),
        }
        if market_current:
            planned_variant["generation_controls"] = copy.deepcopy(variant["generation_controls"])
            planned_variant["deadline_blueprint"] = stream.deadline_blueprint_from_deadlines(
                variant["three_layer_deadlines"],
            )
        planned_variants.append(planned_variant)
    shared_core = copy.deepcopy(document["shared_core"])
    if market_current:
        stream_schema = stream.SCHEMA_VERSION
        stream_contract = stream.CONTRACT_ID
        quality_gates = copy.deepcopy(stream.QUALITY_GATES)
    else:
        # Historical v4/v5 releases remain readable through the immutable v1
        # streaming envelope.  Do not silently promote them to the v2 market
        # contract, which would require schema-1.4 fields they never carried.
        stream_schema = stream.LEGACY_SCHEMA_VERSION
        stream_contract = stream.LEGACY_CONTRACT_ID
        serializer_id = document["variants"][0]["renderings"]["prompt"].get("serializer_id")
        quality_gates = copy.deepcopy(
            stream.LEGACY_V5_QUALITY_GATES
            if serializer_id == stream.LEGACY_V5_PROMPT_SERIALIZER_ID
            else stream.LEGACY_QUALITY_GATES
        )
    plan = {
        "schema_version": stream_schema,
        "contract_id": stream_contract,
        "batch_run_id": "streaming-integration-self-test",
        "batch_compile_id": document["batch_compile_id"],
        "sku_family_id": document["sku_family_id"],
        "plan_revision": 0,
        "plan_locked": True,
        "batch_key": copy.deepcopy(document["batch_key"]),
        "shared_core": shared_core,
        "shared_core_sha256": stream.canonical_sha256(shared_core),
        "quality_gates": quality_gates,
        "planned_variant_count": len(planned_variants),
        "planned_variants": planned_variants,
    }
    if market_current:
        plan["market_prompt_contract_id"] = _market_contract.MARKET_PROMPT_CONTRACT_ID
    plan["differentiation_plan_sha256"] = stream.canonical_sha256(stream._planned_payload(plan))
    return plan


def run_self_test() -> dict[str, Any]:
    cases: list[tuple[str, dict[str, Any], bool, str | None]] = []
    cases.append(("legacy_1_0_positive", _fixture(), True, None))
    cases.append(("quality_1_1_positive", _fixture_v11(), True, None))
    cases.append(("motion_1_2_positive", _fixture_v12(), True, None))
    cases.append(("evidence_1_3_positive", _fixture_v13(), True, None))
    cases.append(("evidence_1_3_legacy_v4_read_positive", _fixture_v13_legacy_v4(), True, None))
    cases.append(("evidence_1_3_review_theme_positive", _fixture_v13_review_sample(), True, None))
    cases.append(("evidence_1_3_review_contrast_positive", _fixture_v13_review_contrast(), True, None))
    compiled_legacy = compile_document(_fixture())
    cases.append(("legacy_1_0_compile_positive", compiled_legacy, True, None))
    compiled_quality = compile_document(_fixture_v11())
    cases.append(("quality_1_1_compile_positive", compiled_quality, True, None))
    compiled_motion = compile_document(_fixture_v12())
    cases.append(("motion_1_2_compile_positive", compiled_motion, True, None))
    compiled_evidence = compile_document(_fixture_v13())
    cases.append(("evidence_1_3_compile_positive", compiled_evidence, True, None))

    garment_in_identity_slot = _fixture_v13()
    garment_in_identity_slot["variants"][0]["references"][0]["interface_tag"] = "@Image1"
    _recompile_v13(garment_in_identity_slot, 0)
    cases.append(("IDENTITY_garment_cannot_use_image1", garment_in_identity_slot, False, "REFERENCE_TAG_INVALID"))

    garment_tag_gap = _fixture_v13()
    garment_tag_gap["variants"][0]["references"][0]["interface_tag"] = "@Image3"
    _recompile_v13(garment_tag_gap, 0)
    cases.append(("IDENTITY_garment_tags_start_at_image2", garment_tag_gap, False, "REFERENCE_TAG_INVALID"))

    identity_pixels_unverified = _fixture_v13()
    del identity_pixels_unverified["variants"][0]["references"][0]["human_identity_pixels_absent"]
    _recompile_v13(identity_pixels_unverified, 0)
    cases.append(("IDENTITY_zero_human_pixels_required", identity_pixels_unverified, False, "THREE_VIEW_QC_FAILED"))

    identity_non_transfer_missing = _fixture_v13()
    identity_non_transfer_missing["variants"][0]["references"][0]["must_not_transfer"].remove("reference_skin_tone")
    _recompile_v13(identity_non_transfer_missing, 0)
    cases.append(("IDENTITY_expanded_non_transfer_required", identity_non_transfer_missing, False, "REFERENCE_TRANSFER_CONTRACT_INVALID"))

    unused_garment_reference = _fixture_v13()
    extra_reference = copy.deepcopy(unused_garment_reference["variants"][0]["references"][0])
    extra_reference["reference_id"] = "ref-unused-garment"
    extra_reference["interface_tag"] = "@Image3"
    unused_garment_reference["variants"][0]["references"].append(extra_reference)
    _recompile_v13(unused_garment_reference, 0)
    cases.append(("IDENTITY_unused_outbound_reference_blocked", unused_garment_reference, False, "VARIANT_REFERENCE_MISMATCH"))

    lip_sync_priority = _fixture_v13(); lip_variant = lip_sync_priority["variants"][0]
    lip_variant["quality_plan"]["secondary_fidelity_spend"] = "lip_sync"
    lip_beat = lip_variant["canonical_timeline"]["beats"][1]
    lip_beat.update({
        "spoken_line": "Look closely at how the neckline sits open and clear under my jacket.",
        "speech_mode": "on_camera_dialogue", "mouth_visibility": "visible", "lip_sync_required": True,
        "actor": "the same creator wearing the unchanged reference garment with face visible",
        "micro_expression": "brighter eyes and a small confident smile as the neckline proof lands",
        "audio": "exact on-camera dialogue over quiet room tone",
    })
    _recompile_v13(lip_sync_priority, 0)
    cases.append(("PERFORMANCE_lip_sync_priority_three_anchor_positive", lip_sync_priority, True, None))

    default_lip_budget = copy.deepcopy(lip_sync_priority)
    default_lip_budget["variants"][0]["quality_plan"]["secondary_fidelity_spend"] = "visible_proof"
    _recompile_v13(default_lip_budget, 0)
    cases.append(("PERFORMANCE_default_lip_sync_budget_rejects_long_anchor", default_lip_budget, False, "LIPSYNC_COMPLEXITY_OVERLOAD"))

    sparse_voiceover = _fixture_v13(); sparse_variant = sparse_voiceover["variants"][0]
    sparse_variant["canonical_timeline"]["beats"][0]["spoken_line"] = "Boxy fit?"
    sparse_variant["canonical_timeline"]["beats"][0]["micro_expression"] = "warm questioning eyebrow lift"
    sparse_variant["canonical_timeline"]["beats"][-1]["spoken_line"] = "Tap below."
    sparse_variant["creative_delta"]["hook_text_original"] = "Boxy fit?"
    _recompile_v13(sparse_voiceover, 0)
    cases.append(("PERFORMANCE_sparse_english_voiceover", sparse_voiceover, False, "VOICEOVER_PACING_INVALID"))

    flat_performance = _fixture_v13(); flat_variant = flat_performance["variants"][0]
    flat_variant["canonical_timeline"]["beats"][-1]["micro_expression"] = flat_variant["canonical_timeline"]["beats"][0]["micro_expression"]
    _recompile_v13(flat_performance, 0)
    cases.append(("PERFORMANCE_flat_visible_expression", flat_performance, False, "FLAT_PERFORMANCE_RISK"))

    missing_close_proof = _fixture_v13(); arc_beats = missing_close_proof["variants"][0]["canonical_timeline"]["beats"]
    arc_beats[3]["end_seconds"] = 9.5
    arc_beats[4]["start_seconds"] = 9.5; arc_beats[4]["end_seconds"] = 12
    arc_beats[5]["start_seconds"] = 12
    _recompile_v13(missing_close_proof, 0)
    cases.append(("PERFORMANCE_close_detail_phase_requires_proof", missing_close_proof, False, "PERFORMANCE_ARC_INVALID"))

    repeated_hip_reset = _fixture_v13(); hip_beats = repeated_hip_reset["variants"][0]["canonical_timeline"]["beats"]
    for hip_beat in hip_beats[:3]:
        for side in ("left", "right"):
            hip_beat["hand_plan"][side]["start_anchor"] = f"{side} hip"
            hip_beat["hand_plan"][side]["end_anchor"] = f"{side} hip"
    _recompile_v13(repeated_hip_reset, 0)
    cases.append(("PERFORMANCE_repeated_symmetric_hip_reset", repeated_hip_reset, False, "STIFF_POSTURE_RISK"))

    walk_drape = _fixture_v13(); walk_variant = walk_drape["variants"][0]
    walk_plan = walk_variant["quality_plan"]["claim_proof_plan"][2]
    walk_plan.update({"action_type": "walk_settle", "hands_required": "body"})
    walk_beat = walk_variant["canonical_timeline"]["beats"][3]
    walk_beat.update({
        "core_action": "the creator takes one step back until the side drape settles",
        "proof_action_type": "walk_settle", "motion_budget": {"camera": "locked", "performer": "simple", "active_hands": "zero"},
        "hand_plan": _motion_hand_plan("none", "stays relaxed", "stays relaxed"),
        "posture_id": "mirror_step_back",
    })
    _recompile_v13(walk_drape, 0)
    cases.append(("PERFORMANCE_walk_drape_positive", walk_drape, True, None))

    research_missing = _fixture_v13(); del research_missing["shared_core"]["research_bundle"]
    cases.append(("EVIDENCE_research_bundle_required", research_missing, False, "RESEARCH_BUNDLE_INVALID"))
    claim_evidence_missing = _fixture_v13(); del claim_evidence_missing["shared_core"]["claims_registry"][1]["evidence_ids"]
    cases.append(("EVIDENCE_claim_ids_required", claim_evidence_missing, False, "CLAIM_EVIDENCE_INVALID"))
    seller_as_buyer = _fixture_v13(); seller_source = seller_as_buyer["shared_core"]["research_bundle"]["evidence_sources"][0]
    seller_source.update({"source_kind": "amazon_seller_copy", "source_role": "buyer_experience"})
    cases.append(("EVIDENCE_seller_cannot_be_buyer", seller_as_buyer, False, "RESEARCH_BUNDLE_INVALID"))
    summary_claim = _fixture_v13(); summary_bundle = summary_claim["shared_core"]["research_bundle"]
    summary_bundle["evidence_sources"].append({
        "source_id": "src-summary-only", "source_kind": "amazon_review_summary", "source_role": "discovery_only",
        "url": "https://www.amazon.com/dp/B0TEST1234", "product_id": "B0TEST1234", "child_product_id": "B0TEST1234",
        "variant_scope": "exact_child", "locator": "review summary", "captured_at": "2026-07-13T00:00:00Z",
        "content_sha256": hashlib.sha256(b"summary").hexdigest(), "review_id": None, "parent_review_id": None,
        "rating": None, "review_date": None, "review_date_status": None, "verified_purchase": None,
    })
    summary_bundle["evidence_items"].append({
        "evidence_id": "ev-summary-only", "source_id": "src-summary-only", "assertion_kind": "visible_feature",
        "statement": "Summary-only discovery item.", "exact_product_match": True, "exact_variant_match": True,
        "conflict_status": "clear", "permitted_uses": ["discovery_only"], "performance_demo_allowed": False,
    })
    summary_sleeve_claim = next(claim for claim in summary_claim["shared_core"]["claims_registry"] if claim["claim_id"] == "claim-sleeve")
    summary_sleeve_claim["evidence_ids"] = ["ev-summary-only"]
    cases.append(("EVIDENCE_summary_discovery_only", summary_claim, False, "CLAIM_EVIDENCE_INVALID"))
    exact_variant_claim = _fixture_v13()
    exact_variant_item = next(item for item in exact_variant_claim["shared_core"]["research_bundle"]["evidence_items"] if item["evidence_id"] == "ev-claim-sleeve")
    exact_variant_item["exact_variant_match"] = False
    cases.append(("EVIDENCE_exact_variant_claim_required", exact_variant_claim, False, "CLAIM_EVIDENCE_INVALID"))
    basis_mismatch = _fixture_v13(); basis_bundle = basis_mismatch["shared_core"]["research_bundle"]
    basis_bundle["evidence_sources"].append({
        "source_id": "src-seller-mismatch", "source_kind": "amazon_seller_copy", "source_role": "seller_marketing",
        "url": "https://www.amazon.com/dp/B0TEST1234", "product_id": "B0TEST1234", "child_product_id": "B0TEST1234",
        "variant_scope": "exact_child", "locator": "seller copy", "captured_at": "2026-07-13T00:00:00Z",
        "content_sha256": hashlib.sha256(b"seller mismatch").hexdigest(), "review_id": None, "parent_review_id": None,
        "rating": None, "review_date": None, "review_date_status": None, "verified_purchase": None,
    })
    basis_bundle["evidence_items"].append({
        "evidence_id": "ev-seller-mismatch", "source_id": "src-seller-mismatch", "assertion_kind": "visible_feature",
        "statement": "Seller copy is not a visible reference.", "exact_product_match": True, "exact_variant_match": True,
        "conflict_status": "clear", "permitted_uses": ["visual_only"], "performance_demo_allowed": False,
    })
    basis_sleeve_claim = next(claim for claim in basis_mismatch["shared_core"]["claims_registry"] if claim["claim_id"] == "claim-sleeve")
    basis_sleeve_claim["evidence_ids"] = ["ev-seller-mismatch"]
    cases.append(("EVIDENCE_basis_source_kind_mismatch", basis_mismatch, False, "CLAIM_EVIDENCE_INVALID"))
    evidence_swap = _fixture_v13(); swapped_proof = evidence_swap["variants"][0]["quality_plan"]["claim_proof_plan"][0]
    swapped_proof["evidence_ids"] = ["ev-claim-sleeve"]
    _recompile_v13(evidence_swap, 0)
    cases.append(("EVIDENCE_proof_swap_rejected", evidence_swap, False, "CLAIM_EVIDENCE_INVALID"))
    pain_missing = _fixture_v13(); del pain_missing["shared_core"]["pain_solution_map"]
    cases.append(("EVIDENCE_pain_map_required", pain_missing, False, "BUYER_PAIN_MAP_INVALID"))
    pain_focus_drift = _fixture_v13(); pain_focus_drift["variants"][0]["creative_delta"]["pain_focus_id"] = "pain-polished-outfit"
    _recompile_v13(pain_focus_drift, 0)
    cases.append(("EVIDENCE_pain_focus_binding", pain_focus_drift, False, "PAIN_PROOF_BINDING_INVALID"))
    duplicate_review = _fixture_v13_review_sample(); duplicate_sources = duplicate_review["shared_core"]["research_bundle"]["evidence_sources"]
    duplicate_sources[-1]["review_id"] = duplicate_sources[-2]["review_id"]
    cases.append(("EVIDENCE_duplicate_review_id", duplicate_review, False, "REVIEW_EVIDENCE_INVALID"))
    duplicate_review_item = _fixture_v13_review_sample(); duplicate_bundle = duplicate_review_item["shared_core"]["research_bundle"]
    extra_review_item = copy.deepcopy(next(item for item in duplicate_bundle["evidence_items"] if item["evidence_id"] == "ev-review-1"))
    extra_review_item["evidence_id"] = "ev-review-1-normalized-again"
    duplicate_bundle["evidence_items"].append(extra_review_item)
    duplicate_theme = duplicate_bundle["review_analysis"]["themes"][0]
    duplicate_theme["review_evidence_ids"] = ["ev-review-1", "ev-review-1-normalized-again", "ev-review-2"]
    duplicate_theme["mention_count"] = 3
    duplicate_pain = next(item for item in duplicate_review_item["shared_core"]["pain_solution_map"] if item["pain_point_id"] == "pain-material-expectation")
    duplicate_pain["evidence_ids"] = copy.deepcopy(duplicate_theme["review_evidence_ids"])
    duplicate_review_item["variants"][1]["canonical_timeline"]["beats"][0]["evidence_ids"] = copy.deepcopy(duplicate_theme["review_evidence_ids"])
    _recompile_v13(duplicate_review_item, 1)
    cases.append(("EVIDENCE_theme_counts_unique_review_ids", duplicate_review_item, False, "REVIEW_EVIDENCE_INVALID"))
    weak_aggregate = _fixture_v13_review_sample(); weak_theme = weak_aggregate["shared_core"]["research_bundle"]["review_analysis"]["themes"][0]
    weak_theme["review_evidence_ids"] = weak_theme["review_evidence_ids"][:2]; weak_theme["mention_count"] = 2
    weak_pain = next(item for item in weak_aggregate["shared_core"]["pain_solution_map"] if item["pain_point_id"] == "pain-material-expectation")
    weak_pain["evidence_ids"] = copy.deepcopy(weak_theme["review_evidence_ids"])
    weak_aggregate["variants"][1]["canonical_timeline"]["beats"][0]["evidence_ids"] = copy.deepcopy(weak_theme["review_evidence_ids"])
    _recompile_v13(weak_aggregate, 1)
    cases.append(("EVIDENCE_aggregate_needs_three_reviews", weak_aggregate, False, "REVIEW_EVIDENCE_INVALID"))
    review_only_solution = _fixture_v13_review_sample(); review_only_shared = review_only_solution["shared_core"]
    texture_claim = next(claim for claim in review_only_shared["claims_registry"] if claim["claim_id"] == "claim-fabric-texture")
    review_only_ids = copy.deepcopy(next(theme for theme in review_only_shared["research_bundle"]["review_analysis"]["themes"] if theme["theme_id"] == "theme-material-expectation")["review_evidence_ids"])
    texture_claim.update({
        "evidence_basis": "product_page", "evidence_ref": "direct customer review sample",
        "assertion_kind": "buyer_experience", "claim_mode": "qualified", "evidence_ids": review_only_ids,
    })
    for item in review_only_shared["research_bundle"]["evidence_items"]:
        if item["evidence_id"] in review_only_ids:
            item["permitted_uses"] = ["pain_context", "claim_qualified"]
    for variant in review_only_solution["variants"]:
        for proof in variant["quality_plan"]["claim_proof_plan"]:
            if proof["claim_id"] == "claim-fabric-texture":
                proof["evidence_ids"] = copy.deepcopy(review_only_ids)
                matching_beat = next(beat for beat in variant["canonical_timeline"]["beats"] if beat.get("claim_proof_id") == proof["claim_proof_id"])
                matching_beat["evidence_ids"] = copy.deepcopy(review_only_ids)
    _recompile_v13(review_only_solution, 0); _recompile_v13(review_only_solution, 1)
    cases.append(("EVIDENCE_review_pain_needs_independent_solution", review_only_solution, False, "BUYER_PAIN_MAP_INVALID"))
    late_solution = _fixture_v13_review_sample()
    late_pain = next(item for item in late_solution["shared_core"]["pain_solution_map"] if item["pain_point_id"] == "pain-material-expectation")
    late_pain["solution_claim_ids"] = ["claim-hem"]
    _recompile_v13(late_solution, 0); _recompile_v13(late_solution, 1)
    cases.append(("EVIDENCE_first_proof_answers_pain", late_solution, False, "PAIN_PROOF_BINDING_INVALID"))
    contrast_question = _fixture_v13_review_contrast()
    contrast_question_hook = contrast_question["variants"][1]["canonical_timeline"]["beats"][0]
    contrast_question_hook["spoken_line"] = "Could this basic tee feel different than expected?"
    contrast_question["variants"][1]["creative_delta"]["hook_text_original"] = contrast_question_hook["spoken_line"]
    _recompile_v13(contrast_question, 1)
    cases.append(("EVIDENCE_contrast_hook_requires_negative_statement", contrast_question, False, "CONTRAST_HOOK_PROOF_INVALID"))
    delayed_contrast_proof = _fixture_v13_review_contrast()
    delayed_contrast_proof["variants"][1]["canonical_timeline"]["beats"][1]["proof_action_type"] = "touch_release"
    _recompile_v13(delayed_contrast_proof, 1)
    cases.append(("EVIDENCE_contrast_hook_requires_immediate_pinch_proof", delayed_contrast_proof, False, "CONTRAST_HOOK_PROOF_INVALID"))
    caption_without_anchors = _fixture_v13(); caption_without_anchors["variants"][0]["caption"] = "Buy now."
    cases.append(("EVIDENCE_caption_text_bindings", caption_without_anchors, False, "CAPTION_INVALID"))
    evil_research_url = _fixture_v13_review_sample(); evil_bundle = evil_research_url["shared_core"]["research_bundle"]
    evil_bundle["requested_url"] = "https://evil.example/?amazon.com"
    evil_bundle["canonical_url"] = "https://evil.example/dp/B0TEST1234"
    _recompile_v13(evil_research_url, 0); _recompile_v13(evil_research_url, 1)
    cases.append(("EVIDENCE_amazon_host_identity", evil_research_url, False, "RESEARCH_BUNDLE_INVALID"))
    wrong_review_url = _fixture_v13_review_sample(); wrong_review_sources = wrong_review_url["shared_core"]["research_bundle"]["evidence_sources"]
    first_review_source = next(source for source in wrong_review_sources if source.get("source_kind") == "amazon_customer_review")
    first_review_source["url"] = "https://www.amazon.com/portal/customer-reviews/srp/-/RWRONGREVIEW"
    cases.append(("EVIDENCE_review_url_id_match", wrong_review_url, False, "REVIEW_EVIDENCE_INVALID"))
    research_hash = _fixture_v13(); research_hash["variants"][0]["renderings"]["prompt"]["research_bundle_sha256"] = "0" * 64
    cases.append(("EVIDENCE_research_hash", research_hash, False, "QUALITY_PLAN_HASH_MISMATCH"))

    low_stretch = _fixture_v13(); low_bundle = low_stretch["shared_core"]["research_bundle"]
    low_bundle["evidence_sources"].append({
        "source_id": "src-low-stretch", "source_kind": "amazon_catalog_attribute", "source_role": "product_fact",
        "url": "https://www.amazon.com/dp/B0LOWSTRETCH", "product_id": "B0LOWSTRETCH", "child_product_id": "B0LOWSTRETCH",
        "variant_scope": "exact_child", "locator": "Product details > Stretch", "captured_at": "2026-07-13T00:00:00Z",
        "content_sha256": hashlib.sha256(b"Low Stretch").hexdigest(), "review_id": None, "parent_review_id": None,
        "rating": None, "review_date": None, "verified_purchase": None,
    })
    low_bundle["evidence_items"].append({
        "evidence_id": "ev-low-stretch", "source_id": "src-low-stretch", "assertion_kind": "stretch_attribute",
        "statement": "Amazon catalog classifies the selected child as Low Stretch.", "exact_product_match": True,
        "exact_variant_match": True, "conflict_status": "clear", "permitted_uses": ["claim_direct"],
        "performance_demo_allowed": False,
    })
    low_claim = low_stretch["shared_core"]["claims_registry"][0]
    low_claim.update({
        "feature_id": "fabric stretch", "product_part_id": "material_performance", "evidence_basis": "product_page",
        "evidence_ref": "Amazon catalog Low Stretch", "assertion_level": "qualitative", "assertion_kind": "stretch_attribute",
        "claim_mode": "direct", "evidence_ids": ["ev-low-stretch"], "spoken_claim_terms": ["fabric stretch"],
    })
    low_variant = low_stretch["variants"][0]; low_plan = low_variant["quality_plan"]["claim_proof_plan"][0]
    low_plan.update({
        "proof_target": "fabric stretch", "product_part_id": "material_performance", "evidence_basis": "product_page",
        "evidence_ids": ["ev-low-stretch"], "framing_class": "detail_closeup", "action_type": "pull_release",
        "hands_required": "two", "expected_visible_change": "fabric extension and recovery remain visible",
    })
    low_beat = low_variant["canonical_timeline"]["beats"][1]
    low_beat.update({
        "framing": "fabric stretch close-up", "core_action": "both hands perform one coordinated pull-release cycle on the fabric stretch area",
        "action_target": "fabric stretch", "spoken_line": "Here you can see the fabric stretch clearly.",
        "product_point": "fabric stretch", "visible_endpoint": "fabric extension and recovery remain visible",
        "proof_target": "fabric stretch", "proof_action_type": "pull_release", "product_part_id": "material_performance",
        "evidence_basis": "product_page", "evidence_ids": ["ev-low-stretch"],
        "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "two"},
        "hand_plan": {
            "active_hands": "both",
            "left": {"start_anchor": "left fabric edge", "action": "pulls left once", "end_anchor": "left fabric edge"},
            "right": {"start_anchor": "right fabric edge", "action": "pulls right once", "end_anchor": "right fabric edge"},
        },
    })
    _recompile_v13(low_stretch, 0)
    cases.append(("EVIDENCE_low_stretch_blocks_pull", low_stretch, False, "UNSUPPORTED_PERFORMANCE_DEMO"))

    claims_missing = _fixture_v12(); del claims_missing["shared_core"]["claims_registry"]
    cases.append(("MOTION_claims_registry", claims_missing, False, "CLAIMS_REGISTRY_INVALID"))
    allowlist_missing = _fixture_v12(); del allowlist_missing["shared_core"]["claims_allowlist"]
    cases.append(("MOTION_claims_allowlist", allowlist_missing, False, "CLAIMS_REGISTRY_INVALID"))
    multi_target_claim = _fixture_v12(); multi_target_claim["shared_core"]["claims_registry"][0]["feature_id"] = "neckline and sleeve"
    cases.append(("MOTION_registry_one_target", multi_target_claim, False, "CLAIMS_REGISTRY_INVALID"))
    proof_plan_missing = _fixture_v12(); del proof_plan_missing["variants"][0]["quality_plan"]["claim_proof_plan"]
    cases.append(("MOTION_proof_plan", proof_plan_missing, False, "PROOF_PLAN_MISSING"))
    multi_target_proof = _fixture_v12(); multi_target_proof["variants"][0]["quality_plan"]["claim_proof_plan"][0]["proof_target"] = "neckline and sleeve"
    _recompile_v12(multi_target_proof, 0)
    cases.append(("MOTION_proof_one_target", multi_target_proof, False, "PROOF_PLAN_MISSING"))
    static_selling = _fixture_v12(); static_beat = static_selling["variants"][0]["canonical_timeline"]["beats"][1]
    static_beat["core_action"] = "holds still for the whole explanation"
    static_beat["motion_budget"].update({"performer": "still", "active_hands": "zero"})
    static_beat["hand_plan"] = _motion_hand_plan("none", "stays anchored", "stays anchored")
    _recompile_v12(static_selling, 0)
    cases.append(("MOTION_static_selling", static_selling, False, "STATIC_SELLING_BEAT"))
    silent_proof = _fixture_v12(); silent_beat = silent_proof["variants"][0]["canonical_timeline"]["beats"][1]
    silent_beat.update({"spoken_line": None, "silence_reason": "visual proof only", "speech_mode": "none", "mouth_visibility": "not_visible", "lip_sync_required": False})
    _recompile_v12(silent_proof, 0)
    cases.append(("MOTION_claim_action_needs_voiceover", silent_proof, False, "SPEECH_MODE_INVALID"))
    wrong_frame = _fixture_v12(); wrong_frame["variants"][0]["canonical_timeline"]["beats"][1]["framing"] = "hem close-up"
    _recompile_v12(wrong_frame, 0)
    cases.append(("MOTION_wrong_frame", wrong_frame, False, "CLAIM_PROOF_FRAMING_MISMATCH"))
    wide_part_frame = _fixture_v12(); wide_variant = wide_part_frame["variants"][0]
    wide_variant["quality_plan"]["claim_proof_plan"][0]["framing_class"] = "full_fit"
    wide_variant["canonical_timeline"]["beats"][1].update({"framing": "neckline full-body shot", "product_visibility": "full"})
    _recompile_v12(wide_part_frame, 0)
    cases.append(("MOTION_part_requires_closeup", wide_part_frame, False, "CLAIM_PROOF_FRAMING_MISMATCH"))
    wrong_frame_class_text = _fixture_v12(); wrong_frame_class_text["variants"][0]["canonical_timeline"]["beats"][1]["framing"] = "neckline full-body shot"
    _recompile_v12(wrong_frame_class_text, 0)
    cases.append(("MOTION_frame_text_class", wrong_frame_class_text, False, "CLAIM_PROOF_FRAMING_MISMATCH"))
    wrong_action = _fixture_v12(); action_beat = wrong_action["variants"][0]["canonical_timeline"]["beats"][1]
    action_beat["action_target"] = "hem"; action_beat["product_point"] = "hem"
    _recompile_v12(wrong_action, 0)
    cases.append(("MOTION_wrong_action", wrong_action, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    wrong_action_type = _fixture_v12(); wrong_type_variant = wrong_action_type["variants"][0]
    wrong_type_variant["quality_plan"]["claim_proof_plan"][0]["action_type"] = "pocket_use"
    wrong_type_variant["canonical_timeline"]["beats"][1]["proof_action_type"] = "pocket_use"
    _recompile_v12(wrong_action_type, 0)
    cases.append(("MOTION_wrong_action_type", wrong_action_type, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    wrong_action_text = _fixture_v12(); wrong_action_text["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "the right hand smooths the neckline once and moves clear"
    _recompile_v12(wrong_action_text, 0)
    cases.append(("MOTION_action_text_mismatch", wrong_action_text, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    wrong_action_object = _fixture_v12(); wrong_action_object["variants"][0]["canonical_timeline"]["beats"][2]["core_action"] = "the right fingertips touch her hair once and release"
    _recompile_v12(wrong_action_object, 0)
    cases.append(("MOTION_action_object_binding", wrong_action_object, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    negated_action = _fixture_v12(); negated_action["variants"][0]["canonical_timeline"]["beats"][2]["core_action"] = "the right hand does not touch the sleeve"
    _recompile_v12(negated_action, 0)
    cases.append(("MOTION_negated_action", negated_action, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    overloaded_action_text = _fixture_v12(); overloaded_action_text["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "the right index finger traces the neckline, then smooths it and makes one slow half-turn"
    _recompile_v12(overloaded_action_text, 0)
    cases.append(("MOTION_multiple_actions_one_beat", overloaded_action_text, False, "ACTION_SEQUENCE_OVERLOAD"))
    extra_action_text = _fixture_v12(); extra_action_text["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "the right index finger traces the neckline while she walks, waves a bag, nods, and bends"
    _recompile_v12(extra_action_text, 0)
    cases.append(("MOTION_unrelated_actions_one_beat", extra_action_text, False, "ACTION_SEQUENCE_OVERLOAD"))
    multi_claim_line = _fixture_v12(); multi_claim_line["variants"][0]["canonical_timeline"]["beats"][1]["spoken_line"] = "The neckline and sleeve are both easy to see."
    _recompile_v12(multi_claim_line, 0)
    cases.append(("MOTION_one_spoken_target_per_beat", multi_claim_line, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    wrong_spoken_target = _fixture_v12(); wrong_spoken_target["variants"][0]["canonical_timeline"]["beats"][2]["spoken_line"] = "Here you can see the neckline clearly."
    _recompile_v12(wrong_spoken_target, 0)
    cases.append(("MOTION_spoken_target_binding", wrong_spoken_target, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    spoken_claim_id_drift = _fixture_v12(); spoken_claim_id_drift["variants"][0]["canonical_timeline"]["beats"][1]["spoken_claim_ids"] = ["claim-sleeve"]
    _recompile_v12(spoken_claim_id_drift, 0)
    cases.append(("MOTION_spoken_claim_id_binding", spoken_claim_id_drift, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    wrong_endpoint = _fixture_v12(); wrong_endpoint["variants"][0]["canonical_timeline"]["beats"][1]["visible_endpoint"] = "generic result"
    _recompile_v12(wrong_endpoint, 0)
    cases.append(("MOTION_wrong_endpoint", wrong_endpoint, False, "CLAIM_PROOF_ENDPOINT_MISMATCH"))
    unsupported_stretch = _fixture_v12(); stretch_plan = unsupported_stretch["variants"][0]["quality_plan"]["claim_proof_plan"][0]
    stretch_plan["action_type"] = "pull_release"; unsupported_stretch["variants"][0]["canonical_timeline"]["beats"][1]["proof_action_type"] = "pull_release"
    _recompile_v12(unsupported_stretch, 0)
    cases.append(("MOTION_unsupported_stretch", unsupported_stretch, False, "UNSUPPORTED_PERFORMANCE_DEMO"))
    unsupported_spoken = _fixture_v12(); unsupported_spoken["variants"][0]["canonical_timeline"]["beats"][1]["spoken_line"] = "This neckline is stretchy."
    _recompile_v12(unsupported_spoken, 0)
    cases.append(("MOTION_unsupported_spoken_performance", unsupported_spoken, False, "UNSUPPORTED_PERFORMANCE_DEMO"))
    unsupported_implied = _fixture_v12(); unsupported_implied["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "the right hand pulls and releases the neckline fabric"
    _recompile_v12(unsupported_implied, 0)
    cases.append(("MOTION_unsupported_implied_performance", unsupported_implied, False, "UNSUPPORTED_PERFORMANCE_DEMO"))
    hook_performance = _fixture_v12(); hook_performance["variants"][0]["canonical_timeline"]["beats"][0].update({"spoken_line": "This top is stretchy, breathable, and waterproof.", "hook_semantics": "context"})
    hook_performance["variants"][0]["creative_delta"]["hook_text_original"] = hook_performance["variants"][0]["canonical_timeline"]["beats"][0]["spoken_line"]
    _recompile_v12(hook_performance, 0)
    cases.append(("MOTION_unbound_hook_performance", hook_performance, False, "UNSUPPORTED_PERFORMANCE_DEMO"))
    hook_product_claim = _fixture_v12(); hook_product_claim["variants"][0]["canonical_timeline"]["beats"][0].update({"spoken_line": "This top has deep pockets.", "hook_semantics": "context"})
    hook_product_claim["variants"][0]["creative_delta"]["hook_text_original"] = hook_product_claim["variants"][0]["canonical_timeline"]["beats"][0]["spoken_line"]
    _recompile_v12(hook_product_claim, 0)
    cases.append(("MOTION_unbound_hook_product_claim", hook_product_claim, False, "PROOF_PLAN_MISSING"))
    german_hook_claim = _fixture_v12(); german_hook_claim["variants"][0]["canonical_timeline"]["beats"][0].update({"spoken_line": "Dieses Top hat tiefe Taschen.", "hook_semantics": "context"})
    german_hook_claim["variants"][0]["creative_delta"]["hook_text_original"] = german_hook_claim["variants"][0]["canonical_timeline"]["beats"][0]["spoken_line"]
    _recompile_v12(german_hook_claim, 0)
    cases.append(("MOTION_german_unbound_hook_claim", german_hook_claim, False, "PROOF_PLAN_MISSING"))
    pain_question_hook = _fixture_v12(); pain_question_hook["variants"][0]["canonical_timeline"]["beats"][0]["spoken_line"] = "Tired of sleeves that always fit awkwardly?"
    pain_question_hook["variants"][0]["creative_delta"]["hook_text_original"] = pain_question_hook["variants"][0]["canonical_timeline"]["beats"][0]["spoken_line"]
    _recompile_v12(pain_question_hook, 0)
    cases.append(("MOTION_legal_part_pain_question", pain_question_hook, True, None))
    crosswired_performance = _fixture_v12(); cross_variant = crosswired_performance["variants"][0]
    cross_claim = crosswired_performance["shared_core"]["claims_registry"][0]
    cross_claim.update({"feature_id": "fabric breathability", "product_part_id": "material_performance", "evidence_basis": "product_page", "evidence_ref": "product page breathability claim", "assertion_level": "qualitative", "spoken_claim_terms": ["breathable", "breathability"]})
    cross_plan = cross_variant["quality_plan"]["claim_proof_plan"][0]
    cross_plan.update({"proof_target": "fabric breathability", "product_part_id": "material_performance", "evidence_basis": "product_page", "framing_class": "detail_closeup", "action_type": "pinch_release", "hands_required": "one", "expected_visible_change": "fabric fold returns clearly visible"})
    cross_beat = cross_variant["canonical_timeline"]["beats"][1]
    cross_beat.update({"framing": "fabric breathability close-up", "core_action": "the right hand pinches one small fabric breathability test fold and releases", "action_target": "fabric breathability", "spoken_line": "This fabric is stretchy.", "product_point": "fabric breathability", "visible_endpoint": "fabric fold returns clearly visible", "proof_target": "fabric breathability", "proof_action_type": "pinch_release", "product_part_id": "material_performance", "evidence_basis": "product_page"})
    _recompile_v12(crosswired_performance, 0)
    cases.append(("MOTION_performance_claim_crosswire", crosswired_performance, False, "UNSUPPORTED_PERFORMANCE_DEMO"))
    repeated_targets = _fixture_v12(); repeated_variant = repeated_targets["variants"][0]
    neckline_claim_id = "claim-neckline"
    for plan, beat in zip(repeated_variant["quality_plan"]["claim_proof_plan"], repeated_variant["canonical_timeline"]["beats"][1:5]):
        plan.update({"claim_id": neckline_claim_id, "proof_target": "neckline", "product_part_id": "neckline", "framing_class": "detail_closeup", "action_type": "point_trace", "hands_required": "one", "expected_visible_change": "neckline edge stays centered and unobstructed"})
        beat.update({"purpose": "proof", "beat_role": "product_claim", "framing": "neckline close-up", "core_action": "the right index finger traces the neckline once and moves clear", "action_target": "neckline", "spoken_line": "Here you can see the neckline clearly.", "product_point": "neckline", "styling_point": None, "product_visibility": "detail", "visible_endpoint": "neckline edge stays centered and unobstructed", "proof_target": "neckline", "proof_action_type": "point_trace", "product_part_id": "neckline", "spoken_claim_ids": [neckline_claim_id], "posture_id": "hands_only_detail", "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "one"}, "hand_plan": _motion_hand_plan("right", "stays anchored", "traces neckline once")})
    _recompile_v12(repeated_targets, 0)
    cases.append(("MOTION_distinct_part_coverage", repeated_targets, False, "ACTION_COVERAGE_INSUFFICIENT"))
    alias_targets = _fixture_v12(); alias_variant = alias_targets["variants"][0]
    alias_specs = [
        ("claim-neckline", "neckline"), ("claim-sleeve", "collar"), ("claim-side-drape", "v-neck"),
    ]
    registry_by_id = {claim["claim_id"]: claim for claim in alias_targets["shared_core"]["claims_registry"]}
    for (claim_id, alias), plan, beat in zip(alias_specs, alias_variant["quality_plan"]["claim_proof_plan"][:3], alias_variant["canonical_timeline"]["beats"][1:4]):
        registry_by_id[claim_id].update({"feature_id": alias, "product_part_id": "neckline", "spoken_claim_terms": [alias]})
        endpoint = f"{alias} remains centered and unobstructed"
        plan.update({"claim_id": claim_id, "proof_target": alias, "product_part_id": "neckline", "framing_class": "detail_closeup", "action_type": "point_trace", "hands_required": "one", "expected_visible_change": endpoint})
        beat.update({"purpose": "proof", "beat_role": "product_claim", "framing": f"{alias} close-up", "core_action": f"the right index finger points once to the {alias} and moves clear", "action_target": alias, "spoken_line": f"Here you can see the {alias} clearly.", "product_point": alias, "styling_point": None, "product_visibility": "detail", "visible_endpoint": endpoint, "proof_target": alias, "proof_action_type": "point_trace", "product_part_id": "neckline", "spoken_claim_ids": [claim_id], "posture_id": "hands_only_detail", "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "one"}, "hand_plan": _motion_hand_plan("right", "stays anchored", f"points to {alias} once")})
    _recompile_v12(alias_targets, 0)
    cases.append(("MOTION_aliases_share_product_part", alias_targets, False, "ACTION_COVERAGE_INSUFFICIENT"))
    low_coverage = _fixture_v12(); low_variant = low_coverage["variants"][0]
    removed = low_variant["quality_plan"]["claim_proof_plan"].pop()
    low_beat = next(beat for beat in low_variant["canonical_timeline"]["beats"] if beat["beat_id"] == removed["beat_id"])
    low_beat.update({"purpose": "hook", "beat_role": "reaction", "claim_proof_id": None, "proof_target": None, "proof_action_type": None, "product_part_id": None, "evidence_basis": None, "spoken_claim_ids": [], "spoken_line": "One more look."})
    _recompile_v12(low_coverage, 0)
    cases.append(("MOTION_low_action_coverage", low_coverage, False, "ACTION_COVERAGE_INSUFFICIENT"))
    hand_mismatch = _fixture_v12(); hand_mismatch["variants"][0]["canonical_timeline"]["beats"][1]["hand_plan"]["active_hands"] = "both"
    _recompile_v12(hand_mismatch, 0)
    cases.append(("MOTION_hand_mismatch", hand_mismatch, False, "HAND_ACTIVITY_MISMATCH"))
    wrong_hand_side = _fixture_v12(); wrong_hand_side["variants"][0]["canonical_timeline"]["beats"][2]["core_action"] = "the left hand touches the sleeve once and releases"
    _recompile_v12(wrong_hand_side, 0)
    cases.append(("MOTION_core_action_hand_side", wrong_hand_side, False, "HAND_ACTIVITY_MISMATCH"))
    extra_limb = _fixture_v12(); extra_limb["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] += " while a third hand holds a bag"
    _recompile_v12(extra_limb, 0)
    cases.append(("MOTION_extra_limb_text", extra_limb, False, "ANATOMY_RISK_OVERLOAD"))
    helper_limb = _fixture_v12(); helper_limb["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "the right index finger traces the neckline while a helper hand holds a bag"
    _recompile_v12(helper_limb, 0)
    cases.append(("MOTION_helper_limb_text", helper_limb, False, "ANATOMY_RISK_OVERLOAD"))
    modal_non_action = _fixture_v12(); modal_non_action["variants"][0]["canonical_timeline"]["beats"][2]["core_action"] = "the right hand intends to touch the sleeve but remains posed"
    _recompile_v12(modal_non_action, 0)
    cases.append(("MOTION_modal_non_action", modal_non_action, False, "STATIC_SELLING_BEAT"))
    untouched_non_action = _fixture_v12(); untouched_non_action["variants"][0]["canonical_timeline"]["beats"][2]["core_action"] = "the right hand stays near the sleeve and leaves it untouched"
    _recompile_v12(untouched_non_action, 0)
    cases.append(("MOTION_untouched_non_action", untouched_non_action, False, "CLAIM_PROOF_ACTION_MISMATCH"))
    legal_pullover = _fixture_v12(); legal_pullover["variants"][0]["canonical_timeline"]["beats"][1]["core_action"] = "the right index finger points once to the pullover neckline and moves clear"
    _recompile_v12(legal_pullover, 0)
    cases.append(("MOTION_legal_pullover_point", legal_pullover, True, None))
    stiff_hook = _fixture_v12(); hook_beat = stiff_hook["variants"][0]["canonical_timeline"]["beats"][0]
    hook_beat["core_action"] = "stands still for the whole hook"; hook_beat["motion_budget"].update({"performer": "still", "active_hands": "zero"})
    hook_beat["hand_plan"] = _motion_hand_plan("none", "stays anchored", "stays anchored")
    _recompile_v12(stiff_hook, 0)
    cases.append(("MOTION_stiff_hook", stiff_hook, False, "STIFF_POSTURE_RISK"))
    partial_target = _fixture_v12(); partial_beat = partial_target["variants"][1]["canonical_timeline"]["beats"][3]
    partial_beat["action_target"] = "side"; partial_beat["product_point"] = "side"
    _recompile_v12(partial_target, 1)
    cases.append(("MOTION_full_target_match", partial_target, False, "CLAIM_PROOF_ACTION_MISMATCH"))

    legal_open_neckline = _fixture_v12(); legal_variant = legal_open_neckline["variants"][0]
    legal_variant["quality_plan"]["claim_proof_plan"][0]["action_type"] = "touch_release"
    legal_variant["canonical_timeline"]["beats"][1].update({"core_action": "the right hand touches the open neckline once and releases", "proof_action_type": "touch_release"})
    _recompile_v12(legal_open_neckline, 0)
    cases.append(("MOTION_legal_open_neckline_touch", legal_open_neckline, True, None))

    verified_stretch = _fixture_v12(); stretch_variant = verified_stretch["variants"][0]
    stretch_claim = verified_stretch["shared_core"]["claims_registry"][0]
    stretch_claim.update({"feature_id": "fabric stretch", "product_part_id": "material_performance", "evidence_basis": "product_page", "evidence_ref": "product page stretch claim", "assertion_level": "qualitative", "spoken_claim_terms": ["fabric stretches", "stretch"]})
    stretch_plan = stretch_variant["quality_plan"]["claim_proof_plan"][0]
    stretch_plan.update({"proof_target": "fabric stretch", "product_part_id": "material_performance", "evidence_basis": "product_page", "action_type": "pull_release", "hands_required": "two", "expected_visible_change": "fabric extension and recovery remain visible"})
    stretch_beat = stretch_variant["canonical_timeline"]["beats"][1]
    stretch_beat.update({
        "framing": "fabric stretch close-up", "core_action": "both hands perform one coordinated gentle pull-release cycle on the fabric stretch area",
        "action_target": "fabric stretch", "product_point": "fabric stretch", "spoken_line": "The fabric stretches and recovers clearly.",
        "visible_endpoint": "fabric extension and recovery remain visible",
        "proof_target": "fabric stretch", "proof_action_type": "pull_release", "evidence_basis": "product_page",
        "product_part_id": "material_performance",
        "motion_budget": {"camera": "locked", "performer": "micro", "active_hands": "two"},
        "hand_plan": {
            "active_hands": "both",
            "left": {"start_anchor": "left fabric edge", "action": "pulls left once", "end_anchor": "left fabric edge"},
            "right": {"start_anchor": "right fabric edge", "action": "pulls right once", "end_anchor": "right fabric edge"},
        },
    })
    _recompile_v12(verified_stretch, 0)
    cases.append(("MOTION_verified_two_hand_stretch", verified_stretch, True, None))

    formal_voiceover = _fixture_v12()
    formal_voiceover["variants"][0]["canonical_timeline"]["beats"][1]["spoken_line"] = "Furthermore, this garment features the neckline."
    _recompile_v12(formal_voiceover, 0)
    cases.append(("VOICEOVER_formal_brochure_language", formal_voiceover, False, "VOICEOVER_STYLE_TOO_FORMAL"))

    generic_voiceover = _fixture_v12()
    generic_voiceover["variants"][0]["canonical_timeline"]["beats"][1]["spoken_line"] = "The neckline looks nice."
    _recompile_v12(generic_voiceover, 0)
    cases.append(("VOICEOVER_generic_detail_praise", generic_voiceover, False, "VOICEOVER_DETAIL_TOO_GENERIC"))

    repeated_voiceover = _fixture_v12()
    repeated_variant = repeated_voiceover["variants"][0]
    for beat in repeated_variant["canonical_timeline"]["beats"][1:4]:
        beat["spoken_line"] = f"Look at the {beat['proof_target']}."
    _recompile_v12(repeated_voiceover, 0)
    cases.append(("VOICEOVER_repeated_proof_opener", repeated_voiceover, False, "VOICEOVER_STYLE_TOO_REPETITIVE"))

    quality_missing = _fixture_v11(); del quality_missing["variants"][0]["quality_plan"]
    cases.append(("Q1_quality_plan_missing", quality_missing, False, "QUALITY_PLAN_MISSING"))
    tag_invalid = _fixture_v11(); tag_invalid["variants"][0]["references"][0]["interface_tag"] = "Image1"
    cases.append(("Q2_reference_tag", tag_invalid, False, "REFERENCE_TAG_INVALID"))
    tag_mojibake = _fixture_v11(); tag_mojibake["variants"][0]["references"][0]["interface_tag"] = "@鍥剧墖1"
    cases.append(("Q2_reference_tag_mojibake", tag_mojibake, False, "REFERENCE_TAG_INVALID"))
    tag_chinese = _fixture_v11(); tag_chinese["variants"][0]["references"][0]["interface_tag"] = "@图片1"; _recompile_v11(tag_chinese, 0)
    cases.append(("Q2_reference_tag_chinese_positive", tag_chinese, True, None))
    transfer_invalid = _fixture_v11(); transfer_invalid["variants"][0]["references"][0]["positive_replacement"] = "copy_reference_model"
    cases.append(("Q3_reference_transfer", transfer_invalid, False, "REFERENCE_TRANSFER_CONTRACT_INVALID"))
    quality_hash = _fixture_v11(); quality_hash["variants"][0]["renderings"]["prompt"]["quality_plan_sha256"] = "0" * 64
    cases.append(("Q4_quality_hash", quality_hash, False, "QUALITY_PLAN_HASH_MISMATCH"))
    speech_invalid = _fixture_v11(); speech_invalid["variants"][0]["canonical_timeline"]["beats"][0]["mouth_visibility"] = "partial"; _recompile_v11(speech_invalid, 0)
    cases.append(("Q5_speech_mode", speech_invalid, False, "SPEECH_MODE_INVALID"))
    setup_dense = _fixture_v11()
    for beat_index, beat in enumerate(setup_dense["variants"][0]["canonical_timeline"]["beats"]):
        beat["camera_setup_id"] = f"dense-{beat_index + 1}"
    _recompile_v11(setup_dense, 0)
    cases.append(("Q6_setup_runs", setup_dense, False, "SHOT_DENSITY_EXCEEDED"))
    lipsync_overload = _fixture_v11(); lipsync_overload["variants"][0]["canonical_timeline"]["beats"][0]["motion_budget"].update({"camera": "active", "active_hands": "zero"}); _recompile_v11(lipsync_overload, 0)
    cases.append(("Q7_lipsync_overload", lipsync_overload, False, "LIPSYNC_COMPLEXITY_OVERLOAD"))
    detail_overload = _fixture_v11(); detail_overload["variants"][0]["canonical_timeline"]["beats"][1]["motion_budget"].update({"camera": "active", "active_hands": "zero"}); _recompile_v11(detail_overload, 0)
    cases.append(("Q8_detail_overload", detail_overload, False, "DETAIL_SHOT_COMPLEXITY_OVERLOAD"))
    cta_overload = _fixture_v11(); cta_overload["variants"][0]["canonical_timeline"]["beats"][3]["motion_budget"]["performer"] = "simple"; _recompile_v11(cta_overload, 0)
    cases.append(("Q9_cta_overload", cta_overload, False, "CTA_COMPLEXITY_OVERLOAD"))
    beats_hash = _fixture_v11(); beats_hash["variants"][0]["renderings"]["prompt"]["canonical_beats_sha256"] = "0" * 64
    cases.append(("Q10_beats_hash", beats_hash, False, "CANONICAL_BEATS_HASH_MISMATCH"))
    json_leak = _fixture_v11(); leak_prompt = json_leak["variants"][0]["renderings"]["prompt"]
    leak_prompt["compiled_text"] += "\n" + _canonical_json(leak_prompt["beats"])
    leak_prompt["compiled_text_sha256"] = _sha256_text(leak_prompt["compiled_text"])
    cases.append(("Q11_json_leak", json_leak, False, "PROMPT_V2_FULL_JSON_LEAK"))
    serializer_drift = _fixture_v11(); drift_prompt = serializer_drift["variants"][0]["renderings"]["prompt"]
    drift_prompt["compiled_text"] += " extra"
    drift_prompt["compiled_text_sha256"] = _sha256_text(drift_prompt["compiled_text"])
    cases.append(("Q12_serializer", serializer_drift, False, "PROMPT_SERIALIZER_MISMATCH"))
    batch_key_missing = _fixture_v11(); batch_key_missing["batch_key"]["platform"] = ""
    cases.append(("Q13_batch_key", batch_key_missing, False, "SUBMISSION_FIELD_MISSING"))
    malformed_compile = _fixture_v11(); malformed_compile["variants"][0]["canonical_timeline"]["beats"][0] = "not-an-object"; malformed_compile["variants"][0].pop("renderings", None)
    cases.append(("Q14_malformed_compile", compile_document(malformed_compile), False, "TIMELINE_INVALID"))
    missing_base = _fixture_v11(); del missing_base["variants"][0]["canonical_timeline"]["beats"][0]["actor"]; _recompile_v11(missing_base, 0)
    cases.append(("Q15_missing_base_beat_field", missing_base, False, "BEAT_QUALITY_FIELD_MISSING"))
    invalid_enums = _fixture_v11(); invalid_enums["variants"][0]["canonical_timeline"]["beats"][0]["purpose"] = "purchase"; invalid_enums["variants"][0]["canonical_timeline"]["beats"][0]["product_visibility"] = "macro"; _recompile_v11(invalid_enums, 0)
    cases.append(("Q16_beat_enums", invalid_enums, False, "BEAT_ENUM_INVALID"))
    action_dense = _fixture_v11(); action_dense["variants"][0]["canonical_timeline"]["beats"][2]["motion_budget"]["active_hands"] = "one"; _recompile_v11(action_dense, 0)
    cases.append(("Q17_shot_action_density", action_dense, False, "SHOT_ACTION_DENSITY_EXCEEDED"))

    cases.append(("D2", _fixture(style_b=0), False, "DIFF_COLOR_ONLY"))
    t2 = _fixture(); t2["variants"][1]["renderings"]["script"]["beats"][0]["spoken_line"] = "paraphrased"
    cases.append(("T2", t2, False, "TIMELINE_VO_DRIFT"))
    m1 = _fixture(); m1["variants"][1]["renderings"]["prompt"]["reference_ids"] = [m1["variants"][0]["references"][0]["reference_id"]]
    cases.append(("M1", m1, False, "VARIANT_REFERENCE_MISMATCH"))
    m2 = _fixture()
    shared_text = m2["variants"][0]["renderings"]["prompt"]["compiled_text"] + "\n" + m2["variants"][1]["renderings"]["prompt"]["compiled_text"]
    shared_hash = hashlib.sha256(shared_text.encode("utf-8")).hexdigest()
    for variant in m2["variants"]:
        variant["renderings"]["prompt"]["compiled_text"] = shared_text
        variant["renderings"]["prompt"]["compiled_text_sha256"] = shared_hash
    cases.append(("M2", m2, False, "PROMPT_REUSED_ACROSS_VARIANTS"))
    r2 = _fixture(); r2["variants"][1]["garment_signature"]["neckline"] = "crew-neck"
    cases.append(("R2", r2, False, "BATCH_NOT_SAME_SKU"))
    gap = _fixture()
    gap["variants"][1]["canonical_timeline"]["beats"][1]["start_seconds"] = 5
    for name, key in (("script", "beats"), ("broll", "shots"), ("prompt", "beats")):
        gap["variants"][1]["renderings"][name][key][1]["start_seconds"] = 5
    cases.append(("timeline_gap", gap, False, "TIMELINE_GAP"))
    reports = []
    for name, fixture, expected_valid, expected_error in cases:
        actual = validate(fixture)
        passed = actual["valid"] == expected_valid and (expected_error is None or actual["primary_error"] == expected_error)
        reports.append({"name": name, "passed": passed, "expected_valid": expected_valid, "expected_primary_error": expected_error, "actual_primary_error": actual["primary_error"]})
    historical_gate = validate(_fixture_v11())
    historical_gate_passed = historical_gate.get("valid") is True and historical_gate.get("eligible_for_new_submission") is False
    reports.append({
        "name": "historical_schema_not_submission_eligible", "passed": historical_gate_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if historical_gate_passed else "SUBMISSION_ELIGIBILITY_MISMATCH",
    })
    motion_gate = validate(_fixture_v12())
    motion_gate_passed = motion_gate.get("valid") is True and motion_gate.get("eligible_for_new_submission") is False
    reports.append({
        "name": "motion_1_2_historical_not_submission_eligible", "passed": motion_gate_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if motion_gate_passed else "SUBMISSION_ELIGIBILITY_MISMATCH",
    })
    evidence_gate = validate(_fixture_v13())
    evidence_gate_passed = evidence_gate.get("valid") is True and evidence_gate.get("eligible_for_new_submission") is False
    reports.append({
        "name": "evidence_1_3_historical_not_submission_eligible", "passed": evidence_gate_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if evidence_gate_passed else "SUBMISSION_ELIGIBILITY_MISMATCH",
    })
    legacy_v4_gate = validate(_fixture_v13_legacy_v4())
    legacy_v4_gate_passed = (
        legacy_v4_gate.get("valid") is True
        and legacy_v4_gate.get("eligible_for_new_submission") is False
        and legacy_v4_gate.get("historical_prompt_v4_read_only") is True
    )
    reports.append({
        "name": "evidence_1_3_v4_historical_not_submission_eligible", "passed": legacy_v4_gate_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if legacy_v4_gate_passed else "SUBMISSION_ELIGIBILITY_MISMATCH",
    })
    streaming_source = _fixture_v14(market="US")
    streaming_plan = _streaming_plan_from_compile(streaming_source)
    streaming_module = _load_streaming_plan_module()
    second_planned_variant = copy.deepcopy(
        _streaming_plan_from_compile(_fixture_v13())["planned_variants"][1]
    )
    second_planned_variant["generation_controls"] = copy.deepcopy(
        streaming_source["variants"][0]["generation_controls"]
    )
    second_scene_strategy = second_planned_variant["generation_controls"]["scene_strategy"]
    second_planned_variant["creative_delta"]["scene_id"] = second_scene_strategy["primary_scene_id"]
    second_planned_variant["creative_delta"]["styling_signature"] = second_scene_strategy["outfit_answer"]
    second_deadlines = copy.deepcopy(streaming_source["variants"][0]["three_layer_deadlines"])
    second_asset_deadlines = second_deadlines["asset_deadlines"]
    second_asset_deadlines["color_name"] = second_planned_variant["color_name"]
    second_asset_deadlines["garment_identity"] = (
        f"The same {second_planned_variant['color_name']} apparel SKU remains stable across every shot."
    )
    second_asset_deadlines["garment_signature"] = copy.deepcopy(second_planned_variant["garment_signature"])
    second_asset_deadlines["scene_strategy"] = copy.deepcopy(second_scene_strategy)
    second_asset_deadlines["outfit"] = second_scene_strategy["outfit_answer"]
    second_planned_variant["deadline_blueprint"] = streaming_module.deadline_blueprint_from_deadlines(
        second_deadlines
    )
    streaming_plan["planned_variants"].append(second_planned_variant)
    streaming_plan["planned_variant_count"] = len(streaming_plan["planned_variants"])
    streaming_plan["differentiation_plan_sha256"] = streaming_module.canonical_sha256(
        streaming_module._planned_payload(streaming_plan)
    )
    with tempfile.TemporaryDirectory(prefix="zibuyu-streaming-self-test-") as temp_dir:
        plan_path = Path(temp_dir) / "batch-plan.json"
        three_view_path = Path(temp_dir) / "black-three-view.png"
        three_view_path.write_bytes(b"self-test-three-view-file")
        three_view_sha256 = hashlib.sha256(three_view_path.read_bytes()).hexdigest()
        plan_raw = json.dumps(streaming_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        plan_path.write_bytes(plan_raw)
        plan_sha256 = hashlib.sha256(plan_raw).hexdigest()
        streaming_release = copy.deepcopy(streaming_source)
        streaming_release["compile_mode"] = "single_compile"
        streaming_release["variants"] = [streaming_release["variants"][0]]
        streaming_release["variants"][0]["three_view_path"] = str(three_view_path.resolve())
        streaming_release["variants"][0]["three_view_sha256"] = three_view_sha256
        streaming_release["variants"][0]["references"][0]["local_path"] = str(three_view_path.resolve())
        streaming_release["variants"][0]["references"][0]["sha256"] = three_view_sha256
        streaming_audit = _zero_human_identity_audit(three_view_sha256)
        streaming_release["variants"][0]["references"][0]["identity_cue_audit"] = streaming_audit
        streaming_release["variants"][0]["references"][0]["identity_cue_audit_sha256"] = _canonical_sha256(
            streaming_audit)
        streaming_release["streaming_release"] = {
            "contract_id": streaming_plan["contract_id"],
            "plan_path": str(plan_path.resolve()),
            "plan_sha256": plan_sha256,
            "variant_id": streaming_release["variants"][0]["variant_id"],
            "child_run_id": (
                f"{streaming_plan['batch_run_id']}:"
                f"{streaming_release['variants'][0]['variant_id']}"
            ),
        }
        streaming_release = compile_document(streaming_release)
        streaming_result = validate(streaming_release)
        streaming_passed = streaming_result.get("valid") is True and streaming_result.get("eligible_for_new_submission") is True
        reports.append({
            "name": "streaming_release_integration_positive", "passed": streaming_passed,
            "expected_valid": True, "expected_primary_error": None,
            "actual_primary_error": None if streaming_passed else streaming_result.get("primary_error"),
        })
        streaming_drift = copy.deepcopy(streaming_release)
        streaming_drift["variants"][0]["caption"] = "Unplanned replacement caption."
        drift_result = validate(streaming_drift)
        drift_codes = {item.get("code") for item in drift_result.get("errors", [])}
        drift_passed = drift_result.get("valid") is False and "STREAM_RELEASE_PLAN_DRIFT" in drift_codes
        reports.append({
            "name": "streaming_release_integration_drift", "passed": drift_passed,
            "expected_valid": False, "expected_primary_error": "STREAM_RELEASE_PLAN_DRIFT",
            "actual_primary_error": None if drift_passed else drift_result.get("primary_error"),
        })
    golden = _fixture_v11()
    golden_variant = golden["variants"][0]
    golden_text = golden_variant["renderings"]["prompt"]["compiled_text"]
    golden_contract = (
        "@Image1 = garment identity only: preserve exact color, silhouette, neckline, sleeve construction, "
        "hem, seams, texture scale, thickness, opacity and drape. Replace its white triptych, dividers, "
        "repeated or multiple bodies, reference face or hair, pose, camera, environment, text and watermark "
        "with one creator wearing the same garment in one continuous real-world scene."
    )
    golden_global = (
        "Global: TikTok; 9:16; 15s; 1080x1920; authentic UGC phone video. One creator wears the same garment "
        "throughout. Persistent upper-left fit-stats overlay: exact text \"5'6\\\" / 115 lb / Size S\" from 0-15s, "
        "with no 'Model' label; place it in the upper-left safe area, clearly visible but not covering the face, hands, "
        "body, garment, or product proof; use clean white text with a subtle dark outline or translucent dark strip. "
        "Clean screen otherwise: no generated text, subtitles, watermark, arrows, stickers, icons, badges or UI overlays. "
        "Keep the same creator, outfit, scene and light across shots; two natural connected arms and hands, at most one active hand."
    )
    golden_passed = (
        golden_text.splitlines()[0] == golden_contract
        and golden_text.splitlines()[1] == golden_global
        and [line for line in golden_text.splitlines() if line.startswith("Shot ")]
        == ["Shot 1 (0-4s):", "Shot 2 (4-10s):", "Shot 3 (10-15s):"]
        and "On-camera dialogue: \"Love it? Tap the link in the lower left.\"" in golden_text
        and "the selected construction detail remains centered and unobstructed" in golden_text
        and "setup-qa" not in golden_text and "beat_id" not in golden_text
        and golden_variant["quality_plan"]["directing_intent"] not in golden_text
        and _canonical_json(golden_variant["canonical_timeline"]["beats"]) not in golden_text
    )
    reports.append({
        "name": "quality_1_1_serializer_golden", "passed": golden_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if golden_passed else "SERIALIZER_GOLDEN_MISMATCH",
    })
    motion_golden = _fixture_v12()
    motion_variant = motion_golden["variants"][0]
    motion_prompt = motion_variant["renderings"]["prompt"]
    motion_text = motion_prompt["compiled_text"]
    motion_passed = (
        motion_prompt["serializer_id"] == PROMPT_V3_SERIALIZER_ID
        and "exactly two natural arms connected shoulder-to-wrist-to-hand" in motion_text
        and "Actions occur sequentially, one garment target at a time" in motion_text
        and "complete a new product-proof or styling action about every 2-3 seconds" in motion_text
        and "natural acceleration/deceleration" in motion_text
        and "micro-movements never replace proof" in motion_text
        and [line for line in motion_text.splitlines() if line.startswith("Shot ")]
        == [
            "Shot 1 (0-2.5s):", "Shot 2 (2.5-5s):", "Shot 3 (5-7.5s):",
            "Shot 4 (7.5-10.5s):", "Shot 5 (10.5-15s):",
        ]
        and "Focus: neckline; action type point_trace; evidence visible_reference." in motion_text
        and "Hand plan: left starts left relaxed side" in motion_text
        and "the right index finger traces the neckline once and moves clear" in motion_text
        and "ma-setup" not in motion_text and "claim_proof_id" not in motion_text and "beat_id" not in motion_text
        and motion_variant["quality_plan"]["directing_intent"] not in motion_text
        and _canonical_json(motion_variant["canonical_timeline"]["beats"]) not in motion_text
    )
    reports.append({
        "name": "motion_1_2_serializer_golden", "passed": motion_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if motion_passed else "SERIALIZER_GOLDEN_MISMATCH",
    })
    evidence_golden = _fixture_v13()
    evidence_prompt = evidence_golden["variants"][0]["renderings"]["prompt"]
    evidence_text = evidence_prompt["compiled_text"]
    evidence_cadence_passed = (
        evidence_prompt["serializer_id"] == PROMPT_V5_SERIALIZER_ID
        and evidence_text.splitlines()[0].startswith("Reference 1 / @Image1 = fixed-model identity only")
        and evidence_text.splitlines()[1].startswith("Reference 2 / @Image2 = garment identity only")
        and "human_identity_pixels_absent: true" in evidence_text.splitlines()[1]
        and "skin tone, ethnicity, neck, chest, collarbone, shoulders, arms, hands, fingers, nails, tattoos, jewelry, body shape" in evidence_text.splitlines()[1]
        and "Use native direct-response commerce cadence" in evidence_text
        and "while completing a new product-proof or styling action about every 2-3 seconds" in evidence_text
        and "one continuous friend-to-friend recommendation" in evidence_text
        and "each visible endpoint flows into the next motivated action" in evidence_text
        and "the filming hand continuously holds the phone" in evidence_text
        and "Do not snap to attention, symmetrically reset both hands to the hips" in evidence_text
        and "natural acceleration/deceleration" in evidence_text
        and "realistic garment lag/settling" in evidence_text
        and "micro-movements never replace proof" in evidence_text
        and [line for line in evidence_text.splitlines() if line.startswith("Shot ")]
        == [
            "Shot 1 — Macro phase recognition/result Hook (0-2.5s; continuous creator flow). Performance: warm recognition; direct eye contact; friendly certainty.",
            "Shot 2 — Macro phase animated proof (2.5-10.5s; continuous creator flow). Performance: energy rises; brighter certainty; emphasize proof pivots.",
            "Shot 3 — Macro phase close/detail conviction (10.5-15s; continuous creator flow). Performance: pleased conviction or relief; sincere verdict; warm close.",
        ]
        and evidence_text.count("internal beat:") == 6
        and "Continuity: inherit the prior gaze, weight, hand occupancy, prop ownership and garment state" in evidence_text
    )
    reports.append({
        "name": "evidence_1_3_commerce_cadence_serializer_golden", "passed": evidence_cadence_passed,
        "expected_valid": True, "expected_primary_error": None,
        "actual_primary_error": None if evidence_cadence_passed else "SERIALIZER_GOLDEN_MISMATCH",
    })

    # Append schema-1.4 coverage after the historical 125-case matrix so its
    # ordering and primary-error expectations stay byte-for-byte reviewable.
    v14_cases: list[tuple[str, dict[str, Any], bool, str | None]] = [
        ("market_1_4_us_technical_positive", _fixture_v14(market="US"), True, None),
        (
            "market_1_4_us_compact_positive",
            _fixture_v14(
                market="US", prompt_shell_mode="us_compact_storyboard",
                commerce_cta_mode="none", verdict_mode="ownership_verdict",
            ),
            True, None,
        ),
        (
            "market_1_4_de_live_positive",
            _fixture_v14(market="DE", delivery_mode="de_live_simple"),
            True, None,
        ),
        (
            "market_1_4_de_hybrid_positive",
            _fixture_v14(market="DE", delivery_mode="de_hybrid_proof"),
            True, None,
        ),
    ]
    v14_bad_tuple = _fixture_v14(market="US")
    v14_bad_tuple["batch_key"]["model_preset"] = "德1"
    v14_cases.append(("market_1_4_closed_tuple", v14_bad_tuple, False, "MODEL_PRESET_MARKET_MISMATCH"))
    v14_bad_review = _fixture_v14(market="US")
    v14_bad_review["variants"][0]["voiceover_review"][0]["market_line"] = "drift"
    v14_cases.append(("market_1_4_voiceover_review_projection", v14_bad_review, False, "VOICEOVER_REVIEW_DRIFT"))
    v14_bad_blocks = _fixture_v14(market="US")
    for beat, setup_id in zip(
        v14_bad_blocks["variants"][0]["canonical_timeline"]["beats"],
        ("a", "a", "b", "b", "a", "a"),
    ):
        beat["camera_setup_id"] = setup_id
    v14_bad_blocks = compile_document(v14_bad_blocks)
    v14_cases.append(("market_1_4_cut_block_a_b_a", v14_bad_blocks, False, "CUT_BLOCK_SEQUENCE_INVALID"))
    v14_serializer_drift = _fixture_v14(market="US")
    v14_serializer_drift["variants"][0]["renderings"]["prompt"]["serializer_id"] = PROMPT_V5_SERIALIZER_ID
    v14_cases.append(("market_1_4_serializer_downgrade", v14_serializer_drift, False, "PROMPT_SERIALIZER_MISMATCH"))
    v14_missing_identity_audit = _fixture_v14(market="US")
    v14_missing_identity_audit["variants"][0]["references"][0].pop("identity_cue_audit")
    v14_cases.append((
        "market_1_4_zero_human_identity_audit_required",
        v14_missing_identity_audit, False, "THREE_VIEW_IDENTITY_AUDIT_INVALID",
    ))
    v14_hands_present = _fixture_v14(market="US")
    v14_hands_reference = v14_hands_present["variants"][0]["references"][0]
    v14_hands_reference["identity_cue_audit"]["hands_fingers_nails_present"] = True
    v14_hands_reference["identity_cue_audit_sha256"] = _canonical_sha256(
        v14_hands_reference["identity_cue_audit"])
    v14_cases.append((
        "market_1_4_human_cue_overrides_passed_summary",
        v14_hands_present, False, "THREE_VIEW_IDENTITY_AUDIT_INVALID",
    ))
    v14_wrong_audited_bytes = _fixture_v14(market="US")
    v14_wrong_bytes_reference = v14_wrong_audited_bytes["variants"][0]["references"][0]
    v14_wrong_bytes_reference["identity_cue_audit"]["audited_sha256"] = "0" * 64
    v14_wrong_bytes_reference["identity_cue_audit_sha256"] = _canonical_sha256(
        v14_wrong_bytes_reference["identity_cue_audit"])
    v14_cases.append((
        "market_1_4_identity_audit_binds_exact_bytes",
        v14_wrong_audited_bytes, False, "THREE_VIEW_IDENTITY_AUDIT_INVALID",
    ))
    for name, value, expected_valid, expected_error in v14_cases:
        actual = validate(value)
        actual_codes = {item.get("code") for item in actual.get("errors", [])}
        passed = actual.get("valid") is expected_valid and (
            expected_error is None or expected_error in actual_codes
        )
        reports.append({
            "name": name, "passed": passed,
            "expected_valid": expected_valid, "expected_primary_error": expected_error,
            "actual_primary_error": None if passed else actual.get("primary_error"),
        })
    failed = [report for report in reports if not report["passed"]]
    if failed:
        return _result([_error("SELF_TEST_FAILED", "--self-test", "one or more self-tests failed", failed=failed)], mode="self-test", tests=reports, passed=len(reports) - len(failed), failed=len(failed))
    return _result([], mode="self-test", tests=reports, passed=len(reports), failed=0)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("json_source", nargs="?", help="JSON file path, inline JSON, or - for stdin")
    parser.add_argument("--self-test", action="store_true", help="run in-memory test matrix")
    parser.add_argument("--compile", action="store_true", help="rebuild renderings and hashes from canonical timelines before validation")
    parser.add_argument("--output", help="write the compiled document to this JSON path; requires --compile")
    try:
        args = parser.parse_args(argv)
        if args.self_test:
            if args.json_source is not None or args.compile or args.output is not None:
                raise ValueError("--self-test does not accept json_source, --compile, or --output")
            result = run_self_test()
        else:
            if args.json_source is None:
                raise ValueError("json_source is required")
            if args.output is not None and not args.compile:
                raise ValueError("--output requires --compile")
            source = args.json_source
            if source == "-":
                source_bytes = sys.stdin.buffer.read()
                payload = source_bytes.decode("utf-8-sig")
            elif source.lstrip().startswith(("{", "[")):
                payload = source
                source_bytes = payload.encode("utf-8")
            else:
                source_bytes = Path(source).read_bytes()
                payload = source_bytes.decode("utf-8-sig")
            document = json.loads(payload)
            if args.compile:
                document = compile_document(document)
                compiled_bytes = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                if args.output is not None:
                    output_path = Path(args.output)
                    output_path.write_bytes(compiled_bytes)
                result = validate(document)
                result["compiled"] = True
                result["compiled_output"] = str(Path(args.output).resolve()) if args.output is not None else None
                batch_compile_sha256 = hashlib.sha256(compiled_bytes).hexdigest()
            else:
                result = validate(document)
                batch_compile_sha256 = hashlib.sha256(source_bytes).hexdigest()
            _attach_director_receipts(result, document, batch_compile_sha256)
    except (OSError, UnicodeError) as exc:
        result = _result([_error("INPUT_READ_ERROR", "$", str(exc))])
    except (json.JSONDecodeError, ValueError) as exc:
        result = _result([_error("INVALID_JSON", "$", str(exc))])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
