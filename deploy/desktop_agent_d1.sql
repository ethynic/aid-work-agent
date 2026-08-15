-- Desktop Agent D1 opt-in schema.
-- Execute explicitly before setting DESKTOP_AGENT_ENABLED=true.
-- This file is intentionally not referenced by normal deployment/update scripts.

CREATE TABLE IF NOT EXISTS desktop_remote_tool_invocations (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    response_json JSONB,
    events_json JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, idempotency_key)
);
ALTER TABLE desktop_remote_tool_invocations ALTER COLUMN response_json DROP NOT NULL;
ALTER TABLE desktop_remote_tool_invocations ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE desktop_remote_tool_invocations DROP CONSTRAINT IF EXISTS desktop_remote_tool_invocations_status_check;
ALTER TABLE desktop_remote_tool_invocations ADD CONSTRAINT desktop_remote_tool_invocations_status_check
    CHECK (status IN ('pending', 'running', 'cancelled', 'completed'));
CREATE INDEX IF NOT EXISTS idx_desktop_remote_tool_invocations_user
    ON desktop_remote_tool_invocations (tenant_id, user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS desktop_agent_turn_requests (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    response_json JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, idempotency_key)
);
ALTER TABLE desktop_agent_turn_requests ALTER COLUMN response_json DROP NOT NULL;
ALTER TABLE desktop_agent_turn_requests ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE desktop_agent_turn_requests DROP CONSTRAINT IF EXISTS desktop_agent_turn_requests_status_check;
ALTER TABLE desktop_agent_turn_requests ADD CONSTRAINT desktop_agent_turn_requests_status_check
    CHECK (status IN ('pending', 'completed'));

CREATE TABLE IF NOT EXISTS desktop_authorization_ticket_consumptions (
    ticket_id TEXT PRIMARY KEY,
    consumed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
