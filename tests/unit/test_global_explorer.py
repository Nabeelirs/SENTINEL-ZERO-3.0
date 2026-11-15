"""Synthetic geographic/service fixtures; no invented scientific observations."""
from datetime import datetime,timezone

import pytest

from sentinel_zero.application.global_explorer import COLLECTION,GlobalQuery,centred_query,discover
from sentinel_zero.contracts import CollectionDescriptor,DiscoveryResult
from sentinel_zero.errors import MetadataError

START,END = "2026-10-01T00:00:00Z","2026-11-01T00:00:00Z"


@pytest.mark.parametrize("lat,lon",[(0,0),(6.925,79.975),(-3.12,-60.02),(26.15,91.75),(90,0),(-90,0),(0,180),(0,-180)])
def test_worldwide_centres_and_longitude_latitude_order(lat,lon):
    boxes = centred_query(lat,lon,.1,START,END).validate()
    assert all(-90 <= b.south < b.north <= 90 and -180 <= b.west < b.east <= 180 for b in boxes)
    if abs(lon) == 180: assert len(boxes) == 2
    if lon == 79.975: assert boxes[0].west == pytest.approx(79.925)


@pytest.mark.parametrize("coords",[(float("nan"),0,.1),(91,0,.1),(0,181,.1),(0,0,0),(0,0,6)])
def test_bad_centres_rejected(coords):
    with pytest.raises(MetadataError): centred_query(*coords,START,END)


@pytest.mark.parametrize("values",[(0,0,10,1,START,END),(0,1,1,0,START,END),(0,0,0,1,START,END),
                                  (0,0,1,1,END,START),(0,0,1,1,START,"2028-10-01T00:00:00Z")])
def test_bad_bounds_and_dates_rejected(values):
    with pytest.raises(MetadataError): GlobalQuery(*values).validate()


class EmptyCMR:
    def __init__(self):self.calls=[]
    def search_collections(self,q):
        self.calls.append(q)
        now = datetime.now(timezone.utc)
        c = CollectionDescriptor(COLLECTION,"NISAR_L2_GCOV_PROVISIONAL_V1","Synthetic collection test", "1","L2","PROVISIONAL")
        return DiscoveryResult(q,now,now,1,1,True,collections=(c,))
    def search_granules(self,q,c):
        self.calls.append(q)
        now = datetime.now(timezone.utc)
        return DiscoveryResult(q,now,now,0,0,True)


def test_empty_is_success_not_service_failure_and_query_is_bounded():
    client = EmptyCMR()
    result = discover(centred_query(0,180,.1,START,END),client)
    assert result["status"] == "NO_MATCHES" and result["retrieved_unique"] == 0
    assert result["pagination_complete"] and not result["scientific_acceptance"]
    assert len(client.calls) == 3
    assert all(q.collection_concept_id == COLLECTION for q in client.calls)
    assert client.calls[-1].max_results == 50 and client.calls[-1].max_pages == 5


def test_collection_failure_not_zero_availability():
    client = EmptyCMR()
    def fail(q):
        now = datetime.now(timezone.utc)
        return DiscoveryResult(q,now,now,None,0,False)
    client.search_collections=fail
    result=discover(centred_query(0,0,.1,START,END),client)
    assert result["status"] == "SERVICE_ERROR" and "zero" in result["message"]


def test_fractional_day_above_search_limit_rejected():
    with pytest.raises(MetadataError):GlobalQuery(0,0,1,1,START,"2027-10-02T00:00:01Z").validate()
