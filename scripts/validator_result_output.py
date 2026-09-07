"""Persist complete validator evidence while keeping optional stdout summaries small."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


class ResultOutputError(ValueError):
    """The requested result artifact could not be safely produced."""


def validate_output_options(summary, result_file, protected_paths=()):
    if summary and not result_file:
        raise ResultOutputError("--summary requires --result-file to preserve complete validation evidence")
    if not result_file:
        return None
    try:
        target = Path(result_file).expanduser().resolve()
        for source in protected_paths:
            if source is None:
                continue
            source = Path(source).expanduser().resolve()
            if target == source or (target.exists() and source.exists() and target.samefile(source)):
                raise ResultOutputError(f"Result file collides with a validation input or compiled output: {source}")
        if target.exists() and not target.is_file():
            raise ResultOutputError(f"Result file must be a regular file: {target}")
        return target
    except (OSError, RuntimeError) as exc:
        raise ResultOutputError(f"Cannot resolve result file safely: {exc}") from exc


def document_input_paths(document):
    """Include file bindings and streaming siblings in collision protection only.

    This does not load evidence, authorize a request, or change validation rules.
    """
    paths = []
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    pending.append(child)
                elif (isinstance(child, str) and (key == "path" or key.endswith("_path"))
                      and Path(child).is_absolute()):
                    paths.append(Path(child))
                    if key == "plan_path":
                        releases = Path(child).parent / "releases"
                        paths.extend(releases.glob("*/batch-compile.json"))
                        paths.extend(releases.glob("*/ledger.json"))
        elif isinstance(value, list):
            pending.extend(item for item in value if isinstance(item, (dict, list)))
    return paths


def _error_brief(error):
    if not isinstance(error, dict):
        return {"code": "validation_error", "path": "$"}
    # Messages/details can contain the entire submitted prompt or source text.
    return {"code": str(error.get("code", "validation_error"))[:120],
            "path": str(error.get("path", "$"))[:240]}


def summary_result(result, path, digest):
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    counts = {}
    original_counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    for name, value in original_counts.items():
        if name in {"models", "artifacts", "jobs"} and type(value) is int:
            counts[name] = value
    for name in ("variant_count", "timeline_count", "pair_count"):
        if type(result.get(name)) is int:
            counts[name] = result[name]
    receipts = result.get("director_receipts")
    if isinstance(receipts, list):
        counts["director_receipts"] = len(receipts)
    wave = result.get("wave_plan") if isinstance(result.get("wave_plan"), dict) else {}
    for field in ("authorized_submission_job_keys", "authorized_outbound_requests"):
        if isinstance(wave.get(field), list):
            counts[field] = len(wave[field])
    return {
        "valid": result.get("valid") is True,
        "summary_only": True,
        "result_file": str(path),
        "result_sha256": digest,
        "counts": counts,
        "error_count": len(errors),
        "primary_error": _error_brief(result.get("primary_error") or errors[0]) if errors else None,
        "errors": [_error_brief(error) for error in errors[:20]],
        "errors_truncated": len(errors) > 20,
    }


def prepare_output(result, *, summary=False, result_file=None, protected_paths=()):
    target = validate_output_options(summary, result_file, protected_paths)
    if target is None:
        return result
    temporary = None
    try:
        raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent,
                                         prefix=".validator-result-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck aliases immediately before replacement; never overwrite an input.
        current_target = validate_output_options(summary, result_file, protected_paths)
        if current_target != target:
            raise ResultOutputError("Result file target changed while validation was running")
        os.replace(temporary, target)
        temporary = None
    except (OSError, UnicodeError, TypeError, ValueError, RuntimeError) as exc:
        raise ResultOutputError(f"Cannot persist complete validation result: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Preserve the original persistence failure and nonzero exit code.
                pass
    digest = hashlib.sha256(raw).hexdigest()
    return summary_result(result, target, digest) if summary else result
