# universal-tenant-login Specification

## Purpose
TBD - created by archiving change universal-tenant-login. Update Purpose after archive.
## Requirements
### Requirement: Universal tenant login portal
The system SHALL provide a universal tenant login portal at the root URL (`/`) when demo mode is disabled.

#### Scenario: Demo mode disabled shows tenant portal
- **WHEN** demo mode is disabled (`VITE_DEMO_ENABLED=false`)
- **THEN** the frontend application SHALL display a tenant code input form at the root URL (`/`)

#### Scenario: Demo mode enabled shows demo interface
- **WHEN** demo mode is enabled (`VITE_DEMO_ENABLED=true`)
- **THEN** the frontend application SHALL display the demo chat interface at the root URL (`/`)

### Requirement: Tenant code input and validation
The system SHALL accept tenant codes via a web form and validate them before redirection.

#### Scenario: Valid tenant code submission
- **WHEN** user submits a valid tenant code through the portal form
- **THEN** the system SHALL redirect the user to the corresponding tenant frontend at `/t/{tenant_id}`

#### Scenario: Invalid tenant code submission
- **WHEN** user submits an invalid or non-existent tenant code
- **THEN** the system SHALL display an error message and keep the user on the portal page

#### Scenario: Tenant code format validation
. **WHEN** user enters a tenant code that does not match the required format (4-8 alphanumeric characters)
- **THEN** the frontend SHALL provide immediate client-side validation feedback

#### Scenario: Case-insensitive tenant code login
- **WHEN** user enters a tenant code in lowercase (e.g., "alibb") but it's stored in uppercase (e.g., "ALIBB")
- **THEN** the system SHALL accept the input and successfully redirect to the tenant

### Requirement: Tenant code verification API
The system SHALL provide an API endpoint for tenant code verification.

#### Scenario: API verification of valid tenant code
- **WHEN** a POST request is made to `/api/tenant/enter` with a valid tenant code
- **THEN** the API SHALL return a success response with redirect URL to `/t/{tenant_id}`

#### Scenario: API verification of invalid tenant code
- **WHEN** a POST request is made to `/api/tenant/enter` with an invalid tenant code
- **THEN** the API SHALL return an error response with appropriate error message

#### Scenario: API verification of inactive tenant
- **WHEN** a POST request is made to `/api/tenant/enter` with a tenant code for an inactive or expired tenant
- **THEN** the API SHALL return an error response indicating the tenant status issue

