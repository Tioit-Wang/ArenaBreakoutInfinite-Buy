use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use anyhow::{Context, Result};

use crate::app::types::{RuntimeLogEntry, now_iso};

fn write_lock() -> &'static Mutex<()> {
    static LOG_WRITE_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOG_WRITE_LOCK.get_or_init(|| Mutex::new(()))
}

fn resolve_log_file(logs_dir: &Path, scope: &str) -> PathBuf {
    let file_name = if scope.ends_with(":single") {
        "single.jsonl"
    } else if scope.ends_with(":multi") {
        "multi.jsonl"
    } else {
        "runtime.jsonl"
    };
    logs_dir.join(file_name)
}

pub fn append_log(
    logs_dir: &Path,
    session_id: Option<String>,
    level: impl Into<String>,
    scope: impl Into<String>,
    message: impl Into<String>,
) -> Result<RuntimeLogEntry> {
    let scope = scope.into();
    let entry = RuntimeLogEntry {
        id: None,
        session_id,
        level: level.into(),
        scope: scope.clone(),
        message: message.into(),
        created_at: now_iso(),
        payload: serde_json::json!({}),
    };
    fs::create_dir_all(logs_dir)
        .with_context(|| format!("failed to create logs dir {}", logs_dir.display()))?;
    let path = resolve_log_file(logs_dir, &scope);
    let _guard = write_lock().lock().expect("runtime log mutex poisoned");
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("failed to open runtime log file {}", path.display()))?;
    serde_json::to_writer(&mut file, &entry)
        .with_context(|| format!("failed to encode runtime log file {}", path.display()))?;
    file.write_all(b"\n")
        .with_context(|| format!("failed to write runtime log newline {}", path.display()))?;
    Ok(entry)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::{Path, PathBuf};

    use anyhow::Result;

    use super::append_log;
    use crate::app::types::RuntimeLogEntry;

    fn temp_logs_dir(label: &str) -> PathBuf {
        let unique = format!(
            "arena-buyer-runtime-logs-{label}-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock before unix epoch")
                .as_nanos()
        );
        std::env::temp_dir().join(unique)
    }

    fn read_entries(path: &Path) -> Result<Vec<RuntimeLogEntry>> {
        let raw = fs::read_to_string(path)?;
        raw.lines()
            .filter(|line| !line.trim().is_empty())
            .map(serde_json::from_str::<RuntimeLogEntry>)
            .collect::<Result<Vec<_>, _>>()
            .map_err(Into::into)
    }

    #[test]
    fn writes_single_and_multi_logs_to_separate_files() -> Result<()> {
        let logs_dir = temp_logs_dir("split");
        append_log(
            &logs_dir,
            Some("single-session".to_string()),
            "info",
            "automation:single",
            "single log",
        )?;
        append_log(
            &logs_dir,
            Some("multi-session".to_string()),
            "warn",
            "automation:multi",
            "multi log",
        )?;

        let single_entries = read_entries(&logs_dir.join("single.jsonl"))?;
        let multi_entries = read_entries(&logs_dir.join("multi.jsonl"))?;
        assert_eq!(single_entries.len(), 1);
        assert_eq!(multi_entries.len(), 1);
        assert_eq!(single_entries[0].scope, "automation:single");
        assert_eq!(single_entries[0].message, "single log");
        assert_eq!(multi_entries[0].scope, "automation:multi");
        assert_eq!(multi_entries[0].message, "multi log");

        let _ = fs::remove_dir_all(logs_dir);
        Ok(())
    }

    #[test]
    fn writes_complete_runtime_log_json_line() -> Result<()> {
        let logs_dir = temp_logs_dir("shape");
        append_log(
            &logs_dir,
            Some("single-session".to_string()),
            "error",
            "automation:single",
            "log payload",
        )?;

        let entries = read_entries(&logs_dir.join("single.jsonl"))?;
        assert_eq!(entries.len(), 1);
        let entry = &entries[0];
        assert_eq!(entry.id, None);
        assert_eq!(entry.session_id.as_deref(), Some("single-session"));
        assert_eq!(entry.level, "error");
        assert_eq!(entry.scope, "automation:single");
        assert_eq!(entry.message, "log payload");
        assert!(!entry.created_at.is_empty());

        let _ = fs::remove_dir_all(logs_dir);
        Ok(())
    }
}
