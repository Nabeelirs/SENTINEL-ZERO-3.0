"""Explicitly networked catalog workspace, isolated from the offline pilot."""
from datetime import date,datetime,timedelta,timezone
from html import escape
import json

import streamlit as st

from ..application.global_explorer import COLLECTION,GlobalQuery,centred_query,discover
from ..application.global_workflow import STUDY_WINDOWS,create_work_order,selection_order


def _state_cards(items: tuple[tuple[str,str,str,str],...]) -> None:
    """Small presentation cards; their labels never substitute for catalog data."""
    cards="".join('<div class="global-state-card" data-state="'+escape(state)+'">'
        '<small>'+escape(label)+'</small><strong>'+escape(value)+'</strong><span>'+escape(detail)+'</span></div>'
        for label,value,detail,state in items)
    st.markdown('<div class="global-state-grid">'+cards+'</div>',unsafe_allow_html=True)


def form_query(mode: str,latitude: float,longitude: float,span: float,
               bounds: tuple[float,float,float,float],start: date,end: date,
               polarization: str) -> GlobalQuery:
    """Build the exact bounded UTC query used by the form and its local preview."""
    first=datetime.combine(start,datetime.min.time(),tzinfo=timezone.utc).isoformat()
    last=datetime.combine(end+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).isoformat()
    query=centred_query(latitude,longitude,span,first,last) if mode=="Centre coordinates" else GlobalQuery(*bounds,first,last)
    query=GlobalQuery(query.west,query.south,query.east,query.north,query.start_utc,query.end_utc,"A",polarization)
    query.validate()
    return query


def _choose_window() -> None:
    """Known exploratory coordinates drive the same live query, not fake imagery."""
    selected = st.session_state.get("study_window", "Custom coordinates")
    if selected in STUDY_WINDOWS:
        west,south,east,north = STUDY_WINDOWS[selected]
        st.session_state.update(global_lat=(south+north)/2,global_lon=(west+east)/2,
            global_span=max(east-west,north-south),global_west=west,global_south=south,
            global_east=east,global_north=north)


def aoi_figure(query: GlobalQuery):
    """Offline geographic context driven by the exact queried lon/lat bounds."""
    import plotly.graph_objects as go
    figure=go.Figure()
    for box in query.validate():
        ring=box.ring()
        figure.add_trace(go.Scatter(x=[p[0] for p in ring],y=[p[1] for p in ring],
            mode="lines",fill="toself",name="Selected AOI",line=dict(color="#61d4c2",width=3)))
    # No remote topojson, tiles, invented country boundaries or geocoding service.
    figure.update_layout(height=270,margin=dict(l=50,r=15,t=10,b=45),
        paper_bgcolor="#101c2d",plot_bgcolor="#101c2d",font=dict(color="#d7e5f1",size=12),
        showlegend=False,xaxis_title="Longitude (degrees east)",yaxis_title="Latitude (degrees north)")
    figure.update_xaxes(gridcolor="#26374a",zerolinecolor="#3e546b")
    figure.update_yaxes(gridcolor="#26374a",zerolinecolor="#3e546b")
    return figure


def world_figure(query: GlobalQuery):
    """Offline world-coordinate navigator, with true AOI geometry and centre.

    No country outline, observed coverage or geographic lookup is invented. A
    centre marker keeps a narrow AOI locatable without exaggerating its size.
    """
    import plotly.graph_objects as go
    figure=aoi_figure(query)
    boxes=query.validate()
    for box in boxes:
        figure.add_trace(go.Scatter(x=[(box.west+box.east)/2],y=[(box.south+box.north)/2],
            mode="markers",name="AOI centre",marker=dict(size=11,color="#70e4d1",symbol="cross"),
            hovertemplate="AOI centre<br>%{x:.5f}° E, %{y:.5f}° N<extra></extra>"))
    figure.update_layout(height=280,margin=dict(l=50,r=15,t=15,b=45))
    figure.update_xaxes(range=[-180,180],dtick=60,constrain="domain",fixedrange=False)
    figure.update_yaxes(range=[-90,90],dtick=30,fixedrange=False)
    return figure


