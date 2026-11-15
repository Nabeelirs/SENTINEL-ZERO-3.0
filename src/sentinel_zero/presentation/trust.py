"""Accessible EarthBOM/graph controls and session-isolated evidence challenge."""
import os
from pathlib import Path
import tempfile
from html import escape

import streamlit as st

from ..application.data import ExplorerData,RuntimePaths
from ..evidence.earthbom import EarthBOM,build_earthbom,evaluate
from ..evidence.lab import ARTIFACT_ID,EvidenceLab
from ..evidence.rebuild import rebuild_statistics
from ..evidence.impact import decision_impact


def _trust_styles() -> None:
    """Scoped visual vocabulary shared by challenge, graph and recovery."""
    st.markdown('''<style>
    .uix-trust-eyebrow{display:block;color:#8de2d1;font:600 .64rem/1.6 ui-monospace,monospace;letter-spacing:.12em}
    .uix-trust-intro{padding:20px 24px;border:1px solid #304a61;border-left:3px solid #67d8c3;background:#102234;border-radius:10px;margin:8px 0 18px}
    .uix-trust-intro h3{font-size:1.55rem;letter-spacing:-.04em;padding:5px 0 7px;color:#f1f6fd}
    .uix-trust-intro p{color:#b7c8dc;max-width:850px;margin:0;font-size:.9rem;line-height:1.6}
    .uix-trust-sequence{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid #2c4158;background:#0c192a;border-radius:10px;margin:0 0 16px;overflow:hidden}
    .uix-trust-sequence>div{padding:18px;border-right:1px solid #2c4158;min-width:0}.uix-trust-sequence>div:last-child{border:0}
    .uix-trust-sequence b{font:500 .68rem ui-monospace,monospace;color:#78d8ca;display:block;margin-bottom:10px}
    .uix-trust-sequence strong{display:block;color:#e8f1fc;font-size:.88rem;margin-bottom:5px}.uix-trust-sequence span{color:#a7bbd1;font-size:.75rem;line-height:1.5;display:block}
    .uix-trust-status{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0 16px}
    .uix-trust-state{border:1px solid #2c4058;background:#101d2e;border-radius:8px;padding:15px 17px;min-width:0}
    .uix-trust-state>span,.uix-trust-impact span{color:#a9bdd3;font:500 .62rem/1.6 ui-monospace,monospace;letter-spacing:.07em}
    .uix-trust-state strong{display:block;font-size:.84rem;line-height:1.5;margin:7px 0;overflow-wrap:anywhere;color:#ecf3fd}
    .uix-trust-state small{display:block;color:#a9bdd3;font-size:.72rem;line-height:1.5}
    .uix-trust-state.matched{border-top:2px solid #65d6bd}.uix-trust-state.failed{border-top:2px solid #fb9e9e}.uix-trust-state.conditional{border-top:2px solid #f1c580}.uix-trust-state.unknown{border-top:2px solid #aec2d9}
    .uix-trust-result{border-left:3px solid #65d6bd;background:#102a2b;padding:15px 18px;margin:12px 0;border-radius:4px}
    .uix-trust-result.failed{border-color:#fb9e9e;background:#30202a}.uix-trust-result strong{color:#f2f7fd;display:block;margin-bottom:5px;font-size:.9rem}.uix-trust-result span{color:#c1d0df;font-size:.8rem;line-height:1.6}
    .uix-trust-graph-meta{display:flex;flex-wrap:wrap;gap:10px 24px;padding:12px 0;color:#a4bad0;font:500 .68rem/1.6 ui-monospace,monospace}.uix-trust-graph-meta b{color:#ecf4fc;font-size:.9rem}
    .uix-trust-graph-meta>span:last-child{margin-left:auto;font-size:.58rem;letter-spacing:.08em;color:#91acbf}
    .uix-trust-legend{display:flex;flex-wrap:wrap;gap:9px 20px;font-size:.72rem;padding:10px 0}.uix-trust-legend>span:before{content:'';display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:7px;background:currentColor}
    .uix-trust-legend .matched{color:#65d6bd}.uix-trust-legend .failed{color:#fb9e9e}.uix-trust-legend .conditional{color:#f1c580}.uix-trust-legend .unknown{color:#aec2d9}
    .uix-trust-detail{border:1px solid #2c5266;border-left:3px solid #65d6bd;padding:18px 21px;border-radius:8px;background:#112739;margin:16px 0}
    .uix-trust-detail h3{color:#f3f7fd;font-size:1.15rem;line-height:1.4;padding:6px 0 9px}.uix-trust-detail code{font-size:.76rem;color:#c1d3e4;overflow-wrap:anywhere;white-space:normal}.uix-trust-detail p{color:#c3d5e2;margin:10px 0 0;font-size:.8rem}
    .uix-trust-impact{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:8px 0 15px}.uix-trust-impact>div{background:#101e2f;padding:15px 18px;border:1px solid #2b4055;border-radius:8px}.uix-trust-impact strong{color:#edf4fc;font-size:.84rem;line-height:1.6;display:block;padding-top:7px}
    .uix-trust-list{padding:15px 17px;border:1px solid #30465c;border-left:3px solid #aec2d9;background:#102033;margin:8px 0;border-radius:7px;min-width:0}.uix-trust-list.matched{border-left-color:#65d6bd}.uix-trust-list.failed{border-left-color:#fb9e9e}.uix-trust-list.conditional{border-left-color:#f1c580}
    .uix-trust-list span{color:#a6bdd1;font-size:.67rem}.uix-trust-list strong{display:block;font-size:.9rem;color:#f0f5fc;margin:4px 0}.uix-trust-list small{display:block;font-size:.68rem;letter-spacing:.04em;color:#c4d3e2}.uix-trust-list code{display:block;overflow-wrap:anywhere;white-space:normal;color:#bdcddd;font-size:.69rem;margin-top:7px}
    @media(max-width:850px){.uix-trust-sequence{grid-template-columns:repeat(2,minmax(0,1fr))}.uix-trust-sequence>div{border-bottom:1px solid #2c4158}.uix-trust-sequence>div:nth-child(2){border-right:0}.uix-trust-status{grid-template-columns:1fr}}
    @media(max-width:560px){.uix-trust-impact{grid-template-columns:1fr}.uix-trust-intro{padding:17px}.uix-trust-intro h3{font-size:1.25rem}.uix-trust-sequence>div{padding:14px}.uix-trust-graph-meta>span:last-child{margin-left:0}}
    </style>''', unsafe_allow_html=True)


