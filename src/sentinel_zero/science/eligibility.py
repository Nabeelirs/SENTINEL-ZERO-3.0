"""Linear-power support accounting only; no temporal radar measurement."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ..ingestion.acquisition import AcquisitionError
from ..ingestion.quality import focused_support

# Stable schema-1 code/precedence. First matching reason wins; counts overlap.
PRIMARY_REASONS = (
    "raster_native_nodata", "before_power_nonfinite", "before_power_nonpositive",
    "after_power_nonfinite", "after_power_nonpositive", "before_focusing_mask",
    "after_focusing_mask", "before_looks_nonfinite", "before_looks_nonpositive",
    "after_looks_nonfinite", "after_looks_nonpositive",
)
PRIMARY_LEGEND = {0: "common_eligible", **{i: name for i, name in enumerate(PRIMARY_REASONS, 1)}}


@dataclass(frozen=True)
class EligibilityPolicy:
    """Supported IDs must be declared from the inspected product's semantics."""
    allowed_subswaths: tuple[int, ...] = (1,)

    def __post_init__(self) -> None:
        ids = self.allowed_subswaths
        if not ids or len(set(ids)) != len(ids) or any(type(x) is not int or not 1 <= x < 255 for x in ids):
            raise AcquisitionError("MASK_POLICY", "Declare unique, documented positive subswath IDs (not 255)")


@dataclass(frozen=True)
class EligibilityResult:
    before_valid: np.ndarray
    after_valid: np.ndarray
    common_valid: np.ndarray
    primary_exclusion: np.ndarray
    statistics: dict


def eligibility(before: Mapping[str, np.ma.MaskedArray], after: Mapping[str, np.ma.MaskedArray],
                policy: EligibilityPolicy = EligibilityPolicy()) -> EligibilityResult:
    """Keep independent causes, then partition by fixed primary precedence."""
    av, ac = focused_support(before["HHHH"], before["mask"], before["numberOfLooks"],
                             allowed_subswaths=policy.allowed_subswaths)
    bv, bc = focused_support(after["HHHH"], after["mask"], after["numberOfLooks"],
                             allowed_subswaths=policy.allowed_subswaths)
    if av.shape != bv.shape:
        raise AcquisitionError("GRID", "Support arrays must use the verified common grid")
    reasons: dict[str, np.ndarray] = {}
    native = np.zeros(av.shape, dtype=bool)
    native_counts = {}
    for label, arrays in (("before", before), ("after", after)):
        for layer in ("HHHH", "mask", "numberOfLooks"):
            invalid = np.ma.getmaskarray(arrays[layer])
            native |= invalid
            native_counts[label + "_" + layer] = int(invalid.sum())
        p, m, n = (np.asarray(arrays[k]) for k in ("HHHH", "mask", "numberOfLooks"))
        reasons[label + "_power_nonfinite"] = ~np.isfinite(p)
        reasons[label + "_power_nonpositive"] = np.isfinite(p) & (p <= 0)
        reasons[label + "_focusing_mask"] = ~np.isin(m, policy.allowed_subswaths)
        reasons[label + "_looks_nonfinite"] = ~np.isfinite(n)
        reasons[label + "_looks_nonpositive"] = np.isfinite(n) & (n <= 0)
    reasons["raster_native_nodata"] = native
    common = av & bv
    primary = np.zeros(common.shape, dtype="uint8")
    for code, name in enumerate(PRIMARY_REASONS, 1):
        primary[(primary == 0) & reasons[name]] = code
    if not np.array_equal(primary == 0, common):
        raise AcquisitionError("ACCOUNTING", "Exclusion partition does not match eligibility")
    overlapping = {name: int(values.sum()) for name, values in reasons.items()}
    overlapping.update({
        "before_power_invalid_or_nonpositive": int((reasons["before_power_nonfinite"] | reasons["before_power_nonpositive"]).sum()),
        "after_power_invalid_or_nonpositive": int((reasons["after_power_nonfinite"] | reasons["after_power_nonpositive"]).sum()),
        "before_looks_invalid_or_nonpositive": int((reasons["before_looks_nonfinite"] | reasons["before_looks_nonpositive"]).sum()),
        "after_looks_invalid_or_nonpositive": int((reasons["after_looks_nonfinite"] | reasons["after_looks_nonpositive"]).sum()),
        "common_eligibility_failure": int((~common).sum()),
    })
    counts = {str(code): int((primary == code).sum()) for code in PRIMARY_LEGEND}
    partition = {"jointly_eligible": int(common.sum()), "before_only_eligible": int((av & ~bv).sum()),
                 "after_only_eligible": int((bv & ~av).sum()), "jointly_ineligible": int((~av & ~bv).sum())}
    if sum(counts.values()) != common.size or sum(partition.values()) != common.size:
        raise AcquisitionError("ACCOUNTING", "Pixel partition failed to reconcile")
    stats = {"total_pixels": int(common.size), "before_eligible": int(av.sum()), "after_eligible": int(bv.sum()),
             "common_eligible": int(common.sum()), "common_ineligible": int((~common).sum()),
             "support_partition": partition, "overlapping_reason_counts": overlapping,
             "native_nodata_by_date_layer": native_counts, "primary_category_counts": counts,
             "unexpected_mask_values": {"before": ac["unexpected_mask_values"], "after": bc["unexpected_mask_values"]},
             "observed_mask_values": {label: [int(x) for x in np.unique(arrays["mask"].data)]
                                      for label, arrays in (("before", before), ("after", after))},
             "counting_note": "Reason counts overlap; only support_partition and primary_category_counts are disjoint."}
    for array in (av, bv, common, primary):
        array.flags.writeable = False
    return EligibilityResult(av, bv, common, primary, stats)
