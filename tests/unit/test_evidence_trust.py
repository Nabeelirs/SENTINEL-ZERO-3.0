"""Synthetic software fixtures only; these are not satellite observations."""
import json
from pathlib import Path

import pytest

from sentinel_zero.evidence.earthbom import Artifact, EarthBOM, evaluate
from sentinel_zero.evidence.lab import EvidenceLab
from sentinel_zero.science.preparation import sha256_file


def node(identity,parents=(),state="MATCH"):
    return Artifact(identity,identity,"synthetic_fixture",parents,"a"*64,1,state,{})


def test_real_dag_traversal_separate_integrity_and_science():
    bom = EarthBOM((node("source"),node("derived",("source",)),node("report",("derived",)),node("other")),"test",("Provisional fixture",))
    assert bom.descendants("source") == ("derived","report")
    assert bom.descendants("report") == ()
    failed = evaluate(bom,{"derived":"MISMATCH"})
    assert failed["requires_revalidation"] == ["report"]
    assert failed["artifact_integrity"]["source"] == "MATCH"
    assert failed["scientific_quality"] == "CONDITIONAL_PROVISIONAL"
    recovered = evaluate(bom)
    assert recovered["local_integrity"] == "CONSISTENT_WITH_LOCAL_REFERENCES"
    assert recovered["environmental_interpretation"] == recovered["provider_authenticity"] == "UNVERIFIED"
    assert bom.record()["edges"] == [dict(parent="source",child="derived"),dict(parent="derived",child="report")]


@pytest.mark.parametrize("nodes",[(node("a"),node("a")),(node("a",("absent",)),),(node("a",("b",)),node("b",("a",)))])
def test_bad_graph_rejected(nodes):
    with pytest.raises(ValueError): EarthBOM(nodes,"test",())


def test_unknown_status_or_node_rejected():
    bom = EarthBOM((node("a"),),"test",())
    with pytest.raises(ValueError): evaluate(bom,{"a":"CONFIDENT"})
    with pytest.raises(ValueError): evaluate(bom,{"absent":"MATCH"})


@pytest.fixture
def lab(tmp_path):
    (tmp_path/"protected").mkdir()
    baseline = tmp_path/"protected/change_statistics.json"
    baseline.write_bytes(b'{"synthetic_fixture":true}\n')
    return EvidenceLab.create(tmp_path/"sessions",baseline,sha256_file(baseline))


def test_deterministic_challenge_restore_and_independent_baseline(lab):
    original = lab.baseline.read_bytes()
    assert lab.verify()["state"] == "MATCH"
    bad = lab.challenge()
    assert bad["state"] == "MISMATCH" and bad["observed_sha256"] != bad["expected_sha256"]
    assert lab.baseline.read_bytes() == original
    with pytest.raises(ValueError): lab.challenge()
    assert lab.restore()["state"] == "MATCH"
    assert (lab.root/"derived_copy.json").read_bytes() == original
    assert lab.challenge()["observed_sha256"] == bad["observed_sha256"]
    assert lab.restore()["state"] == "MATCH"
    assert [e["sequence"] for e in lab.events()] == list(range(1,len(lab.events())+1))
    assert "CONTROLLED_MODIFICATION" in [e["action"] for e in lab.events()]


def test_sessions_are_isolated(lab,tmp_path):
    second = EvidenceLab.create(tmp_path/"sessions",lab.baseline,lab.expected_sha256)
    lab.challenge()
    assert second.verify()["state"] == "MATCH" and second.root != lab.root


def test_changed_baseline_blocks_recovery_without_resetting_reference(lab):
    lab.baseline.write_bytes(b"synthetic changed baseline")
    with pytest.raises(ValueError,match="baseline failed"): lab.restore()
    assert lab.baseline.read_bytes() == b"synthetic changed baseline"


def test_missing_copy_is_visible_and_restorable(lab):
    (lab.root/"derived_copy.json").unlink()
    assert lab.verify()["state"] == "MISSING_OR_UNSAFE"
    assert lab.restore()["state"] == "MATCH"


def test_forged_marker_and_path_escape_rejected(lab):
    p = lab.root/"session.json"
    m = json.loads(p.read_text());m["artifact_id"] = "../../source"
    p.write_text(json.dumps(m))
    with pytest.raises(ValueError,match="marker"): lab.challenge()


def test_symlink_or_reparse_target_never_mutates_original(lab,tmp_path,monkeypatch):
    target = lab.root/"derived_copy.json"
    target.unlink()
    try: target.symlink_to(lab.baseline)
    except OSError:
        # Windows may deny symlink creation: exercise the same reparse guard
        # explicitly instead of counting an unexecuted OS fixture as a PASS.
        from types import SimpleNamespace
        target.write_bytes(lab.baseline.read_bytes())
        original_lstat = Path.lstat
        def lstat(p,*a,**kw):
            info = original_lstat(p,*a,**kw)
            return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400) if p == target else info
        monkeypatch.setattr(Path,"lstat",lstat)
    with pytest.raises(ValueError): lab.restore()
    assert sha256_file(lab.baseline) == lab.expected_sha256


def test_one_drive_parent_and_bad_hash_rejected(tmp_path):
    (tmp_path/"protected").mkdir()
    baseline = tmp_path/"protected/change_statistics.json";baseline.write_bytes(b"synthetic")
    with pytest.raises(ValueError): EvidenceLab.create(tmp_path/"OneDrive"/"sessions",baseline,sha256_file(baseline))
    with pytest.raises(ValueError): EvidenceLab.create(tmp_path/"sessions",baseline,"0"*64)


def test_source_file_and_protected_directory_are_not_challenge_targets(lab,tmp_path):
    source=tmp_path/"NASA_mask.tif";source.write_bytes(b"synthetic source fixture")
    with pytest.raises(ValueError,match="approved derived"):EvidenceLab.create(tmp_path/"sessions",source,sha256_file(source))
    with pytest.raises(ValueError,match="protected analysis"):EvidenceLab.create(lab.baseline.parent/"sessions",lab.baseline,lab.expected_sha256)
    assert not (lab.baseline.parent/"sessions").exists()
