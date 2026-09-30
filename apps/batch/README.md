# Batch processing

The batch and historical-analytics jobs live where the code they share lives, rather than
in this folder:

| Job | Command | Code |
|---|---|---|
| Training datasets: simulated runs through the production pipeline, stored as Parquet (zstd) | `make ml-data` | `ml/src/prognos_ml/dataset.py` |
| Window features over Parquet with DuckDB; model training and evaluation | `make ml-train` | `ml/src/prognos_ml/features.py`, `pipeline.py` |
| Fleet scoring cycle over ClickHouse history (every 5 minutes) | `make score` | `ml/src/prognos_ml/scorer.py` |
| Detection back-test against ground truth | `make backtest` | `apps/stream-processor/src/prognos_stream/evaluate.py` |
| Rule calibration from back-tests | `make calibrate` | `scripts/build_calibration.py` |
| Emerging-fault radar back-test | `make radar-backtest` | `apps/stream-processor/src/prognos_stream/radar_eval.py` |
| No-data-loss reconciliation: Kafka vs ClickHouse vs PostgreSQL | `make reconcile` | `scripts/reconcile.py` |
| Per-minute vehicle rollups (continuous, in the database) | automatic | `database/telemetry/migrations/` (`vehicle_minute`) |

See [docs/algorithms/algorithms.md](../../docs/algorithms/algorithms.md) and the
[model card](../../docs/ml/model-card.md).