def _render_pair_workflow(result: dict) -> None:
    ids=selection_order(result)
    if len(ids)<2:
        st.info("Two different acquisition times are required before a processing work order can be created.")
        return
    observations={o["granule_concept_id"]:o for o in result["observations"]}
    preferred=next((p for p in result["pair_assessments"] if p["outcome"]=="PRELIMINARY_CANDIDATE"),None)
    defaults=(preferred["before_id"],preferred["after_id"]) if preferred else (ids[0],ids[-1])
    # Bind widget identity to this actual query, avoiding stale country/date selections.
    token=result["retrieved_at_utc"]
    def label(identifier):return observations[identifier]["acquisition_start"][:19]+" UTC · "+identifier
    st.markdown('<div class="global-kicker">02 / SCREEN A PAIR</div>',unsafe_allow_html=True)
    st.markdown("**Select observations / prepare a reproducible processing work order**")
    a,b=st.columns(2)
    before=a.selectbox("Before observation",ids,index=ids.index(defaults[0]),format_func=label,key="global_before_"+token)
    after=b.selectbox("After observation",ids,index=ids.index(defaults[1]),format_func=label,key="global_after_"+token)
    try:
        order=create_work_order(result,before,after)
    except (ValueError,TypeError,KeyError):
        st.error("Select two different observations in chronological order with recorded metadata checks.")
        return
    outcome=order["catalog_outcome"]
    _state_cards((("Catalog screening",outcome.replace("_"," "),"Recorded acquisition metadata only.","ready" if outcome=="PRELIMINARY_CANDIDATE" else "conditional"),
        ("Scientific processing","NOT YET VERIFIED","Authorized subsets and actual-product inspection are required.","conditional")))
    st.caption("Processing readiness: "+order["processing_readiness"]+
        ". This selector never downloads data or analyses another country using pilot pixels.")
    st.download_button("Download selected-pair processing work order (JSON)",json.dumps(order,indent=2),
        "sentinel-zero-selected-pair-work-order.json","application/json",on_click="ignore")
    with st.expander("Authorized acquisition and actual-product acceptance prerequisites"):
        st.dataframe([dict(Stage=s["stage"],Status=s["status"],Evidence=s["evidence"]) for s in order["steps"]],
                     hide_index=True,width="stretch")
        st.write("Use the official Earthdata Search/Harmony flow: one selected granule per bounded request; Frequency A HHHH, mask and numberOfLooks; GeoTIFF; spatial trim; No Reprojection. "
            "Inspect every result size before transfer and preserve source/job identity. Matching metadata does not accept delivered pixels.")
        st.json(order["acquisition"])
        st.write("Only after both observations pass documented product and quality inspection may the operator call "
            "application.global_workflow.process_inspected_pair. Unknown sizes, missing layers, failed hashes, incompatible native grids or incomplete quality review block processing.")


def footprint_figure(result: dict):
    """Actual CMR coverage polygons in lon/lat, not valid-pixel or imagery maps."""
    import plotly.graph_objects as go
    figure=go.Figure()
    for observation in result["observations"][:10]:
        for ring in observation["footprint"]:
            if not ring or max(p[0] for p in ring)-min(p[0] for p in ring)>=180: continue
            figure.add_trace(go.Scatter(x=[p[0] for p in ring],y=[p[1] for p in ring],mode="lines",fill="toself",
                                       opacity=.4,line=dict(color="#89a7d8",width=1),
                                       name=observation["granule_concept_id"],hovertemplate=observation["granule_concept_id"]+"<br>%{x:.4f} E, %{y:.4f} N<extra></extra>"))
    for box in result["query_boxes"]:
        w,s,e,n = [box[k] for k in ("west","south","east","north")]
        figure.add_trace(go.Scatter(x=[w,e,e,w,w],y=[s,s,n,n,s],mode="lines",name="Requested AOI",
                                   line=dict(color="#61d4c2",width=3,dash="dash")))
    figure.update_layout(height=350,margin=dict(l=45,r=15,t=15,b=45),paper_bgcolor="#142238",plot_bgcolor="#142238",
                         font=dict(color="#e8f0f8"),showlegend=False,xaxis_title="Longitude (degrees east)",yaxis_title="Latitude (degrees north)")
    return figure


@st.cache_data(ttl=600,max_entries=12,show_spinner=False)
def cached_discovery(query: GlobalQuery) -> dict:
    result = discover(query)
    if result["status"] == "SERVICE_ERROR":
        # Exceptions are not cached as a false successful/empty query.
        raise RuntimeError("CMR metadata retrieval failed; retry or check public network access. No availability conclusion can be made.")
    return result