@st.cache_data(show_spinner=False,max_entries=3)
def cached_bom(paths: RuntimePaths,identity: str,_data: ExplorerData) -> EarthBOM:
    return build_earthbom(_data,paths)


def witness_context(data: ExplorerData) -> dict:
    from .witness import witness_context as current_context
    return current_context(data)


def snapshot(data: ExplorerData,paths: RuntimePaths,identity: str) -> tuple[EarthBOM,dict]:
    bom = cached_bom(paths,identity,data)
    lab = st.session_state.get("evidence_lab")
    event = None
    if lab is not None:
        try:
            event = lab.verify()
            state = evaluate(bom,{ARTIFACT_ID:event["state"]})
            state["demonstration"] = dict(session_id=lab.root.name,event_count=len(lab.events()),
                                         state=event["state"],events=lab.events())
        except (OSError,ValueError,KeyError):
            state = evaluate(bom,{ARTIFACT_ID:"MISSING_OR_UNSAFE"})
            state["demonstration"] = dict(state="BLOCKED",reason="Sandbox or trusted reference failed validation; no automatic baseline replacement")
    else:
        state = evaluate(bom)
        state["demonstration"] = dict(state="NOT_STARTED")
    state["witness"] = witness_context(data)
    receipt=st.session_state.get('rebuild_receipt')
    state['rebuild']=dict(receipt) if receipt else None
    if receipt:
        current=(event is not None and event.get('observed_sha256')==receipt['regenerated_sha256'] and event['state']=='MATCH')
        state['rebuild']['current_applicability']='CURRENT_COPY_MATCHES_REBUILD' if current else 'HISTORICAL_REBUILD_COPY_NOW_REQUIRES_REVALIDATION'
        if not current:
            state['rebuild']['local_revalidation']='HISTORICAL_RECEIPT_ONLY_CURRENT_COPY_REQUIRES_REVALIDATION'
    state['decision_impact']=decision_impact(bom,st.session_state.get('selected_evidence_artifact',ARTIFACT_ID),state)
    return bom,state


