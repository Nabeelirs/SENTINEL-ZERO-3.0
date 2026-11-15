"""Real recorded metadata replay; labelled synthetic operator/error fixtures."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel_zero.application.global_explorer import GlobalQuery
from sentinel_zero.application.global_workflow import (STUDY_WINDOWS, InspectedInputs,
    authorize_known_assets,create_work_order,process_inspected_pair,selection_order)
from sentinel_zero.errors import MetadataError
from sentinel_zero.ingestion.acquisition import AcquisitionError

EVIDENCE=Path(__file__).resolve().parents[2]/"docs/evidence/sz009"


def recorded(name="brazil_amazon"):
    return json.loads((EVIDENCE/(name+"_catalog.json")).read_text())["result"]


def order():
    result=recorded()
    pair=next(p for p in result["pair_assessments"] if p["outcome"]=="PRELIMINARY_CANDIDATE")
    return create_work_order(result,pair["before_id"],pair["after_id"])


def test_recorded_brazil_work_order_is_real_metadata_not_scientific_acceptance():
    work=order()
    assert work["source"]["before"]["granule_concept_id"]=="G4357395719-ASF"
    assert work["source"]["after"]["granule_concept_id"]=="G4384604051-ASF"
    assert not work["scientific_acceptance"] and not work["acquisition"]["job_submitted"]
    assert work["processing_readiness"]=="REQUIRES_AUTHORIZED_ACQUISITION_AND_PRODUCT_REVIEW"
    assert len(work["acquisition"]["requested_datasets"])==3
    assert work["acquisition"]["expected_asset_bytes"] is None
    assert not work["acquisition"]["transfer_permitted"]
    assert work["acquisition"]["output_crs"] is None
    assert len(work["steps"])==7


def test_same_query_pair_has_deterministic_work_order_identity():
    assert order()["work_order_id"]==order()["work_order_id"]
    a,b=order(),order()
    assert a["source"]==b["source"]


@pytest.mark.parametrize("selection",[("G4357395719-ASF","G4357395719-ASF"),
    ("G4384604051-ASF","G4357395719-ASF"),("G1-ASF","G2-ASF")])
def test_wrong_dates_duplicate_or_unreturned_observations_rejected(selection):
    with pytest.raises(MetadataError):create_work_order(recorded(),*selection)


def test_incompatible_india_pair_cannot_authorize_scientific_transfer():
    r=recorded("india_brahmaputra")
    p=r["pair_assessments"][0]
    work=create_work_order(r,p["before_id"],p["after_id"])
    assert work["catalog_outcome"]=="INCOMPATIBLE"
    with pytest.raises(AcquisitionError,match="Catalog"):
        authorize_known_assets(work,[100]*6,already_downloaded=0)


def test_work_order_limits_and_identity_check_reject_tampering():
    r=recorded()
    r["observations"].append(deepcopy(r["observations"][0]))
    with pytest.raises(MetadataError,match="Duplicate"):selection_order(r)
    r=recorded();r["query_boxes"][0]["west"]+=.01
    p=r["pair_assessments"][0]
    with pytest.raises(MetadataError,match="bounds"):create_work_order(r,p["before_id"],p["after_id"])


def test_known_sizes_and_existing_cumulative_budget_required():
    work=order()
    assert authorize_known_assets(work,[100]*6,already_downloaded=8_000_000)==600
    for sizes,spent in (([None]*6,0),([100]*5,0),([50_000_000]*6,0),([100]*6,250_000_000)):
        with pytest.raises(AcquisitionError):authorize_known_assets(work,sizes,already_downloaded=spent)


def test_supported_study_presets_are_actual_recorded_query_bounds():
    for (label,expected),name in zip(STUDY_WINDOWS.items(),("sri_lanka","brazil_amazon","india_brahmaputra")):
        q=recorded(name)["query"]
        assert expected==tuple(q[k] for k in ("west","south","east","north"))
        assert "exploratory" in label
        GlobalQuery(**q).validate()


def test_reusable_processor_refuses_metadata_only_and_unrelated_local_pilot(tmp_path):
    work=order()
    wrong={"source":dict(collection_id="C2854338529-ASF",granule_id="G4348875889-ASF")}
    inputs=InspectedInputs(tmp_path,wrong,wrong,{})
    with pytest.raises(AcquisitionError,match="Selected catalog"):
        process_inspected_pair(work,inputs,tmp_path,"synthetic-error-case")
    work["source"]["query"]["west"]-=.01
    with pytest.raises(AcquisitionError,match="changed"):
        process_inspected_pair(work,inputs,tmp_path,"synthetic-error-case")
    assert not list(tmp_path.iterdir())


def synthetic_inventory(selected):
    return {"source":dict(collection_id=selected["collection_concept_id"],
        granule_id=selected["granule_concept_id"],producer_granule_id=selected["producer_granule_id"],
        acquisition_start_utc=selected["acquisition_start"],acquisition_end_utc=selected["acquisition_end"])}


def test_operator_adapter_calls_existing_science_in_order_after_verified_inputs(tmp_path,monkeypatch):
    """Synthetic mocked orchestration only; real processing is a separate gate."""
    from sentinel_zero.science import preparation,change
    import rasterio.warp
    work=order();calls=[]
    inputs=InspectedInputs(tmp_path,synthetic_inventory(work["source"]["before"]),
        synthetic_inventory(work["source"]["after"]),{"synthetic_test_only":True})
    grid={"crs_wkt":"synthetic","bounds_native":[0,0,1,1]}
    def verify(*args):calls.append("verify-six-real-inputs");return [],[],grid
    def prepare(source,output,name,*args,**kwargs):calls.append("prepare");return output/name
    def analyse(prepared,source,output,name,**kwargs):calls.append("analyse");return output/name
    def load(*args,**kwargs):calls.append("independent-reread");return SimpleNamespace(statistics={"eligible_cells":1},limitations=("Synthetic software test only",))
    monkeypatch.setattr(preparation,"verified_inputs",verify)
    monkeypatch.setattr(preparation,"prepare_pair",prepare)
    monkeypatch.setattr(preparation,"sha256_file",lambda _:"a"*64)
    monkeypatch.setattr(change,"analyze",analyse)
    monkeypatch.setattr(change,"load_change_product",load)
    monkeypatch.setattr(rasterio.warp,"transform_bounds",lambda *a,**k:(-60.08,-3.18,-59.98,-3.08))
    result=process_inspected_pair(work,inputs,tmp_path,"synthetic-orchestration")
    assert calls==["verify-six-real-inputs","prepare","analyse","independent-reread"]
    assert result["granule_ids"]==["G4357395719-ASF","G4384604051-ASF"]
    assert result["physical_cause"]=="UNVERIFIED"
    assert result["preparation"]["directory"]=="synthetic-orchestration-preparation"


def test_native_grid_processor_rejects_aoi_mismatch_before_output(tmp_path,monkeypatch):
    from sentinel_zero.science import preparation
    import rasterio.warp
    work=order()
    inputs=InspectedInputs(tmp_path,synthetic_inventory(work["source"]["before"]),synthetic_inventory(work["source"]["after"]),{})
    monkeypatch.setattr(preparation,"verified_inputs",lambda *args:([],[],{"crs_wkt":"synthetic","bounds_native":[0,0,1,1]}))
    monkeypatch.setattr(rasterio.warp,"transform_bounds",lambda *a,**k:(79.95,6.9,80,6.95))
    with pytest.raises(AcquisitionError,match="overlap"):
        process_inspected_pair(work,inputs,tmp_path,"synthetic-wrong-country")
    assert not list(tmp_path.iterdir())


def test_selected_aoi_plot_drives_exact_geographic_bounds():
    from sentinel_zero.presentation.global_view import aoi_figure
    q=GlobalQuery(179.95,-.05,-179.95,.05,"2026-10-01T00:00:00Z","2026-11-01T00:00:00Z")
    figure=aoi_figure(q)
    assert len(figure.data)==2
    assert max(figure.data[0].x)==180 and min(figure.data[1].x)==-180
    assert min(figure.data[0].y)==-.05 and max(figure.data[0].y)==.05


def test_operator_cli_help_and_manifest_integrity_failure(tmp_path,capsys):
    from sentinel_zero.application.global_workflow import main
    with pytest.raises(SystemExit) as help_result:main(["--help"])
    assert help_result.value.code==0
    assert "--work-order-sha256" in capsys.readouterr().out
    work=tmp_path/"synthetic_work_order.json"
    work.write_text("{}")
    args=[]
    for name in ("work-order","source-root","before-inventory","after-inventory","pair-assessment","output-root"):
        args.extend(["--"+name,str(work if name=="work-order" else tmp_path)])
    assert main(args+["--work-order-sha256","a"*64,"--run-id","synthetic-error-case"])==2
    assert "BLOCKED: CHECKSUM" in capsys.readouterr().out
