"""Synthetic QA payloads and injected HTTP only; never evidence of NASA access."""

from hashlib import md5
from http.client import IncompleteRead
from io import BytesIO

import pytest

from sentinel_zero.ingestion.acquisition import AcquisitionError, SourceAsset, download_qa_sidecar

PAYLOAD = b"SYNTHETIC SOFTWARE TEST ONLY\ncheck,result\nexample,PASS\n"


class Response(BytesIO):
    def __init__(self, body=PAYLOAD, *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body)), "Content-Type": "text/csv"}
        self.headers.update(headers or {})


@pytest.fixture
def request_parameters(tmp_path):
    producer = "SYNTHETIC_NOT_A_REAL_ACQUISITION"
    prefix = f"https://nisar.asf.earthdatacloud.nasa.gov/NISAR/SYNTHETIC_GCOV/{producer}/"
    asset = SourceAsset("C1-TEST", "G1-TEST", producer, "SYNTHETIC_GCOV", prefix + producer + ".h5", 6_000_000_000)
    filename = producer + "_QA_SUMMARY.csv"
    return dict(asset=asset, filename=filename, url=prefix+filename, expected_size=len(PAYLOAD), expected_md5=md5(PAYLOAD, usedforsecurity=False).hexdigest(), root=tmp_path)


def test_success_preserves_identity_and_hash(request_parameters):
    result = download_qa_sidecar(**request_parameters, transport=lambda url: Response())
    assert result["bytes"] == len(PAYLOAD) and result["source_granule_id"] == "G1-TEST"
    assert result["provider_md5_match"] and not result["scientific_acceptance"]
    assert (request_parameters["root"] / request_parameters["filename"]).read_bytes() == PAYLOAD
    assert not list(request_parameters["root"].glob("*.partial"))


def test_overwrite_rejected_before_network(request_parameters):
    target = request_parameters["root"] / request_parameters["filename"]
    target.write_bytes(b"PRESERVE SYNTHETIC USER CONTENT")
    def forbidden(url):
        pytest.fail("Must reject before network")
    with pytest.raises(AcquisitionError):
        download_qa_sidecar(**request_parameters, transport=forbidden)
    assert target.read_bytes() == b"PRESERVE SYNTHETIC USER CONTENT"


@pytest.mark.parametrize("change", [dict(expected_size=None), dict(expected_size=250_000_001), dict(filename="../escape.csv"), dict(expected_md5="bad")])
def test_unsafe_transfer_rejected_before_network(change, request_parameters):
    request_parameters.update(change)
    with pytest.raises(AcquisitionError):
        download_qa_sidecar(**request_parameters, transport=lambda url: pytest.fail("No request permitted"))


@pytest.mark.parametrize("headers", [{"Content-Length": "999"}, {"Content-Length": "bad"}, {"Content-Encoding": "gzip"}, {"Content-Type": "text/html"}])
def test_invalid_headers_body_not_read(headers, request_parameters):
    class Unreadable(Response):
        def read(self, size):
            pytest.fail("Invalid headers must be rejected before reading")
    with pytest.raises(AcquisitionError) as error:
        download_qa_sidecar(**request_parameters, transport=lambda url: Unreadable(headers=headers))
    assert error.value.bytes_received == 0


@pytest.mark.parametrize("location,outcome", [("https://urs.earthdata.nasa.gov/oauth?state=SYNTHETIC", "AUTHENTICATION_REQUIRED"), ("https://example.invalid/private?token=SYNTHETIC", "REDIRECT")])
def test_redirect_cannot_leak_secrets_or_bypass_auth(location, outcome, request_parameters):
    calls=[]
    def transport(url):
        calls.append(url)
        return Response(b"", status=302, headers={"Location": location})
    with pytest.raises(AcquisitionError) as error:
        download_qa_sidecar(**request_parameters, transport=transport)
    assert error.value.code == outcome and len(calls) == 1
    assert "SYNTHETIC" not in str(error.value) and error.value.bytes_received == 0


def test_normal_cdn_redirect_is_followed_without_recording_query(request_parameters):
    calls=[]
    def transport(url):
        calls.append(url)
        return Response(b"", status=303, headers={"Location": "https://d1mv8zhcvry6x4.cloudfront.net/qa?signature=SYNTHETIC_PRIVATE"}) if len(calls)==1 else Response()
    result = download_qa_sidecar(**request_parameters, transport=transport)
    assert len(calls)==2 and "SYNTHETIC_PRIVATE" not in str(result)


def test_partial_bytes_count_and_file_not_promoted(request_parameters):
    with pytest.raises(AcquisitionError) as error:
        download_qa_sidecar(**request_parameters, transport=lambda url: Response(PAYLOAD[:5], headers={"Content-Length": str(len(PAYLOAD))}))
    assert error.value.code == "PARTIAL_TRANSFER" and error.value.bytes_received == 5
    assert error.value.partial_filename and (request_parameters["root"] / error.value.partial_filename).read_bytes() == PAYLOAD[:5]
    assert not (request_parameters["root"] / request_parameters["filename"]).exists()


def test_incomplete_read_bytes_counted(request_parameters):
    class Interrupted(Response):
        def read(self, size):
            raise IncompleteRead(PAYLOAD[:3], len(PAYLOAD)-3)
    with pytest.raises(AcquisitionError) as error:
        download_qa_sidecar(**request_parameters, transport=lambda url: Interrupted())
    assert error.value.code == "PARTIAL_TRANSFER" and error.value.bytes_received == 3


def test_checksum_corruption_not_promoted(request_parameters):
    request_parameters["expected_md5"] = "0" * 32
    with pytest.raises(AcquisitionError) as error:
        download_qa_sidecar(**request_parameters, transport=lambda url: Response())
    assert error.value.code == "CHECKSUM" and error.value.bytes_received == len(PAYLOAD)
    assert not (request_parameters["root"] / request_parameters["filename"]).exists()
