-- Initialize AIOps database schema
-- This script runs on first PostgreSQL container start

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Incidents table
CREATE TABLE IF NOT EXISTS incidents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    fingerprint VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    severity VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    source VARCHAR(100) NOT NULL,
    cluster VARCHAR(255) NOT NULL,
    namespace VARCHAR(255) NOT NULL,
    service VARCHAR(255),
    labels JSONB DEFAULT '{}',
    annotations JSONB DEFAULT '{}',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_incident_fingerprint UNIQUE (fingerprint)
);

-- Evidence table
CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    evidence_type VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL,
    entity_name VARCHAR(255) NOT NULL,
    entity_namespace VARCHAR(255) NOT NULL,
    data JSONB NOT NULL,
    signal_strength FLOAT DEFAULT 0.5,
    collected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    time_window_start TIMESTAMP WITH TIME ZONE,
    time_window_end TIMESTAMP WITH TIME ZONE
);

-- Hypotheses table
CREATE TABLE IF NOT EXISTS hypotheses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    category VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    rank INTEGER NOT NULL,
    supporting_evidence_ids UUID[] DEFAULT '{}',
    contradicting_evidence_ids UUID[] DEFAULT '{}',
    recommended_actions TEXT[] DEFAULT '{}',
    why_not_notes TEXT,
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    generated_by VARCHAR(50) NOT NULL
);

-- Remediation actions table
CREATE TABLE IF NOT EXISTS remediation_actions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    hypothesis_id UUID REFERENCES hypotheses(id),
    idempotency_key VARCHAR(500) NOT NULL,
    action_type VARCHAR(100) NOT NULL,
    target_resource VARCHAR(255) NOT NULL,
    target_namespace VARCHAR(255) NOT NULL,
    parameters JSONB DEFAULT '{}',
    risk_level VARCHAR(50) NOT NULL,
    blast_radius_score FLOAT DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'proposed',
    requires_approval BOOLEAN DEFAULT TRUE,
    approved_by VARCHAR(255),
    approved_at TIMESTAMP WITH TIME ZONE,
    executed_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    execution_result JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_action_idempotency UNIQUE (idempotency_key)
);

-- Verification results table
CREATE TABLE IF NOT EXISTS verification_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action_id UUID NOT NULL REFERENCES remediation_actions(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    success BOOLEAN NOT NULL,
    metrics_improved BOOLEAN NOT NULL,
    error_rate_before FLOAT,
    error_rate_after FLOAT,
    latency_before FLOAT,
    latency_after FLOAT,
    verification_details JSONB DEFAULT '{}',
    verified_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Audit log table
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID REFERENCES incidents(id),
    action_type VARCHAR(100) NOT NULL,
    actor VARCHAR(255) NOT NULL,
    details JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Runbooks table
CREATE TABLE IF NOT EXISTS runbooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    commands JSONB DEFAULT '[]',
    dashboard_links JSONB DEFAULT '[]',
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_incidents_fingerprint ON incidents(fingerprint);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_cluster_namespace ON incidents(cluster, namespace);
CREATE INDEX IF NOT EXISTS idx_incidents_started_at ON incidents(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_incident_id ON evidence(incident_id);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence(evidence_type);
CREATE INDEX IF NOT EXISTS idx_hypotheses_incident_id ON hypotheses(incident_id);
CREATE INDEX IF NOT EXISTS idx_actions_incident_id ON remediation_actions(incident_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON remediation_actions(status);
CREATE INDEX IF NOT EXISTS idx_audit_logs_incident_id ON audit_logs(incident_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- Trigger for updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_incidents_updated_at
    BEFORE UPDATE ON incidents
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
