-- migrate:up
-- =============================================================================
-- Prognos relational core (3NF). See docs/database/data-model.md.
--
-- Deliberate denormalisation: tenant_id is repeated on every tenant-owned row
-- (e.g. vehicles.tenant_id is derivable via fleets). This enables row-level
-- security and tenant-leading indexes without joins. Consistency is enforced by
-- composite foreign keys (child.parent_id, child.tenant_id) -> parent(id, tenant_id),
-- so a row can never point at another tenant's parent.
--
-- Only integrity indexes (PK/UNIQUE/FK/business rules) are created here. Query-
-- shape indexes are added in M15 with EXPLAIN ANALYZE before/after evidence.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- ---------------------------------------------------------------------------
-- Reference data (global, not tenant-owned)
-- ---------------------------------------------------------------------------
CREATE TABLE oems (
    oem_code                 text PRIMARY KEY CHECK (oem_code ~ '^[A-Z]{3,10}$'),
    display_name             text NOT NULL UNIQUE,
    payload_format           text NOT NULL CHECK (payload_format IN
                               ('flat_json_metric', 'nested_json_imperial', 'signal_list_json')),
    vin_wmi                  char(3) NOT NULL UNIQUE CHECK (vin_wmi ~ '^[A-HJ-NPR-Z0-9]{3}$'),
    requires_vin_check_digit boolean NOT NULL
);

CREATE TABLE vehicle_models (
    model_code    char(5) PRIMARY KEY CHECK (model_code ~ '^[A-HJ-NPR-Z0-9]{5}$'),
    oem_code      text NOT NULL REFERENCES oems (oem_code),
    display_name  text NOT NULL,
    powertrain    text NOT NULL CHECK (powertrain IN ('ICE', 'HEV', 'BEV')),
    vehicle_class text NOT NULL CHECK (vehicle_class IN ('car', 'van', 'light_truck', 'truck')),
    UNIQUE (oem_code, display_name)
);
CREATE INDEX vehicle_models_oem_idx ON vehicle_models (oem_code);

CREATE TABLE failure_modes (
    failure_mode text PRIMARY KEY CHECK (failure_mode ~ '^[A-Z][A-Z0-9_]+$'),
    display_name text NOT NULL UNIQUE,
    component    text NOT NULL
);

-- Which powertrains a failure mode can occur on (M:N; keeps failure_modes in 1NF).
CREATE TABLE failure_mode_powertrains (
    failure_mode text NOT NULL REFERENCES failure_modes (failure_mode) ON DELETE CASCADE,
    powertrain   text NOT NULL CHECK (powertrain IN ('ICE', 'HEV', 'BEV')),
    PRIMARY KEY (failure_mode, powertrain)
);

CREATE TABLE dtc_codes (
    dtc_code     char(5) PRIMARY KEY CHECK (dtc_code ~ '^[PCBU][0-3][0-9A-F]{3}$'),
    description  text NOT NULL,
    severity     text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    failure_mode text REFERENCES failure_modes (failure_mode),
    is_synthetic boolean NOT NULL DEFAULT false
);
CREATE INDEX dtc_codes_failure_mode_idx ON dtc_codes (failure_mode);

-- ---------------------------------------------------------------------------
-- Tenancy, subscriptions
-- ---------------------------------------------------------------------------
CREATE TABLE tenants (
    tenant_id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                    text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{2,39}$'),
    display_name            text NOT NULL,
    data_region             text NOT NULL DEFAULT 'IN' CHECK (data_region IN ('IN', 'EU', 'US')),
    location_retention_days integer NOT NULL DEFAULT 30
                              CHECK (location_retention_days BETWEEN 1 AND 3650),
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE subscriptions (
    subscription_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    plan            text NOT NULL CHECK (plan IN ('starter', 'growth', 'enterprise')),
    vehicle_limit   integer NOT NULL CHECK (vehicle_limit > 0),
    valid_during    tstzrange NOT NULL CHECK (NOT isempty(valid_during)),
    -- A tenant can never hold two overlapping subscriptions.
    EXCLUDE USING gist (tenant_id WITH =, valid_during WITH &&)
);

-- ---------------------------------------------------------------------------
-- Fleet structure
-- ---------------------------------------------------------------------------
CREATE TABLE fleets (
    fleet_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    name       text NOT NULL,
    base_city  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name),
    UNIQUE (fleet_id, tenant_id)          -- target of composite FKs
);

