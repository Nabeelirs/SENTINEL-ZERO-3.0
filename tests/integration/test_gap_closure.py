"""Actual six NISAR subsets and independently pinned scientific products."""
import json
import os
from pathlib import Path

import pytest

from sentinel_zero.application.data import configured_paths,load_explorer,watched_files
from sentinel_zero.evidence.earthbom import build_earthbom,evaluate
from sentinel_zero.evidence.lab import EvidenceLab,ARTIFACT_ID
from sentinel_zero.evidence.rebuild import rebuild_statistics
from sentinel_zero.science.preparation import sha256_file


@pytest.fixture(scope='module')
def actual_pair():
    if not os.environ.get('SENTINEL_ZERO_SOURCE_ROOT'):pytest.skip('Real reviewed NISAR sources required')
    paths=configured_paths({'SENTINEL_ZERO_RUNTIME_ROOT':str(Path(os.environ['SENTINEL_ZERO_SOURCE_ROOT']).parent)})
    data=load_explorer(paths)
    return paths,data,build_earthbom(data,paths)


@pytest.mark.real_data
def test_recompute_real_statistics_restore_disposable_hash_and_preserve_sources(actual_pair,tmp_path):
    paths,data,bom=actual_pair
    originals={p:sha256_file(p) for p in watched_files(paths)}
    lab=EvidenceLab.create(tmp_path/'sessions',paths.analysis/'change_statistics.json',bom.node(ARTIFACT_ID).sha256)
    lab.challenge()
    receipt=rebuild_statistics(paths,lab)
    assert receipt['operation']=='RECOMPUTED_STATISTICS_FROM_VERIFIED_CHANGE_AND_SUPPORT'
    assert receipt['numerically_equivalent'] and receipt['byte_identical']
    assert receipt['expected_sha256']==receipt['regenerated_sha256']=='d054af94525b4c88c18c1619eddd9eb6658350b9f0f6b23e563121d0eadcd050'
    assert receipt['observed_state']=='MATCH' and receipt['local_revalidation']=='PASS'
    assert len(receipt['dependencies'])==8
    assert json.loads((lab.root/'derived_copy.json').read_text())==dict(data.product.statistics)
    event=next(e for e in lab.events() if e['action']=='REBUILT_FROM_VERIFIED_DEPENDENCIES')
    assert event['details']['numerically_equivalent']
    assert evaluate(bom,{ARTIFACT_ID:receipt['observed_state']})['requires_revalidation']==[]
    assert all(sha256_file(p)==digest for p,digest in originals.items())


@pytest.mark.real_data
def test_equal_numbers_with_different_serialization_never_reset_trusted_hash(actual_pair,tmp_path,monkeypatch):
    import sentinel_zero.evidence.rebuild as module
    paths,data,bom=actual_pair
    lab=EvidenceLab.create(tmp_path/'sessions',paths.analysis/'change_statistics.json',bom.node(ARTIFACT_ID).sha256)
    monkeypatch.setattr(module,'statistics_bytes',lambda value:json.dumps(value,sort_keys=True).encode())
    receipt=rebuild_statistics(paths,lab)
    assert receipt['numerically_equivalent'] and not receipt['byte_identical']
    assert receipt['observed_state']=='MISMATCH'
    assert receipt['local_revalidation']=='NUMERIC_EQUIVALENCE_ONLY_HASH_MISMATCH'
    assert lab.expected_sha256==bom.node(ARTIFACT_ID).sha256


@pytest.mark.real_data
def test_wrong_reference_or_modified_reconstruction_is_blocked(actual_pair,tmp_path,monkeypatch):
    import sentinel_zero.evidence.rebuild as module
    paths,data,bom=actual_pair
    lab=EvidenceLab.create(tmp_path/'sessions',paths.analysis/'change_statistics.json',bom.node(ARTIFACT_ID).sha256)
    before=sha256_file(lab.root/'derived_copy.json')
    monkeypatch.setattr(module,'statistics',lambda *a:dict(data.product.statistics,mean=999))
    with pytest.raises(ValueError,match='Recomputed statistics'):rebuild_statistics(paths,lab)
    assert sha256_file(lab.root/'derived_copy.json')==before


