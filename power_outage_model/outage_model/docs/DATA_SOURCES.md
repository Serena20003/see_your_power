# Real data access and eligibility

## California incident points, adapter caloes/v1

Read-only endpoint:
https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/Power_Outages_(View)/FeatureServer/0

Pinned service metadata: `configs/california-layer-v1.json`. A real schema check and
collection on November 16, 2026 (Los Angeles) retrieved 96 records. The concise
receipt is in `validation/model-source-check.json`; raw pages, ID inventories,
metadata and normalized records are retained in the local output object store.
This receipt verifies transport, not event accuracy or historical coverage.

The metadata describes `StartDate` as **when the outage was first reported by the
system**, not necessarily physical onset. `EstimatedRestoreDate` is never used
as onset. `IncidentId` together with utility/source identifies a candidate event;
OBJECTID is a paging key, and GlobalID/OBJECTID stability across refreshes is not
assumed. Identity reuse and revisions are quarantined. Approximate markers are
not equipment locations. County is supplied by a utility and can describe only
one county even when an incident crosses counties.

`OutageType` contains Planned, Not Planned and Unknown. Cal OES says this can be
calculated from a utility designation **or inferred from cause**. Consequently,
Not Planned maps to unknown by default. Utility-specific `type_audit_refs`,
`onset_audit_refs` and `geometry_audit_refs` must accompany any stronger policy.
Planned and identifiable PSPS are excluded. Unknown classifications are never
silently recoded as unplanned. The default adapter yields no whole-cell coverage.

The [California dataset description](https://data.ca.gov/@california-office-of-emergency-services/power-outage-incidents)
says it contains recent outages, not historical records, refreshes every 15
minutes and reports general locations. Current maps can omit short incidents
between polls and omit entire utilities/areas. A complete paginated response,
an empty response and a fresh edit timestamp do not prove ascertainment.

Default collection is a **one-shot command** suitable for an externally operated
15-minute schedule. No scheduler or cloud resources are provisioned. Each
collection enumerates all IDs, checks all requested pages, asks for WGS84 output,
checks response CRS and repeats the ID enumeration. Schema drift, missing IDs,
transfer-limit responses, source changes and parser failures fail the attempt.
Raw successful responses are retained for omission/identity audits. A stale
source cannot produce a qualified actual-onset event. Collector failures must
be represented as gaps in reviewed coverage, never successful empty polls.

The first active inventory does not establish new-onset times from first-seen.
Only independently audited actual onset or a reviewed bounded interval following
a prior verified complete inventory can qualify. A bounded interval that crosses
a target boundary is excluded. No retrospective backfill is claimed.

## History-access inventory

| Source | Earliest usable aligned history | Gaps | Access/reuse | Onset/location/ascertainment |
| --- | --- | --- | --- | --- |
| Cal OES current points | None established; one prospective collection | Everything before collection and between unpolled snapshots | Public read-only access verified; redistribution rights not independently audited | Reported-system time, approximate markers, utility-dependent typing; omission audit absent |
| ODIN point history | None established | Unknown | Requires verified authorized access/export; no access assumed | See repository ODIN research; public H3/count data do not establish native typed incident history |
| DWD operational forecast history | No complete live aligned batch established in local store | Historical availability unknown | Upstream attribution/pins retained per archive | First-seen availability and exact 72 normalized forecast hours required; reanalysis is not an operational backtest |
| Canopy and analysis land | No production static release pinned locally | Vintage/release availability unverified | Source attribution/license/checksums required | Later static releases are retrospective-only for earlier cutoffs |

## Reviewed coverage and event input

`CoverageInterval.cells` lists exact `(grid_id,row,col)` cells for which the named
reporting domain is an **audited complete union of all relevant utilities over
the whole supported land area**. It is not a cell touched by any utility.
`complete`, `whole_cell`, `ascertainment_audited`, expected/received polls,
footprint version and evidence must all support a zero label. The builder only
stitches contiguous intervals from the same complete domain; it never adds
partial utility percentages. Both known-positive and negative rows are null
when coverage is incomplete. Events remain available for audit.

A later snapshot/revision needs a new frozen dataset and new label content hash.
Rows cannot silently acquire events first seen after the requested freeze.
Reviewed storm blocks must group the same episode across state lines; quiet
blocks use fixed UTC weeks. Do not derive blocks from model errors.
