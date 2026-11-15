"""Map-first Earth Atlas, isolated from the accepted UIX-01 entry point."""
from __future__ import annotations

from html import escape
from importlib.resources import files

import streamlit as st

from ..application.data import configured_paths,fingerprint,pixel
from ..ingestion.acquisition import AcquisitionError
from .atlas_theme import stylesheet
from .earth_viewer import accepted_selection,canvas,display_payload
from .explorer import (STAGES,_exports,_investigate,_lineage,_maps,_provenance,_quality,
                       display_panels,passport_pdf,report_pdf,verified_data)
from .global_view import render_global
from .trust import cached_bom,snapshot

UI_BUILD = "SZ-UIX-02 / EARTH ATLAS 02"


def route(stage: str, workspace: str = "Verified Sri Lanka pilot") -> None:
    if stage not in STAGES or workspace not in ("Verified Sri Lanka pilot","Global NISAR discovery"):
        raise ValueError("Unsupported investigation route")
    st.session_state['stage']=stage
    st.session_state['observe_workspace']=workspace
    selected=st.session_state.get('selected_cell')
    if stage==STAGES[1] and selected is not None:
        st.session_state['pixel_row'],st.session_state['pixel_column']=selected


def compare_observations() -> None:
    route(STAGES[0])
    st.session_state['earth_request_id']=st.session_state.get('earth_request_id',0)+1
    st.session_state['earth_requested_mode']='COMPARE'


def selection_changed() -> None:
    raw=st.session_state.get('earth_canvas',{}).get('selection')
    if isinstance(raw,dict) and raw.get('mode') in ('BEFORE','AFTER','CHANGE','COMPARE'):
        st.session_state['earth_requested_mode']=raw['mode']


@st.cache_data(show_spinner=False,max_entries=3)
def cached_payload(identity: str,_data) -> dict:
    return display_payload(_data)


def _header() -> tuple[str,str,bool]:
    global_mode=st.session_state.get('stage',STAGES[0])==STAGES[0] and st.session_state.get('observe_workspace')=='Global NISAR discovery'
    with st.container(key='earth_header'):
        brand,location,actions=st.columns([1.3,1.4,1.35],gap='small')
        with brand,st.container(key='earth_identity'):
            st.title('SENTINEL ZERO 3.0')
            st.markdown('<div class="ea-subtitle">Earth Evidence Intelligence Platform</div>',unsafe_allow_html=True)
        with location,st.container(key='earth_location'):
            st.markdown('<div class="ea-header-context"><span>'+('GLOBAL MISSION / CATALOG METADATA' if global_mode else 'EARTH ATLAS / HISTORICAL OBSERVATIONS')+'</span><strong>'+('Global observation discovery · processing not yet verified' if global_mode else 'Sri Lanka · Kelani exploratory window')+'</strong></div>',unsafe_allow_html=True)
        with actions,st.container(key='earth_navigation'):
            global_button,twin_button=st.columns(2,gap='small')
            global_button.button('Global Explorer',on_click=route,args=(STAGES[0],'Global NISAR discovery'),width='stretch')
            twin_button.button('Evidence Twin',on_click=route,args=(STAGES[3],),width='stretch')
    with st.sidebar:
        st.markdown('### Your investigation')
        st.caption('Real NISAR observations · protected scientific record')
        stage=st.radio('Investigation stage',STAGES,key='stage')
        workspace=st.radio('Evidence workspace',('Verified Sri Lanka pilot','Global NISAR discovery'),key='observe_workspace')
        reverify=st.button('Reverify local evidence')
        st.caption(UI_BUILD)
        st.caption('Independent team application; NASA–ISRO NISAR attribution, no NASA endorsement.')
    return stage,workspace,reverify


def _actions() -> None:
    with st.container(key='earth_actions'):
        columns=st.columns(4,gap='small')
        columns[0].button('Start Investigation',type='primary',on_click=route,args=(STAGES[1],),width='stretch')
        columns[1].button('Compare Observations',on_click=compare_observations,width='stretch')
        columns[2].button('Explore Global Data',on_click=route,args=(STAGES[0],'Global NISAR discovery'),width='stretch')
        columns[3].button('Open Evidence Trust Twin',on_click=route,args=(STAGES[3],),width='stretch')


