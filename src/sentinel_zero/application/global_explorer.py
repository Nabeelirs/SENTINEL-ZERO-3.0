"""Worldwide bounded CMR metadata discovery. No product downloads or processing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite

from ..contracts import BoundingBox, DiscoveryQuery, jsonable
from ..discovery.cmr import CMRClient
from ..discovery.selection import assess_candidates
from ..errors import MetadataError

COLLECTION = "C2854338529-ASF"


@dataclass(frozen=True)
class GlobalQuery:
    west: float
    south: float
    east: float
    north: float
    start_utc: str
    end_utc: str
    frequency: str = "A"
    polarization: str = "HH"

    def boxes(self) -> tuple[BoundingBox,...]:
        values = (self.west,self.south,self.east,self.north)
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) for v in values):
            raise MetadataError("Coordinates must be finite numbers in longitude/latitude order")
        if not -180 <= self.west <= 180 or not -180 <= self.east <= 180 or not -90 <= self.south < self.north <= 90:
            raise MetadataError("Invalid geographic coordinate ranges")
        width = self.east-self.west if self.east > self.west else 360-self.west+self.east
        if not 0 < width <= 5 or self.north-self.south > 5:
            raise MetadataError("Use a nonempty AOI at most five degrees wide/high; antimeridian crossings are split")
        if self.west < self.east:
            return (BoundingBox(*values),)
        boxes = []
        if self.west < 180: boxes.append(BoundingBox(self.west,self.south,180,self.north))
        if self.east > -180: boxes.append(BoundingBox(-180,self.south,self.east,self.north))
        if not boxes: raise MetadataError("Empty geographic selection")
        return tuple(boxes)

    def validate(self) -> tuple[BoundingBox,...]:
        boxes = self.boxes()
        q = DiscoveryQuery(collection_concept_id=COLLECTION,temporal_start=self.start_utc,temporal_end=self.end_utc)
        if (q.temporal_end-q.temporal_start).total_seconds() > 366*86400:
            raise MetadataError("Limit metadata searches to 366 days")
        if self.frequency not in ("A","B") or self.polarization not in ("HH","HV","VH","VV","RH","RV"):
            raise MetadataError("Unsupported requested channel")
        return boxes


def centred_query(latitude: float, longitude: float, span: float, start: str, end: str) -> GlobalQuery:
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) for v in (latitude,longitude,span)) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or not 0 < span <= 5:
        raise MetadataError("Invalid centre coordinates or angular span")
    wrap = lambda x: (x+180)%360-180
    return GlobalQuery(wrap(longitude-span/2),max(-90,latitude-span/2),wrap(longitude+span/2),min(90,latitude+span/2),start,end)


def discover(query: GlobalQuery, client: CMRClient | None = None) -> dict:
    boxes = query.validate()
    cmr = client or CMRClient(timeout=10,retries=1)
    collection = cmr.search_collections(DiscoveryQuery(collection_concept_id=COLLECTION,page_size=1,max_pages=2,max_results=2))
    if not collection.pagination_complete or len(collection.collections) != 1:
        return dict(status="SERVICE_ERROR",scope="CATALOG_METADATA_ONLY",collection_lookup=jsonable(collection),
                    message="Collection metadata could not be verified; this is not a zero-availability result",scientific_acceptance=False)
    context = collection.collections[0]
    if context.product_level != "L2" or context.maturity != "PROVISIONAL" or "GCOV" not in context.short_name:
        raise MetadataError("Selected collection identity/maturity changed; review before continuing")
    results = [cmr.search_granules(DiscoveryQuery(collection_concept_id=COLLECTION,bounding_box=box,
                                               temporal_start=query.start_utc,temporal_end=query.end_utc,
                                               page_size=10,max_pages=5,max_results=50),context) for box in boxes]
    unique = {o.granule_concept_id:o for r in results for o in r.observations}
    observations = tuple(unique.values())
    pairs = assess_candidates(observations,frequency=query.frequency,polarization=query.polarization,
                              aoi=boxes[0] if len(boxes)==1 else None)
    complete = all(r.pagination_complete for r in results)
    status = "RESULTS" if observations and complete else "NO_MATCHES" if complete else "PARTIAL_RESULTS" if observations else "SERVICE_ERROR"
    return dict(schema_version="1.0",status=status,scope="CATALOG_METADATA_ONLY",collection=jsonable(context),
                query=jsonable(query),query_boxes=jsonable(boxes),retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                discovery=[jsonable(r) for r in results],retrieved_unique=len(observations),pagination_complete=complete,
                matching_counts_per_box=[r.matching_count for r in results],observations=jsonable(observations),
                pair_assessments=jsonable(pairs),scientific_acceptance=False,
                limitations=["Metadata does not establish pixel compatibility or usable/authenticated product access.",
                             "Only the existing Sri Lankan pilot has inspected and prepared pixels; other locations are metadata-only.",
                             "PROVISIONAL collection only; no claim of complete mission inventory.",
                             "At most 50 records per split box; bounded partial retrieval is labelled explicitly.",
                             "Polar/antimeridian/nonconvex footprint overlap may be UNKNOWN in the conservative existing screener."])
