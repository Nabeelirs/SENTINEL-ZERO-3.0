"""SYNTHETIC arrays only; no satellite observations or ground truth."""
from copy import deepcopy

import numpy as np
import pytest

from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.science.eligibility import EligibilityPolicy, PRIMARY_LEGEND, eligibility


def bundle():
    return {"HHHH": np.ma.ones((1, 2), dtype="float32"),
            "mask": np.ma.ones((1, 2), dtype="uint8"),
            "numberOfLooks": np.ma.ones((1, 2), dtype="float32")}


@pytest.mark.parametrize("date", [0, 1])
@pytest.mark.parametrize("layer,value,reason", [
    ("HHHH", np.nan, "power_nonfinite"), ("HHHH", np.inf, "power_nonfinite"),
    ("HHHH", -np.inf, "power_nonfinite"), ("HHHH", 0, "power_nonpositive"),
    ("HHHH", -2, "power_nonpositive"), ("numberOfLooks", np.nan, "looks_nonfinite"),
    ("numberOfLooks", np.inf, "looks_nonfinite"), ("numberOfLooks", -np.inf, "looks_nonfinite"),
    ("numberOfLooks", 0, "looks_nonpositive"), ("numberOfLooks", -2, "looks_nonpositive"),
    ("mask", 0, "focusing_mask"), ("mask", 255, "focusing_mask"), ("mask", 7, "focusing_mask"),
])
def test_each_date_each_eligibility_condition(date, layer, value, reason):
    inputs = [bundle(), bundle()]
    inputs[date][layer][0, 0] = value
    result = eligibility(*inputs)
    label = ("before", "after")[date]
    assert result.common_valid.tolist() == [[False, True]]
    assert result.statistics["overlapping_reason_counts"][label + "_" + reason] == 1
    assert result.primary_exclusion[0, 0] == next(k for k, v in PRIMARY_LEGEND.items() if v == label + "_" + reason)
    assert result.statistics["unexpected_mask_values"][label] == ([7] if layer == "mask" and value == 7 else [])


@pytest.mark.parametrize("date", [0, 1])
@pytest.mark.parametrize("layer", ["HHHH", "mask", "numberOfLooks"])
def test_native_nodata_excludes_even_positive_numeric_storage(date, layer):
    inputs = [bundle(), bundle()]
    inputs[date][layer].mask = [[True, False]]
    result = eligibility(*inputs)
    assert result.primary_exclusion.tolist() == [[1, 0]]
    assert result.statistics["overlapping_reason_counts"]["raster_native_nodata"] == 1


def test_multiple_reasons_overlap_but_primary_precedence_is_disjoint():
    a, b = bundle(), bundle()
    a["HHHH"][0, 0] = np.nan
    a["HHHH"].mask = [[True, False]]
    a["mask"][0, 0] = 255
    b["numberOfLooks"][0, 0] = -1
    result = eligibility(a, b)
    counts = result.statistics["overlapping_reason_counts"]
    assert all(counts[k] == 1 for k in ("raster_native_nodata", "before_power_nonfinite", "before_focusing_mask", "after_looks_nonpositive"))
    assert result.primary_exclusion.tolist() == [[1, 0]]
    assert sum(result.statistics["primary_category_counts"].values()) == 2


def test_four_way_partition_and_no_input_mutation():
    a, b = bundle(), bundle()
    for data in (a, b):
        for key in data:
            data[key] = np.ma.ones((2, 2), dtype=data[key].dtype)
    a["HHHH"][0, 1] = 0
    b["HHHH"][1, 0] = 0
    a["mask"][1, 1] = b["mask"][1, 1] = 0
    before = deepcopy((a, b))
    result = eligibility(a, b)
    assert result.statistics["support_partition"] == dict(jointly_eligible=1, before_only_eligible=1,
                                                          after_only_eligible=1, jointly_ineligible=1)
    assert result.statistics["common_eligible"] + result.statistics["common_ineligible"] == 4
    assert all(np.array_equal(x[k], y[k]) for x, y in zip((a, b), before) for k in x)
    with pytest.raises(ValueError):
        result.common_valid[0, 0] = False


def test_other_documented_positive_subswath_can_be_declared():
    a, b = bundle(), bundle()
    a["mask"][0, 0] = b["mask"][0, 0] = 2
    assert not eligibility(a, b).common_valid[0, 0]
    result = eligibility(a, b, EligibilityPolicy((1, 2)))
    assert result.common_valid.all() and result.statistics["unexpected_mask_values"] == {"before": [], "after": []}


@pytest.mark.parametrize("ids", [(), (0,), (255,), (True,), (1, 1), (-1,)])
def test_invalid_policy_rejected(ids):
    with pytest.raises(AcquisitionError):
        EligibilityPolicy(ids)
