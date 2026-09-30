-- migrate:up
-- Row-level security: a second, database-enforced tenant barrier (M12, ADR-010).
--
-- The API already filters every query by the caller's tenant. RLS makes a forgotten
-- `WHERE tenant_id = ...` return nothing instead of another tenant's rows. The API
-- runs tenant requests as the NOLOGIN role `prognos_tenant` (SET ROLE) with the
-- caller's tenant in `app.tenant_id`. Pipeline services (sink, planner) keep using the
-- owner role, which is not subject to these policies: they write for every tenant.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'prognos_tenant') THEN
        CREATE ROLE prognos_tenant NOLOGIN NOINHERIT;
    END IF;
END
$$;
GRANT prognos_tenant TO CURRENT_USER;  -- lets the API's login role SET ROLE to it

-- The tenant of the current request; NULL (sees nothing) when unset.
CREATE FUNCTION app_tenant() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

GRANT USAGE ON SCHEMA public TO prognos_tenant;
GRANT EXECUTE ON FUNCTION app_tenant() TO prognos_tenant;
-- Least privilege: read everything the policies allow; write only what the API writes.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO prognos_tenant;
REVOKE SELECT ON users FROM prognos_tenant;
GRANT SELECT (user_id, tenant_id, email, display_name, is_active, created_at)
    ON users TO prognos_tenant;                               -- never password hashes
GRANT UPDATE (status, acknowledged_at, acknowledged_by) ON alerts TO prognos_tenant;
GRANT INSERT, UPDATE ON work_orders TO prognos_tenant;
GRANT INSERT ON erasure_requests, audit_log TO prognos_tenant;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO prognos_tenant;

-- One policy per tenant-owned table: rows of the current tenant only, for reads and writes.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['subscriptions', 'fleets', 'workshops', 'vehicles', 'drivers',
                             'driver_assignments', 'users', 'cost_parameters', 'alerts',
                             'work_orders', 'erasure_requests']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I TO prognos_tenant '
            'USING (tenant_id = app_tenant()) WITH CHECK (tenant_id = app_tenant())', t);
    END LOOP;
END
$$;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants TO prognos_tenant
    USING (tenant_id = app_tenant());

-- Audit log: a tenant request may append its own tenant's entries and read them.
-- (Partitions inherit the parent's policies for queries through the parent.)
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON audit_log FOR SELECT TO prognos_tenant
    USING (tenant_id = app_tenant());
CREATE POLICY tenant_append ON audit_log FOR INSERT TO prognos_tenant
    WITH CHECK (tenant_id = app_tenant());

-- migrate:down
DROP POLICY IF EXISTS tenant_append ON audit_log;
DROP POLICY IF EXISTS tenant_read ON audit_log;
ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenants;
ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['subscriptions', 'fleets', 'workshops', 'vehicles', 'drivers',
                             'driver_assignments', 'users', 'cost_parameters', 'alerts',
                             'work_orders', 'erasure_requests']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
    END LOOP;
END
$$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM prognos_tenant;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM prognos_tenant;
REVOKE ALL ON SCHEMA public FROM prognos_tenant;
DROP FUNCTION IF EXISTS app_tenant();
-- The role is cluster-wide and may serve other databases; it is left in place.
