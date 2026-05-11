## ADDED Requirements

### Requirement: Unified tenant login form
The system SHALL provide a unified login form at the root URL (`/`) when demo mode is disabled, allowing users to complete tenant verification and user authentication in a single step.

#### Scenario: Demo mode disabled shows unified login form
- **WHEN** demo mode is disabled (`VITE_DEMO_ENABLED=false`)
- **THEN** the frontend application SHALL display a unified login form at the root URL (`/`) containing:
  - Tenant code input field
  - Username/phone number input field with toggle between input types
  - Password input field
  - Graphical captcha input field

#### Scenario: Unified form submission with valid credentials
- **WHEN** user submits the unified login form with valid tenant code, identifier, password, and captcha
- **THEN** the system SHALL:
  - Verify the tenant code exists and is active
  - Verify the user credentials (identifier + password)
  - Verify the graphical captcha
  - Check that the user belongs to the specified tenant
  - Generate an authentication token
  - Redirect the user to the tenant frontend at `/t/{tenant_id}`

#### Scenario: Tenant code memory feature
- **WHEN** user successfully logs in with a tenant code
- **THEN** the system SHALL store the tenant code in localStorage with key `last_tenant_code`

#### Scenario: Automatic tenant code pre-fill
- **WHEN** user visits the unified login form and localStorage contains a `last_tenant_code` value
- **THEN** the tenant code input field SHALL be pre-filled with that value

#### Scenario: Unified error handling
- **WHEN** user submits the unified login form with invalid data (e.g., wrong tenant code, incorrect password, expired captcha)
- **THEN** the system SHALL return a unified error response containing all validation errors

#### Scenario: Backward compatibility preserved
- **WHEN** demo mode is enabled (`VITE_DEMO_ENABLED=true`)
- **THEN** the frontend application SHALL display the demo chat interface at the root URL (`/`)
- **WHEN** user accesses `/t/{tenant_id}/login` directly
- **THEN** the system SHALL display the existing tenant-specific login page
- **WHEN** user accesses `/portal/login` directly
- **THEN** the system SHALL display the platform administrator login page

### Requirement: Unified login API
The system SHALL provide a unified login API endpoint that validates tenant code and user credentials in a single request.

#### Scenario: API validation of unified login
- **WHEN** a POST request is made to `/api/auth/unified-login` with valid tenant code, identifier, password, and captcha
- **THEN** the API SHALL return a success response with authentication token and redirect URL

#### Scenario: API validation with invalid tenant code
- **WHEN** a POST request is made to `/api/auth/unified-login` with an invalid or non-existent tenant code
- **THEN** the API SHALL return an error response with field-specific error for tenant code

#### Scenario: API validation with invalid user credentials
- **WHEN** a POST request is made to `/api/auth/unified-login` with valid tenant code but invalid user credentials
- **THEN** the API SHALL return an error response with field-specific error for identifier/password

#### Scenario: API validation with user not belonging to tenant
- **WHEN** a POST request is made to `/api/auth/unified-login` with valid tenant code and valid user credentials, but the user does not belong to the specified tenant
- **THEN** the API SHALL return an error response indicating the user does not belong to the tenant

#### Scenario: API validation with expired or inactive tenant
- **WHEN** a POST request is made to `/api/auth/unified-login` with a tenant code for an expired or inactive tenant
- **THEN** the API SHALL return an error response indicating the tenant status issue