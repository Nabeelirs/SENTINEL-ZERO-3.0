"""UIX presentation checks using actual recorded catalog metadata, without network."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from sentinel_zero.application.global_explorer import GlobalQuery
from sentinel_zero.presentation.global_view import aoi_figure,form_query,world_figure


def recorded():
    path=Path(__file__).resolve().parents[2]/"docs/evidence/sz009/brazil_amazon_catalog.json"
    return json.loads(path.read_text())["result"]


def test_form_query_preserves_longitude_latitude_and_inclusive_utc_dates():
    query=form_query("Bounding box",0,0,.1,(-60.08,-3.18,-59.98,-3.08),
        date(2026,10,1),date(2026,10,31),"HH")
    assert (query.west,query.south,query.east,query.north)==(-60.08,-3.18,-59.98,-3.08)
    assert query.start_utc=="2026-10-01T00:00:00+00:00"
    assert query.end_utc=="2026-11-01T00:00:00+00:00"
    assert query.frequency=="A" and query.polarization=="HH"


def test_invalid_form_dates_are_rejected_before_network():
    with pytest.raises(ValueError):
        form_query("Centre coordinates",6.925,79.975,.05,(0,0,1,1),
            date(2026,10,31),date(2026,10,1),"HH")


def test_world_context_preserves_actual_small_aoi_geometry():
    query=GlobalQuery(**recorded()["query"])
    chart=world_figure(query)
    assert chart.layout.xaxis.range==(-180,180)
    assert chart.layout.yaxis.range==(-90,90)
    assert list(chart.data[0].x)==list(aoi_figure(query).data[0].x)
    assert list(chart.data[0].y)==list(aoi_figure(query).data[0].y)
    assert chart.data[1].x[0]==pytest.approx(-60.03)
    assert chart.data[1].y[0]==pytest.approx(-3.13)
    assert chart.data[1].name=="AOI centre"
    # No remote tile or country-topology dependency in the offline preview.
    assert "mapbox" not in chart.to_plotly_json()["layout"]


def test_antimeridian_world_context_keeps_each_actual_split_box():
    query=GlobalQuery(179.95,-.05,-179.95,.05,"2026-10-01T00:00:00Z","2026-11-01T00:00:00Z")
    chart=world_figure(query)
    assert len(chart.data)==4
    assert max(chart.data[0].x)==180 and min(chart.data[1].x)==-180
    assert chart.data[2].x[0]>179 and chart.data[3].x[0]<-179


def app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_string("from sentinel_zero.presentation.global_view import render_global\nrender_global()",default_timeout=20)


def test_opening_workspace_never_performs_automatic_discovery(monkeypatch):
    import sentinel_zero.presentation.global_view as module
    def forbidden(_):
        pytest.fail("A page render must not issue a NASA request")
    monkeypatch.setattr(module,"cached_discovery",forbidden)
    at=app().run()
    assert not at.exception
    assert any(x.value=="Global Earth Explorer" for x in at.subheader)
    assert at.selectbox(key="study_window").value=="Custom coordinates"
    assert any("No catalog records or satellite measurements are inferred" in x.value for x in at.markdown)


def test_recorded_result_is_visibly_separate_from_unsubmitted_form(monkeypatch):
    import sentinel_zero.presentation.global_view as module
    calls=[]
    def replay(query):
        calls.append(query)
        return deepcopy(recorded())
    monkeypatch.setattr(module,"cached_discovery",replay)
    at=app().run()
    # Values deliberately differ from the actual recorded Brazil result. The UI
    # must retain the recorded query identity instead of relabelling its pixels.
    next(b for b in at.button if b.label=="Search actual NASA CMR").click().run()
    assert not at.exception and len(calls)==1
    assert any("RECORDED QUERY RESULT" in x.value for x in at.markdown)
    assert any("-60.08000" in x.value for x in at.markdown)
    assert any("not unsubmitted form changes" in x.value for x in at.caption)
    assert any("NOT YET VERIFIED" in x.value for x in at.markdown)
    before=next(x for x in at.selectbox if x.label=="Before observation")
    after=next(x for x in at.selectbox if x.label=="After observation")
    assert before.value=="G4357395719-ASF" and after.value=="G4384604051-ASF"
    at.radio(key="global_geographic_preview").set_value("Selected AOI").run()
    assert not at.exception and len(calls)==1
    assert at.session_state["global_result"]["query"]["west"]==-60.08


def test_empty_actual_result_never_enables_pair_processing(monkeypatch):
    import sentinel_zero.presentation.global_view as module
    result=deepcopy(recorded())
    result.update(status="NO_MATCHES",retrieved_unique=0,observations=[],pair_assessments=[])
    monkeypatch.setattr(module,"cached_discovery",lambda _:result)
    at=app().run()
    next(b for b in at.button if b.label=="Search actual NASA CMR").click().run()
    assert not at.exception
    assert any("No matching granules" in x.value for x in at.info)
    assert not any(x.label=="Before observation" for x in at.selectbox)
    assert any("Two different acquisition times" in x.value for x in at.info)
