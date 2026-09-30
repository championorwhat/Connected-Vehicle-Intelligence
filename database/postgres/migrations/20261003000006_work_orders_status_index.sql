-- migrate:up transaction:false
-- M15: serves the status-filtered work-order list in index order (keyset seek, no sort)
-- and the dashboard's per-status counts (index-only).
CREATE INDEX CONCURRENTLY IF NOT EXISTS work_orders_tenant_status_created_idx
    ON work_orders (tenant_id, status, created_at DESC, work_order_id DESC);

-- migrate:down transaction:false
DROP INDEX CONCURRENTLY IF EXISTS work_orders_tenant_status_created_idx;
