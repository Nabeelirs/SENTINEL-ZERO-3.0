"""Labelled synthetic graph fixtures; actual-data rebuild is tested separately."""
import hashlib
import json

import pytest

from sentinel_zero.evidence.earthbom import Artifact,EarthBOM,evaluate
from sentinel_zero.evidence.impact import decision_impact
from sentinel_zero.evidence.rebuild import statistics_bytes


@pytest.fixture
def synthetic_bom():
    def n(i,parents=(),kind='synthetic_fixture'):
        return Artifact(i,i,kind,parents,None,None,'MATCH',{})
    return EarthBOM((n('source'),n('change',('source',)),n('statistics',('change',)),
                     n('manifest',('change','statistics')),n('report',('manifest','statistics'),'runtime_report'),n('unrelated')),'fixture',())


def test_recorded_edges_and_hypothetical_decision_are_separate(synthetic_bom):
    state=evaluate(synthetic_bom,{'statistics':'MISMATCH'})
    report=decision_impact(synthetic_bom,'statistics',state)
    assert report['direct_dependents']==['manifest','report']
    assert report['downstream_artifacts']==['manifest','report']
    assert report['currently_flagged']==['statistics','manifest','report']
    assert report['dependent_reports']==['report']
    assert 'pause reliance' in report['hypothetical_decision_consequence']
    assert report['decision_status']=='HYPOTHETICAL_GUIDANCE_NOT_AN_OPERATIONAL_ALERT'
    assert 'source' not in report['currently_flagged'] and 'change' not in report['currently_flagged']
    assert report['physical_cause']=='UNVERIFIED'


def test_unaffected_leaf_and_recovery_do_not_erase_quality(synthetic_bom):
    state=evaluate(synthetic_bom)
    leaf=decision_impact(synthetic_bom,'unrelated',state)
    assert leaf['direct_dependents']==leaf['downstream_artifacts']==leaf['dependent_reports']==[]
    assert leaf['currently_flagged']==[]
    assert leaf['scientific_quality']=='CONDITIONAL_PROVISIONAL'
    assert 'unresolved' in leaf['hypothetical_decision_consequence']


def test_unknown_node_or_incomplete_state_rejected(synthetic_bom):
    with pytest.raises(StopIteration):decision_impact(synthetic_bom,'missing',evaluate(synthetic_bom))
    with pytest.raises(ValueError):decision_impact(synthetic_bom,'source',{'artifact_integrity':{}})


def test_original_serialization_is_explicit_but_not_numerical_validation():
    fixture={'synthetic_fixture':True,'units':'dB','mean':0.5}
    content=statistics_bytes(fixture)
    assert content==b'{\n  "synthetic_fixture": true,\n  "units": "dB",\n  "mean": 0.5\n}'
    differently_serialized=json.dumps(fixture,sort_keys=True).encode()
    assert json.loads(content)==json.loads(differently_serialized)
    assert hashlib.sha256(content).digest()!=hashlib.sha256(differently_serialized).digest()
    with pytest.raises(ValueError):statistics_bytes({'synthetic_bad':float('nan')})
