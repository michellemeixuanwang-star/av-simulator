"""CSV/JSON input contracts and explicitly synthetic demonstration demand."""

import csv
from dataclasses import asdict
import json
import math
from pathlib import Path
import random
import re

from .domain import Hub, Request, Router, Travel, Zone


def load_zones(path):
    with Path(path).open(newline="") as f:
        zones = [Zone(r["zone_id"], r["name"], float(r["x_miles"]), float(r["y_miles"]),
                      float(r.get("demand_weight", 1))) for r in csv.DictReader(f)]
    if not zones or len({z.zone_id for z in zones}) != len(zones):
        raise ValueError("Zones must be nonempty and uniquely identified")
    for z in zones:
        if not all(math.isfinite(v) for v in (z.x_miles, z.y_miles, z.demand_weight)) or z.demand_weight <= 0:
            raise ValueError("Zone coordinates must be finite and weights positive")
    return zones


def load_layouts(path, zone_ids):
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Layouts JSON must be a nonempty mapping of names to hub lists")
    if any(not re.fullmatch(r"[\w-]+", name) for name in raw):
        raise ValueError("Layout names must use letters, digits, underscores or hyphens")
    try:
        layouts = {name: [Hub(**item) for item in items] for name, items in raw.items()}
    except TypeError as exc:
        raise ValueError(f"Invalid hub fields in layouts JSON: {exc}") from exc
    for name, hubs in layouts.items():
        if not hubs or len({h.hub_id for h in hubs}) != len(hubs):
            raise ValueError(f"Empty layout or duplicate hub IDs: {name}")
        if not any(h.kind == "central" for h in hubs):
            raise ValueError(f"A central depot is required for exceptional work: {name}")
        if any(h.zone_id not in zone_ids for h in hubs):
            raise ValueError(f"Unknown hub zone in {name}")
    return layouts


def make_toy_router(zones, speed_mph=18.0, road_factor=1.15):
    """Local planar coordinates are fictional; no live map or traffic API is used."""
    matrix = {}
    for o in zones:
        for d in zones:
            miles = road_factor * math.hypot(o.x_miles - d.x_miles, o.y_miles - d.y_miles)
            if o.zone_id == d.zone_id:
                miles = 0.35  # average sub-zone access, not exact colocation
            matrix[o.zone_id, d.zone_id] = Travel(miles, 60 * miles / speed_mph)
    return Router([z.zone_id for z in zones], matrix)


def load_router(path, zone_ids):
    matrix = {}
    with Path(path).open(newline="") as f:
        for r in csv.DictReader(f):
            key = r["origin"], r["destination"]
            if key in matrix:
                raise ValueError(f"Duplicate travel matrix pair: {key}")
            matrix[key] = Travel(float(r["distance_miles"]), float(r["duration_minutes"]))
    return Router(zone_ids, matrix)


def load_requests(path, router, config):
    requests = []
    seen = set()
    with Path(path).open(newline="") as f:
        for r in csv.DictReader(f):
            if r["request_id"] in seen:
                raise ValueError(f"Duplicate request ID: {r['request_id']}")
            seen.add(r["request_id"])
            o, d = r["origin"], r["destination"]
            if o not in router.zones or d not in router.zones:
                raise ValueError(f"Unknown request location: {o}, {d}")
            travel = router.between(o, d)
            miles = float(r.get("distance_miles") or travel.distance_miles)
            minutes = float(r.get("duration_minutes") or travel.duration_minutes)
            fare = float(r.get("fare") or (config.fare_base + config.fare_per_mile * miles + config.fare_per_minute * minutes))
            requests.append(Request(r["request_id"], float(r["time_minutes"]), o, d, miles, minutes, fare))
    return sorted(requests, key=lambda r: (r.time_minutes, r.request_id))


def _poisson(rng, mean):
    """Sum small independent Poisson draws to avoid exp(-large mean) underflow."""
    count = 0
    while mean > 1e-12:
        part = min(mean, 25.0)
        threshold = math.exp(-part)
        product, k = 1.0, 0
        while product > threshold:
            product *= rng.random()
            k += 1
        count += k - 1
        mean -= part
    return count


def generate_requests(zones, router, config):
    """Time-varying artificial OD; no Replica, Uber, or Waymo data are implied."""
    hourly = [0.30, 0.22, 0.16, 0.12, 0.14, 0.30, 0.65, 1.25, 1.55, 1.20,
              0.90, 0.95, 1.10, 1.00, 0.95, 1.10, 1.45, 1.65, 1.50, 1.25,
              1.10, 0.95, 0.75, 0.50]
    normalizer = sum(hourly)
    pairs = [(o, d) for o in zones for d in zones if o.zone_id != d.zone_id]
    if not pairs:
        pairs = [(zones[0], zones[0])]
    residual = { (o.zone_id, d.zone_id): 0.0 for o, d in pairs }
    rng = random.Random(config.seed)
    requests = []
    for t in range(0, config.end_minutes, config.demand_bin_minutes):
        hour = (t // 60) % 24
        total_mean = config.daily_requests * config.demand_scale * hourly[hour] / normalizer * config.demand_bin_minutes / 60
        # Morning destinations emphasize higher-weight centers; evening reverses it.
        weights = []
        for o, d in pairs:
            if 7 <= hour < 10:
                w = math.sqrt(o.demand_weight) * d.demand_weight ** 1.6
            elif 16 <= hour < 20:
                w = o.demand_weight ** 1.6 * math.sqrt(d.demand_weight)
            else:
                w = o.demand_weight * d.demand_weight
            weights.append(w)
        weight_sum = sum(weights)
        for (o, d), w in zip(pairs, weights):
            key = o.zone_id, d.zone_id
            mean = total_mean * w / weight_sum
            if config.demand_mode == "sampled":
                n = _poisson(rng, mean)
            else:
                residual[key] += mean
                n = math.floor(residual[key] + 1e-12)
                residual[key] -= n
            travel = router.between(*key)
            for _ in range(n):
                minutes = max(2.0, travel.duration_minutes)
                fare = config.fare_base + config.fare_per_mile * travel.distance_miles + config.fare_per_minute * minutes
                requests.append(Request(f"toy-{len(requests):07d}", t, *key, travel.distance_miles, minutes, fare))
    return requests


def write_requests(path, requests):
    with Path(path).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(requests[0])) if requests else
                                ["request_id", "time_minutes", "origin", "destination", "distance_miles", "duration_minutes", "fare"])
        writer.writeheader()
        writer.writerows(asdict(r) for r in requests)
