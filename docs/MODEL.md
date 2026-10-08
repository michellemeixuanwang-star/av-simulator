# Model and implementation choices

## Decision clock and state

At each fixed grid boundary: finish previously advanced activities, match
current-bin trips, allocate service capacity to remaining vehicles, then advance
every plan for one interval. Requests are binned to the interval start. The
controller observes that bin, not realized requests from later bins. There is
one assignment per vehicle per boundary; within-bin matching and waiting are
abstracted. A vehicle finishing midway through a bin is available at the next
boundary; `grid_delay_vehicle_hours` records the resulting slack.

Vehicle state is zone anchor, battery energy, active deterministic plan,
rides-since-service count and pending service category. A busy vehicle's anchor
is its last dispatch location, not an interpolated physical position. It cannot
be reassigned until the plan ends. No event priority queue is used.

## Energy and visit duration

For a short opportunity visit:

\[
u = [H-\tau_{in}-\tau_{out}-h]_+,\qquad
Q=\min\{Pu/60,[E_{target}-E_{arrival}]_+\}.
\]

`h` combines setup/release with required routine work. This initially treats
non-charging work as serial overhead. Changing those allowances approximates
different overlaps without scheduling individual tasks. Energy is updated as
each grid interval overlaps driving and charging phases, never credited in
advance. Driving uses a constant kWh/mile parameter. Power is constant effective
battery power, not the charger nameplate maximum.

If energy reaches its target early, the short-window reservation still lasts
`H`; unused time is parked time after the modeled return. When energy is below
the critical threshold, use an extended charging visit toward the target.
Required service that cannot fit a short window can use an extended visit.
Noncritical exceptional service does not automatically charge to the target;
only a top-up required for the return energy reserve is added. This is an
explicit policy choice, readily changed in `make_visit`.

Default departure returns to the original zone, accounting for both directions.
`stay_at_hub` removes the return journey, updates the vehicle anchor to the hub,
and lets subsequent pickup travel capture the repositioning effect.

No separate parked-vehicle auxiliary drain, SoC taper, battery degradation,
temperature or microscopic traffic is included. Driving loads and net charging
power are effective parameters; idle auxiliary consumption is zero in this
prototype. These omissions should be tested or added if they affect conclusions.

## Capacity and service obligations

One effective service space bundles a charger and usable operating bay. Reserve
it for every grid bin overlapped by the entire visit, including travel. Admission
is coordinated and capacity cannot be exceeded. A full site causes a deferred
attempt or selection of another feasible site, not a physical queue. Visits
cannot be cancelled mid-window in this first implementation; charging flexibility
is represented by partial sessions, short windows and release allowance.

If a site power budget is given, per-space power is conservatively limited to
the budget divided by all spaces. The budget is expressed in the same
usable-equivalent kW units as effective battery power, not raw AC service power.
Without a budget, the scenario assumes enough site power for the selected rate.
No separate robotic-arm scheduler is implemented.

After `service_every_trips` completed rides, assign a persistent local or central
service need. Local-compatible share `alpha` is tunable; 0.84 is a scenario
reference, not an established fraction of visits inferred from the task statistic.
Deterministic runs use systematic shares with vehicle-specific offsets; sampled
runs use stable vehicle/episode draws. A deferred category is never redrawn.
All required work blocks passenger assignment until completion. Central depots
can handle both categories; charging-only satellites cannot clear routine work.

Simple policies match the nearest energy-feasible vehicle, prioritize pending
service and low energy for hub allocation, and select the nearest feasible hub.
Energy feasibility covers pickup, the passenger trip, reaching a charging site
afterward and a reserve. Future site capacity is not predicted. When short
service plans exist but all are booked, the policy waits rather than escalating
to a longer visit. If none can fit physically, required work may use a longer
visit. Idle vehicles may park locally; there is no forced depot return, curb-ban
cruising policy, or automatic demand-driven repositioning in the default model.

## Comparisons and boundaries

All layouts replay the same requests and initial vehicle states. Request ties
use a seeded hash, avoiding systematic OD-order priority. Service draws use
vehicle identity and service episode, not layout-dependent RNG consumption.
Sweeps hold the request list fixed. The input demand bin is separate from the
decision step, so changing numerical resolution does not regenerate demand.

Warm-up reduces initialization effects, but does not establish stationarity.
Inspect mean energy across days, boundary battery stocks, incomplete trips,
service backlogs and any active visits at the end. No forced restoration or
automatic terminal-state equalization is included. Greedy relocation and raw
cost comparisons remain conditional scenario exercises when these states differ.

The engine checks energy conservation, nonnegative/capacity-bounded battery
energy, hub reservation capacity and exclusive vehicle-time accounting. Tests
also cover hand-calculated sessions, future-energy credit, deferred service,
charging-only sites, warm-up clipping and paired-experiment reproducibility.
