"""Small state objects and deterministic activity plans, not an event queue."""

from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    x_miles: float
    y_miles: float
    demand_weight: float = 1.0


@dataclass(frozen=True)
class Request:
    request_id: str
    time_minutes: float
    origin: str
    destination: str
    distance_miles: float
    duration_minutes: float
    fare: float

    def __post_init__(self):
        if not self.request_id or not self.origin or not self.destination:
            raise ValueError("Request IDs and locations must be nonempty")
        for name in ("time_minutes", "distance_miles", "duration_minutes", "fare"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid request {name}")
        if self.duration_minutes <= 0:
            raise ValueError("Request duration must be positive")


@dataclass(frozen=True)
class Hub:
    hub_id: str
    zone_id: str
    kind: str
    spaces: int
    power_kw: float = 100.0
    supports_routine: bool = True
    site_power_kw: float | None = None
    fixed_cost_per_day: float = 150.0
    space_cost_per_day: float = 20.0

    def __post_init__(self):
        if not isinstance(self.hub_id, str) or not self.hub_id or not isinstance(self.zone_id, str) or not self.zone_id:
            raise ValueError("Hub IDs and zones must be nonempty strings")
        if type(self.supports_routine) is not bool:
            raise ValueError("supports_routine must be a boolean")
        if self.kind not in {"central", "satellite"}:
            raise ValueError("Hub kind must be central or satellite")
        if type(self.spaces) is not int or self.spaces <= 0:
            raise ValueError("Hub spaces must be a positive integer")
        for name in ("power_kw", "fixed_cost_per_day", "space_cost_per_day"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid hub {name}")
        if self.power_kw <= 0:
            raise ValueError("Hub power_kw must be positive")
        if self.site_power_kw is not None and (not math.isfinite(self.site_power_kw) or self.site_power_kw <= 0):
            raise ValueError("site_power_kw must be positive when specified")

    def effective_power(self, scale=1.0):
        """Conservative constant per-space power, with a usable-equivalent site cap."""
        power = self.power_kw * scale
        return power if self.site_power_kw is None else min(power, self.site_power_kw / self.spaces)

    def can_service(self, category):
        return category is None or (category == "local" and (self.supports_routine or self.kind == "central")) or (category == "central" and self.kind == "central")


@dataclass(frozen=True)
class Leg:
    duration_minutes: float
    mode: str
    distance_miles: float = 0.0
    power_kw: float = 0.0


@dataclass
class Activity:
    kind: str
    start_minutes: float
    legs: tuple[Leg, ...]
    destination: str
    request: Request | None = None
    hub_id: str | None = None
    clears_service: bool = False
    opportunity_visit: bool = False

    @property
    def duration_minutes(self):
        return sum(leg.duration_minutes for leg in self.legs)

    @property
    def end_minutes(self):
        return self.start_minutes + self.duration_minutes

    @property
    def added_kwh(self):
        return sum(leg.power_kw * leg.duration_minutes / 60 for leg in self.legs)

    @property
    def distance_miles(self):
        return sum(leg.distance_miles for leg in self.legs)


@dataclass
class Vehicle:
    vehicle_id: int
    zone_id: str
    energy_kwh: float
    rides_since_service: int = 0
    service_episode: int = 0
    pending_service: str | None = None
    activity: Activity | None = None


@dataclass(frozen=True)
class Travel:
    distance_miles: float
    duration_minutes: float


class Router:
    """A complete zone-pair matrix; times are averages, not live traffic."""

    def __init__(self, zones, matrix):
        self.zones = tuple(zones)
        if not self.zones or len(set(self.zones)) != len(self.zones):
            raise ValueError("Zone IDs must be nonempty and unique")
        expected = {(o, d) for o in self.zones for d in self.zones}
        if set(matrix) != expected:
            missing = expected - set(matrix)
            extra = set(matrix) - expected
            raise ValueError(f"Travel matrix must cover every zone pair; missing={sorted(missing)[:3]}, extra={sorted(extra)[:3]}")
        for value in matrix.values():
            if not math.isfinite(value.distance_miles) or not math.isfinite(value.duration_minutes):
                raise ValueError("Travel values must be finite")
            if value.distance_miles < 0 or value.duration_minutes < 0:
                raise ValueError("Travel values must be nonnegative")
            if value.distance_miles > 0 and value.duration_minutes <= 0:
                raise ValueError("Positive distance needs positive travel time")
        self.matrix = dict(matrix)

    def between(self, origin, destination):
        return self.matrix[origin, destination]


@dataclass
class Result:
    summary: dict
    periods: list[dict] = field(default_factory=list)
    hubs: list[dict] = field(default_factory=list)
    vehicles: list[dict] = field(default_factory=list)
    activities: list[dict] = field(default_factory=list)
