"""Controlled owner-supplied historical rainfall context; no provider requests."""
from __future__ import annotations

import json
import os
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from ..corroboration.rainfall import (MAX_UPLOAD_BYTES, assess_rainfall_hypothesis,
                                     import_rainfall_bytes, load_recorded_rainfall, unavailable)


def _acquisitions(data) -> tuple[str, str]:
    sources = data.product.provenance["sources"]
    return sources[0]["acquisition_start_utc"], sources[1]["acquisition_start_utc"]


def witness_context(data) -> dict:
    """Current validated session import or an operator-configured legacy sidecar."""
    before, after = _acquisitions(data)
    imported = st.session_state.get("witness_import")
    if imported is not None:
        if imported.get("source_acquisitions") != [before, after]:
            return dict(status="INSUFFICIENT_EVIDENCE", rows=[],
                        reason="Imported context belongs to a different investigation. Reimport with this study's metadata.")
        return imported["context"]
    path = os.environ.get("SENTINEL_ZERO_RAINFALL_MANIFEST")
    if path:
        try:
            return load_recorded_rainfall(Path(path), before, after)
        except (OSError, ValueError, TypeError, KeyError):
            return dict(status="INSUFFICIENT_EVIDENCE", rows=[],
                        reason="Configured rainfall source/hash/location/time/unit validation failed. No substitute series is displayed.")
    state = unavailable()
    state["reason"] = "Original rainfall exports have not been supplied or validated. Import an original historical CSV/TSV below with its explicit source metadata."
    return state


