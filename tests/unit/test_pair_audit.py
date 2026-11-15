"""SYNTHETIC metadata/arrays ONLY; not real acquisition compatibility evidence."""
from copy import deepcopy
import numpy as np
import pytest

from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.ingestion.compatibility import compare, grids_equal, rectangle_overlap, shared_support, time_mapping
from sentinel_zero.ingestion.quality import focused_support, profile


@pytest.fixture
def reports():
    def one(day, granule):
        producer=f"SYNTHETIC_SOFTWARE_TEST_ONLY_202601{day:02d}T000000_202601{day:02d}T000100_TEST"
        source=dict(collection_id="C1-TEST",granule_id=granule,producer_granule_id=producer,
            acquisition_start_utc=f"2026-01-{day:02d}T00:00:00Z",acquisition_end_utc=f"2026-01-{day:02d}T00:01:00Z",
            maturity="PROVISIONAL",product_version="SYNTHETIC1",processing_crid="SYNTHETIC1")
        rasters=[]
        for layer in ("HHHH","mask","numberOfLooks"):
            rasters.append(dict(layer=layer,filename=producer+"_science_LSAR_GCOV_grids_frequencyA_"+layer+"_subsetted.tif",
                width=2,height=2,crs_wkt="SYNTHETIC_SHARED_PROJECTED_CRS",epsg=32644,
                transform=[10,0,400000,0,-10,770000],resolution=[10,10],first_pixel_center=[400005,769995],
                bounds_native=[400000,769980,400020,770000],driver_scale=1,driver_offset=0,
                band_tags={"units":"1","long_name":"SYNTHETIC HHHH" if layer=="HHHH" else "SYNTHETIC "+layer,
                    "description":"SYNTHETIC description: subswath; partially focused -> 0; fill -> 255"},
                finite_driver_valid_min=1,finite_driver_valid_max=3))
        return dict(source_context=source,rasters=rasters)
    first,second=one(1,"G1-TEST"),one(13,"G2-TEST")
    geometry=dict(track=1,frame=1,stack_id="SYNTHETIC_STACK",orbit_direction="ASCENDING",look_direction="LEFT",
        channels=[dict(frequency="A",range_bandwidth_mhz=40,polarizations=["HH","HV"])])
    profiles={"G1-TEST":deepcopy(geometry),"G2-TEST":deepcopy(geometry)}
    support=dict(before={"unexpected_mask_values":[]},after={"unexpected_mask_values":[]},common_valid_pixels=2)
    review=dict(visual_review_complete=True,evidence_refs=["SYNTHETIC_REVIEW_ONLY"],
        reviewed_granules=["G1-TEST","G2-TEST"],severe_unresolved_artifact=False,
        unresolved_limitations=["SYNTHETIC provider QA unavailable"],before_findings="SYNTHETIC",after_findings="SYNTHETIC")
    return first,second,dict(profiles=profiles,support=support,quality_review=review)


def test_equal_grid_and_explicit_review_permits_only_conditional_go(reports):
    a,b,args=reports
    result=compare(a,b,**args)
    assert result["decision"]=="CONDITIONAL_GO"
    assert result["grids_equal"] and result["overlap_native_square_units"]==400
    assert "restricted exploratory" in result["allowed_next_scope"]


@pytest.mark.parametrize("change",["shift","units","polarization","geometry","maturity","bandwidth","same_date","wrong_date_mapping","no_overlap","dimension"])
def test_actual_critical_incompatibilities_reject_without_correction(reports,change):
    a,b,args=deepcopy(reports)
    r=b["rasters"][0]
    if change=="shift": r["transform"][2]+=10
    elif change=="units": r["band_tags"]["units"]="dB"
    elif change=="polarization": r["band_tags"]["long_name"]="SYNTHETIC HVHV"
    elif change=="geometry": args["profiles"]["G2-TEST"]["track"]=2
    elif change=="maturity": b["source_context"]["maturity"]="BETA"
    elif change=="bandwidth": args["profiles"]["G2-TEST"]["channels"][0]["range_bandwidth_mhz"]=20
    elif change=="same_date": b["source_context"]["acquisition_start_utc"]=a["source_context"]["acquisition_start_utc"]
    elif change=="wrong_date_mapping": b["source_context"]["producer_granule_id"]=a["source_context"]["producer_granule_id"]
    elif change=="no_overlap": r["bounds_native"]=[500000,769980,500020,770000]
    else: r["width"]=3
    result=compare(a,b,**args)
    assert result["decision"]=="REJECTED" and any(c["decision"]=="FAIL" for c in result["checks"])
    if change=="shift":
        assert result["alignment_action"].startswith("Explicit reviewed common grid")


