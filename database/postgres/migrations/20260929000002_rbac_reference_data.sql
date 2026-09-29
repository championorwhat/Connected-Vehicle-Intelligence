-- migrate:up
-- Roles and permissions are security policy, so they ship with the schema
-- (reviewed in migrations), not with seed data. Least privilege: platform_admin
-- manages tenants but has no telemetry or location access.

INSERT INTO roles (role_code, description) VALUES
    ('platform_admin', 'Platform operator: tenant lifecycle; no fleet data access'),
    ('fleet_manager',  'Owns uptime: sees fleet, precise location, alerts, creates work orders'),
    ('technician',     'Workshop staff: sees vehicles and work orders, completes work orders'),
    ('analyst',        'Read-only analytics; location is masked'),
    ('dpo',            'Data protection officer: audit trail and erasure requests');

INSERT INTO role_permissions (role_code, permission) VALUES
    ('platform_admin', 'tenant:admin'),
    ('platform_admin', 'audit:read'),
    ('fleet_manager', 'fleet:read'),
    ('fleet_manager', 'vehicle:read'),
    ('fleet_manager', 'telemetry:read'),
    ('fleet_manager', 'location:read_precise'),
    ('fleet_manager', 'alert:read'),
    ('fleet_manager', 'alert:ack'),
    ('fleet_manager', 'work_order:read'),
    ('fleet_manager', 'work_order:write'),
    ('fleet_manager', 'cost:read'),
    ('technician', 'vehicle:read'),
    ('technician', 'telemetry:read'),
    ('technician', 'alert:read'),
    ('technician', 'work_order:read'),
    ('technician', 'work_order:complete'),
    ('analyst', 'fleet:read'),
    ('analyst', 'vehicle:read'),
    ('analyst', 'telemetry:read'),
    ('analyst', 'alert:read'),
    ('analyst', 'cost:read'),
    ('dpo', 'vehicle:read'),
    ('dpo', 'audit:read'),
    ('dpo', 'privacy:erase');

-- migrate:down
DELETE FROM role_permissions;
DELETE FROM roles;
