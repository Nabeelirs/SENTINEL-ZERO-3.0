"""All fabricated metadata is labelled SYNTHETIC; regression data stays read-only."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path

import pytest

from sentinel_zero.discovery.cmr import CMRClient, HttpResponse, parse_collection, parse_granule


@pytest.fixture
def fixture_data():
    return json.loads((Path(__file__).parent / "fixtures/synthetic_cmr.json").read_text(encoding="utf-8"))


@pytest.fixture
def collection(fixture_data):
    return parse_collection(fixture_data["collection"])


@pytest.fixture
def observation(fixture_data, collection):
    return parse_granule(deepcopy(fixture_data["granule"]), collection)


@pytest.fixture
def after(observation):
    return replace(observation, granule_concept_id="G2-TEST", producer_granule_id="SYNTHETIC_NOT_A_REAL_ACQUISITION_2",
                   acquisition_start=observation.acquisition_start + timedelta(days=12), acquisition_end=observation.acquisition_end + timedelta(days=12))


def response(entries, *, hits=None, cursor=None, status=200, collections=False, headers=None):
    data = {"feed": {"entry": entries}} if collections else {"hits": len(entries) if hits is None else hits, "items": entries}
    response_headers = {"Content-Type": "application/json", "CMR-Hits": str(len(entries) if hits is None else hits)}
    if cursor is not None:
        response_headers["CMR-Search-After"] = cursor
    response_headers.update(headers or {})
    return HttpResponse(status, response_headers, json.dumps(data).encode())


class SequenceTransport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def fake_client(responses, **kwargs):
    transport = SequenceTransport(responses)
    sleeps = []
    return CMRClient(transport, sleep=sleeps.append, **kwargs), transport, sleeps
