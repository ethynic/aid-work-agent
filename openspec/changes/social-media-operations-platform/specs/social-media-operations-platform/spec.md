# social-media-operations-platform Specification

## Requirement: Tenant-isolated social media operations

The system SHALL store social media accounts, content plans, content variants, review records, publish jobs and analytics with tenant isolation.

### Scenario: Account credentials are never returned

- **WHEN** a tenant lists social media accounts
- **THEN** the response SHALL include account status, platform, display name and capabilities
- **AND** the response SHALL NOT include plaintext credentials or encrypted credential blobs

### Scenario: Publish requires approved immutable revision

- **WHEN** a publish job is created
- **THEN** the system SHALL verify the matching approved review record by `variant_id`, `revision` and `content_hash`
- **AND** the system SHALL store the approved revision content in `publish_snapshot_json`

### Scenario: Capability controls available publish mode

- **WHEN** an account lacks `api_publish`
- **THEN** the system SHALL reject API publish job creation
- **WHEN** an account supports `assisted_publish`
- **THEN** the system MAY create a job in `ready_for_manual_publish`

### Scenario: Manual confirmation is distinct from API publishing

- **WHEN** an operator manually confirms a video channel publish job
- **THEN** the job SHALL become `manually_confirmed`
- **AND** the published content SHALL record `confirmation_source=manual`
