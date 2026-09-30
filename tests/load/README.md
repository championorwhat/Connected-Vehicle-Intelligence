# Load tests

Load tests drive the full compose stack and are recorded as evidence, not run in CI (a
100K-vehicle run needs all the CPUs of the machine for minutes):

- `scripts/load_test.py`: fleet size, event rate, burst; samples Prometheus and `docker stats`.
- `scripts/api_load.py`: concurrent dashboard users against the API.
- `scripts/ws_latency.py`: vehicle event → alert on the dashboard WebSocket.

Results: docs/performance/load-tests.md; evidence: evidence/load-tests/.
