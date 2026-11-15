"""Read-only five-stage Streamlit investigation of the reviewed NISAR pilot."""
from __future__ import annotations

from html import escape
from hashlib import sha256
import json
from time import perf_counter

import streamlit as st

from ..application.data import (ANALYSIS_SHA256, PREPARATION_SHA256, ExplorerData, RuntimePaths,
                                candidates, configured_paths, fingerprint, load_explorer,
                                magnitude_count, pixel, summary_json)
from ..ingestion.acquisition import AcquisitionError
from ..reporting.investigation import investigation_pdf
from .display import Panel, comparison, distributions
from .theme import UI_BUILD, stylesheet
from .components import guide_panel, section_heading
from .cell_view import cell_locator
from .global_view import render_global
from .trust import (cached_bom,challenge_controls,graph_view,render_witness,restore_controls,
                    snapshot,status_cards)

STAGES = ("Observe Change", "Investigate Science", "Challenge Evidence", "Trace Consequences", "Restore & Report")


@st.cache_data(show_spinner=False, max_entries=3)
def verified_data(paths: RuntimePaths, content_identity: str) -> ExplorerData:
    """Key includes pinned manifests and every file's identity/size/mtime/ctime."""
    return load_explorer(paths)


@st.cache_data(show_spinner=False, max_entries=3)
def display_panels(content_identity: str, _data: ExplorerData) -> tuple[Panel, ...]:
    return comparison(_data)


@st.cache_data(show_spinner=False, max_entries=4)
def report_pdf(content_identity: str, selected: tuple[int, int] | None,
               threshold_db: float, _data: ExplorerData) -> bytes:
    return investigation_pdf(_data, selected, threshold_db)


def _move(stage: str) -> None:
    st.session_state["stage"] = stage
    st.session_state["uix_visited"] = sorted(set(st.session_state.get("uix_visited", [])) | {stage})


def _open_global() -> None:
    st.session_state["stage"] = STAGES[0]
    st.session_state["observe_workspace"] = "Global NISAR discovery"


def _open_pilot() -> None:
    _move(STAGES[0])
    st.session_state["observe_workspace"] = "Verified Sri Lanka pilot"


@st.cache_data(show_spinner=False,max_entries=4)
def passport_pdf(identity: str,selected: tuple[int,int] | None,threshold: float,trust_json: str,_data: ExplorerData) -> bytes:
    return investigation_pdf(_data,selected,threshold,trust=json.loads(trust_json))


def _legend(panel: Panel) -> None:
    colours = ",".join(panel.colours)
    lo, hi = panel.limits
    midpoint = (lo+hi)/2
    st.markdown(f'<div class="uix-map-legend"><div class="ramp" role="img" aria-label="Display colour legend {lo:g} to {hi:g} decibels" '
                f'style="background:linear-gradient(to right,{colours})"></div>'
                f'<div class="labels">'
                f'<span>{lo:g} dB</span><span>{midpoint:g} dB</span><span>+{hi:g} dB</span></div></div>', unsafe_allow_html=True)


