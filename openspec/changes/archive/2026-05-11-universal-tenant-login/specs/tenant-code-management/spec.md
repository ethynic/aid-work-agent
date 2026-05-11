## ADDED Requirements

### Requirement: Tenant code field in database
The system SHALL store a unique tenant code for each tenant in the database.

#### Scenario: Tenant code field exists
- **WHEN** inspecting the tenants table schema
- **THEN** there SHALL be a `tenant_code` column without UNIQUE constraint (uniqueness enforced at application layer)

#### Scenario: Tenant code format enforcement
- **WHEN** creating or updating a tenant code
- **THEN** the system SHALL enforce the format: 4-8 uppercase alphanumeric characters (`[A-Z0-9]{4,8}`)

#### Scenario: Tenant code storage in uppercase
- **WHEN** a tenant code is saved to the database
- **THEN** it SHALL be stored in uppercase letters regardless of input case

### Requirement: Tenant code uniqueness
The system SHALL ensure tenant codes are globally unique across all tenants.

#### Scenario: Duplicate tenant code prevention
- **WHEN** attempting to create or update a tenant with a code that already exists
- **THEN** the system SHALL reject the operation with a uniqueness error

#### Scenario: Case-insensitive uniqueness
- **WHEN** comparing tenant codes for uniqueness
- **THEN** the system SHALL treat codes as case-insensitive (e.g., "ALIBB" and "alibb" are considered duplicates)

### Requirement: Tenant code generation for existing tenants
The system SHALL generate default tenant codes for existing tenants during migration.

#### Scenario: Default code generation
- **WHEN** migrating existing tenants
- **THEN** each tenant SHALL receive a unique default tenant code based on their tenant_id

#### Scenario: Default code format
- **WHEN** generating default tenant codes
- **THEN** the code SHALL follow the pattern "t" + last 5 characters of tenant_id (e.g., `tenant_a1b2c3d4e5f6` → `TD4E5F`)

### Requirement: Tenant code management in admin interface
The system SHALL provide tenant code management capabilities in the platform admin interface.

#### Scenario: Tenant creation with code
- **WHEN** creating a new tenant in the admin interface
- **THEN** the admin SHALL be required to provide a unique tenant code

#### Scenario: Tenant code validation in UI
- **WHEN** entering a tenant code in the admin interface
- **THEN** the UI SHALL provide real-time validation for format and uniqueness

#### Scenario: UI hint vs actual validation
- **WHEN** viewing the tenant code input field in admin interface
- **THEN** the UI SHALL show a hint "4-6位字母数字组合" but actually accept 4-8 characters

#### Scenario: Tenant code editing
- **WHEN** editing an existing tenant
- **THEN** the admin SHALL be able to modify the tenant code (with uniqueness validation)

#### Scenario: Tenant code display
- **WHEN** viewing the tenant list or details
- **THEN** the tenant code SHALL be displayed alongside other tenant information

### Requirement: Tenant code lookup API
The system SHALL provide API endpoints for tenant code lookup and validation.

#### Scenario: Lookup tenant by code
- **WHEN** querying the system with a tenant code
- **THEN** the system SHALL return the corresponding tenant information if the code exists

#### Scenario: Tenant status verification
- **WHEN** looking up a tenant by code
- **THEN** the system SHALL include tenant status (active, suspended, expired) in the response