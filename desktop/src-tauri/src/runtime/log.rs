use anyhow::Result;

use crate::app::types::{RuntimeLogEntry, now_iso};
use crate::storage::repository::Repository;

pub fn append_log(
    repo: &Repository,
    session_id: Option<String>,
    level: impl Into<String>,
    scope: impl Into<String>,
    message: impl Into<String>,
) -> Result<RuntimeLogEntry> {
    let mut entry = RuntimeLogEntry {
        id: None,
        session_id,
        level: level.into(),
        scope: scope.into(),
        message: message.into(),
        created_at: now_iso(),
        payload: serde_json::json!({}),
    };
    let id = repo.append_runtime_log(&entry)?;
    entry.id = Some(id);
    Ok(entry)
}