def _maps(data: ExplorerData, identity: str) -> None:
    pair = data.product.prepared
    bounds = pair.grid["bounds_native"]
    panels = display_panels(identity, data)
    grid = st.container(key="comparison_grid")
    main_map, context = grid.columns([1.15, 1], gap="medium")
    with main_map:
        with st.container(key="change_surface", border=True):
            st.markdown('<div class="map-kicker">01 / CHANGE FIELD · NISAR FREQUENCY A</div><p class="map-title">October 18 − October 6 · Backscatter change (dB)</p>', unsafe_allow_html=True)
            st.markdown('<div class="uix-coordinate-strip"><span>N ↑ · EPSG:32644</span><span>553 × 553 · 10 m posting</span><span>Grey = excluded</span></div>', unsafe_allow_html=True)
            panel = panels[2]
            with st.container(horizontal=True,horizontal_alignment="center",key="uix_raster_canvas"):
                st.image(panel.png, width="content", output_format="PNG", alt=panel.title+"; north-up actual NISAR pixels; grey cells excluded")
            _legend(panel)
            st.caption(f"Display-saturated: {panel.clipped_cells:,} eligible cells · saved extremes retained")
            st.markdown(f'<div class="uix-coordinate-strip"><span>E {bounds[0]:,.0f} → {bounds[2]:,.0f} m</span><span>N {bounds[1]:,.0f} → {bounds[3]:,.0f} m</span></div>', unsafe_allow_html=True)
    with context:
        with st.container(key="source_surface", border=True):
            st.markdown('<div class="map-kicker">02 / THE OBSERVATIONS</div><p class="map-title">Two dates. One shared measurement scale.</p>', unsafe_allow_html=True)
            for i, (col, panel) in enumerate(zip(st.columns(2), panels[:2])):
                with col:
                    st.markdown("**"+data.product.provenance["sources"][i]["acquisition_start_utc"][:10]+"**")
                    st.caption(panel.granule)
                    st.image(panel.png, width="stretch", output_format="PNG", alt=panel.title+"; north-up actual NISAR pixels; grey cells excluded")
                    _legend(panel)
                    st.caption(f"Display-saturated: {panel.clipped_cells:,} eligible cells")
            st.caption("N ↑ · identical −25 to +15 dB display scale. Use each image's fullscreen control for a larger view.")
        s = data.product.statistics
        with st.container(key="uix_observation_readout", border=True):
            st.markdown('<div class="map-kicker">03 / THE RECORDED FINDING</div>', unsafe_allow_html=True)
            st.write(f"Median response {s['median']:+.3f} dB. Local measurements range from {s['minimum']:.2f} to {s['maximum']:+.2f} dB.")
            st.markdown('<div class="map-readout"><div>Greater backscatter<strong>'+f"{s['positive_cells']:,}"+'</strong>eligible cells</div><div>Lower backscatter<strong>'+f"{s['negative_cells']:,}"+'</strong>eligible cells</div></div>', unsafe_allow_html=True)
            st.caption("Exactly zero: "+f"{s['exactly_zero_cells']:,}"+" cell. These are radar measurements, not impacts.")
        with st.container(key="uix_actions"):
            st.button("Inspect cells · Explore the Change →", type="primary", on_click=_move, args=(STAGES[1],),width="stretch")
            st.button("Follow this finding through its evidence",on_click=_move,args=(STAGES[3],),width="stretch")
    st.markdown('<div class="uix-reading-key"><div><b><i style="background:#a882bf"></i>Lower radar response</b>Negative dB · less power on October 18</div><div><b><i style="background:#f7f7f7"></i>Little calculated change</b>Near zero · not proof of unchanged land</div><div><b><i style="background:#55c4b3"></i>Greater radar response</b>Positive dB · more power on October 18</div></div>',unsafe_allow_html=True)
    st.caption("Display only: both dates −25 to +15 dB; change −6 to +6 dB. Values outside these limits saturate in colour; all saved measurements remain untrimmed. "
               "Native 553 × 553 images, north up. View-only maps; use the cell inspector for exact measurements.")
    with st.expander("Map coordinates and measurement guide"):
        st.write(f"EPSG:32644 bounds: E {bounds[0]:,.0f}–{bounds[2]:,.0f} m; N {bounds[1]:,.0f}–{bounds[3]:,.0f} m. "
                 "The window is approximately 5.53 km along each projected grid dimension, not the whole Kelani River Basin.")
        st.write("Colours measure radar response, not water, flooding or damage. Positive and negative values are candidate evidence to investigate, not verified physical causes.")


def _stats(data: ExplorerData) -> None:
    s = data.product.statistics
    for col, label, value in zip(st.container(key="summary_metrics").columns(4), ("Common eligible", "Excluded", "Mean change", "Median change"),
                                 (f"{s['eligible_cells']:,}", f"{s['excluded_cells']:,}", f"{s['mean']:+.3f} dB", f"{s['median']:+.3f} dB")):
        col.metric(label, value)


def _explain() -> None:
    with st.expander("Understand the measurement"):
        st.markdown("**NISAR** is the NASA–ISRO synthetic aperture radar mission. This investigation measures "
                    "terrain-corrected gamma-zero backscatter power for the HHHH channel. Individual images "
                    "convert positive linear power to dB for display. The saved change is the difference "
                    "between the two logarithmic power values.")
        st.code("Delta_dB = 10 × (log10(P_after) − log10(P_before))", language=None)
        st.write("Moisture, vegetation, surface conditions, geometry and processing artifacts can influence radar response. "
                 "Backscatter change alone does not confirm the cause, flooding, damage or affected people.")
        st.markdown("Sources: [ASF GCOV guide](https://nisar-docs.asf.alaska.edu/gcov/) · "
                    "[Provisional product issues](https://nisar-docs.asf.alaska.edu/provisional-known-issues/). "
                    "Source content was recorded in earlier scientific reviews; no live provider query is required.")


