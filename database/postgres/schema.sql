--
-- PostgreSQL database dump
--


-- Dumped from database version 17.11
-- Dumped by pg_dump version 17.11

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: btree_gist; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public;


--
-- Name: EXTENSION btree_gist; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION btree_gist IS 'support for indexing common datatypes in GiST';


--
-- Name: citext; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;


--
-- Name: EXTENSION citext; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION citext IS 'data type for case-insensitive character strings';


--
-- Name: pg_stat_statements; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;


--
-- Name: EXTENSION pg_stat_statements; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_stat_statements IS 'track planning and execution statistics of all SQL statements executed';


--
-- Name: app_tenant(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.app_tenant() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;


--
-- Name: audit_log_is_append_only(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.audit_log_is_append_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% denied)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;


--
-- Name: ensure_audit_partitions(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ensure_audit_partitions(months_ahead integer DEFAULT 2) RETURNS void
    LANGUAGE plpgsql
    AS $$
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


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    alert_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    vehicle_id uuid NOT NULL,
    fingerprint text NOT NULL,
    rule_code text NOT NULL,
    severity text NOT NULL,
    failure_mode text,
    status text DEFAULT 'open'::text NOT NULL,
    event_ts timestamp with time zone NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    acknowledged_at timestamp with time zone,
    acknowledged_by uuid,
    resolved_at timestamp with time zone,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT alerts_check CHECK (((status <> 'acknowledged'::text) OR (acknowledged_at IS NOT NULL))),
    CONSTRAINT alerts_check1 CHECK (((status <> 'resolved'::text) OR (resolved_at IS NOT NULL))),
    CONSTRAINT alerts_check2 CHECK (((acknowledged_at IS NULL) OR (acknowledged_at >= detected_at))),
    CONSTRAINT alerts_check3 CHECK (((resolved_at IS NULL) OR (resolved_at >= detected_at))),
    CONSTRAINT alerts_rule_code_check CHECK ((rule_code ~ '^[A-Z][A-Z0-9_]+$'::text)),
    CONSTRAINT alerts_severity_check CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text]))),
    CONSTRAINT alerts_status_check CHECK ((status = ANY (ARRAY['open'::text, 'acknowledged'::text, 'resolved'::text, 'suppressed'::text])))
);


--
-- Name: alerts_alert_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.alerts ALTER COLUMN alert_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.alerts_alert_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    audit_id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    actor_type text NOT NULL,
    actor_id text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    outcome text NOT NULL,
    request_id text,
    client_ip inet,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT audit_log_action_check CHECK ((action ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT audit_log_actor_type_check CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'agent'::text]))),
    CONSTRAINT audit_log_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'denied'::text, 'error'::text])))
)
PARTITION BY RANGE (occurred_at);


--
-- Name: audit_log_202609; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log_202609 (
    audit_id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    actor_type text NOT NULL,
    actor_id text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    outcome text NOT NULL,
    request_id text,
    client_ip inet,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT audit_log_action_check CHECK ((action ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT audit_log_actor_type_check CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'agent'::text]))),
    CONSTRAINT audit_log_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'denied'::text, 'error'::text])))
);


--
-- Name: audit_log_202610; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log_202610 (
    audit_id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    actor_type text NOT NULL,
    actor_id text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    outcome text NOT NULL,
    request_id text,
    client_ip inet,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT audit_log_action_check CHECK ((action ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT audit_log_actor_type_check CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'agent'::text]))),
    CONSTRAINT audit_log_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'denied'::text, 'error'::text])))
);


--
-- Name: audit_log_202611; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log_202611 (
    audit_id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    actor_type text NOT NULL,
    actor_id text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    outcome text NOT NULL,
    request_id text,
    client_ip inet,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT audit_log_action_check CHECK ((action ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT audit_log_actor_type_check CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'agent'::text]))),
    CONSTRAINT audit_log_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'denied'::text, 'error'::text])))
);


