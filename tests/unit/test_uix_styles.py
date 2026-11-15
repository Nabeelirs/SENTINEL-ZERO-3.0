"""Synthetic resource-loader checks; never substitute scientific observations."""
from pathlib import Path

from sentinel_zero.presentation import atlas_theme


def test_stylesheet_reads_current_packaged_resource_each_call(monkeypatch):
    class SyntheticResource:
        def __init__(self):
            self.calls=0
        def joinpath(self,name):
            assert name=='atlas.css'
            return self
        def read_text(self,encoding):
            assert encoding=='utf-8'
            self.calls+=1
            return f'/* SYNTHETIC STYLE VERSION {self.calls} */'
    resource=SyntheticResource()
    monkeypatch.setattr(atlas_theme,'files',lambda package:resource)
    assert 'VERSION 1' in atlas_theme.stylesheet()
    assert 'VERSION 2' in atlas_theme.stylesheet()


def test_curated_source_map_carries_actual_offline_css_asset():
    import sys
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'tools'))
    from prepare_release import source_map
    mapping=source_map(root,root/'unused-scientific-bundle-reference.zip')
    css='src/sentinel_zero/presentation/atlas.css'
    assert css in mapping and mapping[css].is_file()
    content=mapping[css].read_text(encoding='utf-8')
    assert '@import' not in content and 'https://' not in content


def test_new_visual_build_keeps_its_identity_in_future_private_packaging(tmp_path,monkeypatch):
    """Synthetic package only; never create or overwrite a real release."""
    import sys
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'tools'))
    import prepare_release as release
    repo=tmp_path/'synthetic-repository'
    theme=repo/'src/sentinel_zero/presentation/theme.py'
    theme.parent.mkdir(parents=True)
    theme.write_text('UI_BUILD = "SZ-UIX-01 / EVIDENCE ATLAS 01"',encoding='utf-8')
    source=repo/'app.py';source.write_text('# synthetic software fixture',encoding='utf-8')
    bundle=repo/'fixture.zip';bundle.write_bytes(b'SYNTHETIC ZIP IDENTITY ONLY')
    monkeypatch.setattr(release,'ARCHIVE_SHA256',release.digest(bundle))
    monkeypatch.setattr(release,'source_map',lambda *_:{'app.py':source,'scientific_data.zip':bundle})
    monkeypatch.setattr(release.subprocess,'check_output',lambda *a,**k:'synthetic-checkpoint')
    target=tmp_path/'synthetic-private-candidate'
    anchor=release.assemble(repo,bundle,target)
    record=release.verify(target,anchor)
    assert record['task']=='SZ-UIX-01' and record['build']=='SZ-UIX-01 / EVIDENCE ATLAS 01'
