"""Operator-only full-source offline bundle, retaining the unchanged SZ-006 loader."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Sequence
from uuid import uuid4
import zipfile

from ..ingestion.acquisition import AcquisitionError, new_destination, safe_filename
from ..science.change import FILES
from ..science.preparation import read_json, sha256_file
from .data import (ANALYSIS_SHA256, PREPARATION_SHA256, RuntimePaths, configured_paths,
                   load_explorer, safe_file, watched_files)

NOTICE = """# SENTINEL ZERO offline scientific bundle

Actual NASA-ISRO NISAR PROVISIONAL GCOV, collection C2854338529-ASF.
Granules G4348875889-ASF (2026-10-06) and G4378876686-ASF (2026-10-18).
Six Frequency A HHHH/mask/numberOfLooks Harmony-derived subsets, not original
HDF5 products; exploratory rectangle 79.95–80.00 E, 6.90–6.95 N, not basin polygon.
NASA/ASF/Earthdata/Harmony source identities, requests and transformations are
preserved in the preparation and analysis manifests. Scientific data are not
synthetic. Credits do not imply NASA/ISRO endorsement of SENTINEL ZERO.

https://nisar-docs.asf.alaska.edu/gcov/
https://nisar-docs.asf.alaska.edu/provisional-known-issues/
https://www.earthdata.nasa.gov/data/projects/nisar

This operator-created local demonstration copy preserves the complete existing
verification scope for these subsets. It does not add independent calibration,
NASA authenticity proof, original HDF5 QA or physical-change validation.
Retain all PROVISIONAL, subset-fidelity, banding/RFI and registration caveats.
No environmental corroboration, flooding/damage classification or confidence.

