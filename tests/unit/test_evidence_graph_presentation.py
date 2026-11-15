"""Synthetic DAG fixtures test presentation only, never NASA scientific evidence."""
from dataclasses import replace

import pytest

from sentinel_zero.evidence.earthbom import Artifact, EarthBOM, evaluate
from sentinel_zero.presentation.evidence_graph import graph_figure, graph_model


def artifact(identity, parents=(), kind="analysis_output", integrity="MATCH"):
    return Artifact(identity, identity, kind, parents, "a" * 64 if integrity == "MATCH" else None,
                    1 if integrity == "MATCH" else None, integrity, {"fixture": "synthetic"})


@pytest.fixture
def synthetic_bom():
    return EarthBOM((
        artifact("scene", kind="NASA_catalog_identity", integrity="UNVERIFIABLE"),
        artifact("subset", ("scene",), "Harmony_subset"),
        artifact("support", ("subset",), "eligibility_output"),
        artifact("change", ("subset", "support")),
        artifact("statistics", ("change", "support")),
        artifact("diagnostics", ("change",)),
        artifact("manifest", ("statistics", "diagnostics"), "provenance_manifest"),
        artifact("passport", ("manifest", "statistics"), "runtime_report", "GENERATED_ON_REQUEST"),
    ), "synthetic-test-only", ("Not satellite evidence",))


def test_focused_lens_has_only_actual_selected_parents_and_descendants(synthetic_bom):
    model = graph_model(synthetic_bom, evaluate(synthetic_bom), "statistics")
    assert {node.id for node in model.nodes} == {"change", "support", "statistics", "manifest", "passport"}
    assert model.parents == ("change", "support")
    assert model.descendants == ("manifest", "passport")
    assert {(edge.parent, edge.child) for edge in model.edges} == {
        ("support", "change"), ("change", "statistics"), ("support", "statistics"),
        ("statistics", "manifest"), ("statistics", "passport"), ("manifest", "passport"),
    }
    assert {n.id: n.relation for n in model.nodes} == {
        "support": "parent", "change": "parent", "statistics": "selected", "manifest": "downstream", "passport": "downstream",
    }


def test_complete_graph_preserves_every_recorded_edge_and_identity_scope(synthetic_bom):
    model = graph_model(synthetic_bom, evaluate(synthetic_bom), "statistics", full_record=True)
    assert len(model.nodes) == len(synthetic_bom.nodes)
    assert {(edge.parent, edge.child) for edge in model.edges} == {
        (parent, node.id) for node in synthetic_bom.nodes for parent in node.parents}
    by_id = {node.id: node for node in model.nodes}
    assert by_id["scene"].status == "IDENTITY ONLY"
    assert by_id["scene"].integrity == "UNVERIFIABLE"
    assert by_id["passport"].status == "GENERATED ON EXPORT"
    assert by_id["subset"].category == "Source subset"
    assert by_id["manifest"].category == "Manifest"
    assert all(by_id[e.parent].x < by_id[e.child].x for e in model.edges)
    assert len({(node.x, node.y) for node in model.nodes}) == len(model.nodes)


def test_integrity_failure_and_real_downstream_flags_remain_separate(synthetic_bom):
    state = evaluate(synthetic_bom, {"statistics": "MISMATCH"})
    model = graph_model(synthetic_bom, state, "statistics")
    nodes = {node.id: node for node in model.nodes}
    assert nodes["statistics"].status == "INTEGRITY FAILURE"
    assert nodes["manifest"].status == nodes["passport"].status == "REVALIDATE"
    assert nodes["change"].status == nodes["support"].status == "LOCAL MATCH"
    assert state["scientific_quality"] == "CONDITIONAL_PROVISIONAL"
    assert state["environmental_interpretation"] == "UNVERIFIED"
    assert {e.child for e in model.edges if e.status == "INTEGRITY FAILURE"} == {"manifest", "passport"}


def test_missing_copy_has_failure_styling_without_inventing_a_match(synthetic_bom):
    state = evaluate(synthetic_bom, {"statistics": "MISSING_OR_UNSAFE"})
    node = next(n for n in graph_model(synthetic_bom, state, "statistics").nodes if n.id == "statistics")
    assert node.status == "INTEGRITY FAILURE" and node.integrity == "MISSING_OR_UNSAFE"


def test_model_does_not_mutate_evidence_or_state(synthetic_bom):
    state = evaluate(synthetic_bom)
    before = synthetic_bom.record()
    original_state = {key: dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value for key, value in state.items()}
    first = graph_model(synthetic_bom, state, "statistics")
    assert first == graph_model(synthetic_bom, state, "statistics")
    assert synthetic_bom.record() == before and state == original_state


@pytest.mark.parametrize("case", ("unknown_selection", "missing_status", "unknown_status", "unknown_revalidation"))
def test_inconsistent_graph_state_is_rejected(synthetic_bom, case):
    state = evaluate(synthetic_bom)
    selected = "statistics"
    if case == "unknown_selection":
        selected = "fabricated-node"
    elif case == "missing_status":
        state["artifact_integrity"].pop("subset")
    elif case == "unknown_status":
        state["artifact_integrity"]["subset"] = "SCIENTIFICALLY_CONFIRMED"
    else:
        state["requires_revalidation"].append("fabricated-node")
    with pytest.raises(ValueError):
        graph_model(synthetic_bom, state, selected)


def test_plotly_canvas_is_based_on_recorded_model_and_escapes_labels(synthetic_bom):
    nodes = tuple(replace(n, label="<unsafe label>") if n.id == "statistics" else n for n in synthetic_bom.nodes)
    bom = replace(synthetic_bom, nodes=nodes)
    model = graph_model(bom, evaluate(bom), "statistics")
    figure = graph_figure(model)
    assert len(figure.data) == len(model.edges) + 1
    assert len(figure.layout.shapes) == len(model.nodes)
    labels = " ".join(a.text or "" for a in figure.layout.annotations)
    assert "&lt;unsafe label&gt;" in labels and "<unsafe label>" not in labels
    assert figure.layout.dragmode == "pan"
    assert figure.layout.xaxis.visible is False and figure.layout.yaxis.visible is False
    assert figure.layout.xaxis.range[0] < min(n.x for n in model.nodes)
    assert "LOCAL MATCH" in labels