CREATE TABLE workshops (
    workshop_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    name           text NOT NULL,
    city           text NOT NULL,
    latitude       numeric(9, 6) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude      numeric(9, 6) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    daily_capacity smallint NOT NULL CHECK (daily_capacity > 0),
    UNIQUE (tenant_id, name),
    UNIQUE (workshop_id, tenant_id)
);

CREATE TABLE vehicles (
    vehicle_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL,
    fleet_id         uuid NOT NULL,
    vin              char(17) NOT NULL UNIQUE CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
    model_code       char(5) NOT NULL REFERENCES vehicle_models (model_code),
    model_year       smallint NOT NULL CHECK (model_year BETWEEN 2010 AND 2039),
    firmware_version text NOT NULL,
    status           text NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'in_workshop', 'retired')),
    home_workshop_id uuid,
    commissioned_on  date NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vehicle_id, tenant_id),
    FOREIGN KEY (fleet_id, tenant_id) REFERENCES fleets (fleet_id, tenant_id),
    FOREIGN KEY (home_workshop_id, tenant_id) REFERENCES workshops (workshop_id, tenant_id)
);
CREATE INDEX vehicles_fleet_idx ON vehicles (fleet_id);
CREATE INDEX vehicles_model_idx ON vehicles (model_code);
CREATE INDEX vehicles_home_workshop_idx ON vehicles (home_workshop_id);

-- Drivers are pseudonymous by design (data minimisation): no names, phones or IDs.
CREATE TABLE drivers (
    driver_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    pseudonym  text NOT NULL CHECK (pseudonym ~ '^DRV-[0-9]{6}$'),
    erased_at  timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, pseudonym),
    UNIQUE (driver_id, tenant_id)
);

CREATE TABLE driver_assignments (
    assignment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     uuid NOT NULL,
    vehicle_id    uuid NOT NULL,
    driver_id     uuid NOT NULL,
    during        tstzrange NOT NULL CHECK (NOT isempty(during)),
    FOREIGN KEY (vehicle_id, tenant_id) REFERENCES vehicles (vehicle_id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (driver_id, tenant_id) REFERENCES drivers (driver_id, tenant_id) ON DELETE CASCADE,
    -- One driver per vehicle at a time, and one vehicle per driver at a time.
    EXCLUDE USING gist (vehicle_id WITH =, during WITH &&),
    EXCLUDE USING gist (driver_id WITH =, during WITH &&)
);

-- ---------------------------------------------------------------------------
-- Identity & RBAC (authentication itself lands in M9)
-- ---------------------------------------------------------------------------
CREATE TABLE roles (
    role_code   text PRIMARY KEY CHECK (role_code ~ '^[a-z_]+$'),
    description text NOT NULL
);

CREATE TABLE role_permissions (
    role_code  text NOT NULL REFERENCES roles (role_code) ON DELETE CASCADE,
    permission text NOT NULL CHECK (permission ~ '^[a-z_]+:[a-z_]+$'),
    PRIMARY KEY (role_code, permission)
);

CREATE TABLE users (
    user_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid REFERENCES tenants (tenant_id) ON DELETE CASCADE,  -- NULL = platform staff
    email         citext NOT NULL UNIQUE CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    display_name  text NOT NULL,
    password_hash text NOT NULL,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);
CREATE INDEX users_tenant_idx ON users (tenant_id);

CREATE TABLE user_roles (
    user_id   uuid NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    role_code text NOT NULL REFERENCES roles (role_code),
    PRIMARY KEY (user_id, role_code)
);
CREATE INDEX user_roles_role_idx ON user_roles (role_code);

-- ---------------------------------------------------------------------------
-- Economics: per-tenant cost inputs, with mandatory provenance
-- ---------------------------------------------------------------------------
CREATE TABLE cost_parameters (
    tenant_id             uuid NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    failure_mode          text NOT NULL REFERENCES failure_modes (failure_mode),
    planned_repair_cost   numeric(12, 2) NOT NULL CHECK (planned_repair_cost >= 0),
    unplanned_repair_cost numeric(12, 2) NOT NULL CHECK (unplanned_repair_cost >= 0),
    downtime_cost_per_day numeric(12, 2) NOT NULL CHECK (downtime_cost_per_day >= 0),
    extra_downtime_days   numeric(5, 2) NOT NULL CHECK (extra_downtime_days >= 0),
    currency              char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    source                text NOT NULL CHECK (length(source) > 0),  -- where the numbers come from
    updated_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, failure_mode)
);
CREATE INDEX cost_parameters_failure_mode_idx ON cost_parameters (failure_mode);

