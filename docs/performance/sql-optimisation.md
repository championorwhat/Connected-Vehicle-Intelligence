# SQL optimisation (M15)

Which queries were slow was **measured, not guessed**. The three slowest were then fixed
and measured again on identical data. Measured on a 4-vCPU Linux container running
PostgreSQL 17 in Docker, **not the target Mac**.

## Method

1. **Realistic data.**
   - Seed of 100,000 vehicles, plus a year of operational history from
     [`scripts/sql_history.py`](../../scripts/sql_history.py).
   - 2,000,000 alerts, about 1.5 % of them active.
   - 150,000 work orders, 3,000 of them active.
   - Deterministic (`setseed`), so "before" and "after" use the same rows. The seed alone
     has no history, so every query looks fast on it.
2. **Real traffic.** [`scripts/sql_workload.py`](../../scripts/sql_workload.py) drives the
   running API with a fleet manager of each of the 20 tenants, for 5 rounds: 3,055
   requests.
   - Dashboard summary, at-risk list.
   - Alert and work-order lists with keyset paging and filters.
   - Vehicle lists and details.
   - Plus 3 planner cycles.
   - `pg_stat_statements` then ranks statements by total time.
3. **Plans.** [`scripts/sql_explain.py`](../../scripts/sql_explain.py) runs
   `EXPLAIN (ANALYZE, BUFFERS)` on each chosen query exactly as production runs it:
   - API queries as the row-level-security tenant role, for all 20 tenants.
   - The planner as the owner role.
   - As a prepared statement under both **custom and generic plans**. psycopg prepares a
     statement after 5 executions, and PostgreSQL may then switch to a generic plan that
     cannot see the parameter values. The M15 regression in Q3 only showed up that way.

Evidence: [summary](../../evidence/performance/m15-summary.json),
workload [before](../../evidence/performance/m15-workload-before.json) and
[after](../../evidence/performance/m15-workload-after.json),
plans [before](../../evidence/performance/m15-before-plans.txt) and
[after](../../evidence/performance/m15-after-plans.txt).

## Result

The same 3,055-request workload: **195.6 s → 23.8 s** of API time.

| # | Query | Before | After | Change |
|---|---|---|---|---|
| Q1 | Dashboard fleet summary (`GET /v1/fleet/summary`) | 1,663 ms mean, 5.2 s max (workload) | **0.97 ms** mean | ~1,700× |
| Q3 | Work orders by status, next page | 58 ms mean, 145 ms max | **0.17 ms** mean, 0.34 ms max | ~340× |
| Q2 | Planner input: active alerts without a work order | 309 ms (workload) | 473 ms (workload) | **Not improved**; see below |

### Q1: dashboard fleet summary

**Before.**
- Eight correlated `count(*)` subqueries, three of them over `alerts`.
- To count about 300 active alerts, each one visited all ~73,000 of the tenant's alerts:
  a parallel sequential scan or a bitmap heap scan of about 35,000 pages. That was
  repeated three times per call.
- The vehicle counts were full scans of all 100,000 vehicles.
- The planner cannot skip rows it has no index for.

**After.**
- One pass per table, using `FILTER` aggregates, and only over rows that can be counted
  (`status IN ('open', 'acknowledged')`, active work orders).
- Three indexes, so every pass is an **index-only scan**:
  - `alerts_active_idx`: a *partial* index holding only active alerts, 2.7 MB against
    2M rows.
  - `vehicles_tenant_status_idx`.
  - `work_orders_tenant_status_created_idx`.
- Median under a generic plan: 590 ms → 1.41 ms.
- **Results identical for all 20 tenants**, checked against the old SQL on the same data.
  `test_query_optimisations.py` compares it with plain counts over every status and
  severity.

### Q3: work-order list filtered by status

**Before.**
- The query selects `work_order_id::text AS work_order_id` and orders by `work_order_id`.
- In PostgreSQL, an `ORDER BY` name binds to the **output alias** first. So the list
  was sorted by the text cast, which no index can supply.
- Every page sorted its matches. With a generic plan, the keyset seek walked the tenant's
  older history row by row to find the next few "proposed" orders.
- This was invisible with literal values: the planner could then combine two bitmap
  indexes.

**After.**
- `ORDER BY work_orders.created_at DESC, work_orders.work_order_id DESC`
  (`LIST_ORDER` in `routers/work_orders.py`), plus the index
  `(tenant_id, status, created_at DESC, work_order_id DESC)`.
- The page is now a single index range scan with no sort.
- The test walks every page and requires each row exactly once, in order, including
  rows with equal `created_at`.

### Q2: planner input (not improved)

The planner reads active alerts that have no active work order, which is **22,500 rows
per cycle** on this data. The SQL was not changed; `alerts_active_idx` is simply
available to it.

| Measurement | Without the index | With the index |
|---|---|---|
| Pages read | ~84,000 (parallel full scan) | ~22,000 (bitmap heap scan) |
| Warm, table in cache | 150–160 ms | 230–245 ms |
| After a PostgreSQL restart (empty shared buffers, OS cache warm) | 156–297 ms | 277–292 ms |
| Workload (3 cycles, including the first) | 309 ms | 473 ms |

- The index cuts I/O 4×.
- But a single-threaded scan of scattered heap pages is slower than a parallel scan of
  a cached table.
- An earlier warm run in another cache state measured 466 → 187 ms (`m15-before.json` /
  `m15-after.json`).
- So the result depends on memory, and **no improvement is claimed**.
- The cost is bounded: one query per 60-second planner cycle, in a background job.
- The real fix is **incremental planning**: consider only alerts opened since the last
  cycle, and those whose capacity situation changed, instead of all 22,500 every minute.
  This is recorded as future work, not done in M15.

### Not changed

The planner's work-order `INSERT`s ranked high by total time (6,987 calls, 0.35 ms
each). That is the cost of writing the proposals; there is nothing to optimise per row.

## Safe rollout

- The indexes are created with `CREATE INDEX CONCURRENTLY`, one per migration marked
  `transaction:false`, so building them on a live 2M-row table does not block the sink's
  writes.
- Build time here: 0.7–1.1 s for `alerts_active_idx`.
- The down migrations drop them concurrently. They were exercised for real during
  measurement: the rollback ran before the "before" workload.

## Pitfalls met while measuring
- `docker compose up api` and `docker compose run planner` also start their
  `migrate-postgres` dependency. That silently **re-applied the indexes** to a "before"
  run, so that run was discarded. Use `--no-deps`; the workload script now does.
- `EXPLAIN` with client-side parameters shows only the custom plan. Production also uses
  generic plans, hence the explicit `PREPARE` / `EXPLAIN EXECUTE` under both modes.
