"""Synthetic packaging checks; no release or NASA scientific fixture is created."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import prepare_release as release


def test_curated_source_includes_whole_local_frontend_and_separate_entry():
    mapping=release.source_map(ROOT,ROOT/'unopened-bundle-reference.zip')
    for name in ('earth_canvas.html','earth_canvas.css','earth_canvas.mjs','earth_shell.css','earth_viewer.py','earth_atlas.py'):
        key='src/sentinel_zero/presentation/'+name
        assert key in mapping and mapping[key].is_file()
    assert mapping['atlas_app.py']==ROOT/'atlas_app.py'
    assert mapping['app.py']==ROOT/'app.py'


def test_explicit_atlas_package_selects_real_entry_and_own_identity_without_replacing_default(tmp_path,monkeypatch):
    repo=tmp_path/'synthetic-repo';repo.mkdir()
    legacy=repo/'app.py';legacy.write_text('# SYNTHETIC LEGACY',encoding='utf-8')
    atlas=repo/'atlas_app.py';atlas.write_text('# SYNTHETIC ATLAS',encoding='utf-8')
    theme=repo/'src/sentinel_zero/presentation/earth_atlas.py';theme.parent.mkdir(parents=True)
    theme.write_text('UI_BUILD = "SZ-UIX-02 / EARTH ATLAS 02"',encoding='utf-8')
    bundle=repo/'fixture.zip';bundle.write_bytes(b'SYNTHETIC SOFTWARE IDENTITY FIXTURE')
    monkeypatch.setattr(release,'ARCHIVE_SHA256',release.digest(bundle))
    monkeypatch.setattr(release,'source_map',lambda *_:{'app.py':legacy,'atlas_app.py':atlas,'scientific_data.zip':bundle})
    monkeypatch.setattr(release.subprocess,'check_output',lambda *a,**k:'synthetic-checkpoint')
    output=tmp_path/'synthetic-candidate'
    sha=release.assemble(repo,bundle,output,entry_point='atlas_app.py')
    record=release.verify(output,sha)
    assert record['task']=='SZ-UIX-02' and record['build']=='SZ-UIX-02 / EARTH ATLAS 02'
    assert record['entry_point_source']=='atlas_app.py'
    assert (output/'app.py').read_bytes()==atlas.read_bytes()
    assert legacy.read_text()=='# SYNTHETIC LEGACY'


@pytest.mark.parametrize('entry',['../app.py','https://example.invalid/app.py','arbitrary.py'])
def test_release_entry_does_not_accept_arbitrary_path_or_url(tmp_path,entry):
    with pytest.raises(ValueError,match='entry point'):
        release.assemble(tmp_path,tmp_path/'absent.zip',tmp_path/'out',entry_point=entry)
