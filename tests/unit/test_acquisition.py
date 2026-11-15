"""Synthetic software fixtures only; no satellite bytes or live service calls."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sentinel_zero.contracts import ProductReference
from sentinel_zero.ingestion.acquisition import (AcquisitionError, DEFAULT_LAYERS, PILOT_LIMIT_BYTES,
                                               SourceAsset, new_destination, probe_head, safe_filename,
                                               source_asset, transfer_budget, validate_capabilities,
                                               verify_local_transfer)


@pytest.fixture
def asset(observation):
    producer = observation.producer_granule_id
    return SourceAsset("C1-TEST", "G1-TEST", producer, "SYNTHETIC_GCOV",
                       f"https://nisar.asf.earthdatacloud.nasa.gov/NISAR/SYNTHETIC_GCOV/{producer}/{producer}.h5", 24)


@pytest.fixture
def capabilities():
    return {"fixture_notice": "SYNTHETIC SOFTWARE TEST ONLY", "conceptId": "C1-TEST",
            "variables": [{"name": name} for name in DEFAULT_LAYERS],
            "services": [{"name": "SYNTHETIC_TEST_ADAPTER", "capabilities": {"subsetting": {"bbox": True, "variable": True}, "outputFormats": [{"mimeType": "image/tiff"}]}}]}


def test_known_cumulative_budget():
    assert transfer_budget([10, 20], already_downloaded=100) == 30
    assert transfer_budget([PILOT_LIMIT_BYTES]) == PILOT_LIMIT_BYTES


@pytest.mark.parametrize("sizes", [[], [None], [0], [-1], [True], [1.5], [PILOT_LIMIT_BYTES + 1], [200_000_000, 50_000_001], [6_094_323_712]])
def test_unknown_or_oversize_is_never_authorized(sizes):
    with pytest.raises(AcquisitionError):
        transfer_budget(sizes)


def test_partial_attempts_consume_authorization():
    with pytest.raises(AcquisitionError):
        transfer_budget([100], already_downloaded=PILOT_LIMIT_BYTES - 99)


@pytest.mark.parametrize("url", ["https://example.invalid/a.h5", "http://nisar.asf.earthdatacloud.nasa.gov/a.h5", "https://nisar.asf.earthdatacloud.nasa.gov/a.h5?token=SYNTHETIC", "s3://synthetic/a.h5", "https://user:SYNTHETIC@nisar.asf.earthdatacloud.nasa.gov/a.h5"])
def test_invalid_product_references(asset, url):
    with pytest.raises(AcquisitionError):
        replace(asset, url=url)


def test_source_identity_preserved(fixture_data, observation, asset):
    item = deepcopy(fixture_data["granule"])
    item["umm"]["DataGranule"]["ArchiveAndDistributionInformation"] = [{"Name": observation.producer_granule_id + ".h5", "SizeInBytes": 24, "Format": "HDF5"}]
    obs = replace(observation, product_references=(ProductReference(asset.url, "GET DATA", "HDF5"),))
    result = source_asset(item, obs)
    assert result.granule_id == observation.granule_concept_id and result.producer_id == observation.producer_granule_id
    item["meta"]["concept-id"] = "G999-TEST"
    with pytest.raises(AcquisitionError, match="disagrees"):
        source_asset(item, obs)


def test_missing_product_reference(fixture_data, observation):
    with pytest.raises(AcquisitionError):
        source_asset(fixture_data["granule"], replace(observation, product_references=()))


@pytest.mark.parametrize("code,headers,outcome", [(401, {}, "AUTHENTICATION_REQUIRED"), (302, {"Location": "https://urs.earthdata.nasa.gov/oauth/authorize?state=SYNTHETIC_PRIVATE_STATE"}, "AUTHENTICATION_REQUIRED"), (403, {}, "ACCESS_DENIED"), (500, {}, "REDIRECT_OR_HTTP_FAILURE")])
def test_auth_http_handling_and_secret_redaction(asset, code, headers, outcome):
    result = probe_head(asset, lambda asset: {"status": code, "headers": headers})
    assert result["outcome"] == outcome and result["scientific_body_bytes"] == 0
    assert "SYNTHETIC_PRIVATE_STATE" not in str(result) and "Location" not in str(result)


def test_head_success_is_not_download_success(asset):
    result = probe_head(asset, lambda asset: {"status": 200, "headers": {"Content-Length": "24", "Accept-Ranges": "bytes"}})
    assert result["catalog_size_matches_head"] and result["content_length"] == 24
    assert not result["protected_get_verified"] and result["scientific_body_bytes"] == 0


def test_head_network_failure(asset):
    def fail(asset):
        raise AcquisitionError("NETWORK", "No response")
    assert probe_head(asset, fail)["outcome"] == "NETWORK"


def test_collection_capability_not_granule_acceptance(capabilities):
    result = validate_capabilities(capabilities, "C1-TEST", DEFAULT_LAYERS)
    assert result["collection_capability_verified"] and not result["selected_granule_execution_verified"]


def test_summary_cannot_combine_incompatible_services(capabilities):
    service = capabilities["services"][0]
    service["capabilities"]["subsetting"]["bbox"] = False
    capabilities["summary"] = {"subsetting": {"bbox": True, "variable": True}}
    with pytest.raises(AcquisitionError):
        validate_capabilities(capabilities, "C1-TEST", DEFAULT_LAYERS)


@pytest.mark.parametrize("mode", ["wrong_collection", "missing_mask", "unadvertised_layer", "empty_selection"])
def test_unsupported_layers_and_modes(capabilities, mode):
    layers = DEFAULT_LAYERS
    if mode == "wrong_collection":
        capabilities["conceptId"] = "C2-TEST"
    elif mode == "missing_mask":
        capabilities["variables"] = [v for v in capabilities["variables"] if not v["name"].endswith("/mask")]
    elif mode == "unadvertised_layer":
        layers = ("/science/LSAR/GCOV/grids/frequencyA/HHHV",)
    else:
        layers = ()
    with pytest.raises(AcquisitionError):
        validate_capabilities(capabilities, "C1-TEST", layers)


@pytest.mark.parametrize("name", ["../escape.tif", "C:/escape.tif", "..\\escape.tif", "CON", "NUL.tif", "name.tif:stream", "space .", ".", "x\n.tif"])
def test_cross_platform_filename_safety(name):
    with pytest.raises(AcquisitionError):
        safe_filename(name)


def test_no_unsafe_overwrite(tmp_path):
    output = new_destination(tmp_path, "SYNTHETIC.tif")
    output.write_bytes(b"SYNTHETIC TEST ONLY")
    with pytest.raises(AcquisitionError):
        new_destination(tmp_path, output.name)
    assert output.read_bytes() == b"SYNTHETIC TEST ONLY"


def test_onedrive_runtime_rejected(tmp_path):
    directory = tmp_path / "OneDrive" / "runtime"
    directory.mkdir(parents=True)
    with pytest.raises(AcquisitionError):
        new_destination(directory, "SYNTHETIC.tif")


def test_reparse_root_rejected_without_platform_symlink_privilege(tmp_path, monkeypatch):
    original = Path.lstat
    def fake_lstat(path):
        details = original(path)
        if path == tmp_path:
            return SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400)
        return details
    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(AcquisitionError, match="reparse"):
        new_destination(tmp_path, "SYNTHETIC.tif")


def test_missing_file(tmp_path):
    with pytest.raises(AcquisitionError) as error:
        verify_local_transfer(tmp_path, "missing.tif", expected_size=24, expected_sha256="0"*64, source_granule_id="G1-TEST")
    assert error.value.code == "MISSING_FILE"


def test_partial_transfer_and_corruption(tmp_path):
    content = b"SYNTHETIC SOFTWARE TEST ONLY"
    path = tmp_path / "SYNTHETIC.tif"
    path.write_bytes(content)
    with pytest.raises(AcquisitionError) as error:
        verify_local_transfer(tmp_path, path.name, expected_size=len(content)+1, expected_sha256=sha256(content).hexdigest(), source_granule_id="G1-TEST")
    assert error.value.code == "PARTIAL_TRANSFER"
    with pytest.raises(AcquisitionError) as error:
        verify_local_transfer(tmp_path, path.name, expected_size=len(content), expected_sha256="0"*64, source_granule_id="G1-TEST")
    assert error.value.code == "CHECKSUM"


def test_checksum_match_not_scientific_acceptance(tmp_path):
    content = b"SYNTHETIC SOFTWARE TEST ONLY"
    path = tmp_path / "SYNTHETIC.tif"
    path.write_bytes(content)
    result = verify_local_transfer(tmp_path, path.name, expected_size=len(content), expected_sha256=sha256(content).hexdigest(), source_granule_id="G1-TEST")
    assert result["source_granule_id"] == "G1-TEST" and result["byte_consistency"] == "MATCH"
    assert not result["scientific_acceptance"] and not result["source_identity_independently_verified"]