def _quality(data: ExplorerData) -> None:
    with st.expander("Scientific limitations and quality diagnostics", expanded=False):
        st.markdown("**Complete technical QA record · interpretation remains conditional**")
        for item in data.product.limitations:
            st.markdown("- "+item)
        st.table([{"Unresolved condition": k.replace("_", " "), "Status": v} for k, v in data.product.provenance["quality_flags"].items()])
        profiles = data.product.diagnostics["spatial_profiles"]["change"]
        st.write(f"Largest adjacent change-median steps: rows {profiles['maximum_adjacent_row_median_step_db']:.3f} dB; "
                 f"columns {profiles['maximum_adjacent_column_median_step_db']:.3f} dB. These describe patterns, not a quality PASS or uncertainty estimate.")
        st.caption("Small-window visual review found no obvious broad straight throughgoing band/seam. "
                   "Fine-scale variability and localized structures remain unexplained; subtle/swath-scale banding, RFI and registration effects are unresolved.")


def _investigate(data: ExplorerData) -> None:
    st.markdown('<div class="uix-eyebrow">MEASUREMENT INSPECTOR / NATIVE GRID</div>',unsafe_allow_html=True)
    st.caption("Exact saved measurements. Row zero is north; columns increase east. Select a cell or jump to the largest recorded magnitude.")
    if st.button("Inspect largest absolute-change cell"):
        first = candidates(data, 1)[0]
        st.session_state["pixel_row"], st.session_state["pixel_column"] = first["row"], first["column"]
    st.session_state.setdefault("pixel_row", st.session_state.get("selected_cell", (276, 276))[0])
    st.session_state.setdefault("pixel_column", st.session_state.get("selected_cell", (276, 276))[1])
    a, b = st.columns(2)
    row = a.number_input("Row (north to south)", min_value=0, max_value=data.product.prepared.grid["height"]-1,
                         value=None, step=1, key="pixel_row")
    col = b.number_input("Column (west to east)", min_value=0, max_value=data.product.prepared.grid["width"]-1,
                         value=None, step=1, key="pixel_column")
    if row is None or col is None:
        st.info("Enter both a row and a column to inspect a cell.")
        return
    cell = pixel(data, int(row), int(col))
    # Persist investigation state separately from widget keys, which Streamlit
    # cleans up when another stage is rendered. Reports never trust supplied values.
    st.session_state["selected_cell"] = (int(row), int(col))
    st.markdown(f'<div class="uix-cell-location"><strong>Cell {int(row)} / {int(col)}</strong> · declared pixel centre<br><code>E {cell["easting_m"]:.1f} · N {cell["northing_m"]:.1f} m / EPSG:32644</code><br>WGS84 {cell["latitude"]:.6f}° N, {cell["longitude"]:.6f}° E · positioning accuracy remains unverified.</div>',unsafe_allow_html=True)
    def db(value):
        return "Excluded" if value is None else f"{value:+.6f} dB"
    measurements, locator = st.columns([1.7,1],gap="medium")
    with measurements:
        if cell["eligible"]:
            st.success("Cell is eligible in both observations")
        else:
            st.warning("Excluded from analysis — primary reason: "+cell["primary_exclusion_reason"]+". No saved change value; causes may overlap.")
        for container, label, value in zip(st.columns(3), ("October 6 backscatter", "October 18 backscatter", "Saved backscatter change"),
                                            (cell["before_db"], cell["after_db"], cell["saved_change_db"])):
            container.metric(label, db(value))
        st.table([{"Observation": "October 6", "Linear gamma-zero power": cell["before_linear_power"], "Number of looks": cell["before_number_of_looks"]},
                  {"Observation": "October 18", "Linear gamma-zero power": cell["after_linear_power"], "Number of looks": cell["after_number_of_looks"]}])
    with locator:
        st.markdown('<div class="uix-eyebrow">SELECTED CELL / LOCATION IN THE CHANGE FIELD</div>',unsafe_allow_html=True)
        st.image(cell_locator(data,int(row),int(col)),width="stretch",alt=f"Actual north-up change display; view-only crosshair marks row {int(row)}, column {int(col)}")
        st.caption("N ↑ · white locator is a view-only overlay. Measurements and native saved pixels remain unchanged; this is not a map-click control.")
    with st.expander("Advanced cell export"):
        st.caption("One inspected cell only: exact values, coordinates and eligibility. This is not the full investigation report.")
        st.download_button("Export inspected-cell evidence (JSON)", json.dumps(cell, indent=2, allow_nan=False), "sentinel-zero-cell.json", "application/json", on_click="ignore")
    st.divider()
    st.subheader("Descriptive backscatter-change magnitude")
    threshold = st.slider("Absolute change magnitude (dB)", min_value=0.0, max_value=12.0, value=6.0, step=.5)
    st.session_state["investigation_threshold"] = threshold
    count = magnitude_count(data, threshold)
    st.metric(f"Eligible cells with |change| ≥ {threshold:g} dB", f"{count:,}")
    st.caption("Inclusive magnitude threshold for exploration only. It is not a flood classifier, significance test or environmental-impact area.")
    with st.expander("Largest measured magnitudes — candidate cells for investigation"):
        st.dataframe(candidates(data), hide_index=True, width="stretch")
        st.caption("Sorted by absolute saved dB value, ties by row/column. Physical causes remain unverified.")
    st.subheader("Quantitative record")
    s = data.product.statistics
    st.table([{"Statistic": label, "Value": f"{s[key]:,}" if key.endswith("_cells") else f"{s[key]:.6f}"} for label, key in (("Minimum (dB)", "minimum"), ("Maximum (dB)", "maximum"),
              ("Population standard deviation (dB)", "standard_deviation"), ("Positive cells", "positive_cells"),
              ("Negative cells", "negative_cells"), ("Exactly zero cells", "exactly_zero_cells"))])
    try:
        for container, chart in zip(st.columns(2), distributions(data)):
            with container:
                st.plotly_chart(chart, width="stretch", config={"displayModeBar": False}, alt="Actual common-support radar-value distribution")
    except ImportError:
        st.info("Optional interactive distribution charts are unavailable. Verified statistics and all three maps remain available.")
    render_witness(data)
    st.button("Review how the evidence was verified →", on_click=_move, args=(STAGES[2],))