@pytest.mark.parametrize("missing",["quality","geometry","support","mask_description","unexpected_mask","reviewed_wrong_granule"])
def test_missing_essential_evidence_never_silently_accepts(reports,missing):
    a,b,args=deepcopy(reports)
    if missing=="quality": args["quality_review"]=None
    elif missing=="geometry": del args["profiles"]["G2-TEST"]["look_direction"]
    elif missing=="support": args["support"]=None
    elif missing=="mask_description": b["rasters"][1]["band_tags"]["description"]=""
    elif missing=="unexpected_mask": args["support"]["after"]["unexpected_mask_values"]=[7]
    else: args["quality_review"]["reviewed_granules"]=["G1-TEST","G3-TEST"]
    assert compare(a,b,**args)["decision"]=="INSUFFICIENT_EVIDENCE"


def test_dimensions_alone_do_not_establish_alignment(reports):
    a,b,_=reports
    b["rasters"][0]["transform"][2]+=.001
    assert not grids_equal(a["rasters"][0],b["rasters"][0])


def test_positive_overlap_and_edge_touch():
    assert rectangle_overlap([0,0,10,10],[5,5,12,12])==25
    assert rectangle_overlap([0,0,10,10],[10,0,12,10])==0
    with pytest.raises(AcquisitionError): rectangle_overlap([10,0,0,10],[0,0,1,1])


def test_shared_support_uses_pixelwise_nodata_mask_and_looks():
    a={"HHHH":np.ma.array([[1.,0,2],[np.nan,3,4]]),"mask":np.ma.array([[1,1,0],[1,255,1]],dtype="uint8"),"numberOfLooks":np.ma.ones((2,3))}
    b={"HHHH":np.ma.array([[2.,1,3],[4,5,6]]),"mask":np.ma.array([[1,1,1],[1,1,255]],dtype="uint8"),"numberOfLooks":np.ma.ones((2,3))}
    result=shared_support(a,b)
    assert result["before"]["eligible_pixels"]==2 and result["after"]["eligible_pixels"]==5
    assert result["common_valid_pixels"]==1 and result["valid_union_pixels"]==6
    assert result["before_only_pixels"]==1 and result["after_only_pixels"]==4


def test_positive_driver_nodata_and_invalid_looks_are_excluded():
    p=np.ma.array([[99.,1],[2,3]],mask=[[1,0],[0,0]])
    m=np.ma.ones((2,2),dtype="uint8");looks=np.ma.array([[1.,0],[np.nan,1]])
    valid,counts=focused_support(p,m,looks)
    assert valid.tolist()==[[False,False],[False,True]] and counts["eligible_pixels"]==1


def test_unknown_subswath_not_binary_good():
    valid,counts=focused_support(np.ma.ones((1,3)),np.ma.array([[0,7,255]],dtype="uint8"),np.ma.ones((1,3)))
    assert not valid.any() and counts["unexpected_mask_values"]==[7]
    with pytest.raises(AcquisitionError): focused_support(np.ma.ones((1,1)),np.ma.ones((1,1)),np.ma.ones((1,1)),allowed_subswaths=(0,))


def test_profiles_keep_empty_support_unknown():
    assert profile(np.array([[1.,2.],[3.,4.]]),np.array([[True,False],[False,False]]),1)==[0.0,None]


def test_date_mapping_is_checked_against_producer_time(reports):
    a,b,_=reports
    assert time_mapping(a["source_context"]) and time_mapping(b["source_context"])
    b["source_context"]["acquisition_start_utc"]="2026-02-01T00:00:00Z"
    assert not time_mapping(b["source_context"])
