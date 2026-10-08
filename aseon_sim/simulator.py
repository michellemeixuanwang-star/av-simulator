"""Fixed-grid dispatch with continuous accounting inside deterministic plans.

There is no stochastic event queue or passenger/service waiting line. A plan is
advanced once per grid interval; overlapping phase lengths allocate actual miles,
energy and mutually exclusive vehicle-time modes without temporal double counting.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, replace
import math
import random

from .domain import Result, Vehicle
from .policy import choose_visit, select_vehicle, service_category, stable_unit, trip_activity

EPS = 1e-8


class CapacityCalendar:
    """Reserve whole grid bins, including travel; admission replaces physical queues."""

    def __init__(self, hubs, step_minutes):
        self.hubs = {h.hub_id: h for h in hubs}
        self.step = step_minutes
        self.bookings = {h.hub_id: Counter() for h in hubs}

    def bins(self, activity):
        start = int(round(activity.start_minutes / self.step))
        stop = math.ceil((activity.end_minutes - 1e-9) / self.step)
        return range(start, max(start + 1, stop))

    def has_capacity(self, hub, activity):
        return all(self.bookings[hub.hub_id][k] < hub.spaces for k in self.bins(activity))

    def reserve(self, activity):
        hub = self.hubs[activity.hub_id]
        if not self.has_capacity(hub, activity):
            raise RuntimeError("Capacity exceeded before reservation")
        self.bookings[hub.hub_id].update(self.bins(activity))

    def occupied(self, hub_id, period):
        return self.bookings[hub_id][period]


class Simulator:
    def __init__(self, config, router, hubs, requests, layout_name="custom",
                 initial_vehicles=None, data_source="synthetic_toy"):
        self.config, self.router = config, router
        self.hubs = tuple(hubs)
        self.layout_name, self.data_source = layout_name, data_source
        if not self.hubs or len({h.hub_id for h in self.hubs}) != len(self.hubs):
            raise ValueError("Nonempty hubs with unique IDs are required")
        if not any(h.kind == "central" for h in self.hubs):
            raise ValueError("At least one central depot is required")
        if any(h.zone_id not in router.zones for h in self.hubs):
            raise ValueError("Unknown hub zone")
        self.onward_miles = {z: min(router.between(z, h.zone_id).distance_miles for h in self.hubs)
                             for z in router.zones}
        rng = random.Random(config.seed + 104729)
        if initial_vehicles is None:
            self.vehicles = [Vehicle(i, router.zones[i % len(router.zones)],
                                    config.battery_kwh * rng.uniform(config.initial_soc_min, config.initial_soc_max))
                             for i in range(config.fleet_size)]
        else:
            self.vehicles = [replace(v) for v in initial_vehicles]
        if len(self.vehicles) != config.fleet_size or len({v.vehicle_id for v in self.vehicles}) != len(self.vehicles):
            raise ValueError("Initial vehicles must match fleet_size and have unique IDs")
        for v in self.vehicles:
            if v.zone_id not in router.zones or v.activity is not None:
                raise ValueError("Initial vehicles require known zones and no active plans")
            if not math.isfinite(v.energy_kwh) or not 0 <= v.energy_kwh <= config.battery_kwh:
                raise ValueError("Invalid initial vehicle energy")
            if v.pending_service not in {None, "local", "central"}:
                raise ValueError("Invalid pending service category")
        self.requests = list(requests)
        if len({r.request_id for r in self.requests}) != len(self.requests):
            raise ValueError("Request IDs must be unique")
        self.request_bins = defaultdict(list)
        # Seeded, policy-independent ordering avoids privileging the first OD pair
        # in a synthetic file when several requests share the same timestamp.
        for r in sorted(self.requests, key=lambda r: (r.time_minutes, stable_unit(config.seed, r.request_id, 0), r.request_id)):
            if r.origin not in router.zones or r.destination not in router.zones:
                raise ValueError("Unknown request zone")
            if r.time_minutes < config.end_minutes:
                self.request_bins[math.floor(r.time_minutes / config.step_minutes)].append(r)
        self.calendar = CapacityCalendar(self.hubs, config.step_minutes)
        self.stats = Counter()
        self.hub_stats = {h.hub_id: Counter() for h in self.hubs}
        self.period_rows, self.hub_rows, self.vehicle_rows, self.activity_rows = [], [], [], []
        self.trace_ids = {v.vehicle_id for v in sorted(self.vehicles, key=lambda v: v.vehicle_id)[:config.trace_vehicle_count]}
        self._has_run = False

    def _measured_request(self, request):
        return self.config.warmup_minutes <= request.time_minutes < self.config.end_minutes

    def _record_activity(self, vehicle, activity):
        if not self.config.record_traces or activity.start_minutes < self.config.warmup_minutes:
            return
        row = {
            "vehicle_id": vehicle.vehicle_id, "kind": activity.kind,
            "start_minutes": activity.start_minutes, "end_minutes": activity.end_minutes,
            "origin": vehicle.zone_id, "destination": activity.destination,
            "request_id": activity.request.request_id if activity.request else "",
            "hub_id": activity.hub_id or "", "service_category": vehicle.pending_service or "",
            "opportunity_visit": activity.opportunity_visit,
            "energy_start_kwh": vehicle.energy_kwh,
            "planned_added_kwh": activity.added_kwh,
            "planned_end_energy_kwh": vehicle.energy_kwh + activity.added_kwh - self.config.drive_kwh_per_mile * activity.distance_miles,
            "planned_empty_miles": sum(l.distance_miles for l in activity.legs if l.mode.startswith("empty")),
            "planned_passenger_miles": sum(l.distance_miles for l in activity.legs if l.mode == "passenger"),
            "planned_charging_minutes": sum(l.duration_minutes for l in activity.legs if l.power_kw > 0),
        }
        self.activity_rows.append(row)

    def _finish(self, vehicle, activity):
        vehicle.zone_id = activity.destination
        if activity.kind == "trip":
            if self._measured_request(activity.request) and activity.end_minutes <= self.config.end_minutes + EPS:
                self.stats["completed_trips"] += 1
                self.stats["gross_revenue"] += activity.request.fare
            vehicle.rides_since_service += 1
            if vehicle.rides_since_service >= self.config.service_every_trips:
                vehicle.service_episode += 1
                vehicle.pending_service = service_category(self.config, vehicle.vehicle_id, vehicle.service_episode)
        elif activity.clears_service:
            if self.config.warmup_minutes < activity.end_minutes <= self.config.end_minutes + EPS:
                self.stats[f"completed_{vehicle.pending_service}_services"] += 1
            vehicle.pending_service = None
            vehicle.rides_since_service = 0
        vehicle.activity = None

    def _advance_vehicle(self, vehicle, t, end, measured):
        flow = Counter()
        activity = vehicle.activity
        if activity is None:
            flow["minutes_parked"] = end - t
            if vehicle.energy_kwh < self.config.critical_soc * self.config.battery_kwh:
                flow["low_energy_idle_minutes"] += end - t
            if vehicle.pending_service:
                flow["pending_service_idle_minutes"] += end - t
            return flow
        cursor, occupied_minutes = activity.start_minutes, 0.0
        for leg in activity.legs:
            leg_end = cursor + leg.duration_minutes
            overlap = max(0.0, min(end, leg_end) - max(t, cursor))
            if overlap > 0:
                fraction = overlap / leg.duration_minutes
                miles = leg.distance_miles * fraction
                added = leg.power_kw * overlap / 60
                used = miles * self.config.drive_kwh_per_mile
                vehicle.energy_kwh += added - used
                flow[f"minutes_{leg.mode}"] += overlap
                flow["added_kwh"] += added
                flow["drive_kwh"] += used
                if leg.mode == "passenger":
                    flow["passenger_miles"] += miles
                elif leg.mode.startswith("empty"):
                    flow[f"{leg.mode}_miles"] += miles
                if activity.opportunity_visit:
                    flow["opportunity_added_kwh"] += added
                if measured and activity.hub_id:
                    hs = self.hub_stats[activity.hub_id]
                    hs["added_kwh"] += added
                    if leg.mode in {"local_hub", "central_hub"}:
                        hs["dwell_minutes"] += overlap
                occupied_minutes += overlap
                if not -EPS <= vehicle.energy_kwh <= self.config.battery_kwh + EPS:
                    raise RuntimeError(f"Energy bounds violated for vehicle {vehicle.vehicle_id}: {vehicle.energy_kwh}")
            cursor = leg_end
        slack = end - t - occupied_minutes
        if slack > EPS:
            flow["minutes_parked"] += slack
            flow["grid_delay_minutes"] += slack
        if activity.end_minutes <= end + EPS:
            self._finish(vehicle, activity)
        return flow

    def run(self):
        if self._has_run:
            raise RuntimeError("Simulator instances are single-use; create a fresh one for each experiment")
        self._has_run = True
        cfg = self.config
        start_state = None
        for period, t in enumerate(range(0, cfg.end_minutes, cfg.step_minutes)):
            measured = t >= cfg.warmup_minutes
            if t == cfg.warmup_minutes:
                start_state = self._boundary_state()
            before_completed = self.stats["completed_trips"]
            offered = self.request_bins[period]
            available = [v for v in self.vehicles if v.activity is None and v.pending_service is None]
            assigned, missed_available, missed_energy = 0, 0, 0
            for request in offered:
                vehicle = select_vehicle(request, available, self.router, self.onward_miles, cfg)
                if vehicle is None:
                    if available:
                        missed_energy += 1
                    else:
                        missed_available += 1
                    continue
                activity = trip_activity(vehicle, request, t, self.router)
                self._record_activity(vehicle, activity)
                vehicle.activity = activity
                available.remove(vehicle)
                assigned += 1
            if measured:
                self.stats["offered_trips"] += len(offered)
                self.stats["accepted_trips"] += assigned
                self.stats["unserved_no_available_vehicle"] += missed_available
                self.stats["unserved_energy_feasibility"] += missed_energy
            visits, blocked, infeasible = 0, 0, 0
            idle = [v for v in self.vehicles if v.activity is None]
            idle.sort(key=lambda v: (v.pending_service is None, v.energy_kwh, v.vehicle_id))
            for vehicle in idle:
                plan, reason = choose_visit(vehicle, self.hubs, t, self.router, cfg, self.calendar.has_capacity)
                if plan is not None:
                    self.calendar.reserve(plan)
                    self._record_activity(vehicle, plan)
                    vehicle.activity = plan
                    visits += 1
                    if measured:
                        self.stats["visits_assigned"] += 1
                        self.stats["opportunity_visits_assigned"] += int(plan.opportunity_visit)
                        self.hub_stats[plan.hub_id]["visits_assigned"] += 1
                elif reason == "capacity_blocked":
                    blocked += 1
                elif reason == "no_feasible_visit":
                    infeasible += 1
            if measured:
                self.stats["capacity_blocked_vehicle_periods"] += blocked
                self.stats["no_feasible_visit_vehicle_periods"] += infeasible
            flow = Counter()
            for vehicle in self.vehicles:
                flow.update(self._advance_vehicle(vehicle, t, t + cfg.step_minutes, measured))
            if measured:
                self.stats.update(flow)
            for hub in self.hubs:
                occupied = self.calendar.occupied(hub.hub_id, period)
                if occupied > hub.spaces:
                    raise RuntimeError("Hub reservation exceeds capacity")
                if measured:
                    hs = self.hub_stats[hub.hub_id]
                    hs["reservation_minutes"] += occupied * cfg.step_minutes
                    hs["max_reserved_spaces"] = max(hs["max_reserved_spaces"], occupied)
                    if cfg.record_traces:
                        self.hub_rows.append({"time_minutes": t, "hub_id": hub.hub_id,
                                              "reserved_spaces": occupied, "capacity": hub.spaces})
            if measured and cfg.record_traces:
                self.period_rows.append({
                    "time_minutes": t, "offered_trips": len(offered), "accepted_trips": assigned,
                    "completed_trips": self.stats["completed_trips"] - before_completed,
                    "unserved_no_available": missed_available, "unserved_energy": missed_energy,
                    "visits_assigned": visits, "capacity_blocked_vehicle_periods": blocked,
                    "added_kwh": flow["added_kwh"], "opportunity_added_kwh": flow["opportunity_added_kwh"],
                    "empty_miles": flow["empty_pickup_miles"] + flow["empty_service_miles"],
                    "mean_soc": sum(v.energy_kwh for v in self.vehicles) / (cfg.fleet_size * cfg.battery_kwh),
                    "pending_services": sum(v.pending_service is not None for v in self.vehicles),
                })
                for v in self.vehicles:
                    if v.vehicle_id in self.trace_ids:
                        self.vehicle_rows.append({"time_minutes": t + cfg.step_minutes,
                                                  "vehicle_id": v.vehicle_id, "anchor_zone": v.zone_id,
                                                  "energy_kwh": v.energy_kwh, "soc": v.energy_kwh / cfg.battery_kwh,
                                                  "activity": v.activity.kind if v.activity else "idle",
                                                  "remaining_minutes": max(0, v.activity.end_minutes - t - cfg.step_minutes) if v.activity else 0,
                                                  "pending_service": v.pending_service or ""})
        return self._result(start_state)

    def _boundary_state(self):
        return {"energy_kwh": sum(v.energy_kwh for v in self.vehicles),
                "pending_local": sum(v.pending_service == "local" for v in self.vehicles),
                "pending_central": sum(v.pending_service == "central" for v in self.vehicles),
                "active_trips": sum(v.activity is not None and v.activity.kind == "trip" for v in self.vehicles),
                "active_visits": sum(v.activity is not None and v.activity.kind == "visit" for v in self.vehicles)}

    def _result(self, start_state):
        cfg, s = self.config, self.stats
        end_state = self._boundary_state()
        residual = start_state["energy_kwh"] + s["added_kwh"] - s["drive_kwh"] - end_state["energy_kwh"]
        modes = ("passenger", "empty_pickup", "empty_service", "local_hub", "central_hub", "parked")
        time_residual = sum(s[f"minutes_{m}"] for m in modes) - cfg.fleet_size * cfg.measurement_minutes
        if abs(residual) > 1e-5 or abs(time_residual) > 1e-5:
            raise RuntimeError(f"Accounting failed: energy residual={residual}, time residual={time_residual}")
        empty = s["empty_pickup_miles"] + s["empty_service_miles"]
        total = empty + s["passenger_miles"]
        fixed = sum(h.fixed_cost_per_day + h.spaces * h.space_cost_per_day for h in self.hubs) * cfg.measured_days
        energy_cost = s["added_kwh"] * cfg.energy_cost_per_kwh
        distance_cost = total * cfg.nonenergy_cost_per_mile
        cost = fixed + energy_cost + distance_cost
        depletion = start_state["energy_kwh"] - end_state["energy_kwh"]
        summary = {
            "layout": self.layout_name, "data_source": self.data_source, "config": cfg.to_dict(),
            "site_count": len(self.hubs), "service_spaces": sum(h.spaces for h in self.hubs),
            "effective_network_power_kw": sum(h.spaces * h.effective_power(cfg.power_scale) for h in self.hubs),
            "requests_loaded": len(self.requests),
            "requests_outside_horizon": sum(r.time_minutes >= cfg.end_minutes for r in self.requests),
            "offered_trips": int(s["offered_trips"]), "accepted_trips": int(s["accepted_trips"]),
            "completed_trips": int(s["completed_trips"]),
            "accepted_unfinished_trips": int(s["accepted_trips"] - s["completed_trips"]),
            "served_fraction_completed": s["completed_trips"] / s["offered_trips"] if s["offered_trips"] else None,
            "served_fraction_accepted": s["accepted_trips"] / s["offered_trips"] if s["offered_trips"] else None,
            "gross_revenue": s["gross_revenue"],
            "revenue_per_vehicle_day": s["gross_revenue"] / (cfg.fleet_size * cfg.measured_days),
            "empty_miles": empty, "empty_pickup_miles": s["empty_pickup_miles"],
            "empty_service_miles": s["empty_service_miles"], "passenger_miles": s["passenger_miles"],
            "empty_miles_per_completed_trip": empty / s["completed_trips"] if s["completed_trips"] else None,
            "added_kwh": s["added_kwh"], "opportunity_added_kwh": s["opportunity_added_kwh"],
            "drive_kwh": s["drive_kwh"], "fixed_infrastructure_cost": fixed,
            "energy_cost": energy_cost, "nonenergy_vehicle_cost": distance_cost, "estimated_total_cost": cost,
            "estimated_revenue_minus_modeled_cost": s["gross_revenue"] - cost,
            "energy_inventory_adjusted_cost": cost + depletion * cfg.energy_cost_per_kwh,
            "start_state": start_state, "end_state": end_state, "energy_depletion_kwh": depletion,
            "energy_balance_residual_kwh": residual, "vehicle_time_residual_minutes": time_residual,
            "grid_delay_vehicle_hours": s["grid_delay_minutes"] / 60,
            "vehicle_hours": {m: s[f"minutes_{m}"] / 60 for m in modes},
            "hub_metrics": {},
        }
        for key in ("unserved_no_available_vehicle", "unserved_energy_feasibility", "visits_assigned",
                    "opportunity_visits_assigned", "capacity_blocked_vehicle_periods",
                    "no_feasible_visit_vehicle_periods", "completed_local_services", "completed_central_services"):
            summary[key] = int(s[key])
        summary["low_energy_idle_vehicle_hours"] = s["low_energy_idle_minutes"] / 60
        summary["pending_service_idle_vehicle_hours"] = s["pending_service_idle_minutes"] / 60
        for h in self.hubs:
            hs = self.hub_stats[h.hub_id]
            denom = h.spaces * cfg.measurement_minutes
            summary["hub_metrics"][h.hub_id] = {
                **asdict(h), "effective_power_kw_per_space": h.effective_power(cfg.power_scale),
                "visits_assigned": int(hs["visits_assigned"]), "added_kwh": hs["added_kwh"],
                "max_reserved_spaces": int(hs["max_reserved_spaces"]),
                "reservation_utilization": hs["reservation_minutes"] / denom,
                "physical_dwell_utilization": hs["dwell_minutes"] / denom,
            }
        return Result(summary, self.period_rows, self.hub_rows, self.vehicle_rows, self.activity_rows)
