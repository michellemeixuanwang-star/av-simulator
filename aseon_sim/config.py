"""Units are minutes, miles, kWh, kW, and illustrative US dollars."""

from dataclasses import asdict, dataclass, fields, replace
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Config:
    step_minutes: int = 10
    idle_window_minutes: float = 10.0
    warmup_minutes: int = 1440
    measurement_minutes: int = 2880
    fleet_size: int = 90
    seed: int = 2026
    battery_kwh: float = 60.0
    initial_soc_min: float = 0.60
    initial_soc_max: float = 0.85
    reserve_soc: float = 0.10
    critical_soc: float = 0.25
    opportunity_soc: float = 0.65
    target_soc: float = 0.80
    drive_kwh_per_mile: float = 0.30
    setup_release_minutes: float = 2.0
    routine_service_minutes: float = 3.0
    central_service_minutes: float = 25.0
    service_every_trips: int = 8
    local_service_share: float = 0.84
    service_mode: str = "deterministic"
    charging_policy: str = "opportunity"
    departure_policy: str = "return_to_origin"
    min_net_topup_kwh: float = 1.0
    power_scale: float = 1.0
    demand_mode: str = "deterministic"
    demand_bin_minutes: int = 5
    daily_requests: float = 3600.0
    demand_scale: float = 1.0
    fare_base: float = 3.0
    fare_per_mile: float = 2.0
    fare_per_minute: float = 0.25
    energy_cost_per_kwh: float = 0.20
    nonenergy_cost_per_mile: float = 0.15
    trace_vehicle_count: int = 3
    record_traces: bool = True

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            default = f.default
            if isinstance(default, bool) and type(value) is not bool:
                raise ValueError(f"{f.name} must be a boolean")
            if isinstance(default, str) and not isinstance(value, str):
                raise ValueError(f"{f.name} must be a string")
            if isinstance(default, (int, float)) and not isinstance(default, bool):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"{f.name} must be numeric")
                if not math.isfinite(value):
                    raise ValueError(f"{f.name} must be finite")
        for name in ("step_minutes", "measurement_minutes", "fleet_size", "service_every_trips", "demand_bin_minutes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("warmup_minutes", "trace_vehicle_count", "seed"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.measurement_minutes % self.step_minutes or self.warmup_minutes % self.step_minutes:
            raise ValueError("Warm-up and measurement durations must be multiples of step_minutes")
        if 1440 % self.demand_bin_minutes:
            raise ValueError("demand_bin_minutes must divide a day")
        for name in ("idle_window_minutes", "battery_kwh", "drive_kwh_per_mile", "power_scale"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("setup_release_minutes", "routine_service_minutes", "central_service_minutes",
                     "min_net_topup_kwh", "daily_requests", "demand_scale", "fare_base",
                     "fare_per_mile", "fare_per_minute", "energy_cost_per_kwh", "nonenergy_cost_per_mile"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if not 0 <= self.initial_soc_min <= self.initial_soc_max <= 1:
            raise ValueError("Initial SoC bounds must satisfy 0 <= min <= max <= 1")
        if not 0 <= self.reserve_soc <= self.critical_soc <= self.opportunity_soc <= self.target_soc <= 1:
            raise ValueError("SoC thresholds must be ordered: reserve <= critical <= opportunity <= target")
        if not 0 <= self.local_service_share <= 1:
            raise ValueError("local_service_share must be in [0, 1]")
        for name, choices in {
            "service_mode": {"deterministic", "sampled"},
            "demand_mode": {"deterministic", "sampled"},
            "charging_policy": {"opportunity", "threshold"},
            "departure_policy": {"return_to_origin", "stay_at_hub"},
        }.items():
            if getattr(self, name) not in choices:
                raise ValueError(f"{name} must be one of {sorted(choices)}")

    @property
    def end_minutes(self):
        return self.warmup_minutes + self.measurement_minutes

    @property
    def reserve_kwh(self):
        return self.battery_kwh * self.reserve_soc

    @property
    def measured_days(self):
        return self.measurement_minutes / 1440

    def with_changes(self, **changes):
        return replace(self, **changes)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text())
        if not isinstance(raw, dict):
            raise ValueError("Configuration JSON must be an object")
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
        return cls(**raw)