@pytest.mark.real_data
def test_rebuild_never_reports_pass_when_written_copy_readback_fails(actual_pair,tmp_path,monkeypatch):
    paths,data,bom=actual_pair
    lab=EvidenceLab.create(tmp_path/'sessions',paths.analysis/'change_statistics.json',bom.node(ARTIFACT_ID).sha256)
    original=EvidenceLab.verify
    def failed_readback(self,action='VERIFIED',**kwargs):
        event=original(self,action,**kwargs)
        if action=='REBUILT_FROM_VERIFIED_DEPENDENCIES':
            event=dict(event,state='MISMATCH',observed_sha256='a'*64)
        return event
    monkeypatch.setattr(EvidenceLab,'verify',failed_readback)
    receipt=rebuild_statistics(paths,lab)
    assert receipt['byte_identical'] and receipt['numerically_equivalent']
    assert receipt['observed_state']=='MISMATCH'
    assert receipt['local_revalidation']=='FAILED_OUTPUT_READBACK'


@pytest.mark.real_data
def test_new_ui_rebuild_decision_and_optional_import_preserve_default_boundary(actual_pair,tmp_path,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from io import BytesIO
    from pypdf import PdfReader
    from sentinel_zero.reporting.investigation import investigation_pdf
    from sentinel_zero.evidence.impact import decision_impact
    paths,data,bom=actual_pair
    monkeypatch.setenv('SENTINEL_ZERO_RUNTIME_ROOT',str(paths.source.parent))
    monkeypatch.delenv('SENTINEL_ZERO_BUNDLE_ROOT',raising=False)
    monkeypatch.setenv('SENTINEL_ZERO_LAB_ROOT',str(tmp_path/'sessions'))
    at=AppTest.from_file(Path(__file__).resolve().parents[2]/'app.py',default_timeout=40).run()
    at.radio(key='stage').set_value('Investigate Science').run()
    assert not at.exception and not at.text_input and not at.get('file_uploader')
    at.toggle(key='open_rainfall_import').set_value(True).run()
    assert not at.exception and len(at.get('file_uploader'))==1 and len(at.text_input)>=4
    at.radio(key='stage').set_value('Challenge Evidence').run()
    next(b for b in at.button if b.label=='Create isolated evidence copy').click().run()
    next(b for b in at.button if b.label=='Modify disposable copy').click().run()
    at.radio(key='stage').set_value('Trace Consequences').run()
    assert not at.exception
    assert any('Decision Impact Analyzer' in x.value for x in at.markdown)
    at.radio(key='stage').set_value('Restore & Report').run()
    next(b for b in at.button if b.label=='Rebuild statistics from verified inputs').click().run()
    assert not at.exception
    receipt=at.session_state['rebuild_receipt']
    assert receipt['local_revalidation']=='PASS' and receipt['byte_identical']
    lab=at.session_state['evidence_lab']
    state=evaluate(bom,{ARTIFACT_ID:lab.verify()['state']})
    state.update(rebuild=receipt,decision_impact=decision_impact(bom,ARTIFACT_ID,state))
    reader=PdfReader(BytesIO(investigation_pdf(data,(157,309),3,trust=state)))
    assert len(reader.pages)==5
    text='\n'.join(p.extract_text() for p in reader.pages)
    assert 'RECOMPUTED_STATISTICS_FROM_VERIFIED_CHANGE_AND_SUPPORT' in text
    assert 'Decision Impact Analyzer' in text and receipt['regenerated_sha256'] in ''.join(text.split())
    assert 'UNVERIFIED' in text
    at.radio(key='stage').set_value('Challenge Evidence').run()
    next(b for b in at.button if b.label=='Modify disposable copy').click().run()
    at.radio(key='stage').set_value('Restore & Report').run()
    assert not at.exception
    assert at.session_state['last_passport_receipt']['local_integrity']=='FAILED'