def status_cards(state: dict) -> None:
    _trust_styles()
    values = (("LOCAL INTEGRITY", state["local_integrity"], "failed" if state["failed_artifacts"] else "matched", "Consistency against recorded local references"),
              ("SCIENTIFIC QUALITY", state["scientific_quality"], "conditional", "Provisional product limitations still apply"),
              ("PHYSICAL CAUSE", state["environmental_interpretation"], "unknown", "No environmental cause established"))
    st.markdown('<div class="uix-trust-status">'+''.join(
        '<div class="uix-trust-state '+tone+'"><span>'+escape(title)+'</span><strong>'+escape(value.replace('_',' '))+'</strong><small>'+escape(note)+'</small></div>'
        for title,value,tone,note in values)+'</div>', unsafe_allow_html=True)
    if state["failed_artifacts"]:
        st.error("Disposable evidence mismatch: dependent report/manifest findings require revalidation. The displayed radar maps still use the protected verified baseline.")


def challenge_controls(data: ExplorerData,paths: RuntimePaths,identity: str) -> None:
    _trust_styles()
    st.markdown("### Controlled evidence challenge")
    st.markdown('<div class="uix-trust-intro"><span class="uix-trust-eyebrow">DERIVED EVIDENCE / ISOLATED DEMONSTRATION</span><h3>Can the evidence survive a challenge?</h3><p>Test one disposable statistics record. Observe a real checksum mismatch, then follow the report dependencies that require revalidation.</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="uix-trust-sequence"><div><b>01</b><strong>Verified reference</strong><span>Protected scientific record</span></div><div><b>02</b><strong>Disposable copy</strong><span>Separate session sandbox</span></div><div><b>03</b><strong>Controlled difference</strong><span>Detect a real byte mismatch</span></div><div><b>04</b><strong>Trace & revalidate</strong><span>Follow actual dependencies</span></div></div>', unsafe_allow_html=True)
    st.caption("Only a service-created copy of the derived statistics JSON is modified. Original NASA subsets, published analysis and protected release baselines remain untouched.")
    try:
        if st.button("Create isolated evidence copy",type="primary",disabled=st.session_state.get("evidence_lab") is not None):
            bom = cached_bom(paths,identity,data)
            parent = Path(os.environ.get("SENTINEL_ZERO_LAB_ROOT",str(Path(tempfile.gettempdir())/"sentinel-zero-lab")))
            st.session_state["evidence_lab"] = EvidenceLab.create(parent,paths.analysis/"change_statistics.json",bom.node(ARTIFACT_ID).sha256)
        lab = st.session_state.get("evidence_lab")
        if lab is not None:
            a,b = st.columns(2)
            if a.button("Modify disposable copy"):
                lab.challenge()
            if b.button("Verify disposable copy"):
                lab.verify()
            state = lab.verify()
            st.write("**Disposable copy:** "+state["state"])
            tone = "failed" if state["state"] != "MATCH" else "matched"
            title = "Difference detected" if state["state"] != "MATCH" else "Copy matches its reference"
            detail = "Follow Trace Consequences to see affected records. The displayed radar maps still use the protected baseline." if state["state"] != "MATCH" else "The disposable record is byte-consistent. Scientific limitations and unknown physical cause remain."
            st.markdown('<div class="uix-trust-result '+tone+'"><strong>'+title+'</strong><span>'+escape(detail)+'</span></div>', unsafe_allow_html=True)
            with st.expander("Expected and observed copy hashes / event history"):
                st.json(dict(expected_sha256=state["expected_sha256"],observed_sha256=state["observed_sha256"],events=lab.events()))
            st.caption("The deterministic challenge appends clearly labelled non-scientific bytes. The corrupted copy is never parsed as scientific results. Restore in stage 5. This local journal is not a tamper-proof external audit anchor.")
    except (OSError,ValueError,KeyError):
        st.error("Evidence challenge blocked by sandbox/reference validation. Protected files and acceptance hashes were not changed.")


