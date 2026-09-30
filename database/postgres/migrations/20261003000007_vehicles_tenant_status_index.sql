-- migrate:up transaction:false
-- M15: vehicle counts per tenant and status were a sequential scan of every tenant's fleet.
CREATE INDEX CONCURRENTLY IF NOT EXISTS vehicles_tenant_status_idx
    ON vehicles (tenant_id, status);

-- migrate:down transaction:false
DROP INDEX CONCURRENTLY IF EXISTS vehicles_tenant_status_idx;
