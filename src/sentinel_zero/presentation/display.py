"""Native-pixel display derivatives only; no analytical output writes."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np
from PIL import Image

from ..application.data import ExplorerData, power_db

POWER_LIMITS = (-25.0, 15.0)
CHANGE_LIMITS = (-6.0, 6.0)
EXCLUDED_RGB = (183, 183, 183)
POWER_COLOURS = ("#00224e", "#434e6c", "#7d7c78", "#bcae6c", "#fee838")
CHANGE_COLOURS = ("#733b95", "#f7f7f7", "#007f70")


@dataclass(frozen=True)
class Panel:
    title: str
    granule: str
    png: bytes
    limits: tuple[float, float]
    colours: tuple[str, ...]
    clipped_cells: int
    width: int
    height: int


def rgb(values: np.ndarray, valid: np.ndarray, limits: tuple[float, float], colours: tuple[str, ...]) -> np.ndarray:
    """Preserve array row/column order. Saturation affects visual colours only."""
    lo, hi = limits
    if values.shape != valid.shape or valid.dtype != np.bool_ or not lo < hi or not np.isfinite(values[valid]).all():
        raise ValueError("Invalid display support or limits")
    anchors = np.array([[int(c[i:i+2], 16) for i in (1, 3, 5)] for c in colours])
    positions = np.linspace(0, 1, len(anchors))
    normalized = np.zeros(values.shape)
    normalized[valid] = np.clip((values[valid]-lo)/(hi-lo), 0, 1)
    result = np.stack([np.rint(np.interp(normalized, positions, anchors[:, i])) for i in range(3)], axis=-1).astype("uint8")
    result[~valid] = EXCLUDED_RGB
    result.flags.writeable = False
    return result


def comparison(data: ExplorerData) -> tuple[Panel, ...]:
    pair, product = data.product.prepared, data.product
    valid = pair.common_valid
    sources = product.provenance["sources"]
    values = (power_db(pair.before_power, valid), power_db(pair.after_power, valid), product.change_db)
    titles = ("A · "+sources[0]["acquisition_start_utc"][:10]+" · Backscatter",
              "B · "+sources[1]["acquisition_start_utc"][:10]+" · Backscatter", "C · Backscatter change (dB)")
    panels = []
    for i, array in enumerate(values):
        limits, colours = (POWER_LIMITS, POWER_COLOURS) if i < 2 else (CHANGE_LIMITS, CHANGE_COLOURS)
        buffer = BytesIO()
        Image.fromarray(rgb(array, valid, limits, colours)).save(buffer, format="PNG", optimize=False)
        clipped = int(((array[valid] < limits[0]) | (array[valid] > limits[1])).sum())
        panels.append(Panel(titles[i], sources[i]["granule_id"] if i < 2 else "October 18 minus October 6",
                            buffer.getvalue(), limits, colours, clipped, array.shape[1], array.shape[0]))
    return tuple(panels)


def distributions(data: ExplorerData):
    """Optional Plotly display; all bins include the actual common-support values."""
    import plotly.graph_objects as go
    pair = data.product.prepared
    before, after = (power_db(a, pair.common_valid)[pair.common_valid] for a in (pair.before_power, pair.after_power))
    edges = np.linspace(min(before.min(), after.min()), max(before.max(), after.max()), 81)
    power = go.Figure()
    for label, values, colour in (("October 6", before, "#886b00"), ("October 18", after, "#23648b")):
        counts, _ = np.histogram(values, bins=edges)
        power.add_trace(go.Scatter(x=(edges[:-1]+edges[1:])/2, y=counts, mode="lines", name=label, line=dict(color=colour)))
    power.update_layout(title="Backscatter distributions", xaxis_title="Gamma-zero power (dB); full range",
                        yaxis_title="Common-eligible cells", height=330, margin=dict(l=35, r=15, t=50, b=40))
    h = data.product.diagnostics["histogram"]
    e = np.asarray(h["edges_db"])
    change = go.Figure(go.Bar(x=(e[:-1]+e[1:])/2, y=h["counts"], marker_color="#487b77"))
    change.update_layout(title="Saved change distribution", xaxis_title="Backscatter change (dB); full range",
                         yaxis_title="Common-eligible cells", height=330, margin=dict(l=35, r=15, t=50, b=40))
    return power, change
