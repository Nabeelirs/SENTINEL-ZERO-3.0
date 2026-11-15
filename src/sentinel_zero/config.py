"""Strict discovery configuration; credentials and arbitrary endpoints excluded."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .contracts import BoundingBox, DiscoveryQuery, SCHEMA_VERSION
from .errors import MetadataError


@dataclass(frozen=True)
class SearchConfig:
    query: DiscoveryQuery
    frequency: str = "A"
    polarization: str = "HH"
    aoi_label: str = "User-supplied exploratory AOI"

    def __post_init__(self) -> None:
        if self.frequency not in ("A", "B") or self.polarization not in ("HH", "HV", "VH", "VV", "RH", "RV"):
            raise MetadataError("Unsupported requested channel")
        if not isinstance(self.aoi_label, str) or not self.aoi_label.strip():
            raise MetadataError("aoi_label must be nonempty text")


def load_config(path: Path) -> SearchConfig:
    if path.stat().st_size > 65536:
        raise MetadataError("Configuration exceeds 64 KiB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise MetadataError("Configuration must be UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise MetadataError("Configuration must be a JSON object")
    allowed = {"schema_version", "aoi_label", "frequency", "polarization", "collection_concept_id", "short_name", "bounding_box", "temporal_start", "temporal_end", "page_size", "max_pages", "max_results", "sort_key"}
    if set(data) - allowed:
        raise MetadataError("Unknown configuration keys")
    if data.pop("schema_version", None) != SCHEMA_VERSION:
        raise MetadataError("Unsupported or missing configuration schema_version")
    options = {k: data.pop(k) for k in ("frequency", "polarization", "aoi_label") if k in data}
    if "bounding_box" in data:
        box = data["bounding_box"]
        if not isinstance(box, list) or len(box) != 4:
            raise MetadataError("bounding_box must contain west,south,east,north")
        data["bounding_box"] = BoundingBox(*box)
    return SearchConfig(DiscoveryQuery(**data), **options)
