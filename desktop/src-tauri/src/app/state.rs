use std::sync::Arc;

use anyhow::{Context, Result};
use tauri::{AppHandle, Manager};

use crate::automation::ocr::OcrManager;
use crate::config::{paths::AppPaths, service::ConfigService};
use crate::legacy::importer::LegacyImporter;
use crate::runtime::manager::AutomationManager;
use crate::storage::repository::Repository;

pub struct AppState {
    pub paths: Arc<AppPaths>,
    pub repo: Arc<Repository>,
    pub config_service: Arc<ConfigService>,
    pub legacy_importer: Arc<LegacyImporter>,
    pub automation: Arc<AutomationManager>,
    pub ocr: Arc<OcrManager>,
}

impl AppState {
    pub fn bootstrap(app: &AppHandle) -> Result<Self> {
        let paths = Arc::new(AppPaths::resolve(app)?);
        app.asset_protocol_scope()
            .allow_directory(&paths.data_dir, true)
            .context("failed to allow asset access to data dir")?;
        if let Some(dir) = &paths.bundled_resources_dir {
            app.asset_protocol_scope()
                .allow_directory(dir, true)
                .with_context(|| format!("failed to allow asset access to {}", dir.display()))?;
        }

        let repo = Arc::new(Repository::new(paths.db_path.clone()));
        repo.init()?;

        let config_service = Arc::new(ConfigService::new(paths.clone(), repo.clone()));
        config_service.ensure_seeded(app)?;

        let automation = Arc::new(AutomationManager::new(paths.clone(), repo.clone()));
        let ocr = Arc::new(OcrManager::new(paths.clone(), repo.clone()));
        let legacy_importer = Arc::new(LegacyImporter::new(paths.clone(), repo.clone()));

        Ok(Self {
            paths,
            repo,
            config_service,
            legacy_importer,
            automation,
            ocr,
        })
    }
}