-- ---------------------------------------------------------------------------
-- Alerts and work orders (CP data: strongly consistent, idempotent writes)
-- ---------------------------------------------------------------------------
CREATE TABLE alerts (
    alert_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       uuid NOT NULL,
    vehicle_id      uuid NOT NULL,
    fingerprint     text NOT NULL,  -- deterministic id from the stream processor => idempotent upsert
    rule_code       text NOT NULL CHECK (rule_code ~ '^[A-Z][A-Z0-9_]+$'),
    severity        text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    failure_mode    text REFERENCES failure_modes (failure_mode),
    status          text NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'acknowledged', 'resolved', 'suppressed')),
    event_ts        timestamptz NOT NULL,  -- when the triggering telemetry happened
    detected_at     timestamptz NOT NULL DEFAULT now(),
    acknowledged_at timestamptz,
    acknowledged_by uuid REFERENCES users (user_id),
    resolved_at     timestamptz,
    details         jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, fingerprint),
    UNIQUE (alert_id, tenant_id),
    FOREIGN KEY (vehicle_id, tenant_id) REFERENCES vehicles (vehicle_id, tenant_id) ON DELETE CASCADE,
    CHECK (status <> 'acknowledged' OR acknowledged_at IS NOT NULL),
    CHECK (status <> 'resolved' OR resolved_at IS NOT NULL),
    CHECK (acknowledged_at IS NULL OR acknowledged_at >= detected_at),
    CHECK (resolved_at IS NULL OR resolved_at >= detected_at)
);
CREATE INDEX alerts_vehicle_idx ON alerts (vehicle_id);
CREATE INDEX alerts_failure_mode_idx ON alerts (failure_mode);
CREATE INDEX alerts_acknowledged_by_idx ON alerts (acknowledged_by);

