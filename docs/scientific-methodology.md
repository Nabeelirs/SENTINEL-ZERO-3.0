# Scientific methodology

NISAR GCOV HHHH is terrain-corrected gamma-zero backscatter **power**. Preserve
linear source values. The two PROVISIONAL Frequency A observations are
`G4348875889-ASF` (6 October 2026) and `G4378876686-ASF` (18 October): recorded
ascending, left-looking, track 84/frame 5, 40 MHz, CRID P05023, version 1.0.11.

All six GeoTIFFs and preparation/analysis manifests are checked against recorded
sizes/SHA-256. Native EPSG:32644, affine `[10,0,384000,0,-10,768350]`, 553 × 553.
No reprojection or resampling is needed. Matching declared grids do not establish
independent empirical physical co-registration or absolute positioning accuracy.

Per-date eligibility requires finite positive power, documented focused/subswath
support, finite positive looks and native validity. Mask 255 is excluded. This
mask is not universal radiometric/RFI QA. Common support is the pixel-by-pixel
intersection: **305,137 eligible, 672 excluded**. Overlapping exclusion reasons
and a deterministic primary category remain recorded.

`Delta_dB = 10 * (log10(P_after) - log10(P_before))`

SZ-006 uses float64 intermediates and saves float32 with NaN nodata on the native
grid. All eligible values are finite; excluded cells remain excluded. No epsilon,
filter, smoothing, normalization or scientific-value clipping conceals inputs.

| Eligible-pixel statistic | dB |
|---|---:|
| Minimum / maximum | −26.151365 / +23.936480 |
| Mean / median | +0.338477 / +0.342195 |
| Population standard deviation | 2.711150 |

Positive/negative/exactly zero counts: 168,905 / 136,231 / 1. These are radar
responses, not classified events. Descriptive magnitude thresholds are not
validated flood/damage thresholds. Projected cell area is not flood extent.

Both date displays share −25 to +15 dB; change uses zero-centred ±6 dB. Legends
disclose saturation; saved values remain intact. Row zero is north; coordinates
use pixel centres. Cell 157/309: −26.151365280151367 dB, E387095/N766775 m.
Canvas pan/zoom/comparison changes display only. See [limitations](limitations.md).
