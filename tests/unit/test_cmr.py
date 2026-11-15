from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import fake_client, response
from sentinel_zero.contracts import BoundingBox, DiscoveryQuery
from sentinel_zero.discovery.cmr import HttpResponse, decode_json, parse_granule, public_http
from sentinel_zero.errors import DiscoveryError, MetadataError


def query(**kwargs):
    return DiscoveryQuery(collection_concept_id="C1-TEST", **kwargs)


def changed_record(item):
    result = deepcopy(item)
    result["meta"]["concept-id"] = "G2-TEST"
    return result


def test_collection_response(fixture_data):
    client, _, _ = fake_client([response([fixture_data["collection"]], collections=True)])
    result = client.search_collections(DiscoveryQuery(short_name="SYNTHETIC*"))
    assert result.pagination_complete and result.collections[0].maturity == "PROVISIONAL"


def test_valid_granule_response(fixture_data, collection):
    client, _, _ = fake_client([response([fixture_data["granule"]])])
    result = client.search_granules(query(), collection)
    assert result.pagination_complete and result.retrieved_count == 1
    assert result.observations[0].channels[0].range_bandwidth_mhz == 40
    assert result.observations[0].missing_fields == ()


def test_empty_results(collection):
    client, _, _ = fake_client([response([])])
    result = client.search_granules(query(), collection)
    assert result.pagination_complete and result.retrieved_count == result.matching_count == 0
    assert not result.failures


@pytest.mark.parametrize("body,content,code", [(b"{", "application/json", "MALFORMED_JSON"), (b"[]", "application/json", "RESPONSE_SCHEMA"), (b"<html>failure</html>", "text/html", "CONTENT_TYPE")])
def test_invalid_response(body, content, code, collection):
    client, _, _ = fake_client([HttpResponse(200, {"Content-Type": content, "CMR-Hits": "0"}, body)])
    result = client.search_granules(query(), collection)
    assert not result.pagination_complete and result.failures[0].code == code


def test_missing_optional_metadata_explicit(fixture_data, collection):
    item = deepcopy(fixture_data["granule"])
    item["umm"]["AdditionalAttributes"] = []
    parsed = parse_granule(item, collection)
    assert parsed.track is None and parsed.product_family is None
    assert "track" in parsed.missing_fields


def test_invalid_record_not_silent(fixture_data, collection):
    item = deepcopy(fixture_data["granule"])
    del item["umm"]["TemporalExtent"]
    client, _, _ = fake_client([response([item])])
    result = client.search_granules(query(), collection)
    assert not result.pagination_complete and result.retrieved_count == 0
    assert result.failures[0].code == "RECORD_SCHEMA"


def test_multiple_pages_and_header_semantics(fixture_data, collection):
    item = fixture_data["granule"]
    client, transport, _ = fake_client([response([item], hits=2, cursor='["first",1]'), response([changed_record(item)], hits=2)])
    result = client.search_granules(query(page_size=1, bounding_box=BoundingBox(79, 6, 81, 8), temporal_start="2026-01-01T00:00:00Z", temporal_end="2026-11-12T00:00:00Z"), collection)
    assert result.pagination_complete and result.retrieved_count == 2 and len(result.pages) == 2
    assert transport.calls[0][0] == transport.calls[1][0]
    assert "CMR-Search-After" not in transport.calls[0][1]
    assert transport.calls[1][1]["CMR-Search-After"] == '["first",1]'
    params = parse_qs(urlsplit(transport.calls[1][0]).query)
    assert params["bounding_box"] == ["79,6,81,8"] and "temporal" in params
    assert "page_num" not in params


def test_empty_final_page_before_hits_is_incomplete(fixture_data, collection):
    client, _, _ = fake_client([response([fixture_data["granule"]], hits=2, cursor="next"), response([], hits=2)])
    result = client.search_granules(query(page_size=1), collection)
    assert not result.pagination_complete and result.failures[-1].code == "MISSING_CURSOR"


@pytest.mark.parametrize("case,code", [("duplicate", "DUPLICATE_RECORD"), ("repeat", "REPEATED_CURSOR"), ("missing", "MISSING_CURSOR"), ("changed", "CATALOG_CHANGED")])
def test_pagination_anomalies(case, code, fixture_data, collection):
    item = fixture_data["granule"]
    if case == "missing":
        replies = [response([item], hits=3)]
    else:
        replies = [response([item], hits=3, cursor="next"), response([item if case == "duplicate" else changed_record(item)], hits=4 if case == "changed" else 3, cursor="next" if case == "repeat" else "last")]
    client, _, _ = fake_client(replies)
    result = client.search_granules(query(page_size=1), collection)
    assert not result.pagination_complete and result.failures[-1].code == code


