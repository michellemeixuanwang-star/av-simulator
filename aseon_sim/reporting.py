"""Portable JSON/CSV outputs; plotting is an optional dependency."""

import csv
import json
from pathlib import Path

LAYOUT_COLORS = {"centralized": "#617c95", "distributed": "#288f87", "hybrid": "#bb8041"}


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else ["no_records"])
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_result(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(result.summary, indent=2, allow_nan=False) + "\n")
    for name in ("periods", "hubs", "vehicles", "activities"):
        write_csv(directory / f"{name}.csv", getattr(result, name))


def comparison_row(result, **labels):
    s = result.summary
    keys = ("layout", "site_count", "service_spaces", "effective_network_power_kw",
            "offered_trips", "accepted_trips", "completed_trips", "accepted_unfinished_trips",
            "served_fraction_completed", "gross_revenue", "empty_miles",
            "empty_miles_per_completed_trip", "opportunity_added_kwh", "estimated_total_cost",
            "energy_inventory_adjusted_cost", "energy_depletion_kwh",
            "capacity_blocked_vehicle_periods", "completed_local_services", "completed_central_services")
    row = {key: s[key] for key in keys}
    row.update(labels)
    row["pending_services_end"] = s["end_state"]["pending_local"] + s["end_state"]["pending_central"]
    return row


def plot_comparison(rows, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Optional plots require matplotlib; the simulator itself needs only Python") from exc
    labels = [row["layout"] for row in rows]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    metrics = [("completed_trips", "Completed trips"),
               ("empty_miles_per_completed_trip", "Empty miles / completed trip"),
               ("estimated_total_cost", "Modeled cost ($)")]
    colors = [LAYOUT_COLORS.get(label, "#617c95") for label in labels]
    for ax, (key, title) in zip(axes, metrics):
        ax.bar(labels, [row[key] or 0 for row in rows], color=colors)
        ax.set_title(title)
        ax.tick_params(axis="x", rotation=15)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Synthetic demonstration — scenario outputs, not SF estimates", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_sweep(rows, parameter, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for layout in sorted({r["layout"] for r in rows}):
        subset = sorted((r for r in rows if r["layout"] == layout), key=lambda r: r["value"])
        x = [r["value"] for r in subset]
        for ax, key in zip(axes, ("completed_trips", "empty_miles_per_completed_trip")):
            ax.plot(x, [r[key] for r in subset], marker="o", label=layout, color=LAYOUT_COLORS.get(layout))
            ax.set_xlabel(parameter)
            ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Completed trips")
    axes[1].set_ylabel("Empty miles / completed trip")
    axes[0].legend()
    fig.suptitle("Synthetic sensitivity experiment")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