def render_global() -> None:
    st.subheader("Global Earth Explorer")
    st.markdown('<div class="global-hero"><div class="global-kicker">EARTH OBSERVATION / NEXT INVESTIGATION</div>'
        '<div class="global-title">Choose a place. Follow the evidence.</div>'
        '<p class="global-summary">Find real NISAR acquisitions anywhere within valid search bounds. '
        'A catalog candidate becomes an investigation only after its actual pixels pass review.</p></div>',unsafe_allow_html=True)
    st.markdown('<div class="global-stage-rail"><span><strong>01</strong> Define an area</span>'
        '<span><strong>02</strong> Discover & screen</span><span><strong>03</strong> Inspect actual products</span></div>',unsafe_allow_html=True)
    controls,geography=st.columns([1.05,1.15],gap="large")
    with controls,st.container(key="global_controls",border=True):
        st.markdown('<div class="global-kicker">01 / DEFINE THE SEARCH</div>',unsafe_allow_html=True)
        st.selectbox("Explore a researched study window",("Custom coordinates",*STUDY_WINDOWS),
            key="study_window",on_change=_choose_window)
        with st.form("global_catalog_form"):
            mode=st.selectbox("Geographic input",("Centre coordinates","Bounding box"))
            a,b=st.columns(2)
            latitude=a.number_input("Latitude (degrees north)",min_value=-90.,max_value=90.,value=6.925,format="%.5f",key="global_lat")
            longitude=b.number_input("Longitude (degrees east)",min_value=-180.,max_value=180.,value=79.975,format="%.5f",key="global_lon")
            span=st.number_input("Window width/height (degrees)",min_value=.01,max_value=5.,value=.05,step=.01,key="global_span")
            with st.expander("Bounding-box inputs / antimeridian support"):
                west,south,east,north=[col.number_input(label,value=v,format="%.5f",key=key) for col,label,v,key in zip(st.columns(2)*2,
                    ("West longitude","South latitude","East longitude","North latitude"),(79.95,6.90,80.,6.95),
                    ("global_west","global_south","global_east","global_north"))]
                st.caption("West, south, east, north. East < west denotes a narrow antimeridian crossing. Maximum 5° per dimension; polar bounds are supported with conservative overlap screening.")
            x,y=st.columns(2)
            start=x.date_input("Acquisition start date (UTC)",value=date(2026,10,1))
            end=y.date_input("Acquisition end date (UTC, inclusive)",value=date(2026,10,31))
            polarization=st.selectbox("Requested polarization",("HH","HV","VV","VH","RH","RV"))
            submit=st.form_submit_button("Search actual NASA CMR",type="primary",width="stretch")
        st.caption("Explicit network action · maximum 50 granules per split box · ten-minute cache with original retrieval time.")
        with st.expander("Search scope and collection"):
            st.write("NASA CMR · PROVISIONAL L2 GCOV · "+COLLECTION)
            st.caption("Presets use recorded exploratory rectangles, not authoritative basin boundaries or a worldwide place lookup. No product download or authentication occurs here.")
            if st.button("Clear catalog cache"):
                cached_discovery.clear()
                st.session_state.pop("global_result",None)
    preview=None
    try:
        preview=form_query(mode,latitude,longitude,span,(west,south,east,north),start,end,polarization)
    except (ValueError,TypeError,OverflowError):
        pass
    with geography,st.container(key="global_geography",border=True):
        st.markdown('<div class="global-kicker">GEOGRAPHIC CONTEXT / OFFLINE</div>',unsafe_allow_html=True)
        if preview is not None:
            try:
                view=st.radio("Geographic preview",("World coordinates","Selected AOI"),horizontal=True,key="global_geographic_preview")
                chart=world_figure(preview) if view=="World coordinates" else aoi_figure(preview)
                st.plotly_chart(chart,width="stretch",config={"displayModeBar":False},alt="Selected geographic AOI boundary on longitude/latitude coordinates; no satellite pixels or country lookup")
                boxes=preview.validate()
                st.markdown('<div class="global-coordinate-readout">'+"".join('<code>'+escape(f"W {b.west:.5f} · S {b.south:.5f} · E {b.east:.5f} · N {b.north:.5f}")+'</code>' for b in boxes)+'</div>',unsafe_allow_html=True)
                st.caption("Exact angular AOI; cross marks its centre. Pan/zoom in Selected AOI view. No remote tiles, invented country outlines or observed-coverage claim.")
            except ImportError:
                st.info("Geographic chart unavailable; the explicit longitude/latitude inputs remain usable.")
        else:
            st.info("Check the coordinate bounds and UTC date order to preview this area.")
        _state_cards((("Previously verified investigation","SRI LANKAN PILOT","Inspected NISAR pixels; the separate verified pilot configuration is required.","ready"),
            ("Other selected locations","CATALOG DISCOVERY","Availability is real metadata; product processing is not yet verified.","conditional")))
        st.caption("Form values apply when Search is pressed. Recorded results below retain their own queried bounds and retrieval time.")
    if submit:
        try:
            query=form_query(mode,latitude,longitude,span,(west,south,east,north),start,end,polarization)
            with st.spinner("Requesting bounded public catalog metadata…"):
                result=cached_discovery(query)
            st.session_state["global_result"]=result
            st.session_state.pop("global_error",None)
        except (ValueError,TypeError,RuntimeError,OSError,OverflowError):
            st.session_state.pop("global_result",None)
            st.session_state["global_error"]="Invalid coordinates/dates or unavailable public CMR service. Check the AOI, date order and network; no result was substituted."
    if st.session_state.get("global_error"):
        st.error(st.session_state["global_error"])
    result = st.session_state.get("global_result")
    if result is None:
        st.markdown('<div class="global-empty"><strong>Your next investigation starts with a real observation.</strong>'
            '<p>Choose an area and date range, then search NASA CMR. No catalog records or satellite measurements are inferred from this selector.</p></div>',unsafe_allow_html=True)
        return
    st.divider()
    st.markdown('<div class="global-kicker">RECORDED QUERY RESULT / CATALOG METADATA</div>',unsafe_allow_html=True)
    state = result["status"]
    if state == "NO_MATCHES":
        st.info("No matching granules were returned for this collection, area and interval. This does not mean NISAR never observes this location.")
    elif state == "PARTIAL_RESULTS":
        st.warning("Partial catalog results: pagination or parsing did not finish. Review the recorded failures and narrow the query; no complete-coverage claim is made.")
    else:
        st.success(f"{result['retrieved_unique']} real catalog granules retrieved")
    compatible=sum(p["outcome"]=="PRELIMINARY_CANDIDATE" for p in result["pair_assessments"])
    _state_cards((("Actual returned records",str(result["retrieved_unique"]),"NASA CMR granule identifiers and UTC acquisitions.","neutral"),
        ("Preliminary candidate pairs",str(compatible),"Metadata screening only; not accepted scientific pairs.","conditional"),
        ("Pagination","COMPLETE" if result["pagination_complete"] else "INCOMPLETE","Bounded query result; no worldwide coverage inference.","ready" if result["pagination_complete"] else "conditional")))
    st.caption("Retrieved UTC: "+result["retrieved_at_utc"]+" · pagination complete: "+str(result["pagination_complete"])+" · these records belong to the recorded query, not unsubmitted form changes")
    st.write("**Queried bounds** · "+"; ".join(f"{b['west']:.5f}, {b['south']:.5f}, {b['east']:.5f}, {b['north']:.5f}" for b in result["query_boxes"]))
    if result["observations"]:
        st.markdown("**Acquisition footprints / catalog coverage only**")
        try:st.plotly_chart(footprint_figure(result),width="stretch",config={"displayModeBar":False},alt="Actual catalog footprint coordinates and requested AOI; no processed satellite pixels")
        except ImportError:st.info("Optional footprint plot unavailable; complete coordinate metadata remains below.")
        st.caption("Dashed cyan = query bounds; shaded polygons = up to ten actual catalog footprints. Longitude/latitude coordinate plot, not an area-preserving map, valid-data mask or processed imagery. Rings spanning the antimeridian are omitted from this preview; full metadata is retained.")
    with st.container(key="global_pair_workspace",border=True):
        _render_pair_workflow(result)
    with st.expander("All returned observations and compatibility checks"):
        st.dataframe([dict(Granule=o["granule_concept_id"],UTC=o["acquisition_start"],Track=o["track"],Frame=o["frame"],
            Orbit=o["orbit_direction"],Look=o["look_direction"],Maturity=o["maturity"],CRID=o["crid"]) for o in result["observations"]],hide_index=True,width="stretch")
        st.markdown("**Catalog compatibility screening / not scientific acceptance**")
        st.dataframe([dict(Before=p["before_id"],After=p["after_id"],Outcome=p["outcome"],
            Reasons="; ".join(c["name"]+": "+c["state"] for c in p["checks"] if c["state"]!="PASS")) for p in result["pair_assessments"]],hide_index=True,width="stretch")
    st.caption("Next gate: authorized, known-size subsets → actual layer/grid/quality inspection → eligible common support → independent change verification. Remote acquisition is not enabled in this visitor workspace. No Sri Lankan pixels are substituted for this location.")
    with st.expander("Coverage footprints and full query/check evidence"):
        st.json(result)
    st.download_button("Export actual catalog evidence (JSON)",json.dumps(result,indent=2),"sentinel-zero-global-catalog.json","application/json",on_click="ignore")
