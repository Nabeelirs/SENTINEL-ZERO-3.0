"""Manifest-driven local EarthBOM and dependency evaluator. No Streamlit imports."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Mapping

from ..application.data import ExplorerData, RuntimePaths, PREPARATION_SHA256, ANALYSIS_SHA256, safe_file
from ..science.preparation import sha256_file


@dataclass(frozen=True)
class Artifact:
    id: str
    label: str
    kind: str
    parents: tuple[str, ...]
    sha256: str | None
    bytes: int | None
    integrity: str
    provenance: Mapping


@dataclass(frozen=True)
class EarthBOM:
    nodes: tuple[Artifact, ...]
    verified_at_utc: str
    limitations: tuple[str, ...]

    def __post_init__(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or any(set(n.parents)-ids for n in self.nodes):
            raise ValueError("Duplicate or unknown evidence dependency")
        tuple(TopologicalSorter({n.id:n.parents for n in self.nodes}).static_order())

    def node(self, identity: str) -> Artifact:
        return next(n for n in self.nodes if n.id == identity)

    def descendants(self, identity: str) -> tuple[str, ...]:
        self.node(identity)
        affected = {identity}
        while True:
            new = {n.id for n in self.nodes if set(n.parents) & affected}
            if new <= affected:
                break
            affected |= new
        return tuple(n.id for n in self.nodes if n.id in affected and n.id != identity)

    def record(self) -> dict:
        return dict(schema_version="1.0", task="SZ-009", kind="EarthBOM",
                    verified_at_utc=self.verified_at_utc, nodes=[asdict(n) for n in self.nodes],
                    edges=[dict(parent=p, child=n.id) for n in self.nodes for p in n.parents],
                    limitations=list(self.limitations),
                    trust="Pinned local manifests; no independent NASA authenticity or calibration guarantee")


def build_earthbom(data: ExplorerData, paths: RuntimePaths) -> EarthBOM:
    """Use actual source/output receipts; do not invent hashes for NASA originals."""
    p = data.product
    prep = p.prepared.provenance
    nodes: list[Artifact] = []

    def local(identity: str, label: str, kind: str, parents: tuple[str, ...], root: Path,
              filename: str, expected: str, size: int, provenance: Mapping) -> None:
        try:
            path = safe_file(root, filename)
            state = "MATCH" if path.stat().st_size == size and sha256_file(path) == expected else "MISMATCH"
        except (OSError, ValueError):
            state = "MISSING_OR_UNSAFE"
        nodes.append(Artifact(identity,label,kind,parents,expected,size,state,dict(provenance,filename=filename)))

    source_ids = []
    for inv in prep["source_inventories"]:
        source = inv["source"]
        scene = "scene:"+source["granule_id"]
        nodes.append(Artifact(scene,source["granule_id"],"NASA_catalog_identity",(),None,None,"UNVERIFIABLE",
                              dict(source,verification_scope="Recorded catalog identity; original HDF5 bytes unavailable")))
        for art in inv["artifacts"]:
            identity = source["granule_id"]+":"+art["layer"]
            source_ids.append(identity)
            local(identity,source["acquisition_start_utc"][:10]+" / "+art["layer"],"Harmony_subset",(scene,),
                  paths.source,art["filename"],art["sha256"],art["bytes"],
                  dict(granule_id=source["granule_id"],layer=art["layer"],transformation="Recorded Harmony subset, not original HDF5"))
    for kind, art in prep["outputs"].items():
        local("prep:"+kind,kind.replace("_"," "),"eligibility_output",tuple(source_ids),paths.preparation,
              art["filename"],art["sha256"],art["bytes"],dict(configuration=prep["configuration"],run_id=prep["run_id"]))
    local("prep:manifest","SZ-005 preparation manifest","provenance_manifest",
          tuple(source_ids)+("prep:common_valid","prep:primary_exclusion"),paths.preparation,
          "manifest.json",PREPARATION_SHA256,p.provenance["preparation"]["bytes"],dict(run_id=prep["run_id"]))
    outputs = p.provenance["outputs"]
    # Edges represent recorded computational/report dependencies, not physical causation.
    parents = {
        "change":tuple(source_ids)+("prep:common_valid","prep:primary_exclusion","prep:manifest"),
        "statistics":("analysis:change","prep:common_valid"),
        "diagnostics":tuple(source_ids)+("analysis:change","prep:common_valid"),
        "preview":tuple(source_ids)+("analysis:change","analysis:diagnostics"),
    }
    for kind, art in outputs.items():
        local("analysis:"+kind,"SZ-006 "+kind,"analysis_output",parents[kind],paths.analysis,
              art["filename"],art["sha256"],art["bytes"],dict(parameters=p.provenance["parameters"],run_id=p.provenance["run_id"]))
    path = safe_file(paths.analysis,"analysis_manifest.json")
    local("analysis:manifest","SZ-006 analysis manifest","provenance_manifest",
          ("prep:manifest",)+tuple("analysis:"+kind for kind in outputs), paths.analysis,
          path.name,ANALYSIS_SHA256,path.stat().st_size,dict(run_id=p.provenance["run_id"]))
    nodes.append(Artifact("report:passport","Current investigation / evidence passport","runtime_report",
                          ("analysis:manifest","analysis:statistics","analysis:diagnostics","analysis:change"),
                          None,None,"GENERATED_ON_REQUEST",dict(recipe="Verified baseline maps/values plus current evidence-state snapshot; PDF receipt created on export")))
    return EarthBOM(tuple(nodes),datetime.now(timezone.utc).isoformat(),tuple(p.limitations))


def evaluate(bom: EarthBOM, overrides: Mapping[str,str] | None = None) -> dict:
    states = {n.id:n.integrity for n in bom.nodes}
    if overrides:
        if set(overrides)-set(states) or any(s not in ("MATCH","MISMATCH","MISSING_OR_UNSAFE") for s in overrides.values()):
            raise ValueError("Invalid evidence-state override")
        states.update(overrides)
    failed = sorted(i for i,s in states.items() if s in ("MISMATCH","MISSING_OR_UNSAFE"))
    affected = sorted({child for i in failed for child in bom.descendants(i)})
    return dict(schema_version="1.0",local_integrity="FAILED" if failed else "CONSISTENT_WITH_LOCAL_REFERENCES",
                artifact_integrity=states,failed_artifacts=failed,requires_revalidation=affected,
                provider_authenticity="UNVERIFIED",scientific_quality="CONDITIONAL_PROVISIONAL",
                environmental_interpretation="UNVERIFIED",limitations=list(bom.limitations),
                trust_boundary="Controlled demo overrides apply only to disposable copies. Displayed radar values remain the verified baseline.")
