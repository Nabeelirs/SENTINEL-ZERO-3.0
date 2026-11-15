from dataclasses import replace
from datetime import timedelta

import pytest

from sentinel_zero.contracts import BoundingBox, Channel, PairOutcome
from sentinel_zero.discovery.selection import assess_candidates, assess_pair


def test_compatible_catalog_is_never_scientific_acceptance(observation, after):
    result = assess_pair(after, observation, aoi=BoundingBox(79.75, 6.7, 80.85, 7.3))
    assert result.outcome == PairOutcome.PRELIMINARY_CANDIDATE
    assert result.before_id == observation.granule_concept_id and not result.scientific_acceptance
    assert all(check.state == "PASS" for check in result.checks)


@pytest.mark.parametrize("field,value", [("orbit_direction", "DESCENDING"), ("look_direction", "RIGHT"), ("track", 48), ("frame", 85), ("maturity", "VALIDATED"), ("crid", "OTHER"), ("product_version", "2.0"), ("product_family", "GSLC")])
def test_incompatible_metadata(field, value, observation, after):
    changes = {field: value}
    if field in ("orbit_direction", "track", "frame"):
        changes["stack_id"] = f"{changes.get('track', after.track):03d}_{changes.get('orbit_direction', after.orbit_direction)[0]}_{changes.get('frame', after.frame):03d}"
    result = assess_pair(observation, replace(after, **changes))
    assert result.outcome == PairOutcome.INCOMPATIBLE
    assert any(c.name == field and c.state == "FAIL" for c in result.checks)


@pytest.mark.parametrize("channels", [(Channel("A", ("VV",), 40),), (Channel("B", ("HH", "HV"), 40),), (Channel("A", ("HH", "HV"), 20),)])
def test_channel_incompatibility(channels, observation, after):
    assert assess_pair(observation, replace(after, channels=channels)).outcome == PairOutcome.INCOMPATIBLE


@pytest.mark.parametrize("field", ["track", "stack_id", "crid", "look_direction", "acquisition_end"])
def test_missing_critical_metadata(field, observation, after):
    result = assess_pair(observation, replace(after, **{field: None}))
    assert result.outcome == PairOutcome.INSUFFICIENT_METADATA and any(c.state == "UNKNOWN" for c in result.checks)


def test_nonoverlapping_footprints(observation, after):
    shifted = tuple(tuple((x + 10, y) for x, y in ring) for ring in after.footprint)
    assert assess_pair(observation, replace(after, footprint=shifted)).outcome == PairOutcome.INCOMPATIBLE


def test_overlap_outside_aoi_does_not_pass(observation, after):
    assert assess_pair(observation, after, aoi=BoundingBox(1, 1, 2, 2)).outcome == PairOutcome.INCOMPATIBLE


def test_unsupported_concave_geometry_unknown(observation, after):
    ring = ((79, 6), (81, 6), (80, 7), (81, 8), (79, 8), (79, 6))
    assert assess_pair(observation, replace(after, footprint=(ring,))).outcome == PairOutcome.INSUFFICIENT_METADATA


def test_identical_acquisition_rejected(observation):
    assert assess_pair(observation, observation).outcome == PairOutcome.INCOMPATIBLE


def test_deterministic_order(observation, after):
    assert assess_candidates((observation, after)) == assess_candidates((after, observation))


def test_latest_pair_preferred_over_seconds_shorter_old_gap(observation, after):
    older = replace(observation, granule_concept_id="G3-TEST", acquisition_start=observation.acquisition_start - timedelta(days=12) + timedelta(seconds=1), acquisition_end=observation.acquisition_end - timedelta(days=12) + timedelta(seconds=1))
    pairs = assess_candidates((older, observation, after))
    assert pairs[0].before_id == observation.granule_concept_id and pairs[0].after_id == after.granule_concept_id


def test_overlapping_boxes_do_not_prove_polygon_overlap(observation, after):
    first = ((0, 0), (2, 0), (0, 2), (0, 0))
    second = ((2, 2), (0.5, 2), (2, 0.5), (2, 2))
    result = assess_pair(replace(observation, footprint=(first,)), replace(after, footprint=(second,)))
    assert result.outcome == PairOutcome.INCOMPATIBLE
