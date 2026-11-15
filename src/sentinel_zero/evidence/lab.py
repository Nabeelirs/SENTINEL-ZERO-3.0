"""Contained derived-copy challenge/recovery; trusted originals are read-only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from ..science.preparation import sha256_file

MARKER = b"\nSENTINEL_ZERO_CONTROLLED_COPY_ONLY\n"
ARTIFACT_ID = "analysis:statistics"


def _regular(path: Path) -> Path:
    for p in (path,*path.parents):
        info = p.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0) & 0x400:
            raise ValueError("Lab paths cannot traverse links/reparse points")
    info = path.stat()
    if not path.is_file() or info.st_nlink != 1:
        raise ValueError("Lab requires a regular unlinked file")
    return path


@dataclass(frozen=True)
class EvidenceLab:
    root: Path
    baseline: Path
    expected_sha256: str

    @classmethod
    def create(cls, parent: Path, baseline: Path, expected_sha256: str) -> EvidenceLab:
        if any(p.lower().startswith("onedrive") for p in parent.absolute().parts):
            raise ValueError("Lab must be outside OneDrive")
        if baseline.name != "change_statistics.json":
            raise ValueError("Only the approved derived statistics artifact is a challenge target")
        _regular(baseline)
        if sha256_file(baseline) != expected_sha256 or baseline.stat().st_size > 100_000:
            raise ValueError("Derived baseline receipt mismatch or size limit")
        if not re.fullmatch(r"[0-9a-f]{64}",expected_sha256):
            raise ValueError("A separate expected SHA-256 is required")
        # Check existing ancestors BEFORE mkdir can follow a reparse boundary.
        for p in (parent,*parent.parents):
            if not p.exists() and not p.is_symlink(): continue
            info = p.lstat()
            if p.is_symlink() or getattr(info,"st_file_attributes",0) & 0x400:
                raise ValueError("Unsafe lab parent")
        if parent.resolve().is_relative_to(baseline.parent.resolve()):
            raise ValueError("Lab storage cannot be inside a protected analysis directory")
        parent.mkdir(parents=True,exist_ok=True)
        if sum(bool(re.fullmatch(r"[0-9a-f]{32}",p.name)) for p in parent.iterdir()) >= 100:
            raise ValueError("Session storage limit reached; operator review required")
        root = parent/uuid4().hex
        root.mkdir()
        with (root/"session.json").open("x",encoding="utf-8") as stream:
            json.dump(dict(schema_version="1.0",purpose="SZ-009_DISPOSABLE_DERIVED_COPY",session_id=root.name,
                           artifact_id=ARTIFACT_ID,expected_sha256=expected_sha256),stream)
        with (root/"events.jsonl").open("x",encoding="utf-8") as stream:
            stream.write("")
        lab = cls(root,baseline,expected_sha256)
        lab._replace(baseline.read_bytes())
        lab.verify("CREATED")
        return lab

    def _guard(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}",self.root.name):
            raise ValueError("Invalid service-created session ID")
        marker = json.loads(_regular(self.root/"session.json").read_text())
        if marker != dict(schema_version="1.0",purpose="SZ-009_DISPOSABLE_DERIVED_COPY",session_id=self.root.name,
                          artifact_id=ARTIFACT_ID,expected_sha256=self.expected_sha256):
            raise ValueError("Sandbox marker mismatch")
        _regular(self.baseline)
        if sha256_file(self.baseline) != self.expected_sha256:
            raise ValueError("Protected baseline failed verification; never reset acceptance hashes")
        _regular(self.root/"events.jsonl")
        if self.baseline.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Trusted baseline must be separate from disposable session")
        if self.root.resolve().is_relative_to(self.baseline.parent.resolve()):
            raise ValueError("Session cannot be inside the protected analysis directory")

    def _replace(self, content: bytes) -> None:
        self._guard()
        target = self.root/"derived_copy.json"
        if target.exists() or target.is_symlink(): _regular(target)
        temporary = self.root/(uuid4().hex+".tmp")
        try:
            with temporary.open("xb") as stream: stream.write(content)
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, action: str = "VERIFIED", *, details: dict | None = None) -> dict:
        self._guard()
        target = self.root/"derived_copy.json"
        try:
            observed = sha256_file(_regular(target))
            state = "MATCH" if observed == self.expected_sha256 else "MISMATCH"
        except (OSError,ValueError):
            observed,state = None,"MISSING_OR_UNSAFE"
        event = dict(sequence=len(self.events())+1,utc=datetime.now(timezone.utc).isoformat(),action=action,
                     artifact_id=ARTIFACT_ID,state=state,expected_sha256=self.expected_sha256,observed_sha256=observed,
                     scope="Disposable derived copy only; original NASA data and saved analysis unchanged")
        if details is not None:
            details=dict(details)
            if details.get('operation')=='RECOMPUTED_STATISTICS_FROM_VERIFIED_CHANGE_AND_SUPPORT':
                details['observed_state']=state
                details['local_revalidation']=('PASS' if details.get('byte_identical') is True and state=='MATCH' else
                    'NUMERIC_EQUIVALENCE_ONLY_HASH_MISMATCH' if details.get('numerically_equivalent') is True and not details.get('byte_identical') and state=='MISMATCH' else
                    'FAILED_OUTPUT_READBACK')
            if len(json.dumps(details,allow_nan=False)) > 40_000:
                raise ValueError("Event details exceed the bounded audit record size")
            event['details']=details
        with (self.root/"events.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(event)+"\n")
        return event

    def events(self) -> list[dict]:
        self._guard()
        text = (self.root/"events.jsonl").read_text()
        if len(text) > 1_000_000:
            raise ValueError("Session event limit reached")
        return [json.loads(line) for line in text.splitlines()]

    def challenge(self) -> dict:
        self._guard()
        if sha256_file(_regular(self.root/"derived_copy.json")) != self.expected_sha256:
            raise ValueError("Restore and reverify before repeating the challenge")
        self._replace(self.baseline.read_bytes()+MARKER)
        return self.verify("CONTROLLED_MODIFICATION")

    def restore(self) -> dict:
        self._replace(self.baseline.read_bytes())
        return self.verify("RESTORED_AND_REVERIFIED")
