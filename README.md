# Distributed fleet charging and servicing simulator

A runnable first-order Python model of autonomous-fleet charging and robotic
servicing, based on the October 2026 Aseon research discussions. The core needs
**Python 3.10+ and no third-party packages**. Plotting optionally uses matplotlib.

The included city, demand, sites, vehicle characteristics, fares, and costs are
**synthetic assumptions**. The toy city has a dense eastern core and residential
western zones, loosely motivated by the SF study context. Its coordinates are
fictional local miles, not geocoded San Francisco properties. Results demonstrate
the simulator and mechanisms; they are not estimates of Aseon's or Waymo's fleet.

## Run immediately

From this folder, no installation is necessary:

```bash
python -m aseon_sim compare --output results/my_comparison
python -m unittest discover -s tests -v
```

Optional installation and plots:

```bash
python -m pip install -e ".[plots]"
python -m aseon_sim compare --plot --output results/my_comparison
```

The example uses one warm-up day followed by two measured days, 90 vehicles,
10-minute decisions and service windows, and **24 service spaces / 2,400 kW**
in each layout. The distributed and hybrid layouts keep central depots for
exceptional work. This is a matched-capacity comparison, not a matched-cost one.

## Experiments

```bash
# One chosen layout
python -m aseon_sim run --layout hybrid --output results/hybrid

# Short-window sensitivity, with the same requests across all cases
python -m aseon_sim sweep --parameter idle_window_minutes --values 5 10 15 20 --plot --output results/windows

# Finer grid: every tested window is an integer multiple of the five-minute step
python -m aseon_sim sweep --step 5 --parameter idle_window_minutes --values 5 10 15 20 --plot --output results/windows_step5

# Effective charging power: multiply every site's baseline power
python -m aseon_sim sweep --parameter power_scale --values 0.5 1 1.5 --output results/power

# Share of service visits suitable for local hubs (a scenario, not measured 84%)
python -m aseon_sim sweep --parameter local_service_share --values 0.5 0.84 1 --output results/service_mix

# Search one round of satellite relocations, keeping depots and capacities fixed
python -m aseon_sim search --layout hybrid --iterations 1 --output results/location_search

# Another demand realization; all layouts still share the same realization
python -m aseon_sim compare --demand-mode sampled --seed 2027 --output results/seed2027

# Isolate step-size effects while keeping the opportunity window unchanged
python -m aseon_sim compare --step 5 --window 10 --output results/step5
```

`search` maximizes completed trips, then minimizes empty miles. It is a small
greedy local search over fictional zone references; it does not optimize global
facility design, identify permitted properties, or automatically enforce a
financial budget. Inspect energy inventories and service backlogs before treating
one layout as preferable. JSON layouts can be edited directly for cost-budget,
site-size, charging-only, or alternative-depot experiments.

Window and decision step are different parameters. With ten-minute decisions, a
15-minute commitment keeps a vehicle unavailable until the 20-minute boundary.
The sweep tests a service-commitment policy, not a guarantee that real vehicles
receive no new requests for that long. Compare a finer grid before interpreting
such changes as an operational result.

## Where to edit

| File | Purpose |
|---|---|
| `examples/config.json` | Fleet, battery, windows, service share/frequency, policies, fares and variable costs |
| `examples/layouts.json` | Hub locations, capacities, routine-service capability, power and fixed costs |
| `examples/zones.csv` | Zone identifiers, fictional geometry and synthetic demand weights |
| `aseon_sim/config.py` | Defaults, units and parameter validation |
| `aseon_sim/data.py` | Input readers and deterministic/sampled toy OD demand |
| `aseon_sim/domain.py` | Vehicles, hubs, requests, routing matrix and activity plans |
| `aseon_sim/policy.py` | Trip matching, service classification and charging decisions |
| `aseon_sim/simulator.py` | Fixed-grid loop, reservations, energy/time accounting and metrics |
| `aseon_sim/experiments.py` | Paired comparisons, sensitivity runs and location search |
| `aseon_sim/reporting.py` | JSON/CSV exports and optional figures |
| `docs/MODEL.md` | Assumptions and interpretation of the simplified model |
| `docs/DATA.md` | Input schemas and routes toward real SF inputs |

The source is arranged as a GitHub-ready project. No remote repository or public
release is required to run it. From Python, a minimal experiment is:

```python
from aseon_sim.config import Config
from aseon_sim.data import load_zones, make_toy_router, load_layouts, generate_requests
from aseon_sim.experiments import compare

cfg = Config.load("examples/config.json")
zones = load_zones("examples/zones.csv")
router = make_toy_router(zones)
layouts = load_layouts("examples/layouts.json", router.zones)
requests = generate_requests(zones, router, cfg)
results = compare(cfg, router, layouts, requests)
print(results["hybrid"].summary["completed_trips"])
```

## Reading outputs

Each run writes an input request CSV and a manifest recording configuration,
version and input-file hashes. Each layout has:

* `summary.json`: throughput, modeled revenue/cost, distance, energy, vehicle
  time, hub utilization, start/end state and accounting residuals.
* `periods.csv`: the measured-period time series.
* `hubs.csv`: reserved spaces per hub and decision period.
* `vehicles.csv`: selected vehicles' energy and activity at grid boundaries.
* `activities.csv`: assigned trips/visits, **planned** charging energy and distance.

`comparison.csv` places the main metrics side by side. `demo_results/` contains
reproducible example results supplied with this project. The optional PNG figures
are for scenario exploration.

Important definitions:

* **Completed trips** and revenue count requests offered during the measured
  window that complete by its end. Accepted-but-unfinished trips are separate.
  No passenger waiting-time model is included.
* Mileage and energy are clipped to the measured physical time window, including
  any activity that began during warm-up. These therefore differ slightly from
  completed-trip-cohort quantities near the boundaries.
* **Capacity-blocked vehicle-periods** count unsuccessful admission attempts,
  not physical queue lengths or distinct vehicles.
* **Reservation utilization** includes conservatively reserved travel/rounding
  time. **Physical dwell utilization** counts actual time at the hub.
* `energy_inventory_adjusted_cost` values starting-minus-ending battery energy
  at the assumed energy price. This is a simple inventory correction; it does
  not make unequal fleet states equivalent or account for the time to recharge.
* Revenue minus modeled cost excludes costs not specified in this prototype.
  It is not observed revenue or an accounting profit estimate.
