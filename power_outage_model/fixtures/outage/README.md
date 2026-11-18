# Test contract only

`raster-batch-v1-test-manifest.json` pins a representative complete 72-hour
upstream **fixture** manifest. Its object keys describe the existing local test
store; they are not claimed as a portable live dependency archive. Integration
tests regenerate the actual small COG/GRIB dependencies in temporary directories,
copy them into the downstream archive, remove upstream originals and replay.

There is no complete real 72-hour forecast/static batch available locally to pin
as live evidence. The single-interval native DWD check in validation is not a
substitute. Generated examples are exclusively test fixtures, never training
history or deployment artifacts.