The unused standalone comparison HTML is retained because the existing loader
verifies its recorded hash. The app does not embed it. Keep receipt SHA-256
separately. Check the archive checksum before extraction; unpack outside OneDrive.
No credentials or runtime environment are included. Install pinned dependencies
separately before offline use. This is not a public publication or a new license
grant: confirm provider reuse/credit terms and project software license before
public redistribution/hosting. No public-release authorization is implied.
"""


def _relative_file(root: Path, relative: str) -> Path:
    p = PurePosixPath(relative)
    if relative == "DATA_NOTICE.md":
        return safe_file(root, relative)
    if len(p.parts) != 2 or p.parts[0] not in ("sources", "preparation", "analysis"):
        raise AcquisitionError("PATH", "Unexpected bundle member")
    safe_filename(p.parts[1])
    return safe_file(root/p.parts[0], p.parts[1])


def verify_bundle_receipt(root: Path, expected_sha256: str) -> dict:
    path = safe_file(root, "bundle_receipt.json")
    if sha256_file(path) != expected_sha256:
        raise AcquisitionError("CHECKSUM", "Portable receipt does not match the separately recorded baseline")
    receipt = read_json(path)
    if (receipt["schema_version"] != "1.0" or receipt["verification_scope"] != "complete existing subset/source verification"
            or receipt["preparation_sha256"] != PREPARATION_SHA256 or receipt["analysis_sha256"] != ANALYSIS_SHA256):
        raise AcquisitionError("MANIFEST", "Unsupported portable scientific contract")
    members = receipt["files"]
    if len(members) != 15 or len({a["path"] for a in members}) != len(members) or sum(a["bytes"] for a in members) > 50_000_000:
        raise AcquisitionError("LIMIT", "Require the bounded complete 15-file bundle inventory")
    for art in members:
        p = _relative_file(root, art["path"])
        if p.stat().st_size != art["bytes"] or sha256_file(p) != art["sha256"]:
            raise AcquisitionError("CHECKSUM", "Portable scientific artifact differs from its receipt")
    expected = {"DATA_NOTICE.md", *("analysis/"+name for name in ("analysis_manifest.json", *FILES.values())),
                *("preparation/"+name for name in ("manifest.json", "common_valid.tif", "primary_exclusion.tif"))}
    prep = read_json(root/"preparation/manifest.json")
    expected.update("sources/"+a["filename"] for inv in prep["source_inventories"] for a in inv["artifacts"])
    if {a["path"] for a in members} != expected:
        raise AcquisitionError("IDENTITY", "Bundle inventory is not exactly the required original and derived artifacts")
    return receipt


def create_bundle(paths: RuntimePaths, output_root: Path, run_id: str) -> tuple[Path, str]:
    """Validate first, exclusively copy known files, reverify, atomically publish."""
    safe_filename(run_id)
    final = new_destination(output_root, run_id)
    load_explorer(paths)
    files = watched_files(paths)
    if paths.bundle is not None:
        raise AcquisitionError("CONFIG", "Create from original local scientific mode, not another bundle")
    stage = new_destination(output_root, "s7-"+uuid4().hex[:12])
    if os.name == "nt" and any(len(str(folder/"sources"/p.name)) >= 260 for folder in (stage, final) for p in files if p.parent == paths.source.absolute()):
        raise AcquisitionError("PATH_LENGTH", "Choose a shorter bundle output root/run ID; original source filenames are retained")
    lock = new_destination(output_root, run_id+".lock")
    with lock.open("xb"):
        pass
    created, members = [], []
    try:
        stage.mkdir()
        for name in ("sources", "preparation", "analysis"):
            (stage/name).mkdir()
        for src in files:
            group = "sources" if src.parent == paths.source.absolute() else "preparation" if src.parent == paths.preparation.absolute() else "analysis"
            dest = stage/group/src.name
            created.append(dest)
            with src.open("rb") as incoming, dest.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024*1024)
            members.append(dict(path=group+"/"+src.name, bytes=dest.stat().st_size, sha256=sha256_file(dest)))
        notice = stage/"DATA_NOTICE.md"
        created.append(notice)
        with notice.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(NOTICE)
        members.append(dict(path=notice.name, bytes=notice.stat().st_size, sha256=sha256_file(notice)))
        receipt = dict(schema_version="1.0", task="SZ-007", run_id=run_id, created_at_utc=datetime.now(timezone.utc).isoformat(),
                       verification_scope="complete existing subset/source verification", preparation_sha256=PREPARATION_SHA256,
                       analysis_sha256=ANALYSIS_SHA256, files=sorted(members, key=lambda a: a["path"]))
        created.append(stage/"bundle_receipt.json")
        with (stage/"bundle_receipt.json").open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, allow_nan=False)
        digest = sha256_file(stage/"bundle_receipt.json")
        load_explorer(RuntimePaths(stage/"sources", stage/"preparation", stage/"analysis", "Verified portable scientific mode", stage, digest))
        if final.exists():
            raise AcquisitionError("EXISTS", "Bundle destination appeared; no overwrite")
        os.rename(stage, final)
    except BaseException:
        for p in reversed(created):
            p.unlink(missing_ok=True)
        for name in ("sources", "preparation", "analysis"):
            if (stage/name).is_dir():
                (stage/name).rmdir()
        if stage.is_dir():
            stage.rmdir()
        raise
    finally:
        lock.unlink(missing_ok=True)
    return final, digest


def archive_bundle(root: Path, expected_sha256: str, output: Path) -> str:
    receipt = verify_bundle_receipt(root, expected_sha256)
    target = new_destination(output.parent, output.name)
    stage = new_destination(output.parent, "sz007-archive-"+uuid4().hex+".zip")
    try:
        with zipfile.ZipFile(stage, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for relative in [*(a["path"] for a in receipt["files"]), "bundle_receipt.json"]:
                p = safe_file(root, relative) if relative == "bundle_receipt.json" else _relative_file(root, relative)
                archive.write(p, arcname=relative)
        with zipfile.ZipFile(stage) as archive:
            if archive.testzip() is not None:
                raise AcquisitionError("ARCHIVE", "Archive CRC check failed")
            references = [*receipt["files"], dict(path="bundle_receipt.json", sha256=expected_sha256)]
            for art in references:
                digest = hashlib.sha256()
                with archive.open(art["path"]) as member:
                    for chunk in iter(lambda: member.read(1024*1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != art["sha256"]:
                    raise AcquisitionError("CHECKSUM", "Archived member differs from the preserved bundle receipt")
        if target.exists():
            raise AcquisitionError("EXISTS", "Archive destination appeared; no overwrite")
        os.rename(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return sha256_file(target)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--runtime-root", type=Path, required=True)
    create.add_argument("--output-root", type=Path, required=True)
    create.add_argument("--run-id", required=True)
    for name in ("verify", "archive"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--bundle-root", type=Path, required=True)
        cmd.add_argument("--receipt-sha256", required=True)
        if name == "archive":
            cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            _, digest = create_bundle(configured_paths({"SENTINEL_ZERO_RUNTIME_ROOT": str(args.runtime_root)}), args.output_root, args.run_id)
            print("BUNDLE_VERIFIED; receipt SHA-256:", digest)
        elif args.command == "archive":
            print("ARCHIVE_VERIFIED; SHA-256:", archive_bundle(args.bundle_root, args.receipt_sha256, args.output))
        else:
            data = load_explorer(RuntimePaths(args.bundle_root/"sources", args.bundle_root/"preparation", args.bundle_root/"analysis",
                                              "Verified portable scientific mode", args.bundle_root, args.receipt_sha256))
            print("BUNDLE_VERIFIED; eligible:", data.product.statistics["eligible_cells"])
        return 0
    except (AcquisitionError, OSError, KeyError, ValueError, TypeError):
        print("BLOCKED: check bundle paths, recorded hashes and complete source files; no overwrite or repair performed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
