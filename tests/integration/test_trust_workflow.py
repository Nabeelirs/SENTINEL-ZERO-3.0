"""Actual reviewed NISAR artifacts, never synthetic scientific acceptance."""
from io import BytesIO
import json
import os
from pathlib import Path

import pytest
from pypdf import PdfReader

from sentinel_zero.application.data import configured_paths,load_explorer
from sentinel_zero.evidence.earthbom import build_earthbom,evaluate
from sentinel_zero.evidence.lab import EvidenceLab,ARTIFACT_ID
from sentinel_zero.science.preparation import sha256_file


@pytest.fixture(scope="module")
def real_evidence():
    source = os.environ.get("SENTINEL_ZERO_SOURCE_ROOT")
    if not source: pytest.skip("Actual NISAR runtime required")
    paths = configured_paths({"SENTINEL_ZERO_RUNTIME_ROOT":str(Path(source).parent)})
    data = load_explorer(paths)
    return data,paths,build_earthbom(data,paths)


@pytest.mark.real_data
def test_actual_earthbom_exact_edges_and_trust_workflow(real_evidence,tmp_path):
    data,paths,bom = real_evidence
    assert len(bom.nodes) == 17
    sources = {n.id for n in bom.nodes if n.kind == "Harmony_subset"}
    assert len(sources) == 6
    assert set(bom.node("prep:common_valid").parents) == sources
    assert set(bom.node("analysis:statistics").parents) == {"analysis:change","prep:common_valid"}
    assert set(bom.node("analysis:manifest").parents) == {"prep:manifest","analysis:change","analysis:statistics","analysis:diagnostics","analysis:preview"}
    assert all(n.integrity == "MATCH" for n in bom.nodes if n.sha256 is not None)
    assert all(n.integrity == "UNVERIFIABLE" for n in bom.nodes if n.kind == "NASA_catalog_identity")
    base = paths.analysis/"change_statistics.json"
    digest = sha256_file(base)
    lab = EvidenceLab.create(tmp_path/"sessions",base,bom.node(ARTIFACT_ID).sha256)
    assert evaluate(bom)["failed_artifacts"] == []
    modified = lab.challenge()
    status = evaluate(bom,{ARTIFACT_ID:modified["state"]})
    assert status["failed_artifacts"] == [ARTIFACT_ID]
    assert status["requires_revalidation"] == ["analysis:manifest","report:passport"]
    assert "analysis:change" not in status["requires_revalidation"]
    restored = lab.restore()
    status = evaluate(bom,{ARTIFACT_ID:restored["state"]})
    assert status["failed_artifacts"] == status["requires_revalidation"] == []
    assert status["scientific_quality"] == "CONDITIONAL_PROVISIONAL"
    assert sha256_file(base) == digest


@pytest.mark.real_data
def test_actual_failed_and_recovered_passports_have_correct_scope(real_evidence,tmp_path):
    from sentinel_zero.reporting.investigation import investigation_pdf
    data,paths,bom = real_evidence
    lab = EvidenceLab.create(tmp_path/"sessions",paths.analysis/"change_statistics.json",bom.node(ARTIFACT_ID).sha256)
    for action in (lab.challenge,lab.restore):
        event = action()
        trust = evaluate(bom,{ARTIFACT_ID:event["state"]})
        trust["demonstration"] = dict(state=event["state"],events=lab.events())
        trust["witness"] = dict(status="INSUFFICIENT_PHYSICAL_EVIDENCE",reason="Original environmental export unavailable")
        content = investigation_pdf(data,(157,309),3,trust=trust)
        reader = PdfReader(BytesIO(content))
        assert len(reader.pages) == 4
        text = "\n".join(p.extract_text() for p in reader.pages)
        assert trust["local_integrity"] in text and "Evidence Trust Passport" in text
        assert "not the challenged copy" in text and "-26.151365280" in text
        assert event["state"] in text and "UNVERIFIED" in text
        if event["state"] == "MISMATCH":
            assert "analysis:manifest" in text and "report:passport" in text
        assert "C:/Users/" not in text and "C:\\Users\\" not in text
    invalid = evaluate(bom);invalid["scientific_quality"] = "FULLY_VALIDATED"
    with pytest.raises(ValueError): investigation_pdf(data,trust=invalid)


@pytest.mark.real_data
def test_new_actual_ui_challenge_graph_recovery_and_report(real_evidence,tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    data,paths,bom = real_evidence
    monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT",str(paths.source.parent))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT",raising=False)
    monkeypatch.setenv("SENTINEL_ZERO_LAB_ROOT",str(tmp_path/"sessions"))
    at = AppTest.from_file(Path(__file__).resolve().parents[2]/"app.py",default_timeout=40).run()
    assert not at.exception
    at.radio(key="stage").set_value("Challenge Evidence").run()
    next(b for b in at.button if b.label == "Create isolated evidence copy").click().run()
    assert not at.exception
    lab=at.session_state["evidence_lab"]
    assert lab.verify()["state"] == "MATCH"
    next(b for b in at.button if b.label == "Modify disposable copy").click().run()
    assert not at.exception and lab.verify()["state"] == "MISMATCH"
    at.radio(key="stage").set_value("Trace Consequences").run()
    assert not at.exception and len(at.get("plotly_chart")) == 1
    assert at.selectbox[0].value == ARTIFACT_ID
    assert any("FAILED" in x.value for x in at.markdown)
    at.radio(key="stage").set_value("Restore & Report").run()
    assert not at.exception and at.session_state["last_passport_receipt"]["local_integrity"] == "FAILED"
    failed_pdf=at.session_state["last_passport_receipt"]["sha256"]
    next(b for b in at.button if b.label == "Restore and Revalidate").click().run()
    assert not at.exception and lab.verify()["state"] == "MATCH"
    assert at.session_state["last_passport_receipt"]["local_integrity"] == "CONSISTENT_WITH_LOCAL_REFERENCES"
    assert at.session_state["last_passport_receipt"]["sha256"] != failed_pdf
    assert len(at.get("download_button")) == 2  # No selected cell, PDF + original full technical JSON.


def test_global_workspace_is_usable_without_any_local_rasters(monkeypatch,tmp_path):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT",str(tmp_path/"unavailable"))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT",raising=False)
    at=AppTest.from_file(Path(__file__).resolve().parents[2]/"app.py",default_timeout=30).run()
    assert at.error  # Pilot properly fails closed.
    next(b for b in at.button if b.label == "Global Earth Explorer").click().run()
    assert not at.exception and not at.error
    assert at.radio(key="observe_workspace").value == "Global NISAR discovery"
    assert any(x.value == "Global Earth Explorer" for x in at.subheader)
