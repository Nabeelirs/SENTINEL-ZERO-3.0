"""Copy a frozen publication manifest into a clean public-main clone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

CONTROL_FILE = "publication_manifest.json"
MANIFEST_VERSION = "SZ-010A-v5"
ADDED_FILES = (
    "LICENSE",
    "tools/package_publication_candidate.py",
    "tests/unit/test_publication_packager.py",
)
REMOVED_FILES = {
    "tools/prepare_publication.py": "superseded v1 helper allowed screenshots and private evidence",
    "tests/unit/test_publication_preparation.py": "superseded v1 helper test requires excluded screenshots and private evidence",
}
PUBLIC_METADATA_FILES = {
    "docs/evidence/sz002/collections.json",
    "docs/evidence/sz002/kelani_provisional_granules.json",
    "docs/evidence/sz002/kelani_validated_granules.json",
    "docs/evidence/sz002/requests.json",
    "docs/evidence/sz002/source_checks.json",
    "docs/evidence/sz002/validation.json",
    "docs/evidence/sz003/kelani_provisional_reviewed.json",
    "docs/evidence/sz004/harmony_capabilities.json",
    "docs/evidence/sz009/brazil_amazon_catalog.json",
    "docs/evidence/sz009/india_brahmaputra_catalog.json",
    "docs/evidence/sz009/sri_lanka_catalog.json",
}
APPROVED_CHANGED_FILES = {
    "LICENSING.md",
    "README.md",
    "docs/evidence-integrity.md",
    "docs/nasa-data-sources.md",
    "docs/release-status.md",
}
PROHIBITED_PREFIXES = ("docs/assets/",)
SKIP_DIRS = {
    ".git", ".venv", "runtime", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "build", "dist",
}
PROHIBITED_SUFFIXES = {
    ".zip", ".tif", ".tiff", ".h5", ".hdf5", ".nc", ".vrt", ".sqlite", ".db",
    ".pem", ".key", ".p12", ".pfx", ".log", ".png", ".jpg", ".jpeg", ".gif", ".webp",
}
TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".json", ".toml", ".ini", ".cfg", ".css", ".html",
    ".mjs", ".js", ".yml", ".yaml",
}
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}
CLAIM_RISK_PATTERNS = {
    "accuracy-or-guarantee": re.compile(
        r"\b(?:\d+(?:\.\d+)?%\s+(?:accuracy|accurate)|guaranteed\s+(?:accuracy|detection|"
        r"classification|results|performance|coverage)|guarantees\s+(?:accuracy|detection|"
        r"classification|results|performance|coverage)|"
        r"proven\s+(?:worldwide|accurate|accuracy))\b",
        re.IGNORECASE,
    ),
    "causal-certainty": re.compile(
        r"\b(?:proves?|demonstrates?)\s+(?:that\s+)?(?:flood(?:ing)?|deforestation|"
        r"causation|the\s+cause)\b",
        re.IGNORECASE,
    ),
}


class PackageError(Exception):
    """Raised when a candidate cannot be assembled without changing scope."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise PackageError(f"Invalid manifest path: {value!r}")
    parts = value.split("/")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or re.match(r"^[A-Za-z]:", value)
        or any(part in ("", ".", "..") for part in parts)
        or ":" in value
    ):
        raise PackageError(f"Unsafe manifest path: {value!r}")
    return path


