"""Synthetic tests for the SZ-010A release packager."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import package_publication_candidate as packager


@pytest.mark.parametrize("value", ["../escape", "/absolute", "C:/absolute", "a\\b", "a//b", "a/./b"])
def test_rejects_unsafe_manifest_paths(value):
    with pytest.raises(packager.PackageError):
        packager.safe_relative_path(value)


def test_hash_mismatch_blocks_before_candidate_writes(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "README.md"
    source.write_text("synthetic fixture", encoding="utf-8")
    record = {"files": [{
        "path": "README.md",
        "source_sha256": "0" * 64,
        "sha256": "0" * 64,
        "bytes": len(b"synthetic fixture"),
        "transformation": "IDENTICAL",
    }]}
    prepared, missing, changed = packager.validate_manifest(source_root, record)
    assert prepared == []
    assert missing == []
    assert changed and "README.md" in changed[0]


def test_missing_required_source_is_reported(tmp_path):
    prepared, missing, changed = packager.validate_manifest(tmp_path, {"files": [{
        "path": "absent.py",
        "source_sha256": "0" * 64,
        "sha256": "0" * 64,
        "bytes": 0,
        "transformation": "IDENTICAL",
    }]})
    assert prepared == []
    assert missing == ["absent.py"]
    assert changed == []


def test_new_manifest_records_additions_removals_and_changed_docs(tmp_path):
    source = tmp_path / "source"
    (source / "docs" / "assets").mkdir(parents=True)
    (source / "docs" / "evidence").mkdir(parents=True)
    (source / "tools").mkdir()
    (source / "README.md").write_text("unchanged", encoding="utf-8")
    (source / "LICENSING.md").write_text("owner-approved MIT clarification", encoding="utf-8")
    (source / "docs" / "assets" / "screen.png").write_bytes(b"synthetic screenshot")
    (source / "docs" / "evidence" / "private.json").write_text("{}", encoding="utf-8")
    (source / "tools" / "new.py").write_text("# synthetic tool", encoding="utf-8")

    def row(path, content):
        import hashlib
        digest = hashlib.sha256(content).hexdigest()
        return {"path": path, "bytes": len(content), "sha256": digest,
                "source_sha256": digest, "transformation": "IDENTICAL"}

    original_rows = [
        row("README.md", b"unchanged"),
        row("LICENSING.md", b"old license note"),
        row("docs/assets/screen.png", b"synthetic screenshot"),
        row("docs/evidence/private.json", b"{}"),
    ]
    original = {"files": original_rows}
    result = packager.build_manifest_data(
        source, original, "a" * 64, "b" * 40,
        added_paths=("tools/new.py",), changed_paths={"LICENSING.md"},
    )
    changes = result["changes_from_original_manifest"]
    assert [item["path"] for item in changes["added"]] == ["tools/new.py"]
    assert [item["path"] for item in changes["removed"]] == [
        "docs/assets/screen.png", "docs/evidence/private.json"
    ]
    assert [item["path"] for item in changes["changed"]] == ["LICENSING.md"]
    assert result["mission_derived_screenshots_included"] is False


def test_manifest_validation_rejects_screenshot_and_private_evidence_paths():
    assert packager.is_prohibited_member("docs/assets/screen.png")
    assert packager.is_prohibited_member("docs/evidence/receipt.json")


def test_scientific_claim_scan_accepts_caution_and_flags_accuracy_claims(tmp_path):
    (tmp_path / "deployment.md").write_text(
        "Do not treat historical values as guaranteed cloud capacity.", encoding="utf-8"
    )
    (tmp_path / "claim.md").write_text("The method guarantees accuracy.", encoding="utf-8")
    hits = packager.scan_candidate(tmp_path, "")["scientific_claim_risk_hits"]
    assert [(hit["path"], hit["pattern"]) for hit in hits] == [
        ("claim.md", "accuracy-or-guarantee")
    ]


def test_license_check_reflects_candidate_copy(tmp_path):
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    source.mkdir()
    candidate.mkdir()
    license_text = (
        "MIT License\nCopyright (c) 2026 Nabeelirs\n"
        "Permission is hereby granted, free of charge\n"
        "The above copyright notice and this permission notice shall be included\n"
        'THE SOFTWARE IS PROVIDED "AS IS"\n'
    )
    (source / "LICENSE").write_text(license_text, encoding="utf-8")
    record = {"files": [{"path": "LICENSE"}]}
    assert packager.validate_license(source, record, candidate)["candidate_contains_LICENSE"] is False
    (candidate / "LICENSE").write_text(license_text, encoding="utf-8")
    assert packager.validate_license(source, record, candidate)["candidate_contains_LICENSE"] is True