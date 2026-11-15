"""Deterministic presentation model of recorded EarthBOM dependencies.

Layout is a view of existing relationships, never a second provenance source.
This module does not mutate evidence or infer scientific authenticity.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..evidence.earthbom import EarthBOM


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    kind: str
    category: str
    integrity: str
    status: str
    relation: str
    column: int
    x: float
    y: float


@dataclass(frozen=True)
class GraphEdge:
    parent: str
    child: str
    status: str


@dataclass(frozen=True)
class GraphModel:
    selected: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    parents: tuple[str, ...]
    descendants: tuple[str, ...]
    full_record: bool


_CATEGORIES = {
    "NASA_catalog_identity": "Catalog identity",
    "Harmony_subset": "Source subset",
    "eligibility_output": "Eligibility output",
    "provenance_manifest": "Manifest",
    "analysis_output": "Scientific output",
    "runtime_report": "Evidence Passport",
}
_INTEGRITY = {"MATCH", "MISMATCH", "MISSING_OR_UNSAFE", "UNVERIFIABLE", "GENERATED_ON_REQUEST"}


def graph_model(bom: EarthBOM, state: dict, selected: str, *, full_record: bool = False) -> GraphModel:
    """Show direct parents and actual descendants, or every recorded DAG edge.

    Column positions follow topological depth. Failure/revalidation styling is
    separate from provider identity and scientific quality; neither is inferred.
    """
    by_id = {node.id: node for node in bom.nodes}
    if selected not in by_id:
        raise ValueError("Selected evidence artifact is not in the EarthBOM")
    integrity = state.get("artifact_integrity", {})
    flagged = set(state.get("requires_revalidation", ()))
    if set(integrity) != set(by_id) or not flagged <= set(by_id):
        raise ValueError("Evidence state does not match the recorded graph")
    if any(value not in _INTEGRITY for value in integrity.values()):
        raise ValueError("Unsupported artifact integrity status")
    parents = by_id[selected].parents
    descendants = bom.descendants(selected)
    visible = set(by_id) if full_record else {selected, *parents, *descendants}
    depth: dict[str, int] = {}

    def level(identity: str) -> int:
        if identity not in depth:
            depth[identity] = 0 if not by_id[identity].parents else 1 + max(level(p) for p in by_id[identity].parents)
        return depth[identity]

    levels = sorted({level(identity) for identity in visible})
    columns = {value: index for index, value in enumerate(levels)}
    points: dict[str, tuple[float, float]] = {}
    for value in levels:
        group = [node.id for node in bom.nodes if node.id in visible and level(node.id) == value]
        for index, identity in enumerate(group):
            points[identity] = (columns[value] * 3.25, ((len(group) - 1) / 2 - index) * 1.8)

    def status(identity: str) -> str:
        if integrity[identity] in ("MISMATCH", "MISSING_OR_UNSAFE"):
            return "INTEGRITY FAILURE"
        if identity in flagged:
            return "REVALIDATE"
        return {
            "MATCH": "LOCAL MATCH",
            "UNVERIFIABLE": "IDENTITY ONLY",
            "GENERATED_ON_REQUEST": "GENERATED ON EXPORT",
        }[integrity[identity]]

    nodes = tuple(GraphNode(
        node.id, node.label, node.kind, _CATEGORIES.get(node.kind, "Recorded artifact"),
        integrity[node.id], status(node.id),
        "selected" if node.id == selected else "parent" if node.id in parents else "downstream" if node.id in descendants else "context",
        columns[level(node.id)], *points[node.id],
    ) for node in bom.nodes if node.id in visible)
    edges = tuple(GraphEdge(parent, node.id,
                            "INTEGRITY FAILURE" if status(parent) == "INTEGRITY FAILURE" else
                            "REVALIDATE" if node.id in flagged else "RECORDED DEPENDENCY")
                  for node in bom.nodes if node.id in visible for parent in node.parents if parent in visible)
    return GraphModel(selected, nodes, edges, parents, descendants, full_record)


def graph_figure(model: GraphModel):
    """Create an offline Plotly dependency canvas from the tested view model."""
    from html import escape
    from textwrap import wrap
    import plotly.graph_objects as go

    by_id = {node.id: node for node in model.nodes}
    palette = {"INTEGRITY FAILURE": "#fb9e9e", "REVALIDATE": "#f1c580", "LOCAL MATCH": "#65d6bd",
               "IDENTITY ONLY": "#aec2d9", "GENERATED ON EXPORT": "#bcb6e6"}
    figures = go.Figure()
    annotations = []
    for edge_index, edge in enumerate(model.edges):
        parent, child = by_id[edge.parent], by_id[edge.child]
        colour = palette.get(edge.status, "#435c73")
        x0, x1 = parent.x + 1.17, child.x - 1.17
        mid = (x0 + x1) / 2
        if child.column - parent.column > 1:
            # Separate skip-stage edges from intermediary node cards. They are
            # still the exact recorded dependency, not an inferred shortcut.
            route_y = (max(n.y for n in model.nodes) + .88) if edge_index % 2 else (min(n.y for n in model.nodes) - .88)
            edge_x = [x0, x0 + .18, x0 + .18, x1 - .18, x1 - .18, x1]
            edge_y = [parent.y, parent.y, route_y, route_y, child.y, child.y]
        else:
            edge_x, edge_y = [x0, mid, mid, x1], [parent.y, parent.y, child.y, child.y]
        figures.add_trace(go.Scatter(x=edge_x, y=edge_y,
                                    mode="lines", line=dict(color=colour, width=1.7 if edge.status != "RECORDED DEPENDENCY" else 1.2),
                                    hoverinfo="skip"))
        annotations.append(dict(x=x1, y=child.y, ax=x1 - .3, ay=child.y, text="", xref="x", yref="y", axref="x", ayref="y",
                                showarrow=True, arrowhead=2, arrowsize=.8, arrowwidth=1.3, arrowcolor=colour))
    for node in model.nodes:
        colour = palette[node.status]
        selected = node.relation == "selected"
        figures.add_shape(type="rect", x0=node.x - 1.17, x1=node.x + 1.17, y0=node.y - .58, y1=node.y + .58,
                          fillcolor="#163347" if selected else "#111f30", line=dict(color=colour if selected or node.status in ("INTEGRITY FAILURE", "REVALIDATE") else "#354b63", width=2.5 if selected else 1.2), layer="above")
        title = "<br>".join(escape(line) for line in wrap(node.label, 21))
        annotations.extend((
            dict(x=node.x, y=node.y + .39, text=escape(node.category.upper()), showarrow=False, font=dict(size=9, color="#a4bad0")),
            dict(x=node.x, y=node.y + .02, text="<b>" + title + "</b>", showarrow=False, font=dict(size=11, color="#edf5ff"), align="center"),
            dict(x=node.x, y=node.y - .4, text=escape(node.status), showarrow=False, font=dict(size=9, color=colour)),
        ))
    figures.add_trace(go.Scatter(x=[n.x for n in model.nodes], y=[n.y for n in model.nodes], mode="markers",
                                marker=dict(size=56, opacity=0), customdata=[[n.id, n.integrity, n.relation] for n in model.nodes],
                                hovertemplate="%{customdata[0]}<br>%{customdata[1]} · %{customdata[2]}<extra></extra>"))
    max_y = max(abs(node.y) for node in model.nodes)
    last_x = max(node.x for node in model.nodes)
    figures.update_layout(height=max(320, int((2 * max_y + 2.2) * 87)), margin=dict(l=8, r=8, t=12, b=12),
                          paper_bgcolor="#0b1625", plot_bgcolor="#0b1625", font=dict(family="Arial, sans-serif", color="#eaf3fc"),
                          annotations=annotations, showlegend=False, dragmode="pan", hoverlabel=dict(bgcolor="#1b3044", font_color="#f3f7fc"),
                          xaxis=dict(visible=False, range=[-1.4, last_x + 1.4], fixedrange=False),
                          yaxis=dict(visible=False, range=[-max_y - 1.05, max_y + 1.05], fixedrange=False),
                          uirevision="earthbom-" + model.selected + "-" + str(model.full_record))
    return figures


def render_graph(bom: EarthBOM, state: dict) -> None:
    """Accessible graph-plus-detail workspace; selectors remain authoritative."""
    from dataclasses import asdict
    from html import escape
    import json
    import streamlit as st

    from ..evidence.impact import decision_impact
    from ..evidence.lab import ARTIFACT_ID

    by_id = {node.id: node for node in bom.nodes}
    identities = list(by_id)
    selected = st.selectbox("Select evidence artifact", identities, index=identities.index(ARTIFACT_ID),
                            format_func=lambda identity: by_id[identity].label, key="selected_evidence_artifact")
    mode = st.radio("Evidence view", ("Dependency graph", "Readable evidence list"), horizontal=True, key="evidence_view")
    full_record = st.checkbox("Show the complete recorded EarthBOM", value=False, key="evidence_full_record")
    model = graph_model(bom, state, selected, full_record=full_record)
    node = by_id[selected]
    active = next(item for item in model.nodes if item.id == selected)
    st.markdown('<div class="uix-trust-graph-meta"><span><b>' + str(len(model.nodes)) + '</b> visible records</span><span><b>' + str(len(model.edges)) + '</b> recorded dependencies</span><span><b>' + str(len(model.descendants)) + '</b> downstream records</span><span>LEFT TO RIGHT / EVIDENCE FLOW</span></div>', unsafe_allow_html=True)
    if mode == "Dependency graph":
        st.plotly_chart(graph_figure(model), width="stretch", config={"displayModeBar": True, "displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d", "toImage"]},
                        alt="Recorded evidence dependencies flow left to right. Use Select evidence artifact for accessible details or choose Readable evidence list.")
        st.caption("Pan or zoom to explore. The selector changes the focused artifact; graph marks are hoverable, not clickable. On small screens, choose Readable evidence list for full-size record details.")
    else:
        groups = (("SELECTED RECORD", [active]), ("DIRECT PARENTS", [n for n in model.nodes if n.id in model.parents]),
                  ("DOWNSTREAM RECORDS", [n for n in model.nodes if n.id in model.descendants]))
        if full_record:
            groups += (("OTHER RECORDED CONTEXT", [n for n in model.nodes if n.relation == "context"]),)
        for title, records in groups:
            st.markdown("**" + title + "**")
            if not records:
                st.caption("No recorded artifacts in this category.")
            for record in records:
                tone = "failed" if record.status == "INTEGRITY FAILURE" else "conditional" if record.status == "REVALIDATE" else "matched" if record.integrity == "MATCH" else "unknown"
                st.markdown('<div class="uix-trust-list ' + tone + '"><span>' + escape(record.category) + '</span><strong>' + escape(record.label) + '</strong><small>' + escape(record.status) + '</small><code>' + escape(record.id) + '</code></div>', unsafe_allow_html=True)
    st.markdown('<div class="uix-trust-legend"><span class="matched">Local match</span><span class="failed">Integrity failure</span><span class="conditional">Requires revalidation</span><span class="unknown">Identity only / generated on export</span></div>', unsafe_allow_html=True)
    st.caption("Only real parent relationships are drawn. A local match is consistency with a recorded reference, not NASA authentication or scientific calibration. Dependencies do not establish physical causation.")
    st.markdown('<div class="uix-trust-detail"><span class="uix-trust-eyebrow">SELECTED / ' + escape(active.category.upper()) + '</span><h3>' + escape(node.label) + '</h3><code>' + escape(node.id) + '</code><p><b>' + escape(active.status) + '</b> · ' + escape(active.relation) + ' evidence</p></div>', unsafe_allow_html=True)
    st.write("**Integrity:** " + state["artifact_integrity"][selected] + (" · REVALIDATION REQUIRED" if selected in state["requires_revalidation"] else ""))
    st.write("**Produced from:** " + (", ".join(by_id[parent].label for parent in node.parents) or "Recorded provider catalog identity; original bytes not locally verified"))
    impact = decision_impact(bom, selected, state)
    st.markdown("### Decision Impact Analyzer")
    st.write("**Recorded evidence consequences:** " + impact["recorded_consequence"])
    direct = ", ".join(by_id[i].label for i in impact["direct_dependents"]) or "None"
    reports = ", ".join(by_id[i].label for i in impact["dependent_reports"]) or "None"
    st.markdown('<div class="uix-trust-impact"><div><span>DIRECT DEPENDENTS</span><strong>' + escape(direct) + '</strong></div><div><span>REPORT DEPENDENCIES</span><strong>' + escape(reports) + '</strong></div></div>', unsafe_allow_html=True)
    st.info("Hypothetical decision guidance: " + impact["hypothetical_decision_consequence"])
    st.caption(impact["evidence_vs_decision"] + " No disaster, population, loss or operational alert is inferred.")
    with st.expander("Downstream evidence records / identifiers"):
        st.dataframe([dict(Artifact=by_id[i].label, ID=i, Status="REVALIDATION REQUIRED" if i in state["requires_revalidation"] else state["artifact_integrity"][i]) for i in model.descendants], hide_index=True, width="stretch")
    with st.expander("Selected record / full machine-readable EarthBOM"):
        st.json(asdict(node))
        st.download_button("Export EarthBOM (JSON)", json.dumps(dict(schema_version="1.0", baseline=bom.record(), current_evaluation=state,
                           last_generated_report=st.session_state.get("last_passport_receipt")), indent=2), "sentinel-zero-earthbom.json", "application/json", on_click="ignore")