--
-- Name: audit_log_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ALTER COLUMN audit_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.audit_log_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_log_default; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log_default (
    audit_id bigint NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    tenant_id uuid,
    actor_type text NOT NULL,
    actor_id text,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    outcome text NOT NULL,
    request_id text,
    client_ip inet,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT audit_log_action_check CHECK ((action ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT audit_log_actor_type_check CHECK ((actor_type = ANY (ARRAY['user'::text, 'system'::text, 'agent'::text]))),
    CONSTRAINT audit_log_outcome_check CHECK ((outcome = ANY (ARRAY['success'::text, 'denied'::text, 'error'::text])))
);


--
-- Name: cost_parameters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cost_parameters (
    tenant_id uuid NOT NULL,
    failure_mode text NOT NULL,
    planned_repair_cost numeric(12,2) NOT NULL,
    unplanned_repair_cost numeric(12,2) NOT NULL,
    downtime_cost_per_day numeric(12,2) NOT NULL,
    extra_downtime_days numeric(5,2) NOT NULL,
    currency character(3) NOT NULL,
    source text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cost_parameters_currency_check CHECK ((currency ~ '^[A-Z]{3}$'::text)),
    CONSTRAINT cost_parameters_downtime_cost_per_day_check CHECK ((downtime_cost_per_day >= (0)::numeric)),
    CONSTRAINT cost_parameters_extra_downtime_days_check CHECK ((extra_downtime_days >= (0)::numeric)),
    CONSTRAINT cost_parameters_planned_repair_cost_check CHECK ((planned_repair_cost >= (0)::numeric)),
    CONSTRAINT cost_parameters_source_check CHECK ((length(source) > 0)),
    CONSTRAINT cost_parameters_unplanned_repair_cost_check CHECK ((unplanned_repair_cost >= (0)::numeric))
);


--
-- Name: driver_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.driver_assignments (
    assignment_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    vehicle_id uuid NOT NULL,
    driver_id uuid NOT NULL,
    during tstzrange NOT NULL,
    CONSTRAINT driver_assignments_during_check CHECK ((NOT isempty(during)))
);


--
-- Name: driver_assignments_assignment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.driver_assignments ALTER COLUMN assignment_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.driver_assignments_assignment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: drivers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.drivers (
    driver_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    pseudonym text NOT NULL,
    erased_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT drivers_pseudonym_check CHECK ((pseudonym ~ '^DRV-[0-9]{6}$'::text))
);


--
-- Name: dtc_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dtc_codes (
    dtc_code character(5) NOT NULL,
    description text NOT NULL,
    severity text NOT NULL,
    failure_mode text,
    is_synthetic boolean DEFAULT false NOT NULL,
    CONSTRAINT dtc_codes_dtc_code_check CHECK ((dtc_code ~ '^[PCBU][0-3][0-9A-F]{3}$'::text)),
    CONSTRAINT dtc_codes_severity_check CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text])))
);


--
-- Name: erasure_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.erasure_requests (
    request_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    status text DEFAULT 'received'::text NOT NULL,
    requested_by uuid,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT erasure_requests_check CHECK (((status <> 'completed'::text) OR (completed_at IS NOT NULL))),
    CONSTRAINT erasure_requests_status_check CHECK ((status = ANY (ARRAY['received'::text, 'in_progress'::text, 'completed'::text, 'rejected'::text]))),
    CONSTRAINT erasure_requests_subject_type_check CHECK ((subject_type = ANY (ARRAY['driver'::text, 'vehicle'::text])))
);


--
-- Name: failure_mode_powertrains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.failure_mode_powertrains (
    failure_mode text NOT NULL,
    powertrain text NOT NULL,
    CONSTRAINT failure_mode_powertrains_powertrain_check CHECK ((powertrain = ANY (ARRAY['ICE'::text, 'HEV'::text, 'BEV'::text])))
);


--
-- Name: failure_modes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.failure_modes (
    failure_mode text NOT NULL,
    display_name text NOT NULL,
    component text NOT NULL,
    CONSTRAINT failure_modes_failure_mode_check CHECK ((failure_mode ~ '^[A-Z][A-Z0-9_]+$'::text))
);


