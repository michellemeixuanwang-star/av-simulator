"""Common-input comparisons and a small outer location search."""

from dataclasses import replace

from .simulator import Simulator


def compare(config, router, layouts, requests, data_source="synthetic_toy"):
    return {name: Simulator(config, router, hubs, requests, name, data_source=data_source).run()
            for name, hubs in layouts.items()}


def sweep(config, router, layouts, requests, parameter, values, data_source="synthetic_toy"):
    allowed = {"step_minutes", "idle_window_minutes", "setup_release_minutes", "routine_service_minutes",
               "central_service_minutes", "power_scale", "local_service_share", "service_every_trips",
               "fleet_size", "battery_kwh", "drive_kwh_per_mile", "critical_soc", "opportunity_soc"}
    if parameter not in allowed:
        raise ValueError(f"Sweep parameter must be one of {sorted(allowed)}")
    integer_fields = {"step_minutes", "service_every_trips", "fleet_size"}
    for value in values:
        if parameter in integer_fields:
            if value != int(value):
                raise ValueError(f"{parameter} requires integer values")
            value = int(value)
        varied = config.with_changes(**{parameter: value})
        # All cases replay the same request list. No layout-dependent RNG state.
        yield value, compare(varied, router, layouts, requests, data_source)


def greedy_relocate(config, router, hubs, requests, iterations=1, data_source="synthetic_toy"):
    """Relocate one satellite at a time; preserve central sites, spaces and power.

    Objective: maximize completed trips, then minimize empty miles. This is a
    local demonstration search, not a globally optimal or calibrated proposal.
    Candidate positions are zone references, not permitted physical properties.
    """
    if iterations < 0:
        raise ValueError("iterations must be nonnegative")
    evaluation_cfg = config.with_changes(record_traces=False)
    current = list(hubs)
    best = Simulator(evaluation_cfg, router, current, requests, "search", data_source=data_source).run()
    history = []

    def score(result):
        s = result.summary
        return s["completed_trips"], -s["empty_miles"]

    for iteration in range(iterations):
        winner, winner_result, change = current, best, None
        for index, hub in enumerate(current):
            if hub.kind != "satellite":
                continue
            for zone in router.zones:
                if zone == hub.zone_id or any(other.zone_id == zone for j, other in enumerate(current) if j != index):
                    continue
                trial = list(current)
                trial[index] = replace(hub, zone_id=zone)
                result = Simulator(evaluation_cfg, router, trial, requests, "search", data_source=data_source).run()
                if score(result) > score(winner_result):
                    winner, winner_result = trial, result
                    change = {"iteration": iteration + 1, "hub_id": hub.hub_id,
                              "old_zone": hub.zone_id, "new_zone": zone,
                              "completed_trips": result.summary["completed_trips"],
                              "empty_miles": result.summary["empty_miles"]}
        if change is None:
            break
        current, best = winner, winner_result
        history.append(change)
    final = Simulator(config, router, current, requests, "searched_layout", data_source=data_source).run()
    return current, final, history
