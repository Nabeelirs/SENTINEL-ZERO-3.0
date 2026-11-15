"""Recompute disposable statistics from reverified saved change/common support."""
from __future__ import annotations

from datetime import datetime,timezone
import hashlib
import json
from uuid import uuid4

from ..application.data import RuntimePaths,load_explorer,safe_file
from ..science.change import statistics
from ..science.preparation import sha256_file
from .lab import EvidenceLab,ARTIFACT_ID


def statistics_bytes(values: dict) -> bytes:
    """Same explicit SZ006 serialization; not copied source bytes."""
    return json.dumps(values,indent=2,allow_nan=False).encode('utf-8')


def rebuild_statistics(paths: RuntimePaths, lab: EvidenceLab) -> dict:
    """Reopen/hash all six sources and products through the existing full loader.

    Only the session-owned copy is replaced. A byte-different equivalent result
    stays a hash mismatch; its trusted reference is never rewritten.
    """
    lab._guard()
    baseline=safe_file(paths.analysis,'change_statistics.json')
    if baseline.resolve()!=lab.baseline.resolve():
        raise ValueError('Rebuild must use the exact independent session reference')
    data=load_explorer(paths)
    product=data.product
    expected=product.provenance['outputs']['statistics']
    if expected['sha256']!=lab.expected_sha256:
        raise ValueError('Rebuild reference differs from the accepted analysis receipt')
    actual=statistics(product.change_db,product.prepared.common_valid,product.prepared.grid)
    if actual!=dict(product.statistics):
        raise ValueError('Recomputed statistics differ from the independently preserved scientific record')
    content=statistics_bytes(actual)
    observed=hashlib.sha256(content).hexdigest()
    inputs=[]
    for inventory in product.prepared.provenance['source_inventories']:
        for art in inventory['artifacts']:
            inputs.append(dict(role='verified_original_subset',granule_id=inventory['source']['granule_id'],
                               layer=art['layer'],sha256=art['sha256'],bytes=art['bytes']))
    for role,root,record in (
        ('saved_change',paths.analysis,product.provenance['outputs']['change']),
        ('common_valid',paths.preparation,product.prepared.provenance['outputs']['common_valid'])):
        path=safe_file(root,record['filename'])
        digest=sha256_file(path)
        if digest!=record['sha256']: raise ValueError('Dependency changed during rebuild')
        inputs.append(dict(role=role,filename=path.name,sha256=digest,bytes=path.stat().st_size))
    receipt=dict(schema_version='1.0',task='SZ-009B',artifact_id=ARTIFACT_ID,
                 timestamp_utc=datetime.now(timezone.utc).isoformat(),
                 operation='RECOMPUTED_STATISTICS_FROM_VERIFIED_CHANGE_AND_SUPPORT',
                 expected_sha256=lab.expected_sha256,regenerated_sha256=observed,
                 regenerated_bytes=len(content),numerically_equivalent=True,
                 byte_identical=observed==lab.expected_sha256,
                 method='Existing SZ006 statistics function on reloaded saved float32 change and common boolean support; no copying of statistics baseline bytes',
                 serialization='UTF-8 JSON indent=2, original field order, no trailing newline, allow_nan=False',
                 dependencies=inputs,scientific_quality='CONDITIONAL_PROVISIONAL',physical_cause='UNVERIFIED',
                 local_revalidation='PENDING_OUTPUT_READBACK')
    # The contained publisher guards the reference again immediately before write.
    lab._replace(content)
    event=lab.verify('REBUILT_FROM_VERIFIED_DEPENDENCIES',details=receipt)
    receipt['observed_state']=event['state']
    receipt['local_revalidation']=('PASS' if receipt['byte_identical'] and event['state']=='MATCH' and event.get('observed_sha256')==observed else
        'NUMERIC_EQUIVALENCE_ONLY_HASH_MISMATCH' if receipt['numerically_equivalent'] and not receipt['byte_identical'] and event['state']=='MISMATCH' and event.get('observed_sha256')==observed else
        'FAILED_OUTPUT_READBACK')
    with (lab.root/('rebuild_receipt_'+uuid4().hex+'.json')).open('x',encoding='utf-8') as stream:
        json.dump(receipt,stream,indent=2,allow_nan=False)
    return receipt
