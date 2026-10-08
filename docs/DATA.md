# Replacing the synthetic inputs

The simulator intentionally starts without proprietary data. Real inputs need
normalization and separate plausibility checks; successful parsing is not
empirical validation.

## Zones CSV

Required columns: `zone_id,name,x_miles,y_miles`; optional `demand_weight`.
Coordinates are only used by the fallback planar router. For SF, define a
consistent zone system and provide a road-distance/time matrix instead of
treating longitude/latitude as miles. Smaller zones or sampled access distances
may be needed to distinguish nearby three-space hubs.

## Travel CSV

```csv
origin,destination,distance_miles,duration_minutes
z00,z00,0.35,1.2
z00,z10,2.3,7.7
```

Provide **every ordered pair**, including diagonals; the two-line example is
only an illustration of the schema. Asymmetric distances/times are supported.
Values represent averages. A positive-distance journey cannot have zero time.
Diagonal values should reflect the intended within-zone access approximation.

## Request CSV

```csv
request_id,time_minutes,origin,destination,distance_miles,duration_minutes,fare
r001,10,z00,z10,2.3,8,9.6
```

Required: `request_id,time_minutes,origin,destination`. Trip distance and duration
default to the matrix when blank; fare defaults to the configuration formula.
Time is minutes from the beginning of the **entire run**, including warm-up.
IDs must be unique. Supplied trip distances/times may differ from the access
matrix. Use explicit unpooled vehicle-request proxies in the first version.

```bash
python -m aseon_sim run --requests my_requests.csv --travel my_matrix.csv --zones my_zones.csv --layouts my_layouts.json --layout hybrid --warmup-days 0 --days 1 --output results/my_inputs
```

A one-day file starting at zero needs `--warmup-days 0`, or appropriately repeated
and offset days for warm-up. Records outside the configured horizon are reported
and ignored. Requests receive no passenger queue; unmatched current-bin demand
counts as unserved. Aggregated expected OD flows must first be converted to
integer request counts under a stated rounding/sampling convention.

## Layouts JSON

Map each layout name to a list of hubs. Each hub requires `hub_id,zone_id,kind,
spaces`. Kind is `central` or `satellite`. Every layout retains a central depot.
Optional fields:

| Field | Interpretation |
|---|---|
| `power_kw` | Baseline effective battery power per space |
| `site_power_kw` | Optional usable-equivalent site cap; conservatively shared across all spaces |
| `supports_routine` | Boolean; false makes a satellite charging-only |
| `fixed_cost_per_day` | Per-site daily-equivalent investment/fixed-operation assumption |
| `space_cost_per_day` | Per-space daily-equivalent cost assumption |

Names use letters, digits, underscores or hyphens. Physical access, permits,
robot reach, property availability, public users and operating hours are not
inferred from coordinates. Represent dedicated/reserved access initially; screen
candidate properties before interpreting a layout as deployable.

## Data leads for the SF study

* [Replica Auto/TNC documentation](https://documentation.replicahq.com/docs/auto-tnc-trips):
  investigate access, seasonal typical days, person-to-vehicle conversion and
  block-group spatial precision. Map to simulator zones and normalize into the
  request schema; no automatic licensed-data download or person-to-order claim
  is included.
* [SFCTA TNCs Today](https://www.sfcta.org/projects/tncs-today-2017): historical
  2016 pickup/drop-off zone counts for pattern checks, not complete current OD.
* [AFDC station fields](https://afdc.energy.gov/data_download/alt_fuel_stations_format)
  and [SFMTA off-street parking](https://catalog.data.gov/dataset/sfmta-managed-off-street-parking):
  candidate location references before property/access/power screening.
* Public vehicle and charger specifications can inform parameter ranges. Aseon's
  judgment can refine site constraints, effective overhead, service mix and
  useful operating metrics. No detailed battery curves or arm logs are required
  to run this first-order version.
