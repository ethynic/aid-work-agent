## ADDED Requirements

### Requirement: Local Redis container setup
The system SHALL provide a Docker Compose configuration to run Redis for local development.

#### Scenario: Developer starts local environment
- **WHEN** developer runs `docker-compose up -d redis` from the `deploy/redis/` directory
- **THEN** a Redis 7.x container starts on port 6379 with persistent data volume
- **AND** the container auto-restarts on failure
- **AND** Redis data persists across container restarts via a named volume

### Requirement: Redis local development documentation
The system SHALL provide documentation for setting up and managing the local Redis container.

#### Scenario: Developer reads setup guide
- **WHEN** developer opens `deploy/redis/README.md`
- **THEN** they find step-by-step instructions for starting, stopping, and connecting to the local Redis
- **AND** they find connection parameters (host, port, default database) for application configuration
- **AND** they find troubleshooting steps for common issues (port conflict, volume permission)
