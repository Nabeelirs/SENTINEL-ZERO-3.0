"""Verified display payload and strict selection boundary for the Earth canvas."""
from __future__ import annotations

import base64
from hashlib import sha256
from importlib.resources import files
import math
from typing import Any

from ..application.data import ANALYSIS_SHA256, ExplorerData, pixel
from .display import comparison

MODES = ("BEFORE", "AFTER", "CHANGE", "COMPARE")


def grid_contract(data: ExplorerData) -> dict[str, Any]:
    grid = data.product.prepared.grid
    transform = list(grid["transform"])
    if (len(transform) != 6 or not all(math.isfinite(v) for v in transform)
            or transform[0] <= 0 or transform[4] >= 0 or transform[1] != 0 or transform[3] != 0):
        raise ValueError("Earth canvas requires the verified, unrotated north-up native grid")
    width, height = grid["width"], grid["height"]
    if type(width) is not int or type(height) is not int or not 1 <= width * height <= 2_000_000:
        raise ValueError("Unsupported canvas dimensions")
    bounds = [transform[2], transform[5] + height * transform[4],
              transform[2] + width * transform[0], transform[5]]
    if any(abs(a-b) > 1e-8 for a,b in zip(bounds,grid["bounds_native"])):
        raise ValueError("Declared canvas bounds do not match the native transform")
    return dict(width=width,height=height,transform=transform,bounds=bounds,epsg=grid["epsg"])


def display_payload(data: ExplorerData) -> dict[str, Any]:
    """PNG colours are existing display derivatives, not recomputed science."""
    grid=grid_contract(data)
    panels=comparison(data)
    sources=data.product.provenance["sources"]
    images={}
    for mode,panel in zip(MODES[:3],panels):
        if (panel.width,panel.height)!=(grid["width"],grid["height"]):
            raise ValueError("Display image does not match the verified grid")
        images[mode]=dict(png="data:image/png;base64,"+base64.b64encode(panel.png).decode('ascii'),
            sha256=sha256(panel.png).hexdigest(),limits=list(panel.limits),colours=list(panel.colours),
            saturated=panel.clipped_cells,granule=panel.granule)
    return dict(schema_version="1.0",identity=ANALYSIS_SHA256,grid=grid,images=images,
        before_date=sources[0]["acquisition_start_utc"][:10],after_date=sources[1]["acquisition_start_utc"][:10],
        source_ids=[s["granule_id"] for s in sources],maturity="PROVISIONAL",
        scientific_scope="Exploratory gamma-zero HHHH radar response; physical cause unverified")


def accepted_selection(data: ExplorerData, value: Any) -> dict[str, Any]:
    """Never accept measurement values, paths or scientific identities from JS."""
    if not isinstance(value,dict) or set(value)-{"row","column","identity","mode","sequence"}:
        raise ValueError("Unsupported canvas selection record")
    if value.get("identity")!=ANALYSIS_SHA256 or value.get("mode") not in MODES:
        raise ValueError("Canvas selection identity or observation mode mismatch")
    row,column=value.get("row"),value.get("column")
    if type(row) is not int or type(column) is not int:
        raise ValueError("Canvas selection needs integer native indices")
    return pixel(data,row,column)


_component = None
_component_signature = None


def canvas(data: dict, *, key: str = "earth_canvas", on_selection_change=None):
    """Mount only packaged trusted code; all external labels use textContent."""
    global _component,_component_signature
    resources=files("sentinel_zero.presentation")
    html=resources.joinpath("earth_canvas.html").read_text(encoding="utf-8")
    css=resources.joinpath("earth_canvas.css").read_text(encoding="utf-8")
    js=resources.joinpath("earth_canvas.mjs").read_text(encoding="utf-8")
    signature=sha256((html+css+js).encode('utf-8')).hexdigest()[:12]
    if _component is None or signature!=_component_signature:
        import streamlit.components.v2 as components
        _component=components.component("sentinel-zero.earth-atlas-"+signature,html=html,css=css,js=js,isolate_styles=True)
        _component_signature=signature
    return _component(data=data,key=key,default={"selection":None},on_selection_change=on_selection_change or (lambda:None),height="content")