--
-- Name: fleets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fleets (
    fleet_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name text NOT NULL,
    base_city text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: oems; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.oems (
    oem_code text NOT NULL,
    display_name text NOT NULL,
    payload_format text NOT NULL,
    vin_wmi character(3) NOT NULL,
    requires_vin_check_digit boolean NOT NULL,
    CONSTRAINT oems_oem_code_check CHECK ((oem_code ~ '^[A-Z]{3,10}$'::text)),
    CONSTRAINT oems_payload_format_check CHECK ((payload_format = ANY (ARRAY['flat_json_metric'::text, 'nested_json_imperial'::text, 'signal_list_json'::text]))),
    CONSTRAINT oems_vin_wmi_check CHECK ((vin_wmi ~ '^[A-HJ-NPR-Z0-9]{3}$'::text))
);


--
-- Name: role_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_permissions (
    role_code text NOT NULL,
    permission text NOT NULL,
    CONSTRAINT role_permissions_permission_check CHECK ((permission ~ '^[a-z_]+:[a-z_]+$'::text))
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    role_code text NOT NULL,
    description text NOT NULL,
    CONSTRAINT roles_role_code_check CHECK ((role_code ~ '^[a-z_]+$'::text))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    subscription_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    plan text NOT NULL,
    vehicle_limit integer NOT NULL,
    valid_during tstzrange NOT NULL,
    CONSTRAINT subscriptions_plan_check CHECK ((plan = ANY (ARRAY['starter'::text, 'growth'::text, 'enterprise'::text]))),
    CONSTRAINT subscriptions_valid_during_check CHECK ((NOT isempty(valid_during))),
    CONSTRAINT subscriptions_vehicle_limit_check CHECK ((vehicle_limit > 0))
);


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    tenant_id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    display_name text NOT NULL,
    data_region text DEFAULT 'IN'::text NOT NULL,
    location_retention_days integer DEFAULT 30 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenants_data_region_check CHECK ((data_region = ANY (ARRAY['IN'::text, 'EU'::text, 'US'::text]))),
    CONSTRAINT tenants_location_retention_days_check CHECK (((location_retention_days >= 1) AND (location_retention_days <= 3650))),
    CONSTRAINT tenants_slug_check CHECK ((slug ~ '^[a-z0-9][a-z0-9-]{2,39}$'::text))
);


--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_roles (
    user_id uuid NOT NULL,
    role_code text NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    email public.citext NOT NULL,
    display_name text NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone,
    CONSTRAINT users_email_check CHECK ((email OPERATOR(public.~) '^[^@\s]+@[^@\s]+\.[^@\s]+$'::public.citext))
);


--
-- Name: vehicle_models; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vehicle_models (
    model_code character(5) NOT NULL,
    oem_code text NOT NULL,
    display_name text NOT NULL,
    powertrain text NOT NULL,
    vehicle_class text NOT NULL,
    CONSTRAINT vehicle_models_model_code_check CHECK ((model_code ~ '^[A-HJ-NPR-Z0-9]{5}$'::text)),
    CONSTRAINT vehicle_models_powertrain_check CHECK ((powertrain = ANY (ARRAY['ICE'::text, 'HEV'::text, 'BEV'::text]))),
    CONSTRAINT vehicle_models_vehicle_class_check CHECK ((vehicle_class = ANY (ARRAY['car'::text, 'van'::text, 'light_truck'::text, 'truck'::text])))
);


--
-- Name: vehicles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vehicles (
    vehicle_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    fleet_id uuid NOT NULL,
    vin character(17) NOT NULL,
    model_code character(5) NOT NULL,
    model_year smallint NOT NULL,
    firmware_version text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    home_workshop_id uuid,
    commissioned_on date NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vehicles_model_year_check CHECK (((model_year >= 2010) AND (model_year <= 2039))),
    CONSTRAINT vehicles_status_check CHECK ((status = ANY (ARRAY['active'::text, 'in_workshop'::text, 'retired'::text]))),
    CONSTRAINT vehicles_vin_check CHECK ((vin ~ '^[A-HJ-NPR-Z0-9]{17}$'::text))
);


--
-- Name: work_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.work_orders (
    work_order_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    vehicle_id uuid NOT NULL,
    workshop_id uuid NOT NULL,
    source_alert_id bigint,
    failure_mode text NOT NULL,
    status text DEFAULT 'proposed'::text NOT NULL,
    failure_probability numeric(5,4),
    expected_cost_avoided numeric(12,2),
    model_version text,
    scheduled_for date,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    outcome text,
    CONSTRAINT work_orders_check CHECK (((status <> 'completed'::text) OR ((completed_at IS NOT NULL) AND (outcome IS NOT NULL)))),
    CONSTRAINT work_orders_check1 CHECK (((completed_at IS NULL) OR (completed_at >= created_at))),
    CONSTRAINT work_orders_check2 CHECK (((status <> 'scheduled'::text) OR (scheduled_for IS NOT NULL))),
    CONSTRAINT work_orders_failure_probability_check CHECK (((failure_probability >= (0)::numeric) AND (failure_probability <= (1)::numeric))),
    CONSTRAINT work_orders_outcome_check CHECK ((outcome = ANY (ARRAY['fault_confirmed'::text, 'no_fault_found'::text, 'other'::text]))),
    CONSTRAINT work_orders_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'scheduled'::text, 'in_progress'::text, 'completed'::text, 'cancelled'::text])))
);


