"""Actual NISAR Earth Atlas contracts; frontend behavior has separate real-browser QA."""
from dataclasses import replace
from io import BytesIO
import base64
import json
import os
from pathlib import Path
import subprocess
import shutil

import numpy as np
from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from sentinel_zero.application.data import ANALYSIS_SHA256,configured_paths,load_explorer,pixel,watched_files
from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.presentation.display import comparison
from sentinel_zero.presentation.earth_viewer import accepted_selection,display_payload,grid_contract
from sentinel_zero.science.preparation import sha256_file

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def actual():
    source=os.environ.get('SENTINEL_ZERO_SOURCE_ROOT')
    if not source:pytest.skip('Actual verified NISAR runtime required, not a synthetic substitute')
    paths=configured_paths({'SENTINEL_ZERO_RUNTIME_ROOT':str(Path(source).parent)})
    return paths,load_explorer(paths)


@pytest.fixture
def app(actual,monkeypatch,tmp_path):
    paths,_=actual
    monkeypatch.setenv('SENTINEL_ZERO_RUNTIME_ROOT',str(paths.source.parent))
    monkeypatch.delenv('SENTINEL_ZERO_BUNDLE_ROOT',raising=False)
    monkeypatch.setenv('SENTINEL_ZERO_LAB_ROOT',str(tmp_path/'isolated-earth-sessions'))
    return AppTest.from_file(ROOT/'atlas_app.py',default_timeout=40).run()


@pytest.mark.real_data
def test_actual_payload_contains_exact_native_three_view_pngs_and_provenance(actual):
    paths,data=actual
    hashes={p:sha256_file(p) for p in watched_files(paths)}
    payload=display_payload(data)
    assert payload['identity']==ANALYSIS_SHA256
    assert payload['source_ids']==['G4348875889-ASF','G4378876686-ASF']
    assert payload['before_date']=='2026-10-06' and payload['after_date']=='2026-10-18'
    assert payload['grid']['transform']==[10,0,384000,0,-10,768350]
    assert payload['grid']['width']==payload['grid']['height']==553
    panels=comparison(data)
    for mode,panel in zip(('BEFORE','AFTER','CHANGE'),panels):
        content=base64.b64decode(payload['images'][mode]['png'].split(',',1)[1],validate=True)
        assert content==panel.png
        array=np.asarray(Image.open(BytesIO(content)))
        assert array.shape==(553,553,3) and tuple(array[0,0])==(183,183,183)
    assert payload['images']['BEFORE']['limits']==payload['images']['AFTER']['limits']==[-25,15]
    assert payload['images']['CHANGE']['limits']==[-6,6]
    assert {p:sha256_file(p) for p in hashes}==hashes


@pytest.mark.real_data
@pytest.mark.parametrize('mode',['BEFORE','AFTER','CHANGE','COMPARE'])
def test_selection_returns_authoritative_record_regardless_of_view_mode(actual,mode):
    _,data=actual
    result=accepted_selection(data,dict(row=157,column=309,identity=ANALYSIS_SHA256,mode=mode))
    assert result==pixel(data,157,309)
    assert result['saved_change_db']==-26.151365280151367
    assert (result['easting_m'],result['northing_m'])==(387095.0,766775.0)
    excluded=accepted_selection(data,dict(row=0,column=0,identity=ANALYSIS_SHA256,mode=mode))
    assert not excluded['eligible'] and excluded['saved_change_db'] is None


@pytest.mark.real_data
@pytest.mark.parametrize('mutation',[{'row':True},{'row':-1},{'column':553},{'column':1.5},
    {'identity':'0'*64},{'mode':'FLOOD'},{'value':100},{'path':'../private'},{'column':None}])
def test_untrusted_selection_cannot_supply_science_identity_values_or_paths(actual,mutation):
    _,data=actual
    raw=dict(row=157,column=309,identity=ANALYSIS_SHA256,mode='CHANGE');raw.update(mutation)
    with pytest.raises((ValueError,AcquisitionError)):accepted_selection(data,raw)


@pytest.mark.real_data
@pytest.mark.parametrize('transform',[[10,1,384000,0,-10,768350],[-10,0,384000,0,-10,768350],
    [10,0,384000,0,10,768350],[10,0,384000,0,float('nan'),768350]])
def test_viewer_refuses_rotated_reversed_or_nonfinite_grid_instead_of_repairing(actual,transform):
    _,data=actual
    wrong=replace(data.product.prepared,grid=dict(data.product.prepared.grid,transform=transform))
    with pytest.raises(ValueError):grid_contract(replace(data,product=replace(data.product,prepared=wrong)))