def _dossier(data,trust: dict) -> None:
    sources=data.product.provenance['sources'];stats=data.product.statistics
    st.markdown('<div class="ea-dossier"><span class="ea-kicker">INVESTIGATION / 001</span><h2>A window into the Kelani landscape.</h2><p>Two real radar observations. One native grid. Inspect the measurement before interpreting the change.</p><div class="ea-observation"><span>BEFORE</span><strong>'+escape(sources[0]['acquisition_start_utc'][:10])+'</strong><small>'+escape(sources[0]['granule_id'])+'</small></div><div class="ea-observation"><span>AFTER</span><strong>'+escape(sources[1]['acquisition_start_utc'][:10])+'</strong><small>'+escape(sources[1]['granule_id'])+'</small></div><div class="ea-reading"><strong>'+f"{stats['median']:+.3f} dB"+'</strong><span>median measured backscatter change<br>Physical cause remains unverified.</span></div></div>',unsafe_allow_html=True)
    integrity='Protected references match' if not trust['failed_artifacts'] else 'Disposable evidence needs revalidation'
    tone='match' if not trust['failed_artifacts'] else 'failed'
    st.markdown('<div class="ea-trust '+tone+'"><span>LOCAL EVIDENCE</span><strong>'+integrity+'</strong><small>Local consistency, not NASA authentication.</small></div><div class="ea-trust conditional"><span>SCIENTIFIC INTERPRETATION</span><strong>Conditional · PROVISIONAL GCOV</strong><small>Possible radiometric artifacts; cause and physical registration remain unverified.</small></div>',unsafe_allow_html=True)
    selected=st.session_state.get('selected_cell')
    with st.container(key='earth_selected'):
        if selected is None:
            st.markdown('<div class="ea-pixel-empty"><strong>Inspect a cell in the atlas</strong><span>Click the image, or use arrow keys and Enter. Exact verified measurements appear here.</span></div>',unsafe_allow_html=True)
        else:
            cell=pixel(data,*selected)
            st.markdown(f'<div class="ea-kicker">SELECTED / R {selected[0]} · C {selected[1]}</div>',unsafe_allow_html=True)
            st.caption(f"E {cell['easting_m']:.1f} · N {cell['northing_m']:.1f} m / EPSG:32644")
            if cell['eligible']:
                st.markdown('<div class="ea-cell-values">'+''.join('<div><span>'+label+'</span><strong>'+f'{value:+.6f} dB'+'</strong></div>' for label,value in (
                    ('Before',cell['before_db']),('After',cell['after_db']),('Saved change',cell['saved_change_db'])))+'</div>',unsafe_allow_html=True)
            else:
                st.warning('Excluded cell · '+cell['primary_exclusion_reason']+'. No saved change value.')
    st.caption(f"{stats['eligible_cells']:,} common eligible · {stats['excluded_cells']:,} excluded · about 5.53 km per grid dimension, not the whole Kelani Basin.")


def main() -> None:
    st.set_page_config(page_title='SENTINEL ZERO | Earth Atlas',page_icon='◈',layout='wide',initial_sidebar_state='collapsed')
    st.markdown(stylesheet()+'<style>'+files('sentinel_zero.presentation').joinpath('earth_shell.css').read_text(encoding='utf-8')+'</style>',unsafe_allow_html=True)
    stage,workspace,reverify=_header()
    if reverify:
        for cache in (verified_data,display_panels,passport_pdf,report_pdf,cached_bom,cached_payload):cache.clear()
    if stage==STAGES[0] and workspace=='Global NISAR discovery':
        st.button('Return to Earth Atlas',on_click=route,args=(STAGES[0],))
        with st.container(key='earth_global'):
            render_global()
        st.caption(UI_BUILD+' · global catalog metadata; pixels are not processed here.')
        return
    try:
        paths=configured_paths();identity=fingerprint(paths)
        with st.spinner('Verifying protected NISAR evidence…'):data=verified_data(paths,identity)
    except (AcquisitionError,OSError,ValueError,KeyError,TypeError):
        st.error('Scientific display blocked — missing or inconsistent verified data.')
        st.write('The operator must restore the recorded source/manifest identities and configured runtime. Global catalog discovery remains available. No substitute image or measurement is shown.')
        return
    if stage==STAGES[0]:
        _actions()
        st.caption('NASA–ISRO NISAR · L2 GCOV · Frequency A / HHHH  |  79.95–80.00° E · 6.90–6.95° N · exploratory rectangle')
        with st.container(key='earth_workspace'):
            view,rail=st.columns([2.8,1],gap='medium')
            with view:
                try:
                    # Consume the event before constructing the next canvas payload.
                    # Otherwise a second click is visually reset to the prior cell.
                    raw=st.session_state.get('earth_canvas',{}).get('selection')
                    if raw is not None and raw!=st.session_state.get('earth_seen_selection'):
                        cell=accepted_selection(data,raw)
                        st.session_state['selected_cell']=(cell['row'],cell['column'])
                        st.session_state['earth_seen_selection']=raw
                    payload=dict(cached_payload(identity,data),request=dict(id=st.session_state.get('earth_request_id',0),mode=st.session_state.get('earth_requested_mode','AFTER')),
                        selected=dict(zip(('row','column'),st.session_state['selected_cell'])) if st.session_state.get('selected_cell') is not None else None)
                    canvas(payload,on_selection_change=selection_changed)
                except (OSError,ValueError,ImportError,AcquisitionError):
                    st.error('Interactive atlas unavailable or selection rejected. Use the static scientific comparison and native cell inspector.')
            with rail:
                _dossier(data,snapshot(data,paths,identity)[1])
        with st.expander('Scientific meaning · what the images establish'):
            st.write('This is terrain-corrected gamma-zero HHHH backscatter power. Positive saved change means greater radar power on October 18, negative means lower. It does not establish flooding, damage or a physical cause. Coordinates describe matching declared grids; independent physical co-registration is unverified.')
        if st.toggle('Use static scientific comparison',value=False,key='earth_static'):
            _maps(data,identity)
    else:
        with st.container(key='earth_route'):
            for col,index in zip(st.columns(5),range(5)):
                col.button(STAGES[index],key=f'earth_route_{index}',on_click=route,args=(STAGES[index],),width='stretch',type='primary' if STAGES[index]==stage else 'secondary')
        st.caption('EARTH ATLAS / '+stage.upper()+' · same verified Sri Lankan investigation')
        if stage==STAGES[1]:
            with st.container(key='pixel_workspace'):_investigate(data)
        elif stage==STAGES[2]:
            with st.container(key='trust_workspace'):_provenance(data,paths,identity)
        elif stage==STAGES[3]:_lineage(data,paths,identity)
        else:
            with st.container(key='trust_workspace'):_exports(data,identity,paths)
    _quality(data)
    st.markdown('<div class="ea-footer"><span>'+UI_BUILD+'</span><span>Detect the Change. Investigate the Evidence. Protect the Decision.</span><span>Verified local record · conditional science · no live feed</span></div>',unsafe_allow_html=True)


if __name__=='__main__':main()