--
-- Name: workshops; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workshops (
    workshop_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name text NOT NULL,
    city text NOT NULL,
    latitude numeric(9,6) NOT NULL,
    longitude numeric(9,6) NOT NULL,
    daily_capacity smallint NOT NULL,
    CONSTRAINT workshops_daily_capacity_check CHECK ((daily_capacity > 0)),
    CONSTRAINT workshops_latitude_check CHECK (((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric))),
    CONSTRAINT workshops_longitude_check CHECK (((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric)))
);


--
-- Name: audit_log_202609; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ATTACH PARTITION public.audit_log_202609 FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');


--
-- Name: audit_log_202610; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ATTACH PARTITION public.audit_log_202610 FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');


--
-- Name: audit_log_202611; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ATTACH PARTITION public.audit_log_202611 FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');


--
-- Name: audit_log_default; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log ATTACH PARTITION public.audit_log_default DEFAULT;


--
-- Name: alerts alerts_alert_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_alert_id_tenant_id_key UNIQUE (alert_id, tenant_id);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (alert_id);


--
-- Name: alerts alerts_tenant_id_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_tenant_id_fingerprint_key UNIQUE (tenant_id, fingerprint);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (audit_id, occurred_at);


--
-- Name: audit_log_202609 audit_log_202609_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log_202609
    ADD CONSTRAINT audit_log_202609_pkey PRIMARY KEY (audit_id, occurred_at);


--
-- Name: audit_log_202610 audit_log_202610_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log_202610
    ADD CONSTRAINT audit_log_202610_pkey PRIMARY KEY (audit_id, occurred_at);


--
-- Name: audit_log_202611 audit_log_202611_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log_202611
    ADD CONSTRAINT audit_log_202611_pkey PRIMARY KEY (audit_id, occurred_at);


--
-- Name: audit_log_default audit_log_default_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log_default
    ADD CONSTRAINT audit_log_default_pkey PRIMARY KEY (audit_id, occurred_at);


--
-- Name: cost_parameters cost_parameters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_parameters
    ADD CONSTRAINT cost_parameters_pkey PRIMARY KEY (tenant_id, failure_mode);


--
-- Name: driver_assignments driver_assignments_driver_id_during_excl; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_assignments
    ADD CONSTRAINT driver_assignments_driver_id_during_excl EXCLUDE USING gist (driver_id WITH =, during WITH &&);


--
-- Name: driver_assignments driver_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_assignments
    ADD CONSTRAINT driver_assignments_pkey PRIMARY KEY (assignment_id);


--
-- Name: driver_assignments driver_assignments_vehicle_id_during_excl; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_assignments
    ADD CONSTRAINT driver_assignments_vehicle_id_during_excl EXCLUDE USING gist (vehicle_id WITH =, during WITH &&);


--
-- Name: drivers drivers_driver_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_driver_id_tenant_id_key UNIQUE (driver_id, tenant_id);


--
-- Name: drivers drivers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_pkey PRIMARY KEY (driver_id);


--
-- Name: drivers drivers_tenant_id_pseudonym_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_tenant_id_pseudonym_key UNIQUE (tenant_id, pseudonym);


--
-- Name: dtc_codes dtc_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dtc_codes
    ADD CONSTRAINT dtc_codes_pkey PRIMARY KEY (dtc_code);


--
-- Name: erasure_requests erasure_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.erasure_requests
    ADD CONSTRAINT erasure_requests_pkey PRIMARY KEY (request_id);


--
-- Name: failure_mode_powertrains failure_mode_powertrains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_mode_powertrains
    ADD CONSTRAINT failure_mode_powertrains_pkey PRIMARY KEY (failure_mode, powertrain);


--
-- Name: failure_modes failure_modes_display_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_modes
    ADD CONSTRAINT failure_modes_display_name_key UNIQUE (display_name);


--
-- Name: failure_modes failure_modes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_modes
    ADD CONSTRAINT failure_modes_pkey PRIMARY KEY (failure_mode);


