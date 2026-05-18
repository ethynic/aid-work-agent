## 1. Database

- [x] 1.1 Add `log_error` table with indexes to `deploy/init-postgres.sql`
- [x] 1.2 Add `log_error` table migration to `deploy/db_update.sql`

## 2. Backend — Error Log Storage

- [x] 2.1 Create `src/core/error_log_sink.py`: implement custom Loguru sink that writes ERROR-level logs to `log_error` table asynchronously
- [x] 2.2 Register the error log sink in `src/config/logging.py` `setup_logging()` function

## 3. Backend — Admin API

- [x] 3.1 Create `src/api/admin_error_logs.py`: `GET /api/admin/error-logs` — query error logs with pagination, status filter, sorted by time DESC
- [x] 3.2 Add `PUT /api/admin/error-logs/{log_id}/status` — update processing status (requires platform_admin role)
- [x] 3.3 Add `DELETE /api/admin/error-logs/cleanup` — delete DB records older than 30 days + delete old log files in `log/agent/`
- [x] 3.4 Register router in `src/main.py`

## 4. Frontend — Error Log Page

- [x] 4.1 Create `frontend/src/api/error-logs.ts` — API client for error log endpoints with auth headers
- [x] 4.2 Create `frontend/src/components/saas/ErrorLogs.vue` — error log list page with status filter, pagination, detail modal with copy button, status update buttons, and "清理旧日志（30天）" button with confirmation dialog
- [x] 4.3 Add route `/portal/error-logs` in `frontend/src/main.ts`
- [x] 4.4 Add menu item "错误日志" in `PortalLayout.vue` portalMenuItems

## 5. Verification

- [x] 5.1 Run `cd frontend && npm run build` to verify no build errors
- [ ] 5.2 Trigger a test error and verify it appears in the error log page
- [ ] 5.3 Verify status update workflow (unprocessed → processed/ignored)
- [ ] 5.4 Verify original `log/agent/error.log` file continues to work
