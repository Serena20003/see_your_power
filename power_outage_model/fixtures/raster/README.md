# Raster test inputs

`icon-temperature-metadata.json` and `icon-wind-metadata.json` are small metadata
snapshots decoded from DWD ICON Global run 2026-11-16 00 UTC, lead 001. They record
actual packing/grid/parameter metadata; they do not imply an eligible 72-hour run
or historical availability. Attribution: Deutscher Wetterdienst, ICON Global.

Tests generate tiny regular-latitude/longitude GRIB2 messages using ecCodes
samples. The explicit `make-fixture` command generates a complete 79-step source
catalog with constant known weather values and a two-cell target grid. These
synthetic values and generated static arrays are CC0 test fixtures and have no
meteorological interpretation. Fixture mode never supplies a live fallback.

The native CDO test uses real GRIB encoding and checks numerical remapping.
The live default-domain benchmark record is in `../../validation/`; its large
source GRIBs, full ICON geometry and cached weights are excluded from Git.
