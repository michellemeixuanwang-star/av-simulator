"""Run from the repository root: python -m aseon_sim compare --plot."""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

from . import __version__
from .config import Config
from .data import generate_requests, load_layouts, load_requests, load_router, load_zones, make_toy_router, write_requests
from .experiments import compare, greedy_relocate, sweep
from .reporting import comparison_row, plot_comparison, plot_sweep, write_csv, write_result
from .simulator import Simulator


def parser():
    p = argparse.ArgumentParser(description="First-order distributed fleet servicing simulator")
    p.add_argument("command", choices=["run", "compare", "sweep", "search"])
    p.add_argument("--config", default="examples/config.json")
    p.add_argument("--zones", default="examples/zones.csv")
    p.add_argument("--layouts", default="examples/layouts.json")
    p.add_argument("--travel", help="Complete routing CSV; otherwise use the fictional planar model")
    p.add_argument("--requests", help="Normalized request CSV; otherwise generate synthetic demand")
    p.add_argument("--layout", default="hybrid", help="Layout for run/search")
    p.add_argument("--output", default="results/demo")
    p.add_argument("--seed", type=int)
    p.add_argument("--days", type=int, help="Number of measured days")
    p.add_argument("--warmup-days", type=int)
    p.add_argument("--fleet", type=int)
    p.add_argument("--step", type=int)
    p.add_argument("--window", type=float)
    p.add_argument("--demand-mode", choices=["deterministic", "sampled"])
    p.add_argument("--parameter", default="idle_window_minutes")
    p.add_argument("--values", nargs="+", type=float, default=[5, 10, 15, 20])
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--plot", action="store_true", help="Optional matplotlib output")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        cfg = Config.load(args.config)
        changes = {}
        for arg, key in (("seed", "seed"), ("fleet", "fleet_size"), ("step", "step_minutes"),
                         ("window", "idle_window_minutes"), ("demand_mode", "demand_mode")):
            if getattr(args, arg) is not None:
                changes[key] = getattr(args, arg)
        if args.days is not None:
            changes["measurement_minutes"] = args.days * 1440
        if args.warmup_days is not None:
            changes["warmup_minutes"] = args.warmup_days * 1440
        cfg = cfg.with_changes(**changes)
        zones = load_zones(args.zones)
        router = load_router(args.travel, [z.zone_id for z in zones]) if args.travel else make_toy_router(zones)
        layouts = load_layouts(args.layouts, router.zones)
        requests = load_requests(args.requests, router, cfg) if args.requests else generate_requests(zones, router, cfg)
        source = "normalized_csv_unvalidated" if args.requests else "synthetic_toy"
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        write_requests(out / "input_requests.csv", requests)
        inputs = [args.config, args.zones, args.layouts] + [x for x in (args.travel, args.requests) if x]
        manifest = {"model_version": __version__, "python_version": sys.version.split()[0],
                    "data_source": source, "routing": "provided_matrix" if args.travel else "fictional_planar",
                    "config": cfg.to_dict(), "command": args.command,
                    "parameter": args.parameter if args.command == "sweep" else None,
                    "values": args.values if args.command == "sweep" else None,
                    "input_sha256": {str(x): hashlib.sha256(Path(x).read_bytes()).hexdigest() for x in inputs}}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if args.command in {"run", "search"} and args.layout not in layouts:
            raise ValueError(f"Unknown layout; available: {sorted(layouts)}")
        rows = []
        if args.command == "run":
            result = Simulator(cfg, router, layouts[args.layout], requests, args.layout, data_source=source).run()
            write_result(result, out / args.layout)
            rows.append(comparison_row(result))
        elif args.command == "compare":
            for name, result in compare(cfg, router, layouts, requests, source).items():
                write_result(result, out / name)
                rows.append(comparison_row(result))
        elif args.command == "sweep":
            for value, results in sweep(cfg, router, layouts, requests, args.parameter, args.values, source):
                for name, result in results.items():
                    write_result(result, out / f"{args.parameter}_{value:g}" / name)
                    rows.append(comparison_row(result, parameter=args.parameter, value=value))
        else:
            hubs, result, history = greedy_relocate(cfg, router, layouts[args.layout], requests, args.iterations, source)
            write_result(result, out / "searched_layout")
            (out / "searched_layout.json").write_text(json.dumps({"searched_layout": [asdict(h) for h in hubs]}, indent=2) + "\n")
            write_csv(out / "search_history.csv", history)
            rows.append(comparison_row(result))
        write_csv(out / "comparison.csv", rows)
        if args.plot and args.command == "compare":
            plot_comparison(rows, out / "comparison.png")
        elif args.plot and args.command == "sweep":
            plot_sweep(rows, args.parameter, out / "sensitivity.png")
        for row in rows:
            value = f" {row['parameter']}={row['value']:g}" if "parameter" in row else ""
            per_trip = row["empty_miles_per_completed_trip"]
            per_trip_text = "n/a" if per_trip is None else f"{per_trip:.3f}"
            print(f"{row['layout']}{value}: completed={row['completed_trips']}/{row['offered_trips']}, "
                  f"empty_miles/trip={per_trip_text}, cost=${row['estimated_total_cost']:.2f}, "
                  f"energy_depletion={row['energy_depletion_kwh']:.2f} kWh")
        print(f"Outputs: {out.resolve()}")
        print("Inputs are synthetic or user-normalized; these are scenario results, not calibrated SF predictions.")
        return 0
    except (ValueError, KeyError, OSError, RuntimeError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
