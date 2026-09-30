-- migrate:up
-- Indexes for the API's keyset pagination: each list is filtered by tenant and ordered
-- newest first, so one composite index serves the seek (row-value comparison) and the
-- ORDER BY without a sort, O(log n + page) per request.
CREATE INDEX alerts_tenant_detected_idx
    ON alerts (tenant_id, detected_at DESC, alert_id DESC);
CREATE INDEX work_orders_tenant_created_idx
    ON work_orders (tenant_id, created_at DESC, work_order_id DESC);
-- Vehicles list: (tenant_id, vehicle_id) is already unique (composite FK target).

-- migrate:down
DROP INDEX IF EXISTS work_orders_tenant_created_idx;
DROP INDEX IF EXISTS alerts_tenant_detected_idx;