--
-- Name: fleets fleets_fleet_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fleets
    ADD CONSTRAINT fleets_fleet_id_tenant_id_key UNIQUE (fleet_id, tenant_id);


--
-- Name: fleets fleets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fleets
    ADD CONSTRAINT fleets_pkey PRIMARY KEY (fleet_id);


--
-- Name: fleets fleets_tenant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fleets
    ADD CONSTRAINT fleets_tenant_id_name_key UNIQUE (tenant_id, name);


--
-- Name: oems oems_display_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oems
    ADD CONSTRAINT oems_display_name_key UNIQUE (display_name);


--
-- Name: oems oems_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oems
    ADD CONSTRAINT oems_pkey PRIMARY KEY (oem_code);


--
-- Name: oems oems_vin_wmi_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.oems
    ADD CONSTRAINT oems_vin_wmi_key UNIQUE (vin_wmi);


--
-- Name: role_permissions role_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_pkey PRIMARY KEY (role_code, permission);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (role_code);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (subscription_id);


--
-- Name: subscriptions subscriptions_tenant_id_valid_during_excl; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_tenant_id_valid_during_excl EXCLUDE USING gist (tenant_id WITH =, valid_during WITH &&);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (tenant_id);


--
-- Name: tenants tenants_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_slug_key UNIQUE (slug);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_code);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: vehicle_models vehicle_models_oem_code_display_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicle_models
    ADD CONSTRAINT vehicle_models_oem_code_display_name_key UNIQUE (oem_code, display_name);


--
-- Name: vehicle_models vehicle_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicle_models
    ADD CONSTRAINT vehicle_models_pkey PRIMARY KEY (model_code);


--
-- Name: vehicles vehicles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_pkey PRIMARY KEY (vehicle_id);


--
-- Name: vehicles vehicles_vehicle_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_vehicle_id_tenant_id_key UNIQUE (vehicle_id, tenant_id);


--
-- Name: vehicles vehicles_vin_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_vin_key UNIQUE (vin);


--
-- Name: work_orders work_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_pkey PRIMARY KEY (work_order_id);


--
-- Name: workshops workshops_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workshops
    ADD CONSTRAINT workshops_pkey PRIMARY KEY (workshop_id);


--
-- Name: workshops workshops_tenant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workshops
    ADD CONSTRAINT workshops_tenant_id_name_key UNIQUE (tenant_id, name);


--
-- Name: workshops workshops_workshop_id_tenant_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workshops
    ADD CONSTRAINT workshops_workshop_id_tenant_id_key UNIQUE (workshop_id, tenant_id);


--
-- Name: alerts_acknowledged_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_acknowledged_by_idx ON public.alerts USING btree (acknowledged_by);


--
-- Name: alerts_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_active_idx ON public.alerts USING btree (tenant_id, status, severity) INCLUDE (vehicle_id, failure_mode) WHERE (status = ANY (ARRAY['open'::text, 'acknowledged'::text]));


--
-- Name: alerts_failure_mode_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_failure_mode_idx ON public.alerts USING btree (failure_mode);


--
-- Name: alerts_tenant_detected_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_tenant_detected_idx ON public.alerts USING btree (tenant_id, detected_at DESC, alert_id DESC);


--
-- Name: alerts_vehicle_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alerts_vehicle_idx ON public.alerts USING btree (vehicle_id);


--
-- Name: cost_parameters_failure_mode_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX cost_parameters_failure_mode_idx ON public.cost_parameters USING btree (failure_mode);


--
-- Name: dtc_codes_failure_mode_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX dtc_codes_failure_mode_idx ON public.dtc_codes USING btree (failure_mode);


--
-- Name: erasure_requests_requested_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX erasure_requests_requested_by_idx ON public.erasure_requests USING btree (requested_by);


--
-- Name: erasure_requests_tenant_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX erasure_requests_tenant_idx ON public.erasure_requests USING btree (tenant_id);


--
-- Name: user_roles_role_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_roles_role_idx ON public.user_roles USING btree (role_code);


--
-- Name: users_tenant_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_tenant_idx ON public.users USING btree (tenant_id);


--
-- Name: vehicle_models_oem_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vehicle_models_oem_idx ON public.vehicle_models USING btree (oem_code);


--
-- Name: vehicles_fleet_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vehicles_fleet_idx ON public.vehicles USING btree (fleet_id);