def graph_view(bom: EarthBOM,state: dict) -> None:
    from .evidence_graph import render_graph
    _trust_styles()
    render_graph(bom,state)


def restore_controls(paths: RuntimePaths | None = None) -> None:
    _trust_styles()
    lab = st.session_state.get("evidence_lab")
    if lab is None:
        st.info("No disposable-copy challenge is active. Start one in Challenge Evidence; original scientific evidence is never a recovery target.")
        return
    try:
        current = lab.verify()
    except (OSError,ValueError,KeyError):
        st.error("Recovery blocked: inspect the sandbox and independent reference. No protected baseline was changed.")
        return
    st.markdown('<div class="uix-trust-impact"><div><span>RESTORE BYTES</span><strong>Replace the disposable copy from its verified reference, then check the hash.</strong></div><div><span>RECOMPUTE THE SCIENCE</span><strong>Regenerate the statistics from verified change and common-support arrays.</strong></div></div>',unsafe_allow_html=True)
    if st.button("Restore and Revalidate",type="primary"):
        try:
            result = lab.restore()
            st.success("Disposable bytes restored and hash reverified: "+result["state"]+". Scientific limitations remain unchanged.")
        except (OSError,ValueError,KeyError):
            st.error("Recovery blocked: inspect the sandbox and independent reference. No protected baseline was changed.")
    if paths is not None:
        st.markdown('#### Reconstruct from verified dependencies')
        st.write('Recompute the statistics from the protected saved change raster and common eligibility mask. This is scientific recomputation of a derived summary, separate from copying baseline bytes.')
        if st.button('Rebuild statistics from verified inputs',type='primary'):
            try:
                receipt=rebuild_statistics(paths,lab)
                st.session_state['rebuild_receipt']=receipt
                if receipt['byte_identical'] and receipt['observed_state']=='MATCH':
                    st.success('REBUILT AND REVERIFIED: regenerated values and SHA-256 match the independent scientific record. Provisional quality and physical-cause limitations remain.')
                else:
                    st.warning('Numerically equivalent reconstruction has different bytes. Local integrity remains MISMATCH; the trusted hash was not changed.')
            except (OSError,ValueError,KeyError):
                st.error('Reconstruction blocked: a protected dependency, identity or scientific comparison failed. No acceptance reference was rewritten.')
        if st.session_state.get('rebuild_receipt'):
            with st.expander('Last rebuild receipt / current copy status is shown separately'):
                st.json(st.session_state['rebuild_receipt'])
    try:
        # Render after action handlers, so the same visible run cannot show the
        # pre-recovery status beside a successful restoration/rebuild message.
        current = lab.verify()
        tone = "matched" if current["state"] == "MATCH" else "failed"
        st.markdown('<div class="uix-trust-result '+tone+'"><strong>Disposable evidence / '+escape(current['state'])+'</strong><span>Byte restoration and genuine statistics recomputation use independent protected references. Neither removes unresolved scientific limitations.</span></div>', unsafe_allow_html=True)
    except (OSError,ValueError,KeyError):
        st.error("Current disposable-copy state could not be reverified. No protected baseline was changed.")


def render_witness(data: ExplorerData) -> None:
    from .witness import render_witness as current_view
    with st.container(key='witness_controls'):
        current_view(data)
