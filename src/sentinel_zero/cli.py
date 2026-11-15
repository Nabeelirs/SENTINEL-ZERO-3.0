"""CLI for metadata only. Exit 0 complete, 2 input, 3 discovery, 4 output failure."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .config import SearchConfig, load_config
from .contracts import BoundingBox, DiscoveryQuery, PairOutcome, jsonable
from .discovery.cmr import CMRClient
from .discovery.selection import assess_candidates
from .errors import MetadataError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="SENTINEL ZERO public CMR metadata discovery; no satellite assets downloaded")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="Discover granules and screen preliminary pairs")
    inputs = discover.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--config", type=Path, help="Version 1.0 discovery JSON configuration")
    inputs.add_argument("--collection", help="CMR collection concept ID")
    discover.add_argument("--bbox", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    discover.add_argument("--start", help="ISO UTC acquisition start filter")
    discover.add_argument("--end", help="ISO UTC acquisition end filter")
    discover.add_argument("--frequency", choices=("A", "B"))
    discover.add_argument("--polarization", choices=("HH", "HV", "VH", "VV", "RH", "RV"))
    collections = commands.add_parser("collections", help="Search collection metadata")
    identity = collections.add_mutually_exclusive_group(required=True)
    identity.add_argument("--short-name", help="CMR short name; quote wildcard patterns")
    identity.add_argument("--collection", help="Exact CMR collection concept ID")
    for sub in (discover, collections):
        sub.add_argument("--page-size", type=int)
        sub.add_argument("--max-pages", type=int)
        sub.add_argument("--max-results", type=int)
        sub.add_argument("--output", type=Path, help="Create new JSON audit file; never overwrite")
    return root


def main(argv: Sequence[str] | None = None, *, client: CMRClient | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.output and (args.output.exists() or args.output.suffix.lower() != ".json"):
            print("Output must be a new .json file; existing evidence is never overwritten", file=sys.stderr)
            return 4
        if args.command == "discover":
            if args.config:
                if any(getattr(args, k) is not None for k in ("bbox", "start", "end", "frequency", "polarization")):
                    raise MetadataError("Use configured spatial/temporal/channel values or direct arguments, not both")
                config = load_config(args.config)
            else:
                config = SearchConfig(DiscoveryQuery(collection_concept_id=args.collection, bounding_box=BoundingBox(*args.bbox) if args.bbox else None,
                                                    temporal_start=args.start, temporal_end=args.end), args.frequency or "A", args.polarization or "HH")
        else:
            config = SearchConfig(DiscoveryQuery(collection_concept_id=args.collection, short_name=args.short_name))
        limits = {key: getattr(args, key) for key in ("page_size", "max_pages", "max_results") if getattr(args, key) is not None}
        config = replace(config, query=replace(config.query, **limits))
        if args.command == "discover" and config.query.collection_concept_id is None:
            raise MetadataError("Granule discovery requires a collection concept ID")
        if args.command == "discover" and config.query.max_results > 100:
            raise MetadataError("Pair discovery supports max_results <= 100")
    except (MetadataError, OSError, TypeError, ValueError):
        print("Invalid discovery configuration or arguments; check identifiers, times, AOI, schema and limits", file=sys.stderr)
        return 2
    cmr = client or CMRClient()
    if args.command == "collections":
        result = cmr.search_collections(config.query)
        collection_result = None
    else:
        collection_result = cmr.search_collections(DiscoveryQuery(collection_concept_id=config.query.collection_concept_id, page_size=10, max_pages=2, max_results=10))
        if not collection_result.pagination_complete or len(collection_result.collections) != 1:
            result = collection_result
        else:
            result = cmr.search_granules(config.query, collection_result.collections[0])
    pairs = assess_candidates(result.observations, frequency=config.frequency, polarization=config.polarization, aoi=config.query.bounding_box) if result.observations else ()
    recommendation = next((p for p in pairs if p.outcome == PairOutcome.PRELIMINARY_CANDIDATE), None) if result.pagination_complete else None
    payload = {"schema_version": "1.0", "software_version": __version__, "scope": "CATALOG_METADATA_ONLY",
               "aoi_label": config.aoi_label, "requested_query": config.query,
               "collection_lookup": collection_result, "discovery": result,
               "ranking_policy": "Outcome, VALIDATED before PROVISIONAL, newest after acquisition, closest compatible predecessor, stable granule IDs; inspection order, no confidence score or physical-event optimum.",
               "pair_assessments": pairs, "recommended_pair": recommendation,
               "scientific_acceptance": False}
    if args.output:
        try:
            encoded = json.dumps(jsonable(payload), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
        except (OSError, TypeError, ValueError):
            print("Could not create JSON audit output", file=sys.stderr)
            return 4
    print(f"CMR matches={result.matching_count} retrieved={result.retrieved_count} pages={len(result.pages)} pagination_complete={result.pagination_complete}")
    for failure in result.failures:
        print(f"{failure.code}: {failure.message}", file=sys.stderr)
    for observation in result.observations:
        print(f"{observation.granule_concept_id} {jsonable(observation.acquisition_start)} {observation.maturity} stack={observation.stack_id} version={observation.product_version} CRID={observation.crid} missing={','.join(observation.missing_fields) or 'none'}")
    for collection in result.collections:
        print(f"{collection.concept_id} {collection.short_name} maturity={collection.maturity}")
    if recommendation:
        print(f"PRELIMINARY_CANDIDATE {recommendation.before_id} -> {recommendation.after_id}; scientific acceptance=False")
    elif args.command == "discover":
        print("No recommended pair: incompatible, incomplete metadata or incomplete retrieval; inspect JSON checks")
    return 0 if result.pagination_complete and (args.command == "collections" or bool(collection_result and len(collection_result.collections) == 1)) else 3


if __name__ == "__main__":
    raise SystemExit(main())
