"""Assemble a curated private RC. Explicit allowlist; no Git commit or publication."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from run_demo import ARCHIVE_SHA256, digest

ASSETS = ("desktop_full.png", "valid.png", "evidence.png", "lineage.png", "report.png", "mobile.png")


def source_map(repo: Path, bundle: Path) -> dict[str, Path]:
    items = {str(p.relative_to(repo)).replace("\\", "/"): p for p in (repo / "src").rglob("*")
             if p.is_file() and p.suffix in (".py", ".css", ".html", ".mjs")}
    for name in ("app.py", "pyproject.toml", "requirements-ui.txt", ".streamlit/config.toml"):
        items[name] = repo / name
    if (repo/"atlas_app.py").is_file():
        items["atlas_app.py"]=repo/"atlas_app.py"
    for name in ("run_demo.py", "hosted_app.py"):
        items[name] = repo / "tools" / name
    for p in (repo / "docs/submission").rglob("*"):
        if p.is_file():
            items[str(p.relative_to(repo)).replace("\\", "/")] = p
    items["README.md"] = repo / "docs/submission/portable_launch.md"
    for name in ASSETS:
        items["assets/" + name] = repo / "docs/evidence/sz007/redesign/accepted_v2" / name
    items["assets/investigation_report.pdf"] = repo / "docs/evidence/sz007/redesign/investigation_report_final.pdf"
    items["scientific_data.zip"] = bundle
    # SZ-009 additions are explicit evidence assets, not a bulk evidence export.
    newer = repo/"docs/evidence/sz009"
    for name in ("final_pilot.png","final_global.png","final_twin.png","final_recovery.png","final_mobile_twin.png",
                 "earthbom.json","global_summary.json","recovered_passport.pdf"):
        if (newer/name).is_file(): items["assets/sz009/"+name] = newer/name
    if (newer/"recovered_passport.pdf").is_file():
        items["assets/investigation_report.pdf"] = newer/"recovered_passport.pdf"
    closure=repo/'docs/evidence/sz009b'
    for name in ('rebuild_desktop.png','global_desktop.png','global_mobile.png','witness_mobile_verified.png',
                 'reconstruction_workflow.json','global/pilot_adapter_replay.json','witness/actual_context_availability.json'):
        if (closure/name).is_file():items['assets/sz009b/'+name]=closure/name
    if (closure/'recomputed_passport.pdf').is_file():
        items['assets/investigation_report.pdf']=closure/'recomputed_passport.pdf'
    return items


def assemble(repo: Path, bundle: Path, output: Path, *, entry_point: str = "app.py") -> str:
    if entry_point not in ("app.py","atlas_app.py"):
        raise ValueError("Unsupported release application entry point")
    if output.exists():
        raise FileExistsError("Release destination exists; choose a new version")
    if digest(bundle) != ARCHIVE_SHA256:
        raise ValueError("Original scientific bundle checksum mismatch")
    sources = source_map(repo, bundle)
    if entry_point=="atlas_app.py":
        if not (repo/entry_point).is_file():raise ValueError("Earth Atlas entry point missing")
        sources["app.py"]=repo/entry_point
    if not sources or any(not p.is_file() or p.is_symlink() for p in sources.values()):
        raise ValueError("Required release file missing or linked")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name("rc-" + uuid4().hex[:8])
    stage.mkdir()
    try:
        rows = []
        for name, src in sorted(sources.items()):
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as incoming, dest.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            sha = digest(dest)
            if sha != digest(src):
                raise ValueError("Source changed while preparing release")
            rows.append(dict(path=name, bytes=dest.stat().st_size, sha256=sha))
        (stage / "requirements.txt").write_text("-r requirements-ui.txt\n.\n", encoding="utf-8")
        rows.append(dict(path="requirements.txt", bytes=(stage/"requirements.txt").stat().st_size,
                         sha256=digest(stage/"requirements.txt")))
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        label = "SZ-007B / EXPLORER 04"
        theme = repo/"src/sentinel_zero/presentation"/("earth_atlas.py" if entry_point=="atlas_app.py" else "theme.py")
        if entry_point=="atlas_app.py" and not theme.is_file():raise ValueError("Earth Atlas identity missing")
        if theme.is_file():
            for statement in ast.parse(theme.read_text(encoding="utf-8")).body:
                if isinstance(statement,ast.Assign) and any(isinstance(t,ast.Name) and t.id == "UI_BUILD" for t in statement.targets):
                    label = ast.literal_eval(statement.value)
        if not isinstance(label,str) or not label.startswith("SZ-"): raise ValueError("Missing release build identity")
        task='SZ-UIX-02' if label.startswith('SZ-UIX-02') else 'SZ-UIX-01' if label.startswith('SZ-UIX-01') else 'SZ-009B' if label.startswith('SZ-009B') else 'SZ-009' if label.startswith('SZ-009') else 'SZ-008'
        manifest = dict(schema_version="1.0", task=task, release_id=output.name, build=label,entry_point_source=entry_point,
                        created_at_utc=datetime.now(timezone.utc).isoformat(), baseline_git_revision=head,
                        source_state="uncommitted working-tree files individually hashed; not a release commit",
                        ai_assistance="OpenAI Codex assisted code, tests and documentation; satellite observations are real NISAR data",
                        publication_status="HOLD: owner license, third-party notices, data reuse and form review required",
                        files=sorted(rows, key=lambda r:r["path"]))
        (stage / "release_manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
        result = digest(stage / "release_manifest.json")
        if output.exists():
            raise FileExistsError("Release destination appeared; no overwrite")
        stage.rename(output)
        return result
    finally:
        if stage.exists():
            assert stage.parent == output.parent and stage.name.startswith("rc-")
            shutil.rmtree(stage)


def verify(root: Path, expected_sha256: str) -> dict:
    if digest(root / "release_manifest.json") != expected_sha256:
        raise ValueError("Release manifest checksum mismatch")
    record = json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))
    rows = record["files"]
    names = [r["path"] for r in rows]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate release member")
    for row in rows:
        name = row["path"]
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe release member")
        p = root / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size != row["bytes"] or digest(p) != row["sha256"]:
            raise ValueError("Release member mismatch")
    actual = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()}
    if actual != set(names) | {"release_manifest.json"}:
        raise ValueError("Unexpected or missing release files")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("assemble")
    build.add_argument("--repo", type=Path, required=True)
    build.add_argument("--bundle", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--entry-point",choices=("app.py","atlas_app.py"),default="app.py")
    check = sub.add_parser("verify")
    check.add_argument("--release", type=Path, required=True)
    check.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "assemble":
            print("RELEASE_ASSEMBLED", assemble(args.repo, args.bundle, args.output,entry_point=args.entry_point))
        else:
            print("RELEASE_VERIFIED", len(verify(args.release, args.sha256)["files"]))
        return 0
    except (ValueError, OSError, KeyError):
        print("BLOCKED: release identity, allowlist or destination check failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
