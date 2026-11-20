BEGIN;

CREATE TABLE normguard_tenant_grants (
    grant_id text PRIMARY KEY,
    principal_id text NOT NULL,
    tenant_id text NOT NULL,
    actions text[] NOT NULL CHECK (cardinality(actions) > 0),
    resource_types text[] NOT NULL CHECK (cardinality(resource_types) > 0),
    active_from timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > active_from)
);

CREATE INDEX normguard_grants_principal_active_idx
    ON normguard_tenant_grants (principal_id, active_from, expires_at);

CREATE TABLE normguard_policy_artifacts (
    tenant_id text NOT NULL,
    policy_id text NOT NULL,
    policy_version text NOT NULL,
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_sha256 char(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    bundle bytea NOT NULL CHECK (octet_length(bundle) > 0),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (
        tenant_id, policy_id, policy_version, payload_sha256, artifact_sha256
    )
);

CREATE TABLE normguard_tenant_activations (
    tenant_id text NOT NULL,
    policy_id text NOT NULL,
    policy_version text NOT NULL,
    payload_sha256 char(64) NOT NULL,
    artifact_sha256 char(64) NOT NULL,
    minimum_version text NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    change_id text NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, policy_id),
    FOREIGN KEY (
        tenant_id, policy_id, policy_version, payload_sha256, artifact_sha256
    ) REFERENCES normguard_policy_artifacts (
        tenant_id, policy_id, policy_version, payload_sha256, artifact_sha256
    ) ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION normguard_reject_policy_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'policy artifacts are immutable';
END;
$$;

CREATE TRIGGER normguard_policy_artifacts_immutable
BEFORE UPDATE OR DELETE ON normguard_policy_artifacts
FOR EACH ROW EXECUTE FUNCTION normguard_reject_policy_mutation();

ALTER TABLE normguard_tenant_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE normguard_tenant_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY normguard_grants_principal_isolation ON normguard_tenant_grants
    USING (
        principal_id = current_setting('normguard.principal_id', true)
    );

ALTER TABLE normguard_policy_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE normguard_policy_artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY normguard_policy_tenant_read ON normguard_policy_artifacts
    FOR SELECT USING (
        tenant_id = current_setting('normguard.tenant_id', true)
    );
CREATE POLICY normguard_policy_tenant_insert ON normguard_policy_artifacts
    FOR INSERT WITH CHECK (
        tenant_id = current_setting('normguard.tenant_id', true)
    );

ALTER TABLE normguard_tenant_activations ENABLE ROW LEVEL SECURITY;
ALTER TABLE normguard_tenant_activations FORCE ROW LEVEL SECURITY;
CREATE POLICY normguard_activation_tenant_read ON normguard_tenant_activations
    FOR SELECT USING (
        tenant_id = current_setting('normguard.tenant_id', true)
    );
CREATE POLICY normguard_activation_tenant_insert ON normguard_tenant_activations
    FOR INSERT WITH CHECK (
        tenant_id = current_setting('normguard.tenant_id', true)
    );
CREATE POLICY normguard_activation_tenant_update ON normguard_tenant_activations
    FOR UPDATE
    USING (tenant_id = current_setting('normguard.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('normguard.tenant_id', true));

REVOKE UPDATE, DELETE, TRUNCATE ON normguard_policy_artifacts FROM PUBLIC;
REVOKE ALL ON normguard_tenant_grants FROM PUBLIC;
REVOKE ALL ON normguard_policy_artifacts FROM PUBLIC;
REVOKE ALL ON normguard_tenant_activations FROM PUBLIC;

COMMIT;
