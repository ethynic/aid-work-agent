from loguru import logger


def init_social_media_tables(conn) -> None:
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_accounts (
            account_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            platform TEXT,
            display_name TEXT,
            external_account_id TEXT,
            auth_type TEXT,
            credentials_encrypted TEXT,
            credential_key_version TEXT,
            status TEXT DEFAULT 'active',
            capabilities_json JSONB,
            capability_expires_at TIMESTAMP,
            last_validated_at TIMESTAMP,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_social_accounts_unique
        ON social_accounts(tenant_id, platform, external_account_id)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_content_plans (
            plan_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            name TEXT,
            period_start DATE,
            period_end DATE,
            goal TEXT,
            target_audience TEXT,
            status TEXT DEFAULT 'draft',
            owner_user_id TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_content_items (
            item_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            plan_id TEXT,
            topic TEXT,
            objective TEXT,
            planned_at TIMESTAMP,
            timezone TEXT,
            owner_user_id TEXT,
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_content_masters (
            master_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            item_id TEXT,
            title TEXT,
            brief TEXT,
            facts_json JSONB,
            source_refs_json JSONB,
            brand_constraints_json JSONB,
            revision INTEGER DEFAULT 1,
            content_hash TEXT,
            status TEXT DEFAULT 'draft',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_media_assets (
            asset_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            storage_file_id TEXT,
            asset_type TEXT,
            mime_type TEXT,
            file_size INTEGER,
            checksum TEXT,
            source_type TEXT,
            source_uri TEXT,
            license_type TEXT,
            license_owner TEXT,
            license_expires_at TIMESTAMP,
            status TEXT DEFAULT 'active',
            metadata_json JSONB,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_content_asset_links (
            link_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            master_id TEXT,
            asset_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_content_variants (
            variant_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            master_id TEXT,
            account_id TEXT,
            platform TEXT,
            content_type TEXT,
            revision INTEGER DEFAULT 1,
            content_json JSONB,
            content_hash TEXT,
            spec_version TEXT,
            prompt_version TEXT,
            status TEXT DEFAULT 'draft',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_social_variants_revision
        ON social_content_variants(tenant_id, variant_id, revision)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_review_records (
            review_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            variant_id TEXT,
            variant_revision INTEGER,
            content_hash TEXT,
            decision TEXT,
            comment TEXT,
            reviewer_user_id TEXT,
            reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_publish_jobs (
            job_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            account_id TEXT,
            variant_id TEXT,
            variant_revision INTEGER,
            content_hash TEXT,
            publish_mode TEXT,
            scheduled_at TIMESTAMP,
            timezone TEXT,
            status TEXT DEFAULT 'draft',
            idempotency_key TEXT UNIQUE,
            publish_snapshot_json JSONB,
            external_task_id TEXT,
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            next_retry_at TIMESTAMP,
            lease_owner TEXT,
            lease_expires_at TIMESTAMP,
            last_error_code TEXT,
            last_error_message TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_due ON social_publish_jobs(status, scheduled_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_tenant_account ON social_publish_jobs(tenant_id, account_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_external_task ON social_publish_jobs(external_task_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_social_publish_jobs_lease ON social_publish_jobs(lease_expires_at)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_publish_attempts (
            attempt_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            job_id TEXT,
            attempt_no INTEGER,
            trigger_type TEXT,
            request_summary_json JSONB,
            response_summary_json JSONB,
            status TEXT,
            platform_error_code TEXT,
            error_category TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            duration_ms INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_published_contents (
            published_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            job_id TEXT,
            account_id TEXT,
            variant_id TEXT,
            platform TEXT,
            external_content_id TEXT,
            external_url TEXT,
            confirmation_source TEXT,
            published_at TIMESTAMP,
            confirmed_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_metric_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            account_id TEXT,
            published_id TEXT,
            metric_date DATE,
            metric_definition TEXT,
            normalized_metrics_json JSONB,
            raw_metrics_json JSONB,
            source_type TEXT,
            collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            import_batch_id TEXT
        )
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_social_metric_snapshots_unique
        ON social_metric_snapshots(tenant_id, account_id, published_id, metric_date, metric_definition)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS social_data_import_batches (
            batch_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            account_id TEXT,
            platform TEXT,
            template_version TEXT,
            file_digest TEXT,
            status TEXT DEFAULT 'uploaded',
            error_summary TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    logger.info("社媒运营表初始化完成")
