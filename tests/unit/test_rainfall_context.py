"""Explicitly SYNTHETIC CSV fixtures test validation, never actual rainfall."""
import json

import pytest

from sentinel_zero.corroboration.rainfall import load_recorded_rainfall,unavailable
from sentinel_zero.science.preparation import sha256_file

START,END = "2026-10-06T00:00:00Z","2026-10-06T02:00:00Z"


@pytest.fixture
def witness(tmp_path):
    csv = tmp_path/"SYNTHETIC_TEST_ONLY.csv"
    csv.write_text("time,value\n2026-10-06T00:00:00Z,0\n2026-10-06T01:00:00Z,2\n2026-10-06T02:00:00Z,1\n")
    meta = dict(schema_version="1.0",provider="Meteomatics",filename=csv.name,sha256=sha256_file(csv),
                source_url="https://www.meteomatics.com/",retrieved_at_utc="2026-11-15T00:00:00Z",
                latitude=6.925,longitude=79.975,units="mm",time_basis="UTC",interval_hours=1,quantity="interval_precipitation",
                time_column="time",value_column="value",fixture_label="SYNTHETIC SOFTWARE TEST ONLY")
    manifest = tmp_path/"manifest.json";manifest.write_text(json.dumps(meta))
    return manifest,csv,meta


def test_context_has_no_causal_acceptance_or_fabricated_unavailable_rows(witness):
    manifest,_,_ = witness
    result = load_recorded_rainfall(manifest,START,END)
    assert result["status"] == "CONTEXT_AVAILABLE" and result["rows_in_acquisition_interval"] == 3
    assert result["interval_gaps"] == result["missing_values"] == 0
    assert "no causal" in result["interpretation"]
    assert unavailable()["rows"] == [] and unavailable()["status"] == "INSUFFICIENT_PHYSICAL_EVIDENCE"


@pytest.mark.parametrize("field,value",[("units","m"),("time_basis","local"),("quantity","unknown"),("latitude",91),
                                       ("interval_hours",0),("interval_hours",True),("sha256","0"*64),("filename","../escape.csv"),
                                       ("source_url","https://user:password@example.com/")])
def test_bad_source_metadata_blocks(witness,field,value):
    manifest,_,m = witness;m[field]=value;manifest.write_text(json.dumps(m))
    with pytest.raises(ValueError):load_recorded_rainfall(manifest,START,END)


@pytest.mark.parametrize("values",["-1","NaN","inf"])
def test_invalid_and_missing_rainfall_values(witness,values):
    manifest,csv,m=witness
    csv.write_text("time,value\n2026-10-06T00:00:00Z,"+values+"\n")
    m["sha256"]=sha256_file(csv);manifest.write_text(json.dumps(m))
    with pytest.raises(ValueError):load_recorded_rainfall(manifest,START,END)


def test_location_and_time_mismatch_remain_insufficient(witness):
    manifest,_,m=witness;m["longitude"]=-60;manifest.write_text(json.dumps(m))
    result=load_recorded_rainfall(manifest,START,END)
    assert result["status"] == "INSUFFICIENT_PHYSICAL_EVIDENCE"
