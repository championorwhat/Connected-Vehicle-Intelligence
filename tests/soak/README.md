# Soak tests

The soak is a long `scripts/load_test.py` run (30 minutes at 100K vehicles), recorded as
evidence rather than run in CI:

    uv run python scripts/load_test.py --name soak-30min --vehicles 100000 --rate 10000 --duration 1800

Results: docs/performance/load-tests.md#soak; evidence: evidence/load-tests/m14-soak-30min.json.
