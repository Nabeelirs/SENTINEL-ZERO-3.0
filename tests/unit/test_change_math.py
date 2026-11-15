"""SYNTHETIC power arrays test software arithmetic, never NASA observations."""
import math

import numpy as np
import pytest

from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.science.change import calculate_change, statistics

GRID = dict(transform=[10, 0, 384000, 0, -10, 768350], epsg=32644)


@pytest.mark.parametrize("before,after,expected", [(1, 1, 0), (1, 2, 3.0102999566), (2, 1, -3.0102999566),
                                                  (1, 10, 10), (10, 1, -10), (1e-300, 1e300, 6000),
                                                  (1e300, 1e-300, -6000), (5e-324, 5e-324, 0)])
def test_known_scale_sign_and_extreme_positive_powers(before, after, expected):
    actual = calculate_change(np.array([[before]], dtype="float64"), np.array([[after]], dtype="float64"), np.ones((1, 1), bool))
    assert actual.dtype == np.float32 and actual[0, 0] == pytest.approx(expected, abs=2e-6)
    assert actual[0, 0] == pytest.approx(10*(math.log10(after)-math.log10(before)), rel=1e-7, abs=1e-7)


@pytest.mark.parametrize("bad", [0, -1, np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("date", [0, 1])
def test_invalid_eligible_power_is_never_repaired(bad, date):
    arrays = [np.ones((1, 1)), np.ones((1, 1))]
    arrays[date][0, 0] = bad
    with pytest.raises(AcquisitionError): calculate_change(*arrays, np.ones((1, 1), bool))


def test_excluded_invalid_values_remain_nan_and_inputs_unchanged():
    before = np.array([[1, np.nan, 0], [np.inf, -1, 2.]])
    after = np.array([[2, 1, 3], [0, 4, 1.]])
    originals = [a.copy() for a in (before, after)]
    valid = np.array([[1, 0, 0], [0, 0, 1]], bool)
    delta = calculate_change(before, after, valid)
    assert np.isfinite(delta).tolist() == valid.tolist()
    assert np.isnan(delta[~valid]).all() and not delta.flags.writeable
    assert all(np.array_equal(a, b, equal_nan=True) for a, b in zip((before, after), originals))


@pytest.mark.parametrize("kind", ["all_excluded", "shape", "mask_type", "one_dimensional", "complex"])
def test_impossible_support_and_types_fail(kind):
    b, a, mask = np.ones((2, 2)), np.ones((2, 2)), np.ones((2, 2), bool)
    if kind == "all_excluded": mask[:] = False
    elif kind == "shape": a = np.ones((1, 2))
    elif kind == "mask_type": mask = mask.astype("uint8")
    elif kind == "one_dimensional": b = b.ravel()
    else: a = a.astype("complex128")
    with pytest.raises(AcquisitionError): calculate_change(b, a, mask)


def test_statistics_saved_values_threshold_inclusivity_and_partition():
    delta = np.array([[-6, -3, 0, 3, 6, np.nan]], dtype="float32")
    valid = np.isfinite(delta)
    s = statistics(delta, valid, GRID)
    assert (s["positive_cells"], s["negative_cells"], s["exactly_zero_cells"]) == (2, 2, 1)
    assert s["eligible_cells"] + s["excluded_cells"] == 6
    assert s["eligible_percent"] == pytest.approx(500/6)
    assert s["descriptive_thresholds"]["at_least_plus_3_db"] == 2
    assert s["descriptive_thresholds"]["at_most_minus_3_db"] == 2
    assert s["descriptive_thresholds"]["absolute_at_least_6_db"] == 2
    assert s["mean"] == s["median"] == 0
    assert s["standard_deviation"] == pytest.approx(math.sqrt(18))
    assert s["quantiles"]["0.25"] == -3 and s["eligible_projected_grid_area_m2"] == 500


@pytest.mark.parametrize("corrupt", ["valid_nan", "valid_inf", "excluded_finite", "excluded_inf"])
def test_statistics_refuses_improper_nodata(corrupt):
    delta, valid = np.array([[1., np.nan]]), np.array([[True, False]])
    if corrupt == "valid_nan": delta[0, 0] = np.nan
    elif corrupt == "valid_inf": delta[0, 0] = np.inf
    elif corrupt == "excluded_finite": delta[0, 1] = 0
    else: delta[0, 1] = np.inf
    with pytest.raises(AcquisitionError): statistics(delta, valid, GRID)


def test_independent_safe_ratio_reference_for_moderate_powers():
    rng = np.random.default_rng(6006)
    before, after = (10**rng.uniform(-8, 8, size=(7, 11)) for _ in range(2))
    delta = calculate_change(before, after, np.ones(before.shape, bool))
    reference = np.array([10*math.log10(float(a)/float(b)) for b, a in zip(before.flat, after.flat)]).reshape(before.shape)
    np.testing.assert_allclose(delta, reference, rtol=1e-7, atol=1e-6)


@pytest.mark.parametrize("epsg", [4326, 2263])
def test_geographic_degrees_or_projected_feet_not_reported_as_square_metres(epsg):
    with pytest.raises(AcquisitionError):
        statistics(np.ones((1, 1), dtype="float32"), np.ones((1, 1), bool), {**GRID, "epsg": epsg})