@pytest.mark.parametrize("limits,code", [({"max_pages": 1}, "PAGE_LIMIT"), ({"max_results": 1}, "RESULT_LIMIT")])
def test_limits_not_false_success(limits, code, fixture_data, collection):
    client, _, _ = fake_client([response([fixture_data["granule"]], hits=3, cursor="next")])
    result = client.search_granules(query(page_size=1, **limits), collection)
    assert not result.pagination_complete and result.failures[-1].code == code


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_retry(status, collection):
    client, transport, sleeps = fake_client([response([], status=status, headers={"Retry-After": "2"}), response([])])
    assert client.search_granules(query(), collection).pagination_complete
    assert len(transport.calls) == 2 and sleeps == [2]


def test_timeout_retry_bounded(collection):
    client, transport, sleeps = fake_client([TimeoutError(), TimeoutError(), TimeoutError()])
    result = client.search_granules(query(), collection)
    assert result.failures[0].code == "TIMEOUT"
    assert len(transport.calls) == 3 and sleeps == [1, 2]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_http_retries_exhausted(status, collection):
    client, transport, sleeps = fake_client([response([], status=status)] * 3)
    result = client.search_granules(query(), collection)
    assert not result.pagination_complete and result.failures[0].http_status == status
    assert len(transport.calls) == 3 and sleeps == [1, 2]


def test_network_failure_distinct_from_empty(collection):
    client, transport, _ = fake_client([DiscoveryError("NETWORK", "CMR network connection failed")])
    result = client.search_granules(query(), collection)
    assert result.matching_count is None and not result.pagination_complete
    assert result.failures[0].code == "NETWORK" and len(transport.calls) == 1


def test_invalid_hit_count(collection):
    client, _, _ = fake_client([response([], headers={"CMR-Hits": "1.5"})])
    assert client.search_granules(query(), collection).failures[0].code == "RESPONSE_SCHEMA"


def test_body_count_conflict(collection):
    client, _, _ = fake_client([response([], headers={"CMR-Hits": "3"})])
    assert client.search_granules(query(), collection).failures[0].code == "COUNT_MISMATCH"


def test_response_size_limit(collection):
    from sentinel_zero.discovery.cmr import MAX_BODY
    client, _, _ = fake_client([HttpResponse(200, {"Content-Type": "application/json"}, b" " * (MAX_BODY + 1))])
    assert client.search_granules(query(), collection).failures[0].code == "RESPONSE_LIMIT"


def test_page_overshoot_is_bounded(fixture_data, collection):
    client, _, _ = fake_client([response([fixture_data["granule"], changed_record(fixture_data["granule"])])])
    assert client.search_granules(query(page_size=1), collection).failures[0].code == "RESPONSE_SCHEMA"


@pytest.mark.parametrize("status", [401, 403, 400, 404, 302])
def test_permanent_errors_not_retried(status, collection):
    client, transport, sleeps = fake_client([response([], status=status)])
    result = client.search_granules(query(), collection)
    assert result.failures[0].http_status == status and len(transport.calls) == 1 and not sleeps


def test_server_partial_timeout(collection):
    client, _, _ = fake_client([response([], headers={"CMR-Time-Out": "true"})])
    assert client.search_granules(query(), collection).failures[0].code == "CMR_TIMEOUT"


def test_retry_after_budget_not_violated(collection):
    client, transport, sleeps = fake_client([response([], status=429, headers={"Retry-After": "120"})])
    assert client.search_granules(query(), collection).failures[0].code == "RETRY_DEFERRED"
    assert not sleeps and len(transport.calls) == 1


def test_urls_never_followed_or_saved_with_secrets(fixture_data, collection):
    item = deepcopy(fixture_data["granule"])
    item["umm"]["RelatedUrls"].extend([{"URL": "https://example.invalid/file?token=private", "Type": "GET DATA"}, {"URL": "https://example.invalid/s3credentials", "Type": "GET DATA"}])
    obs = parse_granule(item, collection)
    assert len(obs.product_references) == 1 and obs.metadata_warnings
    with pytest.raises(DiscoveryError):
        public_http("https://example.invalid/large.h5", {}, 1)


def test_conflicting_collection_rejected(fixture_data, collection):
    item = deepcopy(fixture_data["granule"])
    item["umm"]["CollectionReference"]["Version"] = "2"
    with pytest.raises(MetadataError):
        parse_granule(item, collection)
