"""Read-only real public NASA CMR response recordings, retrieved in SZ-009."""
import json
from pathlib import Path

import pytest

from sentinel_zero.application.global_explorer import GlobalQuery,discover
from sentinel_zero.discovery.cmr import CMRClient,HttpResponse

EVIDENCE = Path(__file__).resolve().parents[2]/"docs/evidence/sz009"


@pytest.mark.regression
@pytest.mark.parametrize("name",["sri_lanka","brazil_amazon","india_brahmaputra"])
def test_actual_recorded_global_metadata_can_be_replayed_without_network(name):
    record = json.loads((EVIDENCE/(name+"_catalog.json")).read_text())
    pages = iter(record["public_cmr_response_pages"])
    def transport(url,headers,timeout):
        page = next(pages)
        assert url == page["url"]
        assert headers.get("CMR-Search-After") == page["request_search_after"]
        return HttpResponse(page["status"],page["headers"],json.dumps(page["body"]).encode())
    expected = record["result"]
    result = discover(GlobalQuery(**expected["query"]),CMRClient(transport,sleep=lambda _:None))
    assert result["status"] == expected["status"]
    assert result["retrieved_unique"] == expected["retrieved_unique"]
    assert result["observations"] == expected["observations"]
    assert result["pair_assessments"] == expected["pair_assessments"]
    assert result["pagination_complete"] and not result["scientific_acceptance"]
    with pytest.raises(StopIteration):next(pages)


def test_real_coverage_preview_uses_recorded_coordinates():
    from sentinel_zero.presentation.global_view import footprint_figure
    r=json.loads((EVIDENCE/"brazil_amazon_catalog.json").read_text())["result"]
    figure=footprint_figure(r)
    ring=r["observations"][0]["footprint"][0]
    assert list(figure.data[0].x) == [p[0] for p in ring]
    assert list(figure.data[0].y) == [p[1] for p in ring]
    assert figure.data[-1].line.dash == "dash"