def _provenance(data: ExplorerData,paths: RuntimePaths,identity: str) -> None:
    st.markdown('<div class="uix-science-card"><div class="uix-eyebrow">THE EVIDENCE CONTRACT</div><h3>Trust is inspectable. It is not a percentage.</h3><p>The six protected subsets and saved analysis match their local references. The disposable challenge has its own state, and scientific limitations remain separate.</p></div>',unsafe_allow_html=True)
    for col, title, text in zip(st.columns(3), ("01 / Integrity", "02 / Computation", "03 / Interpretation"),
                               ("PASSED · file sizes and SHA-256 references agree.",
                                "PASSED · native grid, common support and saved calculations are checked by the existing loader.",
                                "CONDITIONAL · physical causes and independent scientific calibration remain unverified.")):
        with col.container(border=True):
            st.markdown("**"+title+"**")
            st.write(text)
    cards=[]
    for source in data.product.provenance["sources"]:
        cards.append('<div class="uix-source-card"><small>NASA–ISRO NISAR / SOURCE OBSERVATION</small><strong>'+escape(source['acquisition_start_utc'][:10])+'</strong><span>'+escape(source['granule_id'])+' · '+escape(source['collection_id'])+'</span><span>HHHH power · focusing mask · number of looks</span><span>PROVISIONAL · CRID '+escape(source['processing_crid'])+' · version '+escape(source['product_version'])+'</span></div>')
    st.markdown('<div class="uix-source-grid">'+''.join(cards)+'</div>',unsafe_allow_html=True)
    st.write("Matching checksums establish consistency with recorded references. They do not prove NASA authenticity, calibration or scientific correctness.")
    with st.expander("Advanced Technical Evidence · full hashes and receipts"):
        st.code("Preparation SHA-256\n"+PREPARATION_SHA256+"\n\nAnalysis SHA-256\n"+ANALYSIS_SHA256, language=None)
        rows = [dict(Granule=inv["source"]["granule_id"], Layer=a["layer"], Bytes=a["bytes"], SHA256=a["sha256"])
                for inv in data.product.provenance["input_receipts"] for a in inv["artifacts"]]
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Read-only checks against preserved references. The application cannot rewrite a failed baseline to make it pass.")
    challenge_controls(data,paths,identity)
    bom,state = snapshot(data,paths,identity)
    status_cards(state)
    st.caption(f"EarthBOM: {len(bom.nodes)} recorded artifacts/identities and {len(bom.record()['edges'])} dependencies. Original HDF5 checksums remain unavailable.")
    st.button("Follow the processing path →", on_click=_move, args=(STAGES[3],))