def render_witness(data) -> dict:
    """Render source import, actual timeline, exact accumulation periods and limits."""
    before, after = _acquisitions(data)
    st.markdown("### Physics Witness · historical rainfall")
    st.caption("Independent environmental context helps frame a hypothesis. It cannot establish the cause of a radar response or confirm flooding.")
    context = witness_context(data)
    if st.toggle("Open controlled rainfall import", value=False, key="open_rainfall_import",
                 help="Optional owner action: supply an original historical CSV/TSV and explicit source metadata. No credentials or arbitrary local paths."):
        st.write("Supply one recorded observation location per import. Streamlit receives the original bytes in server session memory; this importer does not write them to disk. No arbitrary filesystem path, account secret or live provider request is accepted.")
        with st.form("rainfall_import_form"):
            uploaded = st.file_uploader("Original historical Meteomatics CSV or TSV (maximum 5 MB)", type=["csv", "tsv"], key="rainfall_upload",max_upload_size=5)
            a, b = st.columns(2)
            time_column = a.text_input("Timestamp column", "validdate")
            value_column = b.text_input("Rainfall amount column", "precip_1h:mm")
            a, b = st.columns(2)
            latitude = a.number_input("Rainfall observation latitude", -90.0, 90.0, 6.925, format="%.5f")
            longitude = b.number_input("Rainfall observation longitude", -180.0, 180.0, 79.975, format="%.5f")
            a, b = st.columns(2)
            cadence = a.number_input("Accumulation interval (hours)", 1 / 60, 24.0, 1.0)
            position = b.selectbox("Timestamp identifies", ["interval_end", "interval_start"])
            a, b = st.columns(2)
            delimiter = a.selectbox("Original file delimiter", ["Semicolon", "Comma", "Tab"])
            offset = b.number_input("UTC offset for timestamps without zone (minutes)", -840, 840, 0, help="UTC = 0; Sri Lanka = 330. Explicit offsets in the file take precedence. Ambiguous date formats are rejected.")
            variable = st.text_input("Original provider variable and units", "precip_1h:mm")
            source = st.text_input("Public source/documentation URL (no credentials or query tokens)", "https://www.meteomatics.com/en/api/available-parameters/precipitation/")
            retrieved = st.text_input("Original retrieval timestamp (ISO-8601 with UTC/offset)", placeholder="YYYY-MM-DDTHH:MM:SSZ")
            confirmed = st.checkbox("I confirm this is the original historical export, interval precipitation amounts in mm, and the location/time/variable metadata above comes from its source record.")
            submitted = st.form_submit_button("Validate and use recorded context", type="primary")
        if submitted:
            try:
                if not confirmed or uploaded is None:
                    raise ValueError("Choose the original export and confirm its historical source metadata")
                if uploaded.size > MAX_UPLOAD_BYTES:
                    raise ValueError("Rainfall upload exceeds 5,000,000 bytes")
                mapping = dict(provider="Meteomatics", source_url=source, retrieved_at_utc=retrieved,
                               latitude=latitude, longitude=longitude, units="mm", quantity="interval_precipitation",
                               interval_hours=cadence, time_column=time_column, value_column=value_column,
                               timestamp_position=position, timezone_offset_minutes=int(offset), variable=variable,
                               record_basis="historical_export", delimiter={"Comma": ",", "Semicolon": ";", "Tab": "\t"}[delimiter])
                context = import_rainfall_bytes(uploaded.getvalue(), mapping, before, after, filename=uploaded.name)
                st.session_state["witness_import"] = dict(source_acquisitions=[before, after], context=context)
                st.success("Original bytes hashed and declared metadata/recorded values validated. Provider authenticity is owner-declared, not independently authenticated.")
            except (ValueError, TypeError, OverflowError, KeyError) as exc:
                # Controlled validation messages contain no source row values or URLs.
                st.error("Rainfall import blocked: " + str(exc))
    if st.session_state.get("witness_import") is not None:
        if st.button("Remove imported rainfall from this session"):
            del st.session_state["witness_import"]
            context = witness_context(data)
    if not context.get("rows"):
        st.info("INSUFFICIENT EVIDENCE · " + context.get("reason", "No validated historical rainfall source available."))
        st.caption("Missing observations are not evidence that no rain occurred. NASA GPM IMERG is assessed separately; it is not silently substituted for the owner's export.")
        return context
    if context.get("schema_version") == "1.1":
        hypothesis = st.selectbox("Optional narrow context question", ["NOT_ASSESSED", "PRECIPITATION_RECORDED_IN_COMPLETE_INTERVALS"],
                                  format_func=lambda v: "Show recorded context only" if v == "NOT_ASSESSED" else "Was any rain recorded in complete sampled intervals?", key="rainfall_hypothesis")
        context = assess_rainfall_hypothesis(context, hypothesis)
        st.session_state["witness_import"] = dict(source_acquisitions=[before, after], context=context)
    st.markdown("**" + context["status"].replace("_", " ") + "**")
    st.write(context.get("reason", context.get("interpretation", "Recorded context only")))
    fig = go.Figure(go.Bar(x=[r["utc"] for r in context["rows"]], y=[r["precipitation_mm"] for r in context["rows"]],
                          name="Recorded interval amount", marker_color="#59d5c4"))
    for value, label in ((before, "NISAR · " + before[:10]), (after, "NISAR · " + after[:10])):
        fig.add_shape(type="line", x0=value, x1=value, y0=0, y1=1, yref="paper", line=dict(color="#e9bb71", dash="dash"))
        fig.add_annotation(x=value, y=1, yref="paper", text=label, showarrow=False, yshift=12, font=dict(color="#f0d29e"))
    fig.update_layout(height=290, margin=dict(l=40, r=20, t=45, b=45), paper_bgcolor="#142238", plot_bgcolor="#142238",
                      font=dict(color="#e8f0f8"), xaxis_title="Recorded UTC interval timestamp", yaxis_title="Precipitation (mm / declared interval)", showlegend=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, alt="Recorded rainfall timeline with both NISAR acquisition timestamps")
    m = context.get("metadata", {})
    st.caption(f"Source point {m.get('latitude')}, {m.get('longitude')} · {m.get('interval_hours')} h intervals · {context.get('missing_values', 0)} missing values · {context.get('interval_gaps', 0)} gaps. Point sampling differs from the radar footprint.")
    periods = context.get("accumulation_periods", [])
    if periods:
        st.markdown("**Measured accumulation periods wholly between acquisitions**")
        st.dataframe(periods, hide_index=True, width="stretch")
        st.caption(context["accumulation_rule"] + " Gaps and boundary fractions are never counted as zero rainfall.")
    else:
        st.info("No verified interval convention is available for accumulating this legacy context; original per-row values are shown without inventing totals.")
    with st.expander("Environmental provenance and sampling limitations"):
        st.write(context.get("interpretation"))
        for item in context.get("limitations", []):
            st.write("• " + item)
        st.json({k: v for k, v in context.items() if k != "rows"})
        st.download_button("Export environmental evidence (JSON)", json.dumps(context, indent=2),
                           "sentinel-zero-rainfall-context.json", "application/json", on_click="ignore")
    return context