--
-- Name: vehicles_home_workshop_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vehicles_home_workshop_idx ON public.vehicles USING btree (home_workshop_id);


--
-- Name: vehicles_model_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vehicles_model_idx ON public.vehicles USING btree (model_code);


--
-- Name: vehicles_tenant_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vehicles_tenant_status_idx ON public.vehicles USING btree (tenant_id, status);


--
-- Name: work_orders_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_created_by_idx ON public.work_orders USING btree (created_by);


--
-- Name: work_orders_failure_mode_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_failure_mode_idx ON public.work_orders USING btree (failure_mode);


--
-- Name: work_orders_one_active_per_vehicle_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX work_orders_one_active_per_vehicle_mode ON public.work_orders USING btree (vehicle_id, failure_mode) WHERE (status = ANY (ARRAY['proposed'::text, 'scheduled'::text, 'in_progress'::text]));


--
-- Name: work_orders_source_alert_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_source_alert_idx ON public.work_orders USING btree (source_alert_id);


--
-- Name: work_orders_tenant_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_tenant_created_idx ON public.work_orders USING btree (tenant_id, created_at DESC, work_order_id DESC);


--
-- Name: work_orders_tenant_status_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_tenant_status_created_idx ON public.work_orders USING btree (tenant_id, status, created_at DESC, work_order_id DESC);


--
-- Name: work_orders_vehicle_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_vehicle_idx ON public.work_orders USING btree (vehicle_id);


--
-- Name: work_orders_workshop_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX work_orders_workshop_idx ON public.work_orders USING btree (workshop_id);


--
-- Name: audit_log_202609_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.audit_log_pkey ATTACH PARTITION public.audit_log_202609_pkey;


--
-- Name: audit_log_202610_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.audit_log_pkey ATTACH PARTITION public.audit_log_202610_pkey;


--
-- Name: audit_log_202611_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.audit_log_pkey ATTACH PARTITION public.audit_log_202611_pkey;


--
-- Name: audit_log_default_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.audit_log_pkey ATTACH PARTITION public.audit_log_default_pkey;


--
-- Name: audit_log audit_log_no_update_delete; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER audit_log_no_update_delete BEFORE DELETE OR UPDATE ON public.audit_log FOR EACH ROW EXECUTE FUNCTION public.audit_log_is_append_only();


--
-- Name: alerts alerts_acknowledged_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_acknowledged_by_fkey FOREIGN KEY (acknowledged_by) REFERENCES public.users(user_id);


--
-- Name: alerts alerts_failure_mode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_failure_mode_fkey FOREIGN KEY (failure_mode) REFERENCES public.failure_modes(failure_mode);


--
-- Name: alerts alerts_vehicle_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_vehicle_id_tenant_id_fkey FOREIGN KEY (vehicle_id, tenant_id) REFERENCES public.vehicles(vehicle_id, tenant_id) ON DELETE CASCADE;


--
-- Name: cost_parameters cost_parameters_failure_mode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_parameters
    ADD CONSTRAINT cost_parameters_failure_mode_fkey FOREIGN KEY (failure_mode) REFERENCES public.failure_modes(failure_mode);


--
-- Name: cost_parameters cost_parameters_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_parameters
    ADD CONSTRAINT cost_parameters_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: driver_assignments driver_assignments_driver_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_assignments
    ADD CONSTRAINT driver_assignments_driver_id_tenant_id_fkey FOREIGN KEY (driver_id, tenant_id) REFERENCES public.drivers(driver_id, tenant_id) ON DELETE CASCADE;


--
-- Name: driver_assignments driver_assignments_vehicle_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.driver_assignments
    ADD CONSTRAINT driver_assignments_vehicle_id_tenant_id_fkey FOREIGN KEY (vehicle_id, tenant_id) REFERENCES public.vehicles(vehicle_id, tenant_id) ON DELETE CASCADE;


--
-- Name: drivers drivers_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.drivers
    ADD CONSTRAINT drivers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: dtc_codes dtc_codes_failure_mode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dtc_codes
    ADD CONSTRAINT dtc_codes_failure_mode_fkey FOREIGN KEY (failure_mode) REFERENCES public.failure_modes(failure_mode);


--
-- Name: erasure_requests erasure_requests_requested_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.erasure_requests
    ADD CONSTRAINT erasure_requests_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES public.users(user_id);


