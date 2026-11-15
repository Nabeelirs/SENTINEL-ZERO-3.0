"""Public entry contract; real data are separately and explicitly identified."""
import os
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_bundle_environment(monkeypatch):
    for name in ('SENTINEL_ZERO_BUNDLE_ROOT','SENTINEL_ZERO_BUNDLE_SHA256'):
        monkeypatch.delenv(name,raising=False)


def test_missing_public_archive_blocks_without_substitute_or_private_path(tmp_path,monkeypatch):
    monkeypatch.setenv('SENTINEL_ZERO_DEMO_ARCHIVE',str(tmp_path/'absent.zip'))
    app=AppTest.from_file(ROOT/'public_app.py',default_timeout=30).run()
    assert not app.exception
    assert app.error and not app.get('bidi_component') and not app.get('image')
    assert not any(str(tmp_path) in str(e.value) for e in app.markdown)


def test_corrupt_public_archive_blocks_without_overwriting_it(tmp_path,monkeypatch):
    path=tmp_path/'corrupt.zip';content=b'SYNTHETIC CORRUPT ARCHIVE TEST ONLY';path.write_bytes(content)
    monkeypatch.setenv('SENTINEL_ZERO_DEMO_ARCHIVE',str(path))
    app=AppTest.from_file(ROOT/'public_app.py',default_timeout=30).run()
    assert not app.exception and app.error and not app.get('bidi_component')
    assert path.read_bytes()==content


@pytest.mark.real_data
def test_public_archive_uses_accepted_atlas_and_exact_real_pixel(monkeypatch):
    if not os.environ.get('SENTINEL_ZERO_SOURCE_ROOT'):
        pytest.skip('Actual runtime unavailable; no synthetic NASA data substituted')
    archive=Path(os.environ['LOCALAPPDATA'])/'SENTINEL-ZERO/runtime/sz007/offline-v1.zip'
    if not archive.exists():pytest.skip('Verified portable scientific archive unavailable')
    monkeypatch.setenv('SENTINEL_ZERO_DEMO_ARCHIVE',str(archive))
    app=AppTest.from_file(ROOT/'public_app.py',default_timeout=40).run()
    assert not app.exception and not app.error and len(app.get('bidi_component'))==1
    next(b for b in app.button if b.label=='Start Investigation').click().run()
    next(b for b in app.button if b.label=='Inspect largest absolute-change cell').click().run()
    assert app.number_input(key='pixel_row').value==157
    assert app.number_input(key='pixel_column').value==309
    assert any(m.value=='-26.151365 dB' for m in app.metric)
