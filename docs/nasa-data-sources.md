# NASA sources and acknowledgments

Actual data: **NASA–ISRO NISAR L2 PROVISIONAL GCOV**, distributed by NASA ASF DAAC,
subset using NASA Earthdata Harmony. Collection `C2854338529-ASF`, short name
`NISAR_L2_GCOV_PROVISIONAL_V1`, version 1. DOI:
[10.5067/NIL2GCOV-P1](https://doi.org/10.5067/NIL2GCOV-P1).
Collection metadata names ASF and NASA/JPL/NISAR data centers.

- Before: `G4348875889-ASF`, 2026-10-06T00:14:46Z–00:15:21.999342Z.
- After: `G4378876686-ASF`, 2026-10-18T00:14:46Z–00:15:21.999342Z.
- Each: `/science/LSAR/GCOV/grids/frequencyA/HHHH`, `mask`, `numberOfLooks`.
- Requested AOI: 79.95–80.00° E, 6.90–6.95° N; GeoTIFF spatial subset,
  native projection. Six subset layers, not original full HDF5 products.

Primary references:

- [ASF NISAR Data User Guide](https://nisar-docs.asf.alaska.edu/)
- [GCOV](https://nisar-docs.asf.alaska.edu/gcov/)
- [PROVISIONAL known issues](https://nisar-docs.asf.alaska.edu/provisional-known-issues/)
- [CMR API](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html)
- [Harmony](https://nisar-docs.asf.alaska.edu/harmony/)
- [Collection metadata](https://cmr.earthdata.nasa.gov/search/concepts/C2854338529-ASF.umm_json)

The release audit retrieved the DOI/data centers; UseConstraints and
AccessConstraints were absent/null. Absence does not independently settle data
redistribution rights. The scientific archive remains excluded from the source
publication overlay pending owner clearance. Preserve mission/provider/granule/
processing attribution in data and derived imagery. No endorsement is implied.

Sri Lanka, Brazil and Brahmaputra CMR records are real catalog metadata. Only
Sri Lankan pixels completed this scientific pipeline. Original usable Meteomatics
rainfall is not supplied; an earlier NASA precipitation request returned missing
values. No environmental series or new regional analysis is invented. GPM,
Planetary Computer and additional processing retain their planned/blocked states.

The source-only release retains a bounded set of public CMR/Harmony JSON response
snapshots only as deterministic software-test fixtures. They are provider
metadata, not observation measurements or scientific raster assets, and are not
covered by the project's MIT license. Private inspection inventories, pair
compatibility evidence, screenshots and observation data are excluded.
