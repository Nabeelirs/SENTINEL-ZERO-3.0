"""Operator-only portable bootstrap. No download, credentials or scientific writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4
import zipfile

ARCHIVE_SHA256 = "6153417f06f71485a6cb85f5bc32defdc21e60495a47c2f66fdacda9f1e384cc"
RECEIPT_SHA256 = "378f983519bdaa839b34a4df94dd002747273e18ab2dacf563f9f45402a0db35"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(archive: Path, target: Path) -> Path:
    """Extract only the pinned, bounded scientific archive to a new local root."""
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError("Scientific ZIP checksum differs from the release baseline")
    if target.exists():
        return target  # Full scientific verification follows even for a cached tree.
    for parent in (target.parent, *target.parent.parents):
        if parent.exists() and (parent.is_symlink() or getattr(parent.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Cache root must not traverse links/reparse points")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name("s8-" + uuid4().hex[:8])
    stage.mkdir()
    try:
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            if len(members) != 16 or len({m.filename for m in members}) != 16 or sum(m.file_size for m in members) > 50_000_000:
                raise ValueError("Unexpected scientific archive inventory/size")
            for m in members:
                p = PurePosixPath(m.filename)
                if (p.is_absolute() or ".." in p.parts or "\\" in m.filename or ":" in m.filename
                        or stat.S_ISLNK(m.external_attr >> 16) or m.is_dir()):
                    raise ValueError("Unsafe scientific archive member")
                dest = stage.joinpath(*p.parts)
                if os.name == "nt" and len(str(dest.absolute())) >= 260:
                    raise ValueError("Choose a shorter cache root for original NASA filenames")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, dest.open("xb") as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
        if digest(stage / "bundle_receipt.json") != RECEIPT_SHA256:
            raise ValueError("Scientific receipt checksum mismatch")
        # Existing verifier checks every identity/size/hash before cache publication.
        from sentinel_zero.application.bundle import verify_bundle_receipt
        verify_bundle_receipt(stage, RECEIPT_SHA256)
        if target.exists():
            raise FileExistsError("Cache appeared during extraction; no overwrite")
        stage.rename(target)
    finally:
        if stage.exists():
            # Only this service-created, exact sibling staging tree is removed.
            assert stage.parent == target.parent and stage.name.startswith("s8-")
            shutil.rmtree(stage)
    return target


def configure(release_root: Path, cache_root: Path | None = None) -> Path:
    root = cache_root or Path(tempfile.gettempdir()) / "sz0" / ARCHIVE_SHA256[:10]
    if any(p.lower().startswith("onedrive") for p in root.parts):
        raise ValueError("Scientific cache must be outside OneDrive")
    root = unpack(release_root / "scientific_data.zip", root)
    os.environ["SENTINEL_ZERO_BUNDLE_ROOT"] = str(root.absolute())
    os.environ["SENTINEL_ZERO_BUNDLE_SHA256"] = RECEIPT_SHA256
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    try:
        release = Path(__file__).resolve().parent
        configure(release, args.cache_root)
        from sentinel_zero.application.data import configured_paths, load_explorer
        data = load_explorer(configured_paths())
        print(json.dumps({"status": "SCIENTIFIC_BUNDLE_VERIFIED", "eligible_cells": data.product.statistics["eligible_cells"],
                          "mode": data.mode, "receipt_sha256": RECEIPT_SHA256}))
        if args.verify_only:
            return 0
        if not 1024 <= args.port <= 65535:
            raise ValueError("Use an unprivileged local port")
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(release / "app.py"),
                                "--server.address", "127.0.0.1", "--server.port", str(args.port),
                                "--server.headless", "true"], cwd=release)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        print("BLOCKED: verify the release ZIP, installed dependencies and writable short cache root", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
