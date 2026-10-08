"""Interpretable rules, isolated so alternative policies can be compared."""

import hashlib
import math

from .domain import Activity, Leg

EPS = 1e-9


def stable_unit(seed, vehicle_id, episode):
    data = f"{seed}:{vehicle_id}:{episode}".encode()
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big") / 2**64


def service_category(config, vehicle_id, episode):
    """Common identity/episode draws across layouts, independent of execution order."""
    alpha = config.local_service_share
    if config.service_mode == "sampled":
        local = stable_unit(config.seed, vehicle_id, episode) < alpha
    else:
        offset = stable_unit(config.seed, vehicle_id, 0)
        local = math.floor(alpha * episode + offset) > math.floor(alpha * (episode - 1) + offset)
    return "local" if local else "central"


def select_vehicle(request, available, router, onward_miles, config):
    choices = []
    for vehicle in available:
        if vehicle.pending_service is not None or vehicle.activity is not None:
            continue
        pickup = router.between(vehicle.zone_id, request.origin)
        need = config.drive_kwh_per_mile * (pickup.distance_miles + request.distance_miles + onward_miles[request.destination]) + config.reserve_kwh
        if vehicle.energy_kwh + EPS >= need:
            choices.append((pickup.duration_minutes, pickup.distance_miles, vehicle.vehicle_id, vehicle))
    return min(choices, key=lambda x: x[:3])[-1] if choices else None


def trip_activity(vehicle, request, t, router):
    pickup = router.between(vehicle.zone_id, request.origin)
    legs = [Leg(pickup.duration_minutes, "empty_pickup", pickup.distance_miles),
            Leg(request.duration_minutes, "passenger", request.distance_miles)]
    return Activity("trip", t, tuple(leg for leg in legs if leg.duration_minutes > 0), request.destination, request=request)


def make_visit(vehicle, hub, t, router, config, short):
    if not hub.can_service(vehicle.pending_service):
        return None
    inbound = router.between(vehicle.zone_id, hub.zone_id)
    returning = config.departure_policy == "return_to_origin"
    outbound = router.between(hub.zone_id, vehicle.zone_id) if returning else None
    out_minutes = outbound.duration_minutes if outbound else 0
    out_miles = outbound.distance_miles if outbound else 0
    arrival = vehicle.energy_kwh - inbound.distance_miles * config.drive_kwh_per_mile
    if arrival < config.reserve_kwh - EPS:
        return None
    service = (config.central_service_minutes if vehicle.pending_service == "central" else
               config.routine_service_minutes if vehicle.pending_service == "local" else 0)
    overhead = config.setup_release_minutes + service
    power = hub.effective_power(config.power_scale)
    deficit = max(0.0, config.target_soc * config.battery_kwh - arrival)
    critical = vehicle.energy_kwh < config.critical_soc * config.battery_kwh - EPS
    if short:
        minutes = config.idle_window_minutes - inbound.duration_minutes - out_minutes - overhead
        if minutes < -EPS:
            return None
        added = min(deficit, power * max(0, minutes) / 60)
    else:
        # Exceptional work does not automatically trigger a full battery top-up.
        added = deficit if critical else max(0, config.reserve_kwh + out_miles * config.drive_kwh_per_mile - arrival)
    end_energy = arrival + added - out_miles * config.drive_kwh_per_mile
    if end_energy < config.reserve_kwh - EPS or arrival + added > config.battery_kwh + EPS:
        return None
    net_topup = added - (inbound.distance_miles + out_miles) * config.drive_kwh_per_mile
    if vehicle.pending_service is None and not critical and net_topup < config.min_net_topup_kwh:
        return None
    mode = "central_hub" if hub.kind == "central" else "local_hub"
    legs = [Leg(inbound.duration_minutes, "empty_service", inbound.distance_miles),
            Leg(overhead, mode), Leg(added * 60 / power, mode, power_kw=power)]
    if outbound:
        legs.append(Leg(outbound.duration_minutes, "empty_service", outbound.distance_miles))
    if short:
        slack = config.idle_window_minutes - sum(leg.duration_minutes for leg in legs)
        if slack > EPS:
            legs.append(Leg(slack, "parked"))
    legs = tuple(leg for leg in legs if leg.duration_minutes > EPS)
    if not legs:
        return None
    return Activity("visit", t, legs, vehicle.zone_id if returning else hub.zone_id,
                    hub_id=hub.hub_id, clears_service=vehicle.pending_service is not None,
                    opportunity_visit=short)


def choose_visit(vehicle, hubs, t, router, config, has_capacity):
    critical = vehicle.energy_kwh < config.critical_soc * config.battery_kwh - EPS
    eligible = (vehicle.pending_service is not None or critical or
                (config.charging_policy == "opportunity" and vehicle.energy_kwh < config.opportunity_soc * config.battery_kwh - EPS))
    if not eligible:
        return None, "not_needed"
    plans = []
    # Critical energy and depot-only work use extended visits. Routine work first
    # tries a short window; if it cannot fit at any site, it may take longer.
    short = not critical and vehicle.pending_service != "central"
    for hub in hubs:
        plan = make_visit(vehicle, hub, t, router, config, short=short)
        if plan is not None:
            plans.append((hub, plan))
    if not plans and vehicle.pending_service is not None and short:
        for hub in hubs:
            plan = make_visit(vehicle, hub, t, router, config, short=False)
            if plan is not None:
                plans.append((hub, plan))
    if not plans:
        return None, "no_feasible_visit"
    feasible = [(hub, plan) for hub, plan in plans if has_capacity(hub, plan)]
    if not feasible:
        return None, "capacity_blocked"
    hub, plan = min(feasible, key=lambda hp: (hp[1].distance_miles, hp[1].duration_minutes, hp[0].hub_id))
    return plan, "assigned"