--
-- Name: erasure_requests erasure_requests_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.erasure_requests
    ADD CONSTRAINT erasure_requests_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: failure_mode_powertrains failure_mode_powertrains_failure_mode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_mode_powertrains
    ADD CONSTRAINT failure_mode_powertrains_failure_mode_fkey FOREIGN KEY (failure_mode) REFERENCES public.failure_modes(failure_mode) ON DELETE CASCADE;


--
-- Name: fleets fleets_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fleets
    ADD CONSTRAINT fleets_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: role_permissions role_permissions_role_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_permissions
    ADD CONSTRAINT role_permissions_role_code_fkey FOREIGN KEY (role_code) REFERENCES public.roles(role_code) ON DELETE CASCADE;


--
-- Name: subscriptions subscriptions_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT subscriptions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_role_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_role_code_fkey FOREIGN KEY (role_code) REFERENCES public.roles(role_code);


--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id) ON DELETE CASCADE;


--
-- Name: users users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: vehicle_models vehicle_models_oem_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicle_models
    ADD CONSTRAINT vehicle_models_oem_code_fkey FOREIGN KEY (oem_code) REFERENCES public.oems(oem_code);


--
-- Name: vehicles vehicles_fleet_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_fleet_id_tenant_id_fkey FOREIGN KEY (fleet_id, tenant_id) REFERENCES public.fleets(fleet_id, tenant_id);


--
-- Name: vehicles vehicles_home_workshop_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_home_workshop_id_tenant_id_fkey FOREIGN KEY (home_workshop_id, tenant_id) REFERENCES public.workshops(workshop_id, tenant_id);


--
-- Name: vehicles vehicles_model_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vehicles
    ADD CONSTRAINT vehicles_model_code_fkey FOREIGN KEY (model_code) REFERENCES public.vehicle_models(model_code);


--
-- Name: work_orders work_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(user_id);


--
-- Name: work_orders work_orders_failure_mode_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_failure_mode_fkey FOREIGN KEY (failure_mode) REFERENCES public.failure_modes(failure_mode);


--
-- Name: work_orders work_orders_source_alert_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_source_alert_id_tenant_id_fkey FOREIGN KEY (source_alert_id, tenant_id) REFERENCES public.alerts(alert_id, tenant_id);


--
-- Name: work_orders work_orders_vehicle_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_vehicle_id_tenant_id_fkey FOREIGN KEY (vehicle_id, tenant_id) REFERENCES public.vehicles(vehicle_id, tenant_id) ON DELETE CASCADE;


--
-- Name: work_orders work_orders_workshop_id_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_workshop_id_tenant_id_fkey FOREIGN KEY (workshop_id, tenant_id) REFERENCES public.workshops(workshop_id, tenant_id);


--
-- Name: workshops workshops_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workshops
    ADD CONSTRAINT workshops_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: alerts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: cost_parameters; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cost_parameters ENABLE ROW LEVEL SECURITY;

--
-- Name: driver_assignments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.driver_assignments ENABLE ROW LEVEL SECURITY;

--
-- Name: drivers; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.drivers ENABLE ROW LEVEL SECURITY;

--
-- Name: erasure_requests; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.erasure_requests ENABLE ROW LEVEL SECURITY;

--
-- Name: fleets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fleets ENABLE ROW LEVEL SECURITY;

--
-- Name: subscriptions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_log tenant_append; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_append ON public.audit_log FOR INSERT TO prognos_tenant WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: alerts tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.alerts TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: cost_parameters tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.cost_parameters TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: driver_assignments tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.driver_assignments TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: drivers tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.drivers TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: erasure_requests tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.erasure_requests TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: fleets tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.fleets TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: subscriptions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.subscriptions TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: tenants tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.tenants TO prognos_tenant USING ((tenant_id = public.app_tenant()));


--
-- Name: users tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.users TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: vehicles tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.vehicles TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: work_orders tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.work_orders TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: workshops tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.workshops TO prognos_tenant USING ((tenant_id = public.app_tenant())) WITH CHECK ((tenant_id = public.app_tenant()));


--
-- Name: audit_log tenant_read; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_read ON public.audit_log FOR SELECT TO prognos_tenant USING ((tenant_id = public.app_tenant()));


--
-- Name: tenants; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: vehicles; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.vehicles ENABLE ROW LEVEL SECURITY;

--
-- Name: work_orders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.work_orders ENABLE ROW LEVEL SECURITY;

--
-- Name: workshops; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.workshops ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--


