# Tables the migration chain must create at head. Extended by each phase's migration.
TABLES_BY_HEAD = ["users", "user_groups", "conversations", "turns", "patterns", "cache_entries",
                  "request_logs", "ledger", "overhead", "notifications", "runtime_config",
                  "collections", "documents", "chunks", "kb_events", "drift_samples",
                  "agent_runs", "datasets", "integrations", "ledger_rollup", "overhead_rollup"]