def _lineage(data: ExplorerData,paths: RuntimePaths,identity: str) -> None:
    bom,state = snapshot(data,paths,identity)
    status_cards(state)
    with st.container(key="twin_workspace"):
        st.subheader("Evidence Trust Digital Twin")
        graph_view(bom,state)
    m = data.product.provenance
    steps = (("01", "NASA NISAR", " + ".join(s["granule_id"] for s in m["sources"])),
             ("02", "Harmony subsets", "Six recorded HHHH, focusing-mask and looks layers"),
             ("03", "Eligibility preparation", m["preparation"]["run_id"]),
             ("04", "dB change analysis", m["run_id"]),
             ("05", "Investigation evidence", "Read-only maps, cell inspection and report"))
    rows = [dict(Stage="NISAR / Harmony", Artifact="Six source subsets", Identity=" / ".join(s["granule_id"] for s in m["sources"])),
            dict(Stage="SZ-005", Artifact="Common support + primary exclusions", Identity=m["preparation"]["run_id"]),
            dict(Stage="SZ-006", Artifact="Saved dB change + statistics + diagnostics", Identity=m["run_id"])]
    with st.expander("Recorded run and derived artifact identities"):
        st.markdown('<div class="lineage-flow">'+"".join('<div class="lineage-step"><small>'+n+'</small><strong>'+escape(title)+
                    '</strong><span>'+escape(detail)+'</span></div>' for n,title,detail in steps)+'</div>',unsafe_allow_html=True)
        st.table(rows)
        st.json(m["outputs"])
    st.caption("Working dependency traversal and local trust propagation. This is not an environmental-impact or physical-causation model; it does not certify NASA authenticity.")
    st.button("Create a readable investigation report →", on_click=_move, args=(STAGES[4],))


def _exports(data: ExplorerData, identity: str,paths: RuntimePaths) -> None:
    selected = st.session_state.get("selected_cell")
    threshold = st.session_state.get("investigation_threshold", 6.0)
    restore_controls(paths)
    bom,trust = snapshot(data,paths,identity)
    status_cards(trust)
    with st.container(key="report_cover", border=True):
        st.markdown('<div class="uix-report-label">SENTINEL ZERO / SCIENTIFIC EVIDENCE PASSPORT</div><h3 class="uix-report-title">Carry the evidence forward.</h3><p class="brand-subtitle">A readable investigation record. Measurements, provenance and uncertainty travel together.</p><div class="uix-report-index"><span>01 · Actual NISAR maps</span><span>02 · Measured findings</span><span>03 · Method & limitations</span><span>04 · Current integrity state</span><span>05 · Reconstruction & impacts</span><span>Optional · Inspected cell</span></div>',unsafe_allow_html=True)
        if selected is not None:
            st.caption(f"Includes your inspected cell: row {selected[0]}, column {selected[1]}; descriptive magnitude ≥ {threshold:g} dB.")
        else:
            st.caption("No selected cell. Inspect a cell in Investigate Science to include its exact measurements.")
        passport = passport_pdf(identity, selected, threshold,json.dumps(trust,sort_keys=True),data)
        st.session_state["last_passport_receipt"] = dict(bytes=len(passport),sha256=sha256(passport).hexdigest(),
                                                        analysis_manifest_sha256=ANALYSIS_SHA256,
                                                        local_integrity=trust["local_integrity"],demonstration=trust["demonstration"]["state"])
        st.download_button("Download Investigation Report (PDF)", passport,
                           "sentinel-zero-investigation-report.pdf", "application/pdf", type="primary", on_click="ignore")
    s = data.product.statistics
    st.markdown(f"**OBSERVATION** · {s['eligible_cells']:,} common eligible cells; median radar-response change **{s['median']:+.3f} dB**.")
    st.write("**INTERPRETATION** · Physical causes remain unverified. The report records evidence, not a confirmed flood, damage assessment or hazard prediction.")
    with st.expander("Advanced Exports · technical evidence (JSON)"):
        st.caption("Full investigation evidence: original schema v1.0, sources, methodology, saved statistics, receipts and limitations. Intended for audit and software reuse.")
        st.download_button("Export Technical Evidence (JSON)", summary_json(data), "sentinel-zero-investigation-summary.json", "application/json", on_click="ignore")
        if selected is not None:
            st.caption("Optional cell evidence: only the selected cell's coordinates, values and eligibility; not a second full report.")
            st.download_button("Export inspected-cell evidence (JSON)", json.dumps(pixel(data, *selected), indent=2, allow_nan=False),
                               "sentinel-zero-cell.json", "application/json", on_click="ignore")
        st.code(summary_json(data).decode("utf-8"), language="json")
        st.caption("Current Evidence Passport receipt (separate from PDF bytes; no self-referential checksum)")
        st.json(st.session_state["last_passport_receipt"])
    st.caption("Recovery targets only the isolated derived copy and rechecks its independent local reference. Source observations remain immutable; restored hashes do not resolve scientific uncertainties. This application publishes and submits nothing.")


