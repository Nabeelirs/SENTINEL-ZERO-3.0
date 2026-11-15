"""Descriptive diagnostics and actual-pixel HTML; never modify analytical arrays."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .preparation import PreparedPair


def _distribution(x: np.ndarray) -> dict[str, Any]:
    x = x.astype("float64")
    q = (.01, .05, .25, .5, .75, .95, .99)
    return dict(minimum=float(x.min()), maximum=float(x.max()), mean=float(x.mean()),
                quantiles={str(p): float(v) for p, v in zip(q, np.quantile(x, q))})


def _profiles(values: np.ndarray, valid: np.ndarray) -> dict[str, Any]:
    def along(axis: int) -> list[float | None]:
        result = []
        for v, ok in zip(np.moveaxis(values, axis, 0), np.moveaxis(valid, axis, 0)):
            result.append(float(np.median(v[ok])) if ok.any() else None)
        return result
    rows, cols = along(0), along(1)
    def step(x: list[float | None]) -> float | None:
        differences = [abs(a-b) for a, b in zip(x[:-1], x[1:]) if a is not None and b is not None]
        return max(differences) if differences else None
    return dict(row_median_db=rows, column_median_db=cols,
                maximum_adjacent_row_median_step_db=step(rows), maximum_adjacent_column_median_step_db=step(cols))


def diagnostics(pair: PreparedPair, delta: np.ndarray) -> dict[str, Any]:
    valid = pair.common_valid
    before = np.full(valid.shape, np.nan)
    after = np.full(valid.shape, np.nan)
    before[valid] = 10*np.log10(pair.before_power[valid].astype("float64"))
    after[valid] = 10*np.log10(pair.after_power[valid].astype("float64"))
    x = delta[valid].astype("float64")
    hist, edges = np.histogram(x, bins=80)
    neighbours, boundary = [], np.zeros(valid.shape, bool)
    for axis in (0, 1):
        s1, s2 = ([slice(None), slice(None)] for _ in range(2))
        s1[axis], s2[axis] = slice(None, -1), slice(1, None)
        a, b = tuple(s1), tuple(s2)
        ok = valid[a] & valid[b]
        neighbours.append(np.abs(delta[a][ok].astype("float64") - delta[b][ok].astype("float64")))
        boundary[a] |= valid[a] & ~valid[b]
        boundary[b] |= valid[b] & ~valid[a]
    jumps = np.concatenate(neighbours)
    return dict(
        distributions=dict(before_linear=_distribution(pair.before_power[valid]), after_linear=_distribution(pair.after_power[valid]),
                           before_db=_distribution(before[valid]), after_db=_distribution(after[valid]), change_db=_distribution(x)),
        spatial_profiles=dict(before=_profiles(before, valid), after=_profiles(after, valid), change=_profiles(delta, valid)),
        identical_input_power_cells=int((pair.before_power[valid] == pair.after_power[valid]).sum()),
        exactly_zero_saved_change_cells=int((x == 0).sum()),
        histogram=dict(counts=hist.tolist(), edges_db=edges.tolist(), includes_all_eligible=True),
        descriptive_extremes=dict(absolute_at_least_6_db=int((np.abs(x) >= 6).sum()),
                                  absolute_at_least_12_db=int((np.abs(x) >= 12).sum()),
                                  meaning="Magnitude summaries only; no artifact or environmental classification"),
        spatial_discontinuities=dict(adjacent_valid_pairs=int(jumps.size),
                                    maximum_absolute_neighbour_step_db=float(jumps.max()) if jumps.size else None,
                                    p99_absolute_neighbour_step_db=float(np.quantile(jumps, .99)) if jumps.size else None),
        missing_boundary=dict(eligible_cells_adjacent_to_excluded=int(boundary.sum()),
                              first_last_rows_eligible=[int(valid[0].sum()), int(valid[-1].sum())],
                              first_last_columns_eligible=[int(valid[:, 0].sum()), int(valid[:, -1].sum())]),
        display=dict(single_date_db_limits=[-25, 15], change_db_limits=[-6, 6],
                     before_clipped_cells=int(((before[valid] < -25) | (before[valid] > 15)).sum()),
                     after_clipped_cells=int(((after[valid] < -25) | (after[valid] > 15)).sum()),
                     change_clipped_cells=int((np.abs(x) > 6).sum()), excluded_colour="#b7b7b7"),
        review_policy="Profiles/jumps are descriptive, not defect tests. Inspect preview; no automatic quality PASS.",
        unresolved="Joint-mode 40 MHz radiometric banding, RFI and physical co-registration remain unverified")


def write_preview(path: Path, pair: PreparedPair, delta: np.ndarray, qa: dict[str, Any]) -> None:
    """Self-contained Plotly, every native pixel, fixed scales and explicit clipping."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    valid, grid = pair.common_valid, pair.grid
    sources = [i["source"] for i in pair.provenance["source_inventories"]]
    dates = [s["acquisition_start_utc"][:10] for s in sources]
    before, after = (np.full(valid.shape, np.nan) for _ in range(2))
    before[valid] = 10*np.log10(pair.before_power[valid].astype("float64"))
    after[valid] = 10*np.log10(pair.after_power[valid].astype("float64"))
    a, _, c, _, e, f = grid["transform"]
    x = c + (np.arange(grid["width"])+.5)*a
    y = f + (np.arange(grid["height"])+.5)*e
    figure = make_subplots(rows=2, cols=3, row_heights=[.72, .28], vertical_spacing=.20,
                          subplot_titles=[f"A — {dates[0]} backscatter (dB)", f"B — {dates[1]} backscatter (dB)",
                                          "C — HHHH backscatter change (dB)", "Change distribution", "Row medians (dB)", "Column medians (dB)"])
    for col, values, scale, lo, hi in ((1, before, "Cividis", -25, 15), (2, after, "Cividis", -25, 15),
                                      (3, delta, [[0, "#762a83"], [.5, "#f7f7f7"], [1, "#1b7837"]], -6, 6)):
        figure.add_trace(go.Heatmap(x=x, y=y, z=values, zmin=lo, zmax=hi, colorscale=scale,
                                    showscale=True, colorbar=dict(title="dB", len=.49, y=.76, x=(col/3)-.04, thickness=10),
                                    hovertemplate="E=%{x:.0f} m<br>N=%{y:.0f} m<br>%{z:.4f} dB<extra></extra>", hoverongaps=False), row=1, col=col)
        figure.update_xaxes(title_text="Easting (m)", range=[grid["bounds_native"][0], grid["bounds_native"][2]], row=1, col=col)
        figure.update_yaxes(title_text="Northing (m)", range=[grid["bounds_native"][1], grid["bounds_native"][3]],
                            scaleanchor="x" + (str(col) if col > 1 else ""), scaleratio=1, row=1, col=col)
    hist = qa["histogram"]
    edges = np.array(hist["edges_db"])
    figure.add_trace(go.Bar(x=(edges[:-1]+edges[1:])/2, y=hist["counts"], marker_color="#454545", showlegend=False), row=2, col=1)
    figure.update_xaxes(title_text="Change (dB); full range", row=2, col=1)
    figure.update_yaxes(title_text="Eligible cells", row=2, col=1)
    for label, colour in (("before", "#886b00"), ("after", "#286b8e"), ("change", "#6e2675")):
        for col, axis in ((2, "row"), (3, "column")):
            figure.add_trace(go.Scatter(y=qa["spatial_profiles"][label][axis+"_median_db"], mode="lines",
                                        name=label, legendgroup=label, showlegend=col == 2, line=dict(color=colour)), row=2, col=col)
            figure.update_xaxes(title_text=axis+" index (native order)", row=2, col=col)
    display = qa["display"]
    figure.update_layout(height=970, width=1450, template="plotly_white", plot_bgcolor="#b7b7b7",
                         title=dict(text="NISAR HHHH Backscatter Change (dB) — PROVISIONAL exploratory pixels<br>"
                                    + sources[0]["granule_id"] + " → " + sources[1]["granule_id"] + " | "
                                    + str(grid["epsg"]) + " | native north-up grid", font=dict(size=20)),
                         margin=dict(t=135, b=100, l=70, r=55), legend=dict(orientation="h", y=-.10))
    note = ("Positive: greater after-date radar power; negative: lower. No flood/land-cover classification. Grey = excluded.<br>"
            f"Display clipping only: before {display['before_clipped_cells']}, after {display['after_clipped_cells']} cells outside [-25,15] dB; "
            f"change {display['change_clipped_cells']} outside [-6,6] dB. Full scientific TIFF/histogram untrimmed.<br>"
            "PROVISIONAL: banding/RFI, subset fidelity and physical co-registration unresolved; 10 m posting is not verified resolution.")
    figure.add_annotation(text=note, xref="paper", yref="paper", x=0, y=-.20, showarrow=False, align="left", font=dict(size=12))
    html = figure.to_html(full_html=True, include_plotlyjs=True, div_id="nisar-sz006-comparison", config={"responsive": True})
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(html)
