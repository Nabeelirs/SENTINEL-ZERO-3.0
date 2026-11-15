"""Reproduce a read-only two-date GCOV compatibility audit, without change analysis."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from ..errors import MetadataError
from .acquisition import AcquisitionError
from .compatibility import compare, grids_equal, shared_support
from .quality import load_date


def read_metadata(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2_000_000:
        raise AcquisitionError("LIMIT", "Audit metadata exceeds the bounded JSON size")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AcquisitionError("METADATA", "Expected a metadata object")
    return value


def audit_pair(root: Path, before_inventory: dict[str, Any], after_inventory: dict[str, Any],
               catalog: dict[str, Any], quality_review: dict[str, Any] | None) -> dict[str, Any]:
    """Reinspect checksummed rasters; support is combined only on an equal grid."""
    before, first = load_date(root, before_inventory)
    after, second = load_date(root, after_inventory)
    a = next(r for r in before["rasters"] if r["layer"] == "HHHH")
    b = next(r for r in after["rasters"] if r["layer"] == "HHHH")
    support = shared_support(first, second) if grids_equal(a, b) else None
    observations = catalog["discovery"]["observations"]
    profiles = {o["granule_concept_id"]: o for o in observations}
    result = compare(before, after, profiles=profiles, support=support, quality_review=quality_review)
    result.update({
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Inspected Harmony-derived subset compatibility; not physical-change validation",
        "before_inspection": before, "after_inspection": after,
        "quality_review": quality_review,
        "temporal_power_difference_computed": False,
        "original_hdf5_inspected": False,
        "empirical_registration_verified": False,
    })
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime-root", "before-inventory", "after-inventory", "catalog", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--quality-review", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.suffix.lower() != ".json":
            raise AcquisitionError("EXISTS", "Output must be a new JSON evidence file")
        result = audit_pair(args.runtime_root, read_metadata(args.before_inventory),
                            read_metadata(args.after_inventory), read_metadata(args.catalog),
                            read_metadata(args.quality_review) if args.quality_review else None)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
        print("Pair decision:", result["decision"], "; temporal power difference computed: False")
        return 0 if result["decision"] in ("CONDITIONAL_GO", "ACCEPTED_FOR_ANALYSIS") else 3
    except (AcquisitionError, MetadataError, OSError, KeyError, TypeError, ValueError) as exc:
        print("Pair audit failed safely:", getattr(exc, "code", "METADATA_OR_IO"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
