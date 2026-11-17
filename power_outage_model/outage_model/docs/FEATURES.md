# Feature registry v1

Registry SHA256: `059a29d90f0ad623f39d8e958ef6cb45d0f7d45f7ef39690d3134c6ccc7c11de`

Predictors are ordered exactly as below. Geographic identifiers remain metadata.

## wind_max_ms

- family: wind
- recipe: max(wind_speed_ms[24 endpoint samples])
- unit: m/s
- valid_range: [0, 150]
- motivation: Storm intensity; not gusts
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## wind_mean_ms

- family: wind
- recipe: mean(wind_speed_ms[24 endpoint samples])
- unit: m/s
- valid_range: [0, 150]
- motivation: Storm persistence; not an hourly continuous mean
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## rain_total_mm

- family: rain
- recipe: sum(precipitation_mm[24 hourly interval totals])
- unit: mm
- valid_range: [0, 3000]
- motivation: Window accumulation
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## rain_max_1h_mm

- family: rain
- recipe: max(precipitation_mm[24 hourly interval totals])
- unit: mm
- valid_range: [0, 1000]
- motivation: Hourly precipitation intensity
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## temp_min_c

- family: temperature
- recipe: min(temperature_c[24 endpoint samples])
- unit: degC
- valid_range: [-100, 70]
- motivation: Cold exposure
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## temp_max_c

- family: temperature
- recipe: max(temperature_c[24 endpoint samples])
- unit: degC
- valid_range: [-100, 70]
- motivation: Heat exposure
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## temp_mean_c

- family: temperature
- recipe: mean(temperature_c[24 endpoint samples])
- unit: degC
- valid_range: [-100, 70]
- motivation: Thermal conditions
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## temp_range_c

- family: temperature
- recipe: temp_max_c - temp_min_c
- unit: degC
- valid_range: [0, 170]
- motivation: Temperature variability
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## canopy_fraction

- family: canopy
- recipe: versioned area-weighted tree canopy on valid land
- unit: 1
- valid_range: [0, 1]
- motivation: Environmental context; not trees at conductors
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## neighbor_wind_max_ms

- family: neighborhood
- recipe: max(wind_max_ms over centered 3x3 analysis-land cells)
- unit: m/s
- valid_range: [0, 150]
- motivation: Surrounding storm intensity
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## neighbor_wind_mean_ms

- family: neighborhood
- recipe: sum(wind_max_ms * analysis_land_area_km2) / sum(analysis_land_area_km2) over centered 3x3
- unit: m/s
- valid_range: [0, 150]
- motivation: Surrounding land-area-weighted wind
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## neighbor_rain_max_mm

- family: neighborhood
- recipe: max(rain_total_mm over centered 3x3 analysis-land cells)
- unit: mm
- valid_range: [0, 3000]
- motivation: Surrounding accumulation maximum
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## neighbor_rain_mean_mm

- family: neighborhood
- recipe: sum(rain_total_mm * analysis_land_area_km2) / sum(analysis_land_area_km2) over centered 3x3
- unit: mm
- valid_range: [0, 3000]
- motivation: Surrounding land-area-weighted rain
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## neighbor_canopy_mean

- family: neighborhood
- recipe: sum(canopy_fraction * analysis_land_area_km2) / sum(analysis_land_area_km2) over centered 3x3
- unit: 1
- valid_range: [0, 1]
- motivation: Surrounding canopy
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## season_sin

- family: season
- recipe: sin(2*pi*(UTC midpoint - year start)/(next year start - year start))
- unit: 1
- valid_range: [-1, 1]
- motivation: Seasonal cycle
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## season_cos

- family: season
- recipe: cos(2*pi*(UTC midpoint - year start)/(next year start - year start))
- unit: 1
- valid_range: [-1, 1]
- motivation: Seasonal cycle
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none

## analysis_land_area_km2

- family: area
- recipe: EPSG:5070 area of densified cell intersect authoritative CONUS analysis land / 1e6
- unit: km2
- valid_range: [0, 100000]
- motivation: Unequal spatial support, not asset exposure
- source: pinned DWD forecast/static archive; availability <= cutoff
- missing_behavior: ineligible, including any missing contributing analysis-land neighbor
- geographic_coverage: CONUS analysis land
- vintage: per-archive source and canopy provenance
- limitations: 3x3 includes center; ocean excluded; truncated at grid edge; physical neighborhood size varies with latitude; endpoint weather is not observed weather
- monotonicity: none
