## ADDED Requirements

### Requirement: Error log menu item for platform admin
The system SHALL display an "错误日志" menu item in the platform admin portal sidebar, visible only to platform administrators.

#### Scenario: Platform admin sees menu item
- **WHEN** a platform admin accesses the portal (`/portal/*`)
- **THEN** the "错误日志" menu item is displayed in the sidebar with an appropriate icon

#### Scenario: Non-admin does not see menu item
- **WHEN** a non-platform-admin user accesses the portal
- **THEN** the "错误日志" menu item is NOT visible

### Requirement: Error log list page
The system SHALL provide an error log list page at `/portal/error-logs` that displays error records in a table sorted by time descending.

#### Scenario: Default view with all errors
- **WHEN** a platform admin navigates to `/portal/error-logs`
- **THEN** all error log records are displayed in reverse chronological order (newest first)
- **AND** each row shows: timestamp, module, error type, message summary, status, and action buttons

#### Scenario: Filter by status
- **WHEN** a platform admin selects a status filter (e.g., "unprocessed", "processed", "ignored")
- **THEN** only error logs matching the selected status are displayed

#### Scenario: Pagination
- **WHEN** there are more than the page size number of error logs
- **THEN** pagination controls are displayed
- **AND** the admin can navigate between pages

### Requirement: Error detail modal
The system SHALL support viewing the complete error information in a modal dialog.

#### Scenario: Open error detail
- **WHEN** a platform admin clicks the "详情" (detail) button on an error log row
- **THEN** a modal dialog opens displaying all fields: timestamp, module, error type, full message, and complete traceback

#### Scenario: Copy full error information
- **WHEN** a platform admin clicks the "复制" (copy) button in the detail modal
- **THEN** the complete error information (all fields including traceback) is copied to the clipboard
- **AND** a success toast notification is displayed

### Requirement: Update error log status
The system SHALL allow platform admins to change the processing status of error log records.

#### Scenario: Mark as processed
- **WHEN** a platform admin clicks "处理" (process) on an `unprocessed` error log
- **THEN** the status changes to `processed`
- **AND** the `processed_by` and `processed_at` fields are updated

#### Scenario: Mark as ignored
- **WHEN** a platform admin clicks "忽略" (ignore) on an `unprocessed` error log
- **THEN** the status changes to `ignored`
- **AND** the `processed_by` and `processed_at` fields are updated

#### Scenario: Unauthorized status change
- **WHEN** a non-platform-admin user attempts to update an error log's status
- **THEN** the system returns a 403 Forbidden error

### Requirement: Cleanup old error logs
The system SHALL provide a "清理旧日志（30天）" button on the error log page that deletes error log records older than 30 days from both the database and the filesystem.

#### Scenario: Cleanup with confirmation
- **WHEN** a platform admin clicks "清理旧日志（30天）"
- **THEN** a confirmation dialog is displayed
- **AND** upon confirmation, the system deletes all `log_error` records with timestamp older than 30 days
- **AND** the system deletes all log files in `log/agent/` with modification time older than 30 days
- **AND** the response shows the count of deleted database records and deleted files

#### Scenario: Cleanup denied for non-admin
- **WHEN** a non-platform-admin user attempts to trigger cleanup
- **THEN** the system returns a 403 Forbidden error