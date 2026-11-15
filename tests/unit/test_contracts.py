from dataclasses import replace
from datetime import timedelta
import json

import pytest

from sentinel_zero.contracts import BoundingBox, Channel, DiscoveryQuery, DiscoveryResult, ObservationDescriptor, ProductReference, jsonable, utc
from sentinel_zero.errors import MetadataError


def test_valid_observation_round_trip_json(observation):
    result = json.loads(json.dumps(jsonable(observation), allow_nan=False))
    assert result["schema_version"] == "1.0"
    assert result["acquisition_start"] == "2026-10-06T00:00:00Z"
    assert result["crid"] == "TEST_CRID"


def test_required_identifiers(observation):
    for field, value in (("producer_granule_id", ""), ("granule_concept_id", "fake"), ("collection_concept_id", "G1-TEST")):
        with pytest.raises(MetadataError):
            replace(observation, **{field: value})
    with pytest.raises(TypeError):
        ObservationDescriptor()


def test_unknown_optional_fields_stay_unknown(observation):
    result = jsonable(replace(observation, track=None, crid=None, channels=()))
    assert result["track"] is None and result["crid"] is None and result["channels"] == []


@pytest.mark.parametrize("box", [(181, 0, 182, 1), (0, -91, 1, 1), (1, 1, 0, 2), (0, 2, 1, 1), (float("nan"), 0, 1, 1), (True, 0, 2, 2)])
def test_invalid_coordinates(box):
    with pytest.raises(MetadataError):
        BoundingBox(*box)


def test_temporal_validation(observation):
    with pytest.raises(MetadataError):
        replace(observation, acquisition_end=observation.acquisition_start - timedelta(seconds=1))
    with pytest.raises(MetadataError):
        utc("2026-01-01T00:00:00")
    assert utc("2026-01-01T05:30:00+05:30") == utc("2026-01-01T00:00:00Z")


@pytest.mark.parametrize("kwargs", [{}, {"short_name": "abc", "collection_concept_id": "C1-TEST"}, {"collection_concept_id": "C1-TEST", "page_size": 0}, {"collection_concept_id": "C1-TEST", "page_size": 11, "max_results": 10}, {"short_name": "abc", "temporal_start": "2026-01-01T00:00:00Z"}, {"short_name": "abc", "temporal_start": "2026-02-01T00:00:00Z", "temporal_end": "2026-01-01T00:00:00Z"}])
def test_query_validation(kwargs):
    with pytest.raises(MetadataError):
        DiscoveryQuery(**kwargs)


@pytest.mark.parametrize("url", ["https://example.invalid/file?token=secret", "https://user:password@example.invalid/a", "https://example.invalid/s3credentials", "http://example.invalid/a"])
def test_secret_references_rejected(url):
    with pytest.raises(MetadataError):
        ProductReference(url, "GET DATA")


def test_channel_and_geometry_validation(observation):
    with pytest.raises(MetadataError):
        Channel("A", ("HH",), 0)
    with pytest.raises(MetadataError):
        replace(observation, footprint=(((0, 0), (1, 1), (1, 0)),))
    with pytest.raises(MetadataError):
        replace(observation, orbit_direction="SIDEWAYS")
    with pytest.raises(MetadataError):
        replace(observation, stack_id="048_D_085")


def test_false_complete_result_rejected(observation):
    with pytest.raises(MetadataError):
        DiscoveryResult(DiscoveryQuery(collection_concept_id="C1-TEST"), observation.acquisition_start, observation.acquisition_start, 2, 1, True, (observation,))
