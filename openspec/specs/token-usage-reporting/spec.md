# token-usage-reporting Specification

## Purpose
TBD - created by archiving change token-reports. Update Purpose after archive.
## Requirements
### Requirement: Platform administrators can view cross-tenant token usage summary
The system SHALL provide a report showing token consumption across all tenants for a selected month, excluding test data.

#### Scenario: Platform admin views token usage for current month
- **WHEN** a platform administrator navigates to the platform token usage report page
- **AND** the current month is selected (default)
- **THEN** the system SHALL display a table with one row per tenant
- **AND** each row SHALL contain: tenant code, tenant name, input tokens (in millions), output tokens (in millions), conversation count
- **AND** the last row SHALL show aggregated totals for all tenants
- **AND** all token values SHALL be displayed in millions with 2 decimal places
- **AND** conversations with prompt_tokens=0 AND completion_tokens=0 SHALL be excluded from statistics

#### Scenario: Platform admin filters by specific month
- **WHEN** a platform administrator selects a specific month from the date picker
- **THEN** the system SHALL display token usage data for that month only
- **AND** the data SHALL be filtered to include only records created within that month

#### Scenario: Non-platform admin attempts to access platform report
- **WHEN** a non-platform administrator (tenant admin or regular user) attempts to access the platform token usage report
- **THEN** the system SHALL deny access with appropriate error response

### Requirement: Tenant administrators can view detailed token usage for their tenant
The system SHALL provide a detailed report showing token consumption per conversation for a tenant's selected month, excluding test data, with pagination.

#### Scenario: Tenant admin views token usage for current month
- **WHEN** a tenant administrator navigates to the tenant token usage report page
- **AND** the current month is selected (default)
- **THEN** the system SHALL display a table with one row per conversation
- **AND** each row SHALL contain: user message preview (first 10 characters), input tokens (in millions), output tokens (in millions), creation time
- **AND** the last row SHALL show aggregated totals for the month including total conversations
- **AND** all token values SHALL be displayed in millions with 2 decimal places
- **AND** conversations with prompt_tokens=0 AND completion_tokens=0 SHALL be excluded from statistics
- **AND** the table SHALL be paginated with 100 rows per page

#### Scenario: Tenant admin filters by specific month
- **WHEN** a tenant administrator selects a specific month from the date picker
- **THEN** the system SHALL display token usage data for that month only
- **AND** the data SHALL be filtered to include only records created within that month

#### Scenario: Tenant admin navigates to second page
- **WHEN** a tenant administrator clicks on page 2 of the pagination controls
- **THEN** the system SHALL display conversations 101-200 for the selected month

#### Scenario: Non-admin attempts to access tenant report
- **WHEN** a non-administrator (regular user) attempts to access the tenant token usage report
- **THEN** the system SHALL deny access with appropriate error response

#### Scenario: Tenant admin attempts to access other tenant's report
- **WHEN** a tenant administrator attempts to access another tenant's token usage report
- **THEN** the system SHALL deny access with appropriate error response

### Requirement: Token values are displayed consistently in millions
The system SHALL consistently display token values in millions with 2 decimal places across all reports.

#### Scenario: Token value formatting
- **WHEN** a token value of 1,234,567 is retrieved from the database
- **THEN** the system SHALL display it as "1.23 million tokens"
- **AND** the API response SHALL include the raw token count for client-side formatting

### Requirement: Date range filtering uses month boundaries correctly
The system SHALL filter data by calendar month boundaries regardless of timezone.

#### Scenario: Month boundary filtering
- **WHEN** a user selects month "2026-05"
- **THEN** the system SHALL include records with created_at from "2026-05-01 00:00:00" to "2026-05-31 23:59:59" in the database server's timezone

### Requirement: Test data is excluded from statistics
The system SHALL exclude historical test data from all token usage calculations.

#### Scenario: Test data exclusion
- **WHEN** the system queries chat_records for token usage statistics
- **THEN** records with prompt_tokens=0 AND completion_tokens=0 SHALL be excluded from all calculations
- **AND** this exclusion SHALL apply to both summary and detailed reports

