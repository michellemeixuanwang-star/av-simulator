import csv
import json
from pathlib import Path
import random
import tempfile
import unittest

from aseon_sim.config import Config
from aseon_sim.data import _poisson, generate_requests, load_requests, load_router, load_zones, make_toy_router, write_requests
from aseon_sim.domain import Hub, Request, Router, Travel, Zone


class InputTests(unittest.TestCase):
    def test_invalid_configuration_is_rejected(self):
        for change in ({"fleet_size": 1.5}, {"step_minutes": 0}, {"local_service_share": 1.1},
                       {"reserve_soc": 0.9}, {"power_scale": float("nan")},
                       {"battery_kwh": "60"}, {"record_traces": "true"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Config(**change)

    def test_unknown_configuration_key_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"idle_window_minute": 15}))
            with self.assertRaises(ValueError):
                Config.load(path)

    def test_incomplete_routing_matrix_is_rejected(self):
        with self.assertRaises(ValueError):
            Router(["a", "b"], {("a", "b"): Travel(1, 3)})

    def test_impossible_instant_positive_distance_is_rejected(self):
        with self.assertRaises(ValueError):
            Router(["a"], {("a", "a"): Travel(1, 0)})

    def test_request_csv_roundtrip_and_duplicate_id_guard(self):
        zones = [Zone("a", "A", 0, 0), Zone("b", "B", 1, 1)]
        router = make_toy_router(zones)
        cfg = Config()
        original = [Request("r", 5, "a", "b", 2, 7, 12)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.csv"
            write_requests(path, original)
            self.assertEqual(load_requests(path, router, cfg), original)
            write_requests(path, original + original)
            with self.assertRaises(ValueError):
                load_requests(path, router, cfg)

    def test_demand_is_reproducible_and_independent_of_control_grid(self):
        zones = [Zone("a", "A", 0, 0), Zone("b", "B", 1, 1)]
        router = make_toy_router(zones)
        cfg = Config(warmup_minutes=0, measurement_minutes=60, daily_requests=1000)
        self.assertEqual(generate_requests(zones, router, cfg), generate_requests(zones, router, cfg.with_changes(step_minutes=5)))
        sampled = cfg.with_changes(demand_mode="sampled")
        self.assertEqual(generate_requests(zones, router, sampled), generate_requests(zones, router, sampled))

    def test_high_rate_poisson_does_not_underflow_to_zero(self):
        self.assertGreater(_poisson(random.Random(42), 1000), 800)


if __name__ == "__main__":
    unittest.main()
