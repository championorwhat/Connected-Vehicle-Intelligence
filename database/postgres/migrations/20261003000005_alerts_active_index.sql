-- migrate:up transaction:false
-- M15 (docs/performance/sql-optimisation.md): only ~1.5 % of alerts are active, yet the
-- dashboard counts and the planner scanned every alert. A partial index holds exactly the
-- active ones; INCLUDE lets the planner's anti-join read vehicle and failure mode from it.
-- CONCURRENTLY: no write lock on a large live table (hence one statement, no transaction).
CREATE INDEX CONCURRENTLY IF NOT EXISTS alerts_active_idx
    ON alerts (tenant_id, status, severity) INCLUDE (vehicle_id, failure_mode)
    WHERE status IN ('open', 'acknowledged');

-- migrate:down transaction:false
DROP INDEX CONCURRENTLY IF EXISTS alerts_active_idx;