@pytest.mark.real_data
def test_new_shell_is_map_first_with_real_actions_and_native_fallback(app):
    assert not app.exception and not app.error
    assert app.title[0].value=='SENTINEL ZERO 3.0'
    assert len(app.get('bidi_component'))==1 and not app.metric
    for label in ('Start Investigation','Compare Observations','Explore Global Data','Open Evidence Trust Twin'):
        assert any(b.label==label for b in app.button)
    app.toggle(key='earth_static').set_value(True).run()
    assert not app.exception and len(app.get('image'))==3
    next(b for b in app.button if b.label=='Compare Observations').click().run()
    assert app.session_state['earth_requested_mode']=='COMPARE'


@pytest.mark.real_data
def test_selected_cell_survives_atlas_to_science_twin_and_report(app):
    app.session_state['selected_cell']=(157,309)
    next(b for b in app.button if b.label=='Start Investigation').click().run()
    assert not app.exception
    assert app.number_input(key='pixel_row').value==157 and app.number_input(key='pixel_column').value==309
    assert any(m.value=='-26.151365 dB' for m in app.metric)
    next(b for b in app.button if b.label=='Evidence Twin').click().run()
    assert not app.exception and len(app.get('plotly_chart'))==1
    assert app.session_state['selected_cell']==(157,309)
    app.radio(key='stage').set_value('Restore & Report').run()
    assert not app.exception and app.get('download_button')[0].proto.label=='Download Investigation Report (PDF)'
    assert app.session_state['selected_cell']==(157,309)


@pytest.mark.real_data
def test_second_canvas_selection_updates_payload_before_rendering(app):
    """Regression: the new cursor must not be reset by a previous server cell."""
    app.session_state['selected_cell']=(0,0)
    app.session_state['earth_canvas']={'selection':dict(row=157,column=309,
        identity=ANALYSIS_SHA256,mode='CHANGE',sequence=2)}
    app.run()
    assert not app.exception and not app.error
    assert app.session_state['selected_cell']==(157,309)
    payload=json.loads(app.get('bidi_component')[0].proto.json)
    assert payload['selected']==dict(row=157,column=309)


def test_global_discovery_remains_available_when_local_science_is_blocked(tmp_path,monkeypatch):
    monkeypatch.setenv('SENTINEL_ZERO_RUNTIME_ROOT',str(tmp_path/'missing'))
    monkeypatch.delenv('SENTINEL_ZERO_BUNDLE_ROOT',raising=False)
    app=AppTest.from_file(ROOT/'atlas_app.py',default_timeout=30).run()
    assert not app.exception and app.error and not app.get('bidi_component')
    next(b for b in app.button if b.label=='Global Explorer').click().run()
    assert not app.exception and not app.error and not app.get('bidi_component')
    assert any(b.label=='Search actual NASA CMR' for b in app.button)


def test_canvas_transform_inverse_zoom_anchor_and_coordinates_use_independent_known_cases():
    """Synthetic software geometry; never evidence of a new NASA observation."""
    node=shutil.which('node')
    if node is None:pytest.skip('Node unavailable for pure frontend math QA; browser acceptance remains required')
    uri=(ROOT/'src/sentinel_zero/presentation/earth_canvas.mjs').as_uri()
    code=f"""import {{fitTransform,pixelAt,pixelCentre,zoomTransform}} from {json.dumps(uri)};
    const rows=[];
    for(const [w,h] of [[1292,572],[900,477],[350,390]]){{
      const t=fitTransform(w,h,553,553,36),p={{row:157,column:309}};
      rows.push(pixelAt(t.x+(p.column+.5)*t.scale,t.y+(p.row+.5)*t.scale,t,553,553));
      rows.push(pixelAt(t.x-.001,t.y,t,553,553));
      rows.push(pixelAt(t.x+553*t.scale,t.y,t,553,553));
      const z=zoomTransform(t,w/2,h/2,1.4,t.scale*.8,t.scale*16);
      rows.push(Math.abs((w/2-t.x)/t.scale-(w/2-z.x)/z.scale)<1e-9);
    }}
    rows.push(pixelCentre(157,309,[10,0,384000,0,-10,768350]));
    rows.push(pixelCentre(0,0,[10,0,384000,0,-10,768350]));
    console.log(JSON.stringify(rows));"""
    result=json.loads(subprocess.check_output([node,'--input-type=module','-e',code],text=True))
    for index in (0,4,8):
        assert result[index:index+4]==[{'row':157,'column':309},None,None,True]
    assert result[-2:]==[{'easting':387095,'northing':766775},{'easting':384005,'northing':768345}]