CREATE TABLE work_orders (
    work_order_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             uuid NOT NULL,
    vehicle_id            uuid NOT NULL,
    workshop_id           uuid NOT NULL,
    source_alert_id       bigint,
    failure_mode          text NOT NULL REFERENCES failure_modes (failure_mode),
    status                text NOT NULL DEFAULT 'proposed' CHECK (status IN
                            ('proposed', 'scheduled', 'in_progress', 'completed', 'cancelled')),
    failure_probability   numeric(5, 4) CHECK (failure_probability BETWEEN 0 AND 1),
    expected_cost_avoided numeric(12, 2),
    model_version         text,
    scheduled_for         date,
    created_by            uuid REFERENCES users (user_id),  -- NULL = created by the system
    created_at            timestamptz NOT NULL DEFAULT now(),
    completed_at          timestamptz,
    outcome               text CHECK (outcome IN ('fault_confirmed', 'no_fault_found', 'other')),
    FOREIGN KEY (vehicle_id, tenant_id) REFERENCES vehicles (vehicle_id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (workshop_id, tenant_id) REFERENCES workshops (workshop_id, tenant_id),
    FOREIGN KEY (source_alert_id, tenant_id) REFERENCES alerts (alert_id, tenant_id),
    CHECK (status <> 'completed' OR (completed_at IS NOT NULL AND outcome IS NOT NULL)),
    CHECK (completed_at IS NULL OR completed_at >= created_at),
    CHECK (status <> 'scheduled' OR scheduled_for IS NOT NULL)
);
-- Business rule (also the idempotency guard): at most one active work order per
-- vehicle and failure mode.
CREATE UNIQUE INDEX work_orders_one_active_per_vehicle_mode
    ON work_orders (vehicle_id, failure_mode)
    WHERE status IN ('proposed', 'scheduled', 'in_progress');
CREATE INDEX work_orders_vehicle_idx ON work_orders (vehicle_id);
CREATE INDEX work_orders_workshop_idx ON work_orders (workshop_id);
CREATE INDEX work_orders_failure_mode_idx ON work_orders (failure_mode);
CREATE INDEX work_orders_source_alert_idx ON work_orders (source_alert_id);
CREATE INDEX work_orders_created_by_idx ON work_orders (created_by);

-- ---------------------------------------------------------------------------
-- Privacy: right-to-erasure requests (workflow implemented in M12)
-- ---------------------------------------------------------------------------
CREATE TABLE erasure_requests (
    request_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants (tenant_id) ON DELETE CASCADE,
    subject_type text NOT NULL CHECK (subject_type IN ('driver', 'vehicle')),
    subject_id   uuid NOT NULL,
    status       text NOT NULL DEFAULT 'received'
                   CHECK (status IN ('received', 'in_progress', 'completed', 'rejected')),
    requested_by uuid REFERENCES users (user_id),
    requested_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    details      jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status <> 'completed' OR completed_at IS NOT NULL)
);
CREATE INDEX erasure_requests_tenant_idx ON erasure_requests (tenant_id);
CREATE INDEX erasure_requests_requested_by_idx ON erasure_requests (requested_by);

-- ---------------------------------------------------------------------------
-- Audit log: append-only, monthly range partitions
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_id      bigint GENERATED ALWAYS AS IDENTITY,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    tenant_id     uuid,              -- no FK: the audit trail must outlive the tenant
    actor_type    text NOT NULL CHECK (actor_type IN ('user', 'system', 'agent')),
    actor_id      text,
    action        text NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
    resource_type text NOT NULL,
    resource_id   text,
    outcome       text NOT NULL CHECK (outcome IN ('success', 'denied', 'error')),
    request_id    text,
    client_ip     inet,
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (audit_id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;

CREATE FUNCTION ensure_audit_partitions(months_ahead integer DEFAULT 2) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    start_month date := date_trunc('month', now())::date;
    m date;
BEGIN
    FOR i IN 0..months_ahead LOOP
        m := (start_month + make_interval(months => i))::date;
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_log FOR VALUES FROM (%L) TO (%L)',
            'audit_log_' || to_char(m, 'YYYYMM'), m, (m + interval '1 month')::date);
    END LOOP;
END $$;

SELECT ensure_audit_partitions(2);

CREATE FUNCTION audit_log_is_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% denied)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

-- migrate:down
DROP TABLE IF EXISTS audit_log CASCADE;
DROP FUNCTION IF EXISTS audit_log_is_append_only();
DROP FUNCTION IF EXISTS ensure_audit_partitions(integer);
DROP TABLE IF EXISTS erasure_requests, work_orders, alerts, cost_parameters, user_roles, users,
    role_permissions, roles, driver_assignments, drivers, vehicles, workshops, fleets,
    subscriptions, tenants, dtc_codes, failure_mode_powertrains, failure_modes, vehicle_models,
    oems CASCADE;
