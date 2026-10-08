# Reproduced synthetic demonstrations

All inputs and dollar amounts are assumptions. These results demonstrate the model, not current SF operations.

Default comparison: 90 vehicles; one warm-up day, two measured days; 10-minute decisions and windows; 24 effective service spaces and 2,400 kW per layout.

| Layout | Completed / offered trips | Empty miles per completed trip | Modeled two-day cost ($) | Start-minus-end battery stock (kWh) |
|---|---:|---:|---:|---:|
| centralized | 6404 / 7204 | 2.094 | 10811.89 | -47.35 |
| distributed | 6587 / 7204 | 1.495 | 11206.69 | 56.10 |
| hybrid | 6617 / 7204 | 1.493 | 11472.26 | -106.52 |

Battery stocks and service backlogs are reported, not forcibly equalized. Negative battery-stock change means the fleet ends with more energy. Interpret throughput and costs together with these boundaries.

## Commands used

```bash
python -m aseon_sim compare --plot --output demo_results/comparison
python -m aseon_sim sweep --parameter idle_window_minutes --values 5 10 15 20 --plot --output demo_results/window_sweep
python -m aseon_sim sweep --step 5 --parameter idle_window_minutes --values 5 10 15 20 --plot --output demo_results/window_sweep_step5
python -m aseon_sim search --layout hybrid --iterations 1 --output demo_results/location_search
python -m aseon_sim run --requests examples/requests_sample.csv --travel examples/travel_sample.csv --warmup-days 0 --days 1 --fleet 2 --output demo_results/csv_example
```

The 10-minute and 5-minute sweeps expose decision-resolution effects. A 15-minute visit under a 10-minute grid delays reassignment until the 20-minute boundary.

The relocation search found no improving one-satellite move from this particular initial hybrid layout under its completed-trips / empty-miles score. This does not establish global optimality. The test suite includes a controlled relocation case with a beneficial move.

Validation: 27 unit/integration-style tests pass; all 29 supplied scenario summaries pass energy, vehicle-time, cost and capacity accounting checks. The core also runs with Python site packages disabled. Plotting requires matplotlib.
