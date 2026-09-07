#!/usr/bin/env python3
"""Validate and materialize the final Zibuyu video/caption delivery pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit


HASHTAG_RE = re.compile(r"^#[^\s#]+$")
TERMINAL_STATES = {"succeeded", "failed"}


class DuplicateKeyError(ValueError):
    pass


def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path):
    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        object_pairs_hook=_object_no_duplicates,
    )


def _is_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _canonical_hash(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_tag(value):
    return unicodedata.normalize("NFKC", value).casefold()


def _tag_identity(value):
    return "".join(
        char for char in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if char.isalnum()
    )


def _valid_hashtag(value):
    if _tag_identity(value) == "imilybela":
        return False
    return bool(HASHTAG_RE.fullmatch(value))


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _error(code, path, message):
    return {"code": code, "path": path, "message": message}


def validate(batch, ledger):
    errors = []
    for field in ("batch_compile_id", "sku_family_id"):
        batch_value = batch.get(field)
        ledger_value = ledger.get(field)
        if not isinstance(batch_value, str) or not batch_value.strip():
            errors.append(_error("DELIVERY_BINDING_MISSING", f"$batch.{field}", f"batch {field} is required"))
        if not isinstance(ledger_value, str) or not ledger_value.strip():
            errors.append(_error("DELIVERY_BINDING_MISSING", f"$ledger.{field}", f"ledger {field} is required"))
        if (
            isinstance(batch_value, str)
            and batch_value.strip()
            and isinstance(ledger_value, str)
            and ledger_value.strip()
            and batch_value != ledger_value
        ):
            errors.append(_error("DELIVERY_BINDING_MISMATCH", f"$ledger.{field}", f"ledger {field} does not match batch compile"))

    variants = batch.get("variants")
    jobs = ledger.get("jobs")
    if not isinstance(variants, list) or not variants:
        errors.append(_error("VARIANTS_INVALID", "$batch.variants", "non-empty variants are required"))
        variants = []
    if not isinstance(jobs, list) or not jobs:
        errors.append(_error("JOBS_INVALID", "$ledger.jobs", "non-empty jobs are required"))
        jobs = []

    variant_map = {}
    for index, variant in enumerate(variants):
        path = f"$batch.variants[{index}]"
        if not isinstance(variant, dict):
            errors.append(_error("VARIANT_INVALID", path, "variant must be an object"))
            continue
        variant_id = variant.get("variant_id")
        if not isinstance(variant_id, str) or not variant_id:
            errors.append(_error("VARIANT_ID_INVALID", path + ".variant_id", "variant_id is required"))
            continue
        if variant_id in variant_map:
            errors.append(_error("VARIANT_DUPLICATE", path + ".variant_id", "variant_id must be unique"))
        variant_map[variant_id] = variant

    items = []
    job_keys = set()
    for index, job in enumerate(jobs):
        path = f"$ledger.jobs[{index}]"
        if not isinstance(job, dict):
            errors.append(_error("JOB_INVALID", path, "job must be an object"))
            continue
        job_key = job.get("job_key")
        if not isinstance(job_key, str) or not job_key.strip():
            errors.append(_error("DELIVERY_JOB_KEY_INVALID", path + ".job_key", "job_key is required"))
        elif job_key in job_keys:
            errors.append(_error("DELIVERY_JOB_KEY_DUPLICATE", path + ".job_key", "job_key must be unique"))
        else:
            job_keys.add(job_key)
        variant_id = job.get("variant_id")
        variant = variant_map.get(variant_id)
        if variant is None:
            errors.append(_error("DELIVERY_VARIANT_MISSING", path + ".variant_id", "job variant is missing from batch compile"))
            continue
        state = job.get("state")
        if state not in TERMINAL_STATES:
            errors.append(_error("DELIVERY_JOB_NONTERMINAL", path + ".state", "delivery requires succeeded or failed status"))

        caption = variant.get("caption")
        if not isinstance(caption, str) or not caption.strip():
            errors.append(_error("DELIVERY_CAPTION_INVALID", f"$batch.variants[{variant_id}].caption", "non-empty caption is required"))
            caption = ""
        else:
            caption = " ".join(caption.split())
            if "#" in caption:
                errors.append(_error("DELIVERY_CAPTION_HASHTAG", f"$batch.variants[{variant_id}].caption", "caption text must not contain hashtags"))

        hashtags = variant.get("hashtags")
        if not isinstance(hashtags, list) or len(hashtags) != 5:
            errors.append(_error("DELIVERY_HASHTAG_COUNT", f"$batch.variants[{variant_id}].hashtags", "exactly five hashtags are required"))
            hashtags = [] if not isinstance(hashtags, list) else hashtags
        else:
            normalized = [tag.strip() if isinstance(tag, str) else "" for tag in hashtags]
            if any(not _valid_hashtag(tag) for tag in normalized):
                errors.append(_error("DELIVERY_HASHTAG_INVALID", f"$batch.variants[{variant_id}].hashtags", "each hashtag must be a valid product-, market-, occasion-, or buyer-search-relevant #tag"))
            if len({_normalized_tag(tag) for tag in normalized}) != 5:
                errors.append(_error("DELIVERY_HASHTAG_DUPLICATE", f"$batch.variants[{variant_id}].hashtags", "hashtags must be unique"))
            hashtags = normalized

        record_id = job.get("record_id")
        video_url = job.get("video_url")
        video_url_status = job.get("video_url_status")
        download = job.get("download") if isinstance(job.get("download"), dict) else {}
        local_video_path = download.get("local_path")
        local_video_usable = (
            download.get("state") == "succeeded"
            and isinstance(local_video_path, str)
            and bool(local_video_path.strip())
            and Path(local_video_path).is_file()
        )
        remote_video_usable = _is_url(video_url) and video_url_status not in {"missing", "unusable", "expired"}
        error_code = job.get("error_code")
        error_message = job.get("error_message")
        failure_reason = error_message or error_code
        if state == "succeeded":
            if not _positive_int(record_id):
                errors.append(_error("DELIVERY_RECORD_ID_INVALID", path + ".record_id", "succeeded job requires a positive record_id"))
            if not remote_video_usable and not local_video_usable:
                errors.append(_error("DELIVERY_VIDEO_LOCATION_INVALID", path, "succeeded job requires a usable HTTP(S) URL or verified local download"))
        if state == "failed":
            video_url = None
            local_video_path = None
            if record_id is not None and not _positive_int(record_id):
                errors.append(_error("DELIVERY_RECORD_ID_INVALID", path + ".record_id", "failed job record_id must be null or a positive integer"))
            if not isinstance(failure_reason, str) or not failure_reason.strip():
                errors.append(_error("DELIVERY_FAILURE_REASON_MISSING", path, "failed job requires error_code or error_message"))

        items.append({
            "job_key": job_key,
            "variant_id": variant_id,
            "color_name": job.get("color_name"),
            "model_preset": job.get("model_preset"),
            "status": state,
            "record_id": record_id,
            "video_url": video_url if remote_video_usable and state == "succeeded" else None,
            "local_video_path": local_video_path if local_video_usable and state == "succeeded" else None,
            "failure_reason": failure_reason if state == "failed" else None,
            "caption": caption,
            "hashtags": hashtags,
            "copy_ready_caption": f"{caption} {' '.join(hashtags)}".strip(),
            "creative_quality_verdict": job.get("rendered_quality_status", "unverified"),
            "completion_reported": job.get("completion_reported") is True,
        })

    delivered_variants = {item["variant_id"] for item in items}
    missing_variants = sorted(set(variant_map) - delivered_variants)
    if missing_variants:
        errors.append(_error(
            "DELIVERY_VARIANT_MISSING",
            "$.jobs",
            f"terminal delivery is missing compiled variants: {', '.join(missing_variants)}",
        ))
    errors.sort(key=lambda item: (item["code"], item["path"]))
    if errors:
        return {"valid": False, "primary_error": errors[0], "errors": errors}
    return {
        "valid": True,
        "schema_version": "1.0",
        "run_id": ledger.get("run_id"),
        "item_count": len(items),
        "delivery_sha256": _canonical_hash(items),
        "items": items,
    }


def self_test():
    batch = {
        "batch_compile_id": "fixture-compile",
        "sku_family_id": "fixture-sku",
        "variants": [{
            "variant_id": "white",
            "caption": "Ein heller Look für jeden Tag.",
            "hashtags": ["#Sommer", "#Outfit", "#Weiss", "#Alltag", "#TikTokShopDE"],
        }]
    }
    ledger = {
        "run_id": "fixture",
        "batch_compile_id": "fixture-compile",
        "sku_family_id": "fixture-sku",
        "jobs": [{
            "job_key": "a" * 64,
            "variant_id": "white",
            "color_name": "white",
            "model_preset": "德1",
            "state": "succeeded",
            "record_id": 123,
            "video_url": "https://example.invalid/video.mp4",
            "completion_reported": False,
        }],
    }
    cases = []
    cases.append(validate(batch, ledger).get("valid") is True)
    broken = json.loads(json.dumps(batch, ensure_ascii=False))
    broken["variants"][0]["hashtags"].pop()
    cases.append(validate(broken, ledger).get("primary_error", {}).get("code") == "DELIVERY_HASHTAG_COUNT")
    broken_ledger = json.loads(json.dumps(ledger, ensure_ascii=False))
    broken_ledger["jobs"][0]["state"] = "running"
    cases.append(validate(batch, broken_ledger).get("primary_error", {}).get("code") == "DELIVERY_JOB_NONTERMINAL")
    broken_ledger = json.loads(json.dumps(ledger, ensure_ascii=False))
    broken_ledger["jobs"][0]["video_url"] = None
    cases.append(validate(batch, broken_ledger).get("primary_error", {}).get("code") == "DELIVERY_VIDEO_LOCATION_INVALID")
    broken_ledger = json.loads(json.dumps(ledger, ensure_ascii=False))
    broken_ledger["batch_compile_id"] = "wrong-compile"
    cases.append(validate(batch, broken_ledger).get("primary_error", {}).get("code") == "DELIVERY_BINDING_MISMATCH")
    broken = json.loads(json.dumps(batch, ensure_ascii=False))
    broken["variants"][0]["caption"] += " #Extra"
    cases.append(validate(broken, ledger).get("primary_error", {}).get("code") == "DELIVERY_CAPTION_HASHTAG")
    broken = json.loads(json.dumps(batch, ensure_ascii=False))
    broken["variants"][0]["hashtags"][0] = "#Imily Bela"
    cases.append(validate(broken, ledger).get("primary_error", {}).get("code") == "DELIVERY_HASHTAG_INVALID")
    broken = json.loads(json.dumps(batch, ensure_ascii=False))
    broken["variants"][0]["hashtags"][0] = "#ImilyBela"
    cases.append(validate(broken, ledger).get("primary_error", {}).get("code") == "DELIVERY_HASHTAG_INVALID")
    broken_ledger = json.loads(json.dumps(ledger, ensure_ascii=False))
    broken_ledger["jobs"].append(json.loads(json.dumps(broken_ledger["jobs"][0], ensure_ascii=False)))
    cases.append(validate(batch, broken_ledger).get("primary_error", {}).get("code") == "DELIVERY_JOB_KEY_DUPLICATE")
    return {
        "valid": all(cases),
        "self_test": {"passed": sum(cases), "failed": len(cases) - sum(cases)},
    }


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("batch_compile", nargs="?")
    parser.add_argument("ledger", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            if args.batch_compile or args.ledger:
                raise ValueError("--self-test does not accept file inputs")
            result = self_test()
        else:
            if not args.batch_compile or not args.ledger:
                raise ValueError("batch_compile and ledger paths are required")
            result = validate(_load(Path(args.batch_compile)), _load(Path(args.ledger)))
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
        error = _error("DELIVERY_INPUT_ERROR", "$", str(exc))
        result = {"valid": False, "primary_error": error, "errors": [error]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
