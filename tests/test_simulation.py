import unittest

from aseon_sim.config import Config
from aseon_sim.domain import Hub, Request, Router, Travel, Vehicle
from aseon_sim.experiments import compare, greedy_relocate
from aseon_sim.policy import make_visit, service_category
from aseon_sim.simulator import Simulator


def router(minutes=3, miles=2):
    return Router(["a", "b"], {("a", "a"): Travel(0, 0), ("b", "b"): Travel(0, 0),
                                ("a", "b"): Travel(miles, minutes), ("b", "a"): Travel(miles, minutes)})


def config(**changes):
    return Config(warmup_minutes=0, measurement_minutes=60, fleet_size=1,
                  charging_policy="threshold", service_every_trips=100).with_changes(**changes)


def request(identifier, t, minutes=5, miles=1, origin="a", destination="a"):
    return Request(identifier, t, origin, destination, miles, minutes, 20)


class AccountingTests(unittest.TestCase):
    def test_hand_calculated_partial_charge_and_round_trip(self):
        cfg = config(measurement_minutes=10, charging_policy="opportunity", min_net_topup_kwh=0.1)
        hub = Hub("depot", "b", "central", 1, power_kw=60)
        result = Simulator(cfg, router(), [hub], [], initial_vehicles=[Vehicle(0, "a", 20)]).run()
        s = result.summary
        # 10 - 3 - 3 - 2 = 2 minutes, 60 kW -> 2 kWh; 4 miles -> 1.2 kWh.
        self.assertAlmostEqual(s["added_kwh"], 2)
        self.assertAlmostEqual(s["empty_service_miles"], 4)
        self.assertAlmostEqual(s["end_state"]["energy_kwh"], 20.8)
        self.assertAlmostEqual(s["vehicle_hours"]["central_hub"], 4 / 60)
        self.assertAlmostEqual(s["vehicle_hours"]["empty_service"], 6 / 60)
        self.assertAlmostEqual(s["energy_balance_residual_kwh"], 0)
        self.assertAlmostEqual(s["vehicle_time_residual_minutes"], 0)

    def test_power_is_clipped_to_energy_target(self):
        cfg = config(step_minutes=5, measurement_minutes=15, idle_window_minutes=15,
                     charging_policy="opportunity", opportunity_soc=0.8, min_net_topup_kwh=0.1)
        hub = Hub("depot", "b", "central", 1)
        result = Simulator(cfg, router(minutes=1, miles=1), [hub], [], initial_vehicles=[Vehicle(0, "a", 47)]).run()
        # Arrival 46.7; target 48; return consumes 0.3.
        self.assertAlmostEqual(result.summary["added_kwh"], 1.3)
        self.assertAlmostEqual(result.summary["end_state"]["energy_kwh"], 47.7)
        self.assertTrue(all(r["energy_kwh"] <= 60 for r in result.vehicles))

    def test_stay_at_hub_changes_anchor_and_next_pickup(self):
        cfg = config(measurement_minutes=20, charging_policy="opportunity",
                     departure_policy="stay_at_hub", min_net_topup_kwh=0.1)
        hub = Hub("depot", "b", "central", 1, power_kw=60)
        result = Simulator(cfg, router(), [hub], [request("next", 10)],
                           initial_vehicles=[Vehicle(0, "a", 20)]).run()
        self.assertEqual(result.activities[0]["destination"], "b")
        self.assertEqual(result.activities[1]["origin"], "b")
        self.assertAlmostEqual(result.summary["added_kwh"], 5)
        self.assertAlmostEqual(result.summary["empty_pickup_miles"], 2)
        self.assertEqual(result.summary["completed_trips"], 1)

    def test_extended_charge_is_not_credited_in_advance(self):
        cfg = config(measurement_minutes=10)
        hub = Hub("depot", "b", "central", 1, power_kw=60)
        result = Simulator(cfg, router(), [hub], [], initial_vehicles=[Vehicle(0, "a", 12)]).run()
        # First three minutes drive, next two setup, only five minutes charge.
        self.assertAlmostEqual(result.summary["added_kwh"], 5)
        self.assertAlmostEqual(result.summary["end_state"]["energy_kwh"], 16.4)
        self.assertEqual(result.summary["end_state"]["active_visits"], 1)
        self.assertGreater(result.activities[0]["planned_added_kwh"], result.summary["added_kwh"])

    def test_warmup_boundary_clips_an_ongoing_trip(self):
        cfg = config(warmup_minutes=10, measurement_minutes=10)
        result = Simulator(cfg, router(), [Hub("d", "b", "central", 1)],
                           [request("long", 0, minutes=30, miles=6)],
                           initial_vehicles=[Vehicle(0, "a", 40)]).run()
        s = result.summary
        self.assertAlmostEqual(s["start_state"]["energy_kwh"], 39.4)
        self.assertAlmostEqual(s["end_state"]["energy_kwh"], 38.8)
        self.assertAlmostEqual(s["passenger_miles"], 2)
        self.assertEqual(s["offered_trips"], 0)
        self.assertEqual(s["completed_trips"], 0)
        self.assertAlmostEqual(s["energy_balance_residual_kwh"], 0)

    def test_unfinished_trip_is_not_counted_as_completed_revenue(self):
        cfg = config(measurement_minutes=10)
        s = Simulator(cfg, router(), [Hub("d", "b", "central", 1)],
                      [request("long", 0, minutes=30)], initial_vehicles=[Vehicle(0, "a", 60)]).run().summary
        self.assertEqual(s["accepted_trips"], 1)
        self.assertEqual(s["completed_trips"], 0)
        self.assertEqual(s["accepted_unfinished_trips"], 1)
        self.assertEqual(s["gross_revenue"], 0)
        self.assertIsNone(s["empty_miles_per_completed_trip"])

    def test_zero_demand_has_defined_accounting_and_null_ratios(self):
        cfg = config(measurement_minutes=60)
        s = Simulator(cfg, router(), [Hub("d", "b", "central", 1, fixed_cost_per_day=120)], [],
                      initial_vehicles=[Vehicle(0, "a", 60)]).run().summary
        self.assertIsNone(s["served_fraction_completed"])
        self.assertAlmostEqual(s["vehicle_hours"]["parked"], 1)
        self.assertAlmostEqual(s["estimated_total_cost"], 140 / 24)


