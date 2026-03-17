pub const MIGRATIONS: &[&str] = &[
    r#"
    CREATE TABLE IF NOT EXISTS app_config (
      key TEXT PRIMARY KEY,
      value_json TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS templates (
      id TEXT PRIMARY KEY,
      slug TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      kind TEXT NOT NULL,
      path TEXT NOT NULL,
      confidence REAL NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS goods (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      search_name TEXT NOT NULL,
      big_category TEXT NOT NULL,
      sub_category TEXT NOT NULL,
      favorite INTEGER NOT NULL DEFAULT 0,
      image_path TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS single_tasks (
      id TEXT PRIMARY KEY,
      item_id TEXT NOT NULL,
      item_name TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      order_index INTEGER NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS multi_tasks (
      id TEXT PRIMARY KEY,
      item_id TEXT NOT NULL,
      name TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      order_index INTEGER NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS price_history (
      id TEXT PRIMARY KEY,
      item_id TEXT NOT NULL,
      item_name TEXT NOT NULL,
      category TEXT,
      price INTEGER NOT NULL,
      observed_at TEXT NOT NULL,
      payload_json TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS purchase_history (
      id TEXT PRIMARY KEY,
      item_id TEXT NOT NULL,
      item_name TEXT NOT NULL,
      category TEXT,
      price INTEGER NOT NULL,
      qty INTEGER NOT NULL,
      amount INTEGER NOT NULL,
      task_id TEXT,
      task_name TEXT,
      used_max INTEGER,
      purchased_at TEXT NOT NULL,
      payload_json TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS runtime_sessions (
      id TEXT PRIMARY KEY,
      mode TEXT NOT NULL,
      state TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      started_at TEXT,
      ended_at TEXT,
      updated_at TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS runtime_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT,
      level TEXT NOT NULL,
      scope TEXT NOT NULL,
      message TEXT NOT NULL,
      created_at TEXT NOT NULL,
      payload_json TEXT NOT NULL
    );
    "#,
    r#"
    CREATE TABLE IF NOT EXISTS imports (
      id TEXT PRIMARY KEY,
      source_root TEXT NOT NULL,
      status TEXT NOT NULL,
      summary_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    "#,
];