def contained_regular_file(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PackageError(f"Symlink path is not allowed: {relative.as_posix()}")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PackageError(f"Path escapes its root: {relative.as_posix()}") from exc
    if not resolved.is_file():
        raise PackageError(f"Not a regular source file: {relative.as_posix()}")
    return resolved


def validate_manifest(source_root: Path, record: dict) -> tuple[list[dict], list[str], list[str]]:
    rows = record.get("files")
    if not isinstance(rows, list) or not rows:
        raise PackageError("Manifest has no files list")
    seen: set[str] = set()
    prepared: list[dict] = []
    missing: list[str] = []
    changed: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise PackageError("Manifest entry is not an object")
        name = row.get("path")
        relative = safe_relative_path(name)
        if name in seen:
            raise PackageError(f"Duplicate manifest path: {name}")
        seen.add(name)
        if is_prohibited_member(name, relative):
            raise PackageError(f"Prohibited scientific/private file in allowlist: {name}")
        try:
            source = contained_regular_file(source_root, relative)
        except FileNotFoundError:
            missing.append(name)
            continue
        except PackageError as exc:
            changed.append(f"{name}: {exc}")
            continue
        source_hash = row.get("source_sha256")
        if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise PackageError(f"Invalid source_sha256 in manifest: {name}")
        before_hash = sha256_file(source)
        if before_hash != source_hash:
            changed.append(f"{name}: source SHA-256 {before_hash} != {source_hash}")
            continue
        transformation = row.get("transformation")
        if transformation != "IDENTICAL":
            raise PackageError(f"Unsupported manifest transformation for {name}: {transformation!r}")
        payload = source.read_bytes()
        if sha256_file(source) != before_hash:
            changed.append(f"{name}: source changed while being read")
            continue
        payload_hash = hashlib.sha256(payload).hexdigest()
        if payload_hash != row.get("sha256") or len(payload) != row.get("bytes"):
            changed.append(f"{name}: transformed bytes/hash do not match frozen manifest")
            continue
        prepared.append({"path": name, "source": source, "payload": payload, "sha256": payload_hash})
    return prepared, missing, changed


def is_prohibited_member(name: str, relative: PurePosixPath | None = None) -> bool:
    relative = relative or safe_relative_path(name)
    lowered = name.lower()
    return (
        any(lowered.startswith(prefix) for prefix in PROHIBITED_PREFIXES)
        or (lowered.startswith("docs/evidence/") and name not in PUBLIC_METADATA_FILES)
        or name in REMOVED_FILES
        or relative.suffix.lower() in PROHIBITED_SUFFIXES
        or any(part.lower() in {".git", ".venv", "runtime", "private", "internal"} for part in relative.parts)
        or relative.name.lower() in {".env", "secrets.toml", "credentials.json"}
    )


def manifest_row(source_root: Path, name: str) -> dict:
    relative = safe_relative_path(name)
    if is_prohibited_member(name, relative):
        raise PackageError(f"Prohibited addition: {name}")
    path = contained_regular_file(source_root, relative)
    digest = sha256_file(path)
    return {
        "path": name,
        "bytes": path.stat().st_size,
        "sha256": digest,
        "source_sha256": digest,
        "transformation": "IDENTICAL",
    }


def build_manifest_data(
    source_root: Path,
    original: dict,
    original_sha256: str,
    base_commit: str,
    added_paths: tuple[str, ...] = ADDED_FILES,
    changed_paths: set[str] = APPROVED_CHANGED_FILES,
) -> dict:
    old_rows = original.get("files")
    if not isinstance(old_rows, list) or not old_rows:
        raise PackageError("Original manifest has no files list")
    old_by_name = {row.get("path"): row for row in old_rows if isinstance(row, dict)}
    if len(old_by_name) != len(old_rows):
        raise PackageError("Original manifest contains invalid or duplicate entries")
    kept: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    for name, old_row in old_by_name.items():
        relative = safe_relative_path(name)
        if is_prohibited_member(name, relative):
            if name.startswith("docs/assets/"):
                reason = "unverified mission-derived screenshots and imagery excluded"
            elif name.startswith("docs/evidence/"):
                reason = "private acceptance and scientific evidence excluded"
            elif name in REMOVED_FILES:
                reason = REMOVED_FILES[name]
            else:
                reason = "unverified scientific or sensitive asset excluded"
            removed.append({"path": name, "old_sha256": old_row.get("sha256"), "reason": reason})
            continue
        path = contained_regular_file(source_root, relative)
        actual_source_hash = sha256_file(path)
        expected_source_hash = old_row.get("source_sha256")
        if name in changed_paths and actual_source_hash != expected_source_hash:
            new_row = manifest_row(source_root, name)
            changed.append({
                "path": name,
                "old_sha256": old_row.get("sha256"),
                "new_sha256": new_row["sha256"],
                "reason": "reviewed publication/licensing clarification or asset-boundary update",
            })
            kept.append(new_row)
            continue
        if actual_source_hash != expected_source_hash:
            raise PackageError(
                f"Unapproved source change for {name}: {actual_source_hash} != {expected_source_hash}"
            )
        transformation = old_row.get("transformation")
        if transformation != "IDENTICAL":
            raise PackageError(f"Unsupported frozen transformation for {name}")
        output_hash = actual_source_hash
        output_bytes = path.stat().st_size
        if output_hash != old_row.get("sha256") or output_bytes != old_row.get("bytes"):
            raise PackageError(f"Frozen output hash/size changed for {name}")
        kept.append(dict(old_row))
    missing_approved_changes = changed_paths - set(old_by_name)
    if missing_approved_changes:
        raise PackageError(f"Expected changed paths are absent from original manifest: {sorted(missing_approved_changes)}")
    additions: list[dict] = []
    for name in added_paths:
        if name in old_by_name:
            raise PackageError(f"Addition already existed in frozen manifest: {name}")
        additions.append(manifest_row(source_root, name))
    kept.extend(additions)
    kept.sort(key=lambda row: row["path"])
    return {
        "schema_version": "1.0",
        "manifest_version": MANIFEST_VERSION,
        "task": "SZ-010A",
        "base_public_main_commit": base_commit,
        "baseline_manifest_sha256": original_sha256,
        "publication_status": "LOCAL CANDIDATE ONLY; no commit, push, merge or deployment authorized",
        "scientific_archive_included": False,
        "mission_derived_screenshots_included": False,
        "private_evidence_included": False,
        "public_metadata_fixtures_not_covered_by_mit": sorted(PUBLIC_METADATA_FILES),
        "exclusion_policy": [
            "Exclude all docs/assets screenshots and mission-derived imagery pending rights clearance.",
            "Exclude private acceptance/scientific evidence; retain only enumerated public CMR/Harmony metadata JSON test fixtures.",
            "Exclude the superseded v1 publisher helper and its evidence-dependent test.",
            "Exclude raw scientific rasters, archives, runtime data, logs, credentials and private files.",
        ],
        "changes_from_original_manifest": {
            "added": [
                {"path": row["path"], "new_sha256": row["sha256"], "reason": "explicit SZ-010A addition"}
                for row in additions
            ],
            "removed": sorted(removed, key=lambda row: row["path"]),
            "changed": sorted(changed, key=lambda row: row["path"]),
        },
        "files": kept,
    }


def create_manifest(args: argparse.Namespace) -> dict:
    source_root = args.source.resolve(strict=True)
    original_path = args.original_manifest.resolve(strict=True)
    if original_path.is_symlink():
        raise PackageError("Original manifest may not be a symlink")
    original_sha256 = sha256_file(original_path)
    if original_sha256 != args.expected_original_sha256.lower():
        raise PackageError(
            f"Original manifest digest mismatch: {original_sha256} != {args.expected_original_sha256}"
        )
    original = json.loads(original_path.read_text(encoding="utf-8"))
    data = build_manifest_data(source_root, original, original_sha256, args.base_commit)
    output = args.output.resolve(strict=False)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite versioned manifest: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
    return {
        "manifest_path": str(output),
        "manifest_sha256": sha256_file(output),
        "manifest_version": data["manifest_version"],
        "payload_files": len(data["files"]),
        "added": len(data["changes_from_original_manifest"]["added"]),
        "removed": len(data["changes_from_original_manifest"]["removed"]),
        "changed": len(data["changes_from_original_manifest"]["changed"]),
    }


def run_git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()


def verify_clean_main_clone(candidate: Path, expected_base: str) -> None:
    top = Path(run_git(candidate, "rev-parse", "--show-toplevel")).resolve()
    if top != candidate:
        raise PackageError("Candidate path is not the Git worktree root")
    if run_git(candidate, "branch", "--show-current") != "main":
        raise PackageError("Candidate clone must be on main before overlay")
    actual_head = run_git(candidate, "rev-parse", "HEAD")
    if actual_head != expected_base:
        raise PackageError(f"Candidate base {actual_head} != expected public main {expected_base}")
    if run_git(candidate, "status", "--porcelain", "--untracked-files=all"):
        raise PackageError("Candidate clone is not clean")
    remote = run_git(candidate, "remote", "get-url", "origin")
    parsed = urlparse(remote)
    if parsed.hostname != "github.com" or parsed.path.rstrip("/").removesuffix(".git").lower() != "/nabeelirs/sentinel-zero-3.0":
        raise PackageError("Candidate origin is not the approved SENTINEL ZERO public repository")


def safe_destination(root: Path, name: str) -> Path:
    relative = safe_relative_path(name)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise PackageError(f"Candidate parent is a symlink: {name}")
    target = root.joinpath(*relative.parts)
    if target.exists() and target.is_symlink():
        raise PackageError(f"Candidate target is a symlink: {name}")
    return target


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sz010a-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def exact_files(root: Path) -> set[str]:
    found: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = current / name
            if path.is_symlink():
                raise PackageError(f"Symlink found in candidate: {path.relative_to(root).as_posix()}")
            found.add(path.relative_to(root).as_posix())
    return found


def enumerate_excluded(source_root: Path, allowed: set[str]) -> tuple[list[dict], list[str]]:
    excluded: list[dict] = []
    skipped: list[str] = []
    for directory, dirnames, filenames in os.walk(source_root, topdown=True, followlinks=False):
        current = Path(directory)
        kept = []
        for name in dirnames:
            path = current / name
            relative = path.relative_to(source_root).as_posix()
            if name in SKIP_DIRS or path.is_symlink():
                skipped.append(f"{relative}/**")
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            relative = path.relative_to(source_root).as_posix()
            if relative in allowed:
                continue
            reason = "symlink not followed" if path.is_symlink() else "not present in frozen manifest"
            if relative.startswith("docs/evidence/"):
                reason = "private acceptance/scientific evidence excluded"
            if path.suffix.lower() in PROHIBITED_SUFFIXES:
                reason = "prohibited scientific/private asset; excluded"
            excluded.append({"path": relative, "reason": reason})
    return excluded, sorted(skipped)


def scan_candidate(root: Path, username: str) -> dict:
    secret_hits: list[dict] = []
    privacy_hits: list[dict] = []
    claim_hits: list[dict] = []
    link_errors: list[dict] = []
    profile_patterns = []
    if username:
        profile_patterns = [
            re.compile(rf"(?i)(?:[A-Z]:)?[/\\]+Users[/\\]+{re.escape(username)}(?:[/\\]|\b)"),
            re.compile(rf"(?i)/Users/{re.escape(username)}(?:/|\b)"),
        ]
    markdown_links = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                secret_hits.append({"path": relative, "pattern": label})
        for pattern in profile_patterns:
            if pattern.search(text):
                privacy_hits.append({"path": relative, "pattern": "current-user-profile-path"})
                break
        if path.suffix.lower() == ".md":
            for match in markdown_links.finditer(text):
                target = match.group(1).strip().split()[0].strip("<>")
                if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
                    continue
                target_path = unquote(target.split("#", 1)[0].split("?", 1)[0])
                if not target_path:
                    continue
                resolved = (path.parent / target_path).resolve()
                try:
                    resolved.relative_to(root.resolve())
                except ValueError:
                    link_errors.append({"path": relative, "target": target, "reason": "link escapes candidate"})
                    continue
                if not resolved.exists():
                    link_errors.append({"path": relative, "target": target, "reason": "target missing"})
        if path.suffix.lower() in {".md", ".txt"}:
            for label, pattern in CLAIM_RISK_PATTERNS.items():
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    claim_hits.append({"path": relative, "line": line, "pattern": label})
    return {
        "secret_pattern_hits": secret_hits,
        "current_user_path_hits": privacy_hits,
        "documentation_link_errors": link_errors,
        "scientific_claim_risk_hits": claim_hits,
        "text_scan_scope": "UTF-8-readable allowlisted text only; binary/OCR and advisory scans not certified",
    }


def validate_license(source_root: Path, record: dict, candidate: Path) -> dict:
    listed = any(row.get("path") == "LICENSE" for row in record["files"])
    source_license = source_root / "LICENSE"
    source_hash = sha256_file(source_license) if source_license.is_file() else None
    text = source_license.read_text(encoding="utf-8") if source_license.is_file() else ""
    required_clauses = (
        "MIT License\n",
        "Copyright (c) 2026 Nabeelirs",
        "Permission is hereby granted, free of charge",
        "The above copyright notice and this permission notice shall be included",
        "THE SOFTWARE IS PROVIDED \"AS IS\"",
    )
    draft_is_mit = all(clause in text for clause in required_clauses)
    return {
        "in_frozen_manifest": listed,
        "source_draft_exists": source_license.is_file(),
        "source_draft_sha256": source_hash,
        "source_draft_matches_expected_mit_text": draft_is_mit,
        "candidate_contains_LICENSE": (candidate / "LICENSE").is_file(),
        "status": "included in frozen payload" if listed else "PROPOSED ADDITION: owner/contributor approval required",
    }


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def package(args: argparse.Namespace) -> dict:
    source_root = args.source.resolve(strict=True)
    candidate = args.candidate.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    report_path = args.report.resolve(strict=False)
    if candidate == source_root or candidate.is_relative_to(source_root):
        raise PackageError("Candidate must be separate from and outside the development workspace")
    if report_path.is_relative_to(source_root) or report_path.is_relative_to(candidate):
        raise PackageError("Report must be outside the source workspace and candidate clone")
    manifest_hash = sha256_file(manifest_path)
    if args.expected_manifest_sha256 and manifest_hash != args.expected_manifest_sha256.lower():
        raise PackageError(f"Manifest digest mismatch: {manifest_hash} != {args.expected_manifest_sha256}")
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    if record.get("scientific_archive_included") is not False:
        raise PackageError("Manifest does not explicitly exclude the scientific archive")
    if manifest_path.is_symlink():
        raise PackageError("Manifest file may not be a symlink")
    rows = record.get("files")
    allowed = {row.get("path") for row in rows if isinstance(row, dict)}
    excluded, skipped = enumerate_excluded(source_root, allowed)
    report = {
        "task": "SZ-010A",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "candidate_root": str(candidate),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_hash,
        "manifest_file_count": len(rows),
        "expected_base": args.expected_base,
        "copied": [],
        "excluded": excluded,
        "excluded_directories_not_traversed": skipped,
        "missing": [],
        "changed": [],
        "additional_files_requiring_approval": [],
        "checks": {},
    }
    report["license"] = validate_license(source_root, record, candidate)
    report["checks"]["license_approval_complete"] = report["license"]["in_frozen_manifest"]
    report["checks"]["candidate_license_present"] = report["license"]["candidate_contains_LICENSE"]
    if not report["license"]["in_frozen_manifest"]:
        report["additional_files_requiring_approval"].append({
            "path": "LICENSE",
            "reason": "present as a draft in the development workspace but absent from the frozen 197-file manifest",
            "sha256": report["license"]["source_draft_sha256"],
        })
        if not any(item.get("path") == "LICENSE" for item in report["excluded"]):
            report["excluded"].append({"path": "LICENSE", "reason": "not copied; frozen allowlist may not be expanded automatically"})
    prepared, missing, changed = validate_manifest(source_root, record)
    report["missing"] = missing
    report["changed"] = changed
    if missing or changed or len(prepared) != len(rows):
        report["checks"]["source_manifest_validation"] = False
        report["package_status"] = "BLOCKED"
        return report
    verify_clean_main_clone(candidate, args.expected_base)
    required = {
        "README.md", "public_app.py", "pyproject.toml", "requirements.txt",
        "requirements-ui.txt", "LICENSING.md", "THIRD_PARTY_NOTICES.md",
    }
    listed = {item["path"] for item in prepared}
    report["checks"]["required_source_members_present"] = required <= listed
    report["checks"]["scientific_archive_excluded_by_manifest"] = record["scientific_archive_included"] is False
    report["checks"]["prohibited_assets_absent_from_allowlist"] = not any(
        is_prohibited_member(name) for name in listed
    )
    if not report["checks"]["required_source_members_present"] or not report["checks"]["prohibited_assets_absent_from_allowlist"]:
        report["checks"]["source_manifest_validation"] = False
        report["package_status"] = "BLOCKED"
        return report
    for item in prepared:
        target = safe_destination(candidate, item["path"])
        atomic_write(target, item["payload"])
        if sha256_file(target) != item["sha256"]:
            raise PackageError(f"Candidate read-back hash mismatch: {item['path']}")
        report["copied"].append({"path": item["path"], "sha256": item["sha256"]})
    manifest_target = safe_destination(candidate, CONTROL_FILE)
    if CONTROL_FILE in listed:
        raise PackageError("Control manifest is already a payload path")
    atomic_write(manifest_target, manifest_path.read_bytes())
    report["copied"].append({"path": CONTROL_FILE, "sha256": manifest_hash, "role": "control record per publication plan"})
    expected_names = listed | {CONTROL_FILE}
    actual_names = exact_files(candidate)
    report["checks"]["candidate_file_inventory_exact"] = actual_names == expected_names
    report["unexpected_candidate_files"] = sorted(actual_names - expected_names)
    report["missing_candidate_files"] = sorted(expected_names - actual_names)
    report["checks"]["sha256_readback"] = all(
        sha256_file(candidate / item["path"]) == item["sha256"] for item in prepared
    ) and sha256_file(manifest_target) == manifest_hash
    report["checks"]["expected_payload_count"] = len(prepared) == len(rows)
    report["expected_candidate_file_count_including_control"] = len(expected_names)
    report["license"] = validate_license(source_root, record, candidate)
    report["content_scans"] = scan_candidate(candidate, os.environ.get("USERNAME", ""))
    report["checks"]["security_secret_scan_clear"] = not report["content_scans"]["secret_pattern_hits"]
    report["checks"]["privacy_profile_path_scan_clear"] = not report["content_scans"]["current_user_path_hits"]
    report["checks"]["documentation_links_clear"] = not report["content_scans"]["documentation_link_errors"]
    report["checks"]["scientific_claim_scan_clear"] = not report["content_scans"]["scientific_claim_risk_hits"]
    report["checks"]["license_approval_complete"] = report["license"]["in_frozen_manifest"]
    report["checks"]["license_text_valid"] = report["license"]["source_draft_matches_expected_mit_text"]
    report["checks"]["candidate_license_present"] = report["license"]["candidate_contains_LICENSE"]
    report["checks"]["third_party_notices_present"] = (
        "THIRD_PARTY_NOTICES.md" in actual_names
        and any(name.startswith("docs/submission/licenses/") for name in actual_names)
    )
    report["checks"]["application_entry_point_present"] = "public_app.py" in actual_names
    report["checks"]["readme_and_dependency_files_present"] = {
        "README.md", "pyproject.toml", "requirements.txt", "requirements-ui.txt"
    } <= actual_names
    report["package_status"] = "PACKAGED_FOR_REVIEW" if all(
        report["checks"].get(key, False) for key in (
            "candidate_file_inventory_exact", "sha256_readback", "expected_payload_count",
            "security_secret_scan_clear", "privacy_profile_path_scan_clear",
            "documentation_links_clear", "scientific_claim_scan_clear",
            "license_approval_complete", "license_text_valid", "candidate_license_present",
            "third_party_notices_present", "application_entry_point_present",
            "readme_and_dependency_files_present",
        )
    ) else "BLOCKED"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-manifest")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--original-manifest", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--base-commit", required=True)
    create.add_argument("--expected-original-sha256", required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--source", type=Path, required=True)
    assemble.add_argument("--manifest", type=Path, required=True)
    assemble.add_argument("--candidate", type=Path, required=True)
    assemble.add_argument("--report", type=Path, required=True)
    assemble.add_argument("--expected-base", required=True)
    assemble.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create-manifest":
            result = create_manifest(args)
            print(json.dumps(result, indent=2))
            return 0
        result = package(args)
        write_report(args.report, result)
        print(json.dumps({
            "package_status": result.get("package_status", "BLOCKED"),
            "manifest_file_count": result.get("manifest_file_count"),
            "copied_file_count": len(result.get("copied", [])),
            "missing_count": len(result.get("missing", [])),
            "changed_count": len(result.get("changed", [])),
            "report": str(args.report),
        }, indent=2))
        return 0 if result.get("package_status") == "PACKAGED_FOR_REVIEW" else 2
    except (OSError, ValueError, PackageError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        failure = {
            "task": "SZ-010A",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "package_status": "BLOCKED",
            "error": str(exc),
            "copied": [],
            "excluded": [],
            "missing": [],
            "changed": [],
        }
        write_report(args.report, failure)
        print(json.dumps(failure, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())