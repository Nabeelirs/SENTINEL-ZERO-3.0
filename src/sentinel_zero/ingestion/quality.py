"""Single-acquisition GCOV diagnostics; no temporal change or defect removal."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.windows import Window
import warnings

from .acquisition import AcquisitionError, new_destination, verify_local_transfer
from .gcov import inspect_bundle


def focused_support(power: np.ma.MaskedArray, mask: np.ma.MaskedArray,
                    looks: np.ma.MaskedArray, *, allowed_subswaths: tuple[int, ...] = (1,)) -> tuple[np.ndarray, dict[str, Any]]:
    """Explicit restricted subswath policy, not a general binary QA mask.

    0 (partial/invalid contributor) and 255 (outside acquisition) are excluded.
    Other undeclared values remain unknown and cannot silently pass validation.
    Driver nodata is respected even when its stored value is positive.
    """
    if power.shape != mask.shape or power.shape != looks.shape or power.ndim != 2:
        raise AcquisitionError("GRID", "Science layers require identical 2D shapes")
    if not allowed_subswaths or any(type(x) is not int or not 1 <= x < 255 for x in allowed_subswaths):
        raise AcquisitionError("MASK", "Declare positive subswath IDs supported by evidence")
    if mask.dtype.kind not in "ui" or power.dtype.kind not in "fui" or looks.dtype.kind not in "fui":
        raise AcquisitionError("RADIOMETRY", "Expected real power/looks and integer subswath mask")
    p, m, n = np.asarray(power), np.asarray(mask), np.asarray(looks)
    p_ok = ~np.ma.getmaskarray(power) & np.isfinite(p) & (p > 0)
    n_ok = ~np.ma.getmaskarray(looks) & np.isfinite(n) & (n > 0)
    m_ok = ~np.ma.getmaskarray(mask) & np.isin(m, allowed_subswaths)
    unknown = sorted(int(x) for x in np.unique(m) if int(x) not in (*allowed_subswaths, 0, 255))
    valid = p_ok & n_ok & m_ok
    return valid, {"total_pixels": int(p.size), "eligible_pixels": int(valid.sum()),
                   "power_excluded": int((~p_ok).sum()), "looks_excluded": int((~n_ok).sum()),
                   "mask_excluded": int((~m_ok).sum()), "mask_zero": int((m == 0).sum()),
                   "mask_fill_255": int((m == 255).sum()), "unexpected_mask_values": unknown,
                   "allowed_subswaths": list(allowed_subswaths),
                   "policy": "Driver-valid finite positive power and looks; declared subswath IDs only. Exclusion counts overlap.",
                   "quality_assurance": "Focusing/subswath support only; does not establish absence of RFI, banding or geolocation error"}


def load_date(root: Path, inventory: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, np.ma.MaskedArray]]:
    report = inspect_bundle(root, inventory)
    if not report["all_layer_grids_match"]:
        raise AcquisitionError("GRID", "Within-date grids disagree; no silent alignment")
    arrays = {}
    for artifact in inventory["artifacts"]:
        with rasterio.Env(GDAL_CACHEMAX=8*1024*1024), rasterio.open(root/artifact["filename"]) as src:
            data = np.empty(src.shape, dtype=src.dtypes[0])
            invalid = np.empty(src.shape, dtype=bool)
            for row in range(0, src.height, 512):
                for col in range(0, src.width, 512):
                    win = Window(col, row, min(512, src.width-col), min(512, src.height-row))
                    tile = src.read(1, window=win, masked=True)
                    region = np.s_[row:row+int(win.height), col:col+int(win.width)]
                    data[region], invalid[region] = tile.data, np.ma.getmaskarray(tile)
            arrays[artifact["layer"]] = np.ma.array(data, mask=invalid)
        verify_local_transfer(root,artifact["filename"],expected_size=artifact["bytes"],
                              expected_sha256=artifact["sha256"],source_granule_id=inventory["source"]["granule_id"])
    return report, arrays


def profile(values: np.ndarray, valid: np.ndarray, axis: int) -> list[float | None]:
    # Median within each row/column in linear power, then display as dB.
    result = []
    for i in range(values.shape[1-axis]):
        v = values[:, i][valid[:, i]] if axis == 0 else values[i, :][valid[i, :]]
        result.append(float(10*np.log10(np.median(v))) if v.size else None)
    return result


def diagnose(root: Path, inventory: Mapping[str, Any], *, allowed_subswaths: tuple[int, ...] = (1,)) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    inspected, arrays = load_date(root, inventory)
    raster = next(x for x in inspected["rasters"] if x["layer"] == "HHHH")
    if raster["band_tags"].get("units") != "1" or "HHHH" not in raster["band_tags"].get("long_name", ""):
        raise AcquisitionError("RADIOMETRY", "HHHH identity/dimensionless power units not confirmed")
    valid, counts = focused_support(arrays["HHHH"], arrays["mask"], arrays["numberOfLooks"], allowed_subswaths=allowed_subswaths)
    p = np.asarray(arrays["HHHH"], dtype=np.float64)
    db = np.full(p.shape, np.nan)
    db[valid] = 10*np.log10(p[valid])  # Single-date display ONLY.
    selected = p[valid]
    rows, cols = profile(p, valid, 1), profile(p, valid, 0)
    def step(v):
        pairs = [(a,b) for a,b in zip(v,v[1:]) if a is not None and b is not None]
        return max((abs(b-a) for a,b in pairs), default=None)
    histogram, edges = np.histogram(db[valid], bins=80) if selected.size else (np.zeros(80, dtype=int), np.linspace(-25,15,81))
    summary = {"schema_version":"1.0", "task":"SZ-004", "kind":"SINGLE_ACQUISITION_DIAGNOSTICS",
        "created_at_utc":datetime.now(timezone.utc).isoformat(), "source":inspected["source_context"],
        "harmony_job_id":inventory["harmony_job_id"], "support":counts,
        "linear_power_quantiles":dict(zip(("min","p01","p05","median","p95","p99","max"),
            [float(x) for x in np.quantile(selected,[0,.01,.05,.5,.95,.99,1])])) if selected.size else None,
        "row_median_db":rows, "column_median_db":cols,
        "max_adjacent_row_median_step_db":step(rows), "max_adjacent_column_median_step_db":step(cols),
        "histogram_db":{"edges":edges.tolist(),"counts":histogram.tolist()},
        "power_display_db_limits":[-25,15], "looks_display_limits":[0,10],
        "display_caveat":"Display clipping only. No filtering, resampling, temporal differences or automatic defect diagnosis. Profiles can reflect real land cover.",
        "quality_decision":"UNREVIEWED", "pair_acceptance":False,
        "unverified":["Provider QA/ISO and original HDF5 identification", "RFI and joint-mode banding absence", "Original versus service pixel preservation", "Independent geolocation/coregistration"]}
    return summary, {"power_db":db,"mask":np.asarray(arrays["mask"]),"looks":np.where(valid,np.asarray(arrays["numberOfLooks"]),np.nan)}


def write_previews(root: Path, prefix: str, summary: dict[str, Any], arrays: dict[str, np.ndarray]) -> list[str]:
    # Plotting remains optional; pure support/compatibility checks need no Plotly.
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    paths = [new_destination(root, prefix+suffix) for suffix in (".html","_power.png","_mask.png","_looks.png")]
    # Full native-size small subset; no smoothing or spatial downsampling.
    title = summary["source"]["granule_id"] + " — " + summary["source"]["acquisition_start_utc"]
    figure = make_subplots(rows=2,cols=3,subplot_titles=("HHHH — display dB [-25,15]", "Subswath mask (not universal QA)", "Number of looks [0,10]", "Column median of linear power, then dB", "Row median of linear power, then dB", "Eligible power histogram — display dB"))
    figure.add_trace(go.Heatmap(z=arrays["power_db"],zmin=-25,zmax=15,colorscale="Gray",showscale=False),row=1,col=1)
    mask_codes=np.full(arrays["mask"].shape,2,dtype="uint8")
    for original,display in ((0,0),(1,1),(255,3)):
        mask_codes[arrays["mask"]==original]=display
    figure.add_trace(go.Heatmap(z=mask_codes,zmin=0,zmax=3,customdata=arrays["mask"],
        colorscale=[[0,"red"],[.1666,"red"],[.1667,"green"],[.4999,"green"],[.5,"orange"],[.8332,"orange"],[.8333,"grey"],[1,"grey"]],
        hovertemplate="Code %{customdata}<extra></extra>",showscale=True,
        colorbar=dict(title="Subswath",tickvals=[0,1,2,3],ticktext=["0 partial/invalid","1 subswath","other: unknown","255 fill"],len=.35,y=.78)),row=1,col=2)
    figure.add_trace(go.Heatmap(z=arrays["looks"],zmin=0,zmax=10,colorscale="Viridis",showscale=False),row=1,col=3)
    for col in (1,2,3):
        figure.update_yaxes(autorange="reversed",title_text="Native row (southward)",row=1,col=col)
        figure.update_xaxes(title_text="Native column (eastward)",row=1,col=col)
    figure.add_trace(go.Scatter(y=summary["column_median_db"],mode="lines",name="Column medians"),row=2,col=1)
    figure.add_trace(go.Scatter(y=summary["row_median_db"],mode="lines",name="Row medians"),row=2,col=2)
    hist = summary["histogram_db"]
    figure.add_trace(go.Bar(x=[(a+b)/2 for a,b in zip(hist["edges"],hist["edges"][1:])],y=hist["counts"],name="Pixel count"),row=2,col=3)
    figure.update_yaxes(title_text="Single-date dB",row=2,col=1)
    figure.update_yaxes(title_text="Single-date dB",row=2,col=2)
    figure.update_xaxes(title_text="Single-date dB",row=2,col=3)
    figure.update_yaxes(title_text="Pixel count",row=2,col=3)
    label = ("SYNTHETIC SOFTWARE TEST ONLY — not satellite evidence" if
             "synthetic" in summary["source"]["producer_granule_id"].lower() else
             "NISAR-derived source-mapped values; visual diagnostics only")
    figure.update_layout(title=dict(text=title+"<br><sup>"+label+". No filtering or temporal change.</sup>"),height=950,width=1550,showlegend=False,template="plotly_white")
    html = figure.to_html(include_plotlyjs=True,full_html=True)
    with paths[0].open("x",encoding="utf-8") as f:
        f.write(html)
    for name, path, lo, hi in zip(("power_db","mask","looks"),paths[1:],(-25,0,0),(15,255,10)):
        data=arrays[name]
        if name == "mask":
            rgb=np.zeros((*data.shape,3),dtype="uint8")
            rgb[:]=[240,140,0]  # undeclared code
            rgb[data==1]=[30,150,80];rgb[data==0]=[220,30,30];rgb[data==255]=[180,180,180]
        else:
            gray=np.clip(np.nan_to_num((data-lo)/(hi-lo),nan=0),0,1)
            rgb=np.repeat((gray*255).astype("uint8")[...,None],3,axis=2)
            rgb[~np.isfinite(data)]=[190,70,190]
        # Explicit display-only RGB output, not a georeferenced analytical raster.
        # Suppress only that expected writer warning; no source warning hidden.
        with warnings.catch_warnings(), MemoryFile() as memory:
            warnings.simplefilter("ignore",NotGeoreferencedWarning)
            with memory.open(driver="PNG",width=data.shape[1],height=data.shape[0],count=3,dtype="uint8") as dst:
                dst.write(np.moveaxis(rgb,2,0))
            encoded=memory.read()
        with path.open("xb") as target:
            target.write(encoded)
    return [str(p) for p in paths]


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory",type=Path,required=True);p.add_argument("--runtime-root",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--preview-prefix",required=True)
    args=p.parse_args()
    try:
        if args.output.exists() or args.output.suffix.lower()!=".json":
            raise AcquisitionError("EXISTS","Evidence output already exists")
        if args.inventory.stat().st_size>1024*1024:
            raise AcquisitionError("LIMIT","Inventory exceeds the 1 MiB metadata limit")
        inv=json.loads(args.inventory.read_text(encoding="utf-8-sig"))
        result,arrays=diagnose(args.runtime_root,inv)
        result["preview_files"]=write_previews(args.runtime_root,args.preview_prefix,result,arrays)
        with args.output.open("x",encoding="utf-8") as f:
            json.dump(result,f,indent=2,allow_nan=False)
        print("Single-date diagnostics written; quality UNREVIEWED; pair acceptance False")
        return 0
    except (AcquisitionError,ValueError,KeyError,OSError,TypeError) as exc:
        print("Diagnostic failure:",getattr(exc,"code","METADATA_OR_IO"));return 2


if __name__ == "__main__":
    raise SystemExit(main())
