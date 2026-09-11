#!/usr/bin/env python3
"""Build an offline, all-video production-completion snapshot for publishing.

This verifies recorded delivery/QA gates, not the video pixels or remote URLs.
Run again immediately before preparing/dispatching publication: this artifact
never grants publication authorization and is not a substitute for live checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from validate_delivery_pack import validate as validate_delivery


ARTIFACT_TYPE = "zibuyu_publish_handoff"
OUTPUT_NAME = "publish-handoff.json"


class HandoffError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise HandoffError(message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode(data, path):
    value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    require(isinstance(value, dict), f"Expected a JSON object: {path}")
    return value


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _safe_variant(value):
    # Variant IDs become directory names on Windows as well as POSIX.
    require(_text(value) and value not in {".", ".."}
            and value == value.strip() and not value.endswith(".")
            and not any(c in value for c in '/\\:<>"|?*')
            and not any(ord(c) < 32 for c in value),
            f"Unsafe or missing variant_id: {value!r}")
    return value


def _resolve_root(run_dir):
    requested = Path(run_dir)
    require(requested.is_absolute(), "run_dir must be an absolute directory path")
    requested = requested.resolve(strict=True)
    require(requested.is_dir(), "run_dir must be a directory")
    # Resolve the parent even when a child has a missing or malformed compile.
    if requested.parent.name == "releases":
        return requested.parent.parent, True
    if (requested / "batch-plan.json").exists():
        return requested, True
    # Noncanonical streaming locations fail the barrier check below. Do not read
    # the compile before invalidating the old snapshot: malformed inputs must
    # not leave a previous successful handoff behind.
    return requested, False


def _invalidate_previous(root):
    output = root / OUTPUT_NAME
    require(not output.is_symlink(), f"Refusing symlink output: {output}")
    if output.exists():
        require(output.is_file(), f"Output is not a regular file: {output}")
        previous = _decode(output.read_bytes(), output)
        require(previous.get("artifact_type") == ARTIFACT_TYPE,
                f"Refusing to overwrite an unrelated existing file: {output}")
        output.unlink()
    return output


def _source(path, root, sources, role):
    resolved = path.resolve(strict=True)
    require(resolved.is_relative_to(root), f"Production source escapes run directory: {path}")
    require(resolved.is_file(), f"Missing production file: {path}")
    data = resolved.read_bytes()
    sources.append({"role": role, "path": str(resolved),
                    "sha256": hashlib.sha256(data).hexdigest()})
    return _decode(data, resolved), sources[-1]["sha256"]


def _file_identity(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _local_video(item, job, sources):
    if not item.get("local_video_path"):
        return {}
    path = Path(item["local_video_path"])
    require(path.is_absolute(), f"{item['variant_id']}: local video path must be absolute")
    path = path.resolve(strict=True)
    actual_sha, actual_bytes = _file_identity(path)
    download = job["download"]
    require(download.get("sha256") == actual_sha,
            f"{item['variant_id']}: local video SHA-256 changed or was never recorded; repeat original QA/delivery")
    require(isinstance(download.get("bytes"), int) and not isinstance(download.get("bytes"), bool)
            and download["bytes"] == actual_bytes,
            f"{item['variant_id']}: local video bytes mismatch or missing recorded size")
    sources.append({"role": "local_video", "path": str(path), "sha256": actual_sha, "bytes": actual_bytes})
    return {"local_video_path": str(path), "local_video_sha256": actual_sha, "local_video_bytes": actual_bytes}


def _variant_list(document, field):
    variants = document.get(field)
    require(isinstance(variants, list) and variants, f"{field} must be a non-empty list")
    result = {}
    for variant in variants:
        require(isinstance(variant, dict), f"Every {field} entry must be an object")
        variant_id = _safe_variant(variant.get("variant_id"))
        require(variant_id not in result, f"Duplicate intended variant: {variant_id}")
        result[variant_id] = variant
    return result


def _release(directory, root, sources, plan=None, plan_sha=None, planned_variant=None):
    batch, compile_sha = _source(directory / "batch-compile.json", root, sources, "batch_compile")
    ledger, _ = _source(directory / "ledger.json", root, sources, "ledger")
    require(ledger.get("batch_compile_sha256") == compile_sha,
            f"Compile SHA-256 drift or missing binding in {directory / 'ledger.json'}")
    variants = _variant_list(batch, "variants")
    require(_text(ledger.get("run_id")), f"Missing run_id: {directory}")
    batch_key = batch.get("batch_key")
    require(isinstance(batch_key, dict) and _text(batch_key.get("model_preset")),
            f"Missing batch_key.model_preset: {directory}")

    if plan is not None:
        variant_id = planned_variant["variant_id"]
        require(set(variants) == {variant_id}, f"Release must contain only planned variant {variant_id}")
        binding = batch.get("streaming_release")
        require(isinstance(binding, dict), f"Missing streaming_release for {variant_id}")
        binding_path = binding.get("plan_path")
        require(_text(binding_path) and Path(binding_path).is_absolute(),
                f"Absolute parent plan_path required for {variant_id}")
        require(Path(binding_path).resolve(strict=True) == root / "batch-plan.json",
                f"Wrong streaming parent path for {variant_id}")
        require(binding.get("plan_sha256") == plan_sha,
                f"Streaming parent SHA-256 drift for {variant_id}")
        require(binding.get("variant_id") == variant_id
                and binding.get("contract_id") == plan.get("contract_id"),
                f"Streaming release identity mismatch for {variant_id}")
        expected_run = f"{plan['batch_run_id']}:{variant_id}"
        require(binding.get("child_run_id") == expected_run and ledger.get("run_id") == expected_run,
                f"Streaming child run mismatch for {variant_id}")
        for field in ("batch_compile_id", "sku_family_id", "batch_key"):
            require(batch.get(field) == plan.get(field), f"Parent/release {field} mismatch for {variant_id}")
        for field in ("color_name", "caption", "hashtags"):
            require(variants[variant_id].get(field) == planned_variant.get(field),
                    f"Parent/release {field} drift for {variant_id}")
    else:
        require("streaming_release" not in batch, "Streaming child cannot be published as a barrier batch")

    jobs = ledger.get("jobs")
    require(isinstance(jobs, list) and jobs, f"Missing jobs: {directory}")
    seen_variants = set()
    for job in jobs:
        require(isinstance(job, dict), f"Job must be an object: {directory}")
        variant_id = _safe_variant(job.get("variant_id"))
        require(variant_id not in seen_variants,
                f"Multiple outputs/retakes for {variant_id}; resolve the final production selection explicitly")
        seen_variants.add(variant_id)
        require(variant_id in variants, f"Unplanned job variant: {variant_id}")
        require(job.get("state") == "succeeded", f"{variant_id}: every intended video must be succeeded")
        require(job.get("rendered_quality_status") == "keep",
                f"{variant_id}: recorded rendered QA must be keep; complete original video QA first")
        require(job.get("completion_reported") is True,
                f"{variant_id}: complete video, QA verdict and copy delivery before publishing")
        require(job.get("video_url_status") in {"usable", "missing"},
                f"{variant_id}: video location is not recorded usable/missing; refresh the existing record first")
        require(_text(job.get("color_name")) and job.get("color_name") == variants[variant_id].get("color_name"),
                f"{variant_id}: missing or mismatched color_name")
        require(job.get("model_preset") == batch_key["model_preset"],
                f"{variant_id}: model_preset does not match the compile")
        receipt = job.get("director_receipt")
        if receipt is not None:
            require(isinstance(receipt, dict) and receipt.get("batch_compile_sha256") == compile_sha,
                    f"{variant_id}: director receipt compile hash drift")
    require(seen_variants == set(variants), f"Missing intended videos: {sorted(set(variants) - seen_variants)}")

    delivery = validate_delivery(batch, ledger, batch_compile_sha256=compile_sha)
    require(delivery.get("valid") is True,
            f"Delivery validator failed for {directory}: {json.dumps(delivery.get('errors'), ensure_ascii=False)}")
    items = []
    for item, job in zip(delivery["items"], jobs):
        require(item.get("local_video_path") or job.get("video_url_status") == "usable",
                f"{item['variant_id']}: video location is not recorded usable; refresh the existing record first")
        local_identity = _local_video(item, job, sources)
        items.append({**item, **local_identity, "run_id": ledger["run_id"],
                      "video_url": item["video_url"] if job.get("video_url_status") == "usable" else None,
                      "batch_compile_path": str((directory / "batch-compile.json").resolve()),
                      "ledger_path": str((directory / "ledger.json").resolve())})
    return items, ledger["run_id"], batch["sku_family_id"]


def build(run_dir):
    root, streaming = _resolve_root(run_dir)
    output = _invalidate_previous(root)
    sources, items = [], []
    if streaming:
        plan, plan_sha = _source(root / "batch-plan.json", root, sources, "batch_plan")
        require(plan.get("plan_locked") is True, "Streaming parent plan must be locked")
        require(plan.get("contract_id") in {"zibuyu_quality_gated_streaming_v1", "zibuyu_quality_gated_streaming_v2"},
                "Unrecognized streaming plan contract")
        require(_text(plan.get("batch_run_id")), "Missing parent batch_run_id")
        planned = _variant_list(plan, "planned_variants")
        require(plan.get("planned_variant_count") == len(planned), "Parent planned variant count mismatch")
        require(not (root / "parent-closure.json").exists(),
                "Parent closure/migration exists; reconcile every migrated color before creating a handoff")
        release_root = root / "releases"
        require(release_root.is_dir(), "Missing releases directory; finish every planned color first")
        for child in release_root.iterdir():
            if child.is_dir() and ((child / "ledger.json").exists() or (child / "batch-compile.json").exists()):
                require(child.name in planned, f"Unplanned release {child.name}; resolve batch scope explicitly")
        for variant_id, variant in planned.items():
            release_items, _, _ = _release(release_root / variant_id, root, sources, plan, plan_sha, variant)
            items.extend(release_items)
        run_id, sku = plan["batch_run_id"], plan.get("sku_family_id")
    else:
        items, run_id, sku = _release(root, root, sources)
    record_ids = [item["record_id"] for item in items]
    require(len(set(record_ids)) == len(record_ids), "One record_id maps to multiple intended videos; resolve the ambiguity")
    # Prevent a concurrent source change during validation from producing a mixed snapshot.
    for source in sources:
        digest, size = _file_identity(Path(source["path"]))
        require(digest == source["sha256"] and ("bytes" not in source or size == source["bytes"]),
                f"Source changed while building; rerun: {source['path']}")
    result = {
        "artifact_type": ARTIFACT_TYPE, "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_only": True, "revalidate_before_publication": True,
        "readiness": True, "publication_authorized": False,
        "qa_basis": "recorded_original_workflow_verdicts; media and remote availability are not rechecked",
        "run_dir": str(root), "run_id": run_id, "sku_family_id": sku,
        "workflow": "streaming_parent" if streaming else "barrier",
        "item_count": len(items), "sources": sources, "items": items,
    }
    stable_content = {key: value for key, value in result.items() if key != "created_at"}
    result["handoff_sha256"] = hashlib.sha256(json.dumps(
        stable_content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                         prefix=".publish-handoff-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        require(not output.exists() and not output.is_symlink(), "Output appeared during validation; rerun")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return {"valid": True, "readiness": True, "publication_authorized": False,
            "output": str(output), "item_count": len(items), "run_dir": str(root),
            "handoff_sha256": result["handoff_sha256"]}


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Absolute barrier run or streaming parent/child directory")
    args = parser.parse_args(argv)
    try:
        result = build(args.run_dir)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        result = {"valid": False, "readiness": False, "publication_authorized": False,
                  "error": str(exc), "instruction": "Do not use any previous handoff; finish or repair original production, then rerun."}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
