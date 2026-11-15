"""SYNTHETIC end-to-end raster fixtures only; never NASA quality evidence."""
import hashlib
import json

import numpy as np
import pytest
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import Affine

from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.ingestion.quality import diagnose, main, write_previews


@pytest.fixture
def fixture_bundle(tmp_path):
    source=dict(collection_id="C1-TEST",granule_id="G1-TEST",
        producer_granule_id="SYNTHETIC_SOFTWARE_ONLY_20260101T000000_20260101T000100_TEST",
        acquisition_start_utc="2026-01-01T00:00:00Z",acquisition_end_utc="2026-01-01T00:01:00Z",
        maturity=None,product_version=None,processing_crid=None)
    artifacts=[]
    for layer in ("HHHH","mask","numberOfLooks"):
        name=source["producer_granule_id"]+f"_science_LSAR_GCOV_grids_frequencyA_{layer}_subsetted.tif"
        values={"HHHH":np.array([[1,np.nan],[2,-1]],dtype="float32"),
                "mask":np.array([[1,255],[1,0]],dtype="uint8"),
                "numberOfLooks":np.array([[1,np.nan],[2,1]],dtype="float32")}[layer]
        with rasterio.open(tmp_path/name,"w",driver="GTiff",width=2,height=2,count=1,dtype=values.dtype,
                           crs="EPSG:32644",transform=Affine(10,0,400000,0,-10,770000),
                           nodata=255 if layer=="mask" else np.nan) as dst:
            dst.write(values,1)
            dst.update_tags(1,units="1",long_name="SYNTHETIC SOFTWARE ONLY "+layer)
        data=(tmp_path/name).read_bytes()
        artifacts.append(dict(layer=layer,filename=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    return tmp_path,dict(schema_version="1.0",source=source,artifacts=artifacts,
                         conservative_prior_attempt_bytes=0,harmony_job_id="SYNTHETIC_ONLY")


def test_diagnostics_render_actual_fixture_values_without_pair_acceptance(fixture_bundle):
    root,inventory=fixture_bundle
    originals={a["filename"]:a["sha256"] for a in inventory["artifacts"]}
    report,arrays=diagnose(root,inventory)
    assert report["support"]["eligible_pixels"]==2
    assert report["linear_power_quantiles"]["median"]==1.5
    assert sum(report["histogram_db"]["counts"])==2
    assert report["quality_decision"]=="UNREVIEWED" and not report["pair_acceptance"]
    paths=write_previews(root,"SYNTHETIC_PREVIEW",report,arrays)
    assert len(paths)==4 and "SYNTHETIC" in (root/"SYNTHETIC_PREVIEW.html").read_text(encoding="utf-8")
    assert (root/"SYNTHETIC_PREVIEW_power.png").read_bytes().startswith(b"\x89PNG")
    with pytest.warns(NotGeoreferencedWarning), rasterio.open(root/"SYNTHETIC_PREVIEW_power.png") as src:
        assert src.crs is None and src.count==3  # Explicit display-only RGB.
    before={p.name:p.read_bytes() for p in root.glob("SYNTHETIC_PREVIEW*")}
    with pytest.raises(AcquisitionError): write_previews(root,"SYNTHETIC_PREVIEW",report,arrays)
    assert before=={p.name:p.read_bytes() for p in root.glob("SYNTHETIC_PREVIEW*")}
    assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==sha for name,sha in originals.items())


def test_unsafe_prefix_and_unknown_units_fail_visibly(fixture_bundle):
    root,inventory=fixture_bundle
    report,arrays=diagnose(root,inventory)
    with pytest.raises(AcquisitionError): write_previews(root,"../escape",report,arrays)
    a=inventory["artifacts"][0]
    with rasterio.open(root/a["filename"],"r+") as dst:
        dst.update_tags(1,units="dB")
    data=(root/a["filename"]).read_bytes()
    a.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(AcquisitionError,match="units not confirmed"):
        diagnose(root,inventory)


def test_cli_no_overwrite_before_loading_or_rendering(fixture_bundle,monkeypatch):
    root,inventory=fixture_bundle
    manifest=root/"SYNTHETIC.json";manifest.write_text(json.dumps(inventory))
    output=root/"result.json";output.write_text("PRESERVE SYNTHETIC USER CONTENT")
    monkeypatch.setattr("sys.argv",["quality","--inventory",str(manifest),"--runtime-root",str(root),
        "--output",str(output),"--preview-prefix","SYNTHETIC_NEW"])
    assert main()==2 and output.read_text()=="PRESERVE SYNTHETIC USER CONTENT"
    assert not list(root.glob("SYNTHETIC_NEW*"))