class CapacityAndServiceTests(unittest.TestCase):
    def test_capacity_is_reserved_across_all_charge_periods(self):
        cfg = config(fleet_size=2)
        result = Simulator(cfg, router(), [Hub("d", "b", "central", 1, power_kw=60)], [],
                           initial_vehicles=[Vehicle(0, "a", 12), Vehicle(1, "a", 12)]).run()
        self.assertTrue(all(r["reserved_spaces"] <= 1 for r in result.hubs))
        self.assertGreaterEqual(result.summary["capacity_blocked_vehicle_periods"], 5)
        starts = [r["start_minutes"] for r in result.activities]
        self.assertEqual(starts[:2], [0, 50])

    def test_busy_vehicle_cannot_be_reused_before_next_grid_boundary(self):
        cfg = config()
        requests = [request(f"r{t}", t, minutes=25) for t in (0, 10, 20, 30)]
        s = Simulator(cfg, router(), [Hub("d", "b", "central", 2)], requests,
                      initial_vehicles=[Vehicle(0, "a", 60)]).run().summary
        self.assertEqual(s["accepted_trips"], 2)
        self.assertEqual(s["completed_trips"], 2)
        self.assertEqual(s["unserved_no_available_vehicle"], 2)

    def test_depot_only_service_blocks_passenger_assignment(self):
        cfg = config(local_service_share=0, service_every_trips=1)
        requests = [request(f"r{t}", t) for t in (0, 10, 40, 50)]
        s = Simulator(cfg, router(), [Hub("d", "b", "central", 1)], requests,
                      initial_vehicles=[Vehicle(0, "a", 60)]).run().summary
        self.assertEqual(s["completed_trips"], 2)
        self.assertEqual(s["completed_central_services"], 1)
        self.assertEqual(s["end_state"]["pending_central"], 1)

    def test_deferred_service_category_persists(self):
        cfg = config(fleet_size=2, measurement_minutes=20, idle_window_minutes=10)
        vehicles = [Vehicle(0, "a", 20, pending_service="central"),
                    Vehicle(1, "a", 40, pending_service="local")]
        sim = Simulator(cfg, router(), [Hub("d", "b", "central", 1)], [], initial_vehicles=vehicles)
        sim.run()
        self.assertEqual(sim.vehicles[1].pending_service, "local")
        self.assertIsNone(sim.vehicles[1].activity)

    def test_charging_only_satellite_does_not_clear_cleaning(self):
        cfg = config()
        hubs = [Hub("central", "b", "central", 1),
                Hub("charge_only", "a", "satellite", 1, supports_routine=False)]
        result = Simulator(cfg, router(), hubs, [], initial_vehicles=[Vehicle(0, "a", 40, pending_service="local")]).run()
        self.assertEqual(result.activities[0]["hub_id"], "central")
        self.assertEqual(result.summary["completed_local_services"], 1)

    def test_unreachable_charger_does_not_create_energy_or_teleportation(self):
        cfg = config(measurement_minutes=10)
        s = Simulator(cfg, router(), [Hub("d", "b", "central", 1)], [request("r", 0)],
                      initial_vehicles=[Vehicle(0, "a", 2)]).run().summary
        self.assertEqual(s["accepted_trips"], 0)
        self.assertEqual(s["added_kwh"], 0)
        self.assertEqual(s["end_state"]["energy_kwh"], 2)
        self.assertEqual(s["no_feasible_visit_vehicle_periods"], 1)

    def test_site_budget_limits_constant_per_space_power(self):
        hub = Hub("d", "b", "central", 2, power_kw=200, site_power_kw=100)
        self.assertEqual(hub.effective_power(), 50)
        cfg = config(measurement_minutes=10, charging_policy="opportunity", min_net_topup_kwh=0.1)
        plan = make_visit(Vehicle(0, "a", 20), hub, 0, router(), cfg, short=True)
        self.assertAlmostEqual(plan.added_kwh, 50 * 2 / 60)

    def test_service_extremes_and_order_independent_identity(self):
        for share, expected in ((0, "central"), (1, "local")):
            cfg = config(local_service_share=share)
            self.assertEqual({service_category(cfg, 3, k) for k in range(1, 101)}, {expected})
        cfg = config(local_service_share=0.84)
        categories = [service_category(cfg, 7, k) for k in range(1, 101)]
        self.assertLessEqual(abs(categories.count("local") - 84), 1)
        self.assertEqual(categories, [service_category(cfg, 7, k) for k in range(1, 101)])

    def test_sampled_service_draws_are_independent_of_other_vehicle_calls(self):
        cfg = config(service_mode="sampled", local_service_share=0.84)
        first = [service_category(cfg, 7, k) for k in range(1, 101)]
        for k in range(1, 101):
            service_category(cfg, 99, k)
        second = [service_category(cfg, 7, k) for k in range(1, 101)]
        self.assertEqual(first, second)


