## ADDED Requirements

### Requirement: Error log automatic persistence
The system SHALL automatically persist all ERROR-level logs from Loguru to the `log_error` database table.

#### Scenario: Error logged via logger.error()
- **WHEN** any module calls `logger.error()` with an exception
- **THEN** the error log is written to `log_error` table with timestamp, module name, error type, message, and full traceback
- **AND** the status defaults to `unprocessed`

#### Scenario: Warning or Info log is ignored
- **WHEN** any module calls `logger.warning()` or `logger.info()`
- **THEN** the log is NOT written to `log_error` table

#### Scenario: Database write failure does not block
- **WHEN** the `log_error` table write fails (e.g., database unavailable)
- **THEN** the original file-based error logging continues to work normally
- **AND** a warning is logged about the DB write failure

### Requirement: Error log table schema
The system SHALL have a `log_error` table with the following columns: id (SERIAL PRIMARY KEY), timestamp (TIMESTAMP), module (TEXT), error_type (TEXT), message (TEXT), traceback (TEXT), status (TEXT DEFAULT 'unprocessed'), processed_by (TEXT), processed_at (TIMESTAMP).

#### Scenario: Table creation on startup
- **WHEN** the system starts up
- **THEN** the `log_error` table is created if it does not exist
- **AND** indexes are created on `timestamp DESC` and `status`

### Requirement: Error log status enumeration
The system SHALL support three status values for error log records: `unprocessed`, `processed`, `ignored`.

#### Scenario: New error log has default status
- **WHEN** a new error log is created
- **THEN** its status is `unprocessed`

#### Scenario: Status update by platform admin
- **WHEN** a platform admin updates an error log's status
- **THEN** the system records the new status, the admin user ID as `processed_by`, and the current timestamp as `processed_at`