def main() -> None:
    started = perf_counter()
    st.set_page_config(page_title="SENTINEL ZERO 3.0 | Earth Evidence", page_icon="◈", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(stylesheet(), unsafe_allow_html=True)
    with st.container(key="app_header"):
        identity_col, context_col, actions_col = st.columns([1.15,1.6,1],gap="medium")
        with identity_col:
            st.markdown('<div class="uix-wordmark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1 23 7v10l-11 6L1 17V7Z" stroke="currentColor" fill="none"/><path d="M5 9h14M5 15h14M9 5v14M15 5v14" stroke="currentColor" opacity=".65"/><circle cx="12" cy="12" r="3" stroke="currentColor" fill="none"/></svg>EARTH EVIDENCE / INTELLIGENCE</div>',unsafe_allow_html=True)
            st.title("SENTINEL ZERO 3.0")
        with context_col:
            st.markdown('<p class="brand-subtitle"><strong>Earth Evidence Intelligence Platform</strong><br>Detect the Change. Investigate the Evidence. Protect the Decision.</p>',unsafe_allow_html=True)
            st.markdown('<span class="build-tag">'+UI_BUILD+'</span>',unsafe_allow_html=True)
        with actions_col:
            st.button("Explore Earth ↗",on_click=_open_global,width="stretch")
            st.markdown('<div class="uix-console-status"><span>NASA–ISRO NISAR</span><b>LOCAL INVESTIGATION</b></div>',unsafe_allow_html=True)
    with st.sidebar:
        st.markdown('<div class="brand-eyebrow">SENTINEL ZERO / 3.0</div>', unsafe_allow_html=True)
        st.markdown("### Your investigation")
        st.caption("01 / SUPPORTED STUDY WINDOW")
        st.markdown("**Kelani exploratory window**")
        st.caption("79.95–80.00° E · 6.90–6.95° N. A small study rectangle, not the full Kelani Basin boundary.")
        stage = st.radio("Investigation stage", STAGES, key="stage", captions=("What changed in the radar response?", "Which measurements support it?", "What can we verify?", "How was this evidence produced?", "Take a readable report"))
        workspace = st.radio("Evidence workspace",("Verified Sri Lanka pilot","Global NISAR discovery"),key="observe_workspace")
        st.button("Global Earth Explorer",on_click=_open_global)
        reverify = st.button("Reverify local evidence", help="Clear the in-memory application caches and repeat full source/output verification.")
        st.caption("NASA–ISRO NISAR data attribution; independent team application. No NASA endorsement implied.")
    st.session_state["uix_visited"] = sorted(set(st.session_state.get("uix_visited", [])) | {stage})
    with st.container(key="uix_journey"):
        for column,index in zip(st.columns(5),range(5)):
            column.button(STAGES[index],key=f"journey_{index}",type="primary" if STAGES[index]==stage else "secondary",
                          on_click=_move,args=(STAGES[index],),width="stretch",help=f"Stage {index+1} of 5 · "+("Visited" if STAGES[index] in st.session_state["uix_visited"] else "Available"))
    if reverify:
        verified_data.clear()
        display_panels.clear()
        report_pdf.clear()
        passport_pdf.clear()
        cached_bom.clear()
    if stage == STAGES[0]:
        if workspace == "Global NISAR discovery":
            st.button("Return to verified Sri Lanka investigation",on_click=_open_pilot)
            render_global()
            st.caption("Global catalog discovery is independent of local pixel availability. Select Verified Sri Lanka pilot to return to the analysed observations.")
            return
    try:
        paths = configured_paths()
        identity = fingerprint(paths)
        with st.spinner("Verifying local scientific evidence…"):
            data = verified_data(paths, identity)
    except (AcquisitionError, OSError, KeyError, ValueError, TypeError) as exc:
        code = getattr(exc, "code", "MISSING_OR_INVALID_DATA")
        st.error("Scientific display blocked — "+str(code))
        st.write("The operator must check the configured runtime root, required source/manifest files and recorded SHA-256 references. "
                 "For portable mode, set both SENTINEL_ZERO_BUNDLE_ROOT and its separately recorded SENTINEL_ZERO_BUNDLE_SHA256. "
                 "Keep runtime files outside OneDrive; restore the expected artifacts instead of replacing acceptance hashes.")
        st.caption("No substitute maps or measurements are displayed. Server configuration is required; visitor-supplied file paths are not accepted.")
        st.stop()
    sources = data.product.provenance["sources"]
    st.markdown('<div class="study-strip"><strong>SRI LANKA / KELANI EXPLORATORY WINDOW</strong><span>'+sources[0]["acquisition_start_utc"][:10]+" → "+sources[1]["acquisition_start_utc"][:10]+' UTC</span><span>NASA–ISRO NISAR · L2 GCOV · A / HHHH</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="science-note"><strong>PROVISIONAL science</strong> · Radar-response change, physical cause unverified. Protected baseline checks passed; disposable-copy state is assessed separately.</div>', unsafe_allow_html=True)
    index = STAGES.index(stage)
    headings = (("OBSERVATION WORKSPACE", "Read the radar response.", ""),
                ("MEASUREMENT WORKSPACE", "Investigate the observation.", "Exact pixels, descriptive magnitudes and environmental context."),
                ("EVIDENCE ASSURANCE", "Put the evidence to the test.", "Separate local consistency, scientific quality and physical interpretation."),
                ("EVIDENCE TRUST DIGITAL TWIN", "Trace what depends on what.", "Select a real artifact. Follow its recorded parents and downstream dependencies."),
                ("RECOVERY & EVIDENCE PASSPORT", "Revalidate. Then report.", "Restore bytes or genuinely rebuild evidence; carry the current trust state into the report."))
    if stage != STAGES[0]:
        st.markdown(section_heading(f"0{index+1}",*headings[index]),unsafe_allow_html=True)
    _stats(data)
    if stage == STAGES[0]:
        _maps(data, identity)
        _explain()
    elif stage == STAGES[1]:
        with st.container(key="pixel_workspace"):
            _investigate(data)
    elif stage == STAGES[2]:
        with st.container(key="trust_workspace"):
            _provenance(data,paths,identity)
    elif stage == STAGES[3]:
        _lineage(data,paths,identity)
    else:
        with st.container(key="trust_workspace"):
            _exports(data, identity,paths)
    guide_trust = snapshot(data,paths,identity)[1] if stage in STAGES[2:] or st.session_state.get("evidence_lab") is not None else None
    guide_panel(data,st.session_state.get("selected_cell"),guide_trust)
    _quality(data)
    with st.container(key="uix_footer"):
        st.caption(escape(data.mode)+" · offline evidence · "+str(data.product.prepared.grid["resolution"][0])+" m nominal posting, not independently verified resolution")
        st.caption(f"Full evidence verification: {data.verification_seconds:.2f} s · verified {data.verified_at_utc} · "
                   f"this server render {perf_counter()-started:.2f} s. File identity/stat changes invalidate the cache; use Reverify for a fresh full hash check. "
                   f"Visited {len(st.session_state['uix_visited'])}/5 stages; visits do not indicate scientific acceptance.")


if __name__ == "__main__":
    main()