class ExperimentTests(unittest.TestCase):
    def test_layout_order_does_not_change_results(self):
        cfg = config(fleet_size=2, record_traces=False)
        layouts = {"a": [Hub("da", "a", "central", 1)], "b": [Hub("db", "b", "central", 2)]}
        requests = [request(f"r{t}", t) for t in (0, 10, 20, 30)]
        forward = compare(cfg, router(), layouts, requests)
        reverse = compare(cfg, router(), dict(reversed(list(layouts.items()))), requests)
        for name in layouts:
            self.assertEqual(forward[name].summary, reverse[name].summary)

    def test_no_satellite_search_preserves_baseline(self):
        cfg = config(record_traces=False)
        hubs = [Hub("d", "b", "central", 1)]
        selected, result, history = greedy_relocate(cfg, router(), hubs, [request("r", 0)])
        self.assertEqual(selected, hubs)
        self.assertEqual(history, [])
        self.assertEqual(result.summary["service_spaces"], 1)

    def test_location_search_moves_satellite_without_changing_resources(self):
        zones = ["a", "b", "c"]
        matrix = {(o, d): Travel(0, 0) if o == d else Travel(5 if "b" in (o, d) else 8,
                                                           15 if "b" in (o, d) else 24)
                  for o in zones for d in zones}
        routing = Router(zones, matrix)
        cfg = config(initial_soc_min=0.2, initial_soc_max=0.2, record_traces=False)
        hubs = [Hub("depot", "c", "central", 1), Hub("satellite", "b", "satellite", 1)]
        selected, result, history = greedy_relocate(cfg, routing, hubs, [], iterations=1)
        self.assertEqual(selected[0], hubs[0])
        self.assertEqual(selected[1].zone_id, "a")
        self.assertEqual(result.summary["service_spaces"], 2)
        self.assertEqual(result.summary["effective_network_power_kw"], 200)
        self.assertEqual(len(history), 1)
        self.assertAlmostEqual(result.summary["empty_miles"], 0)

    def test_simulator_cannot_accidentally_be_run_twice(self):
        sim = Simulator(config(), router(), [Hub("d", "b", "central", 1)], [])
        sim.run()
        with self.assertRaises(RuntimeError):
            sim.run()


if __name__ == "__main__":
    unittest.main()
