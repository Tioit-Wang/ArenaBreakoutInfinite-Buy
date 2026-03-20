use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};

use anyhow::{Result, anyhow};
use tauri::{AppHandle, Emitter, async_runtime};
use uuid::Uuid;

use crate::app::types::{
    AppConfig, AutomationEvent, AutomationRunState, GoodsRecord, MultiTaskRecord, SingleTaskRecord,
    TemplateConfig, now_iso,
};
use crate::automation::{multi_runner, single_runner};
use crate::config::paths::AppPaths;
use crate::runtime::events::{
    AUTOMATION_LOG_EVENT, AUTOMATION_PROGRESS_EVENT, AUTOMATION_STATE_EVENT,
};
use crate::runtime::log::append_log;
use crate::storage::repository::Repository;

#[derive(Default)]
struct RuntimeControl {
    state: AutomationRunState,
    task: Option<async_runtime::JoinHandle<()>>,
    stop_requested: Option<Arc<AtomicBool>>,
}

#[derive(Clone)]
pub struct AutomationManager {
    paths: Arc<AppPaths>,
    repo: Arc<Repository>,
    control: Arc<Mutex<RuntimeControl>>,
}

impl AutomationManager {
    pub fn new(paths: Arc<AppPaths>, repo: Arc<Repository>) -> Self {
        Self {
            paths,
            repo,
            control: Arc::new(Mutex::new(RuntimeControl::default())),
        }
    }

    pub fn current_state(&self) -> AutomationRunState {
        self.control
            .lock()
            .expect("automation control mutex poisoned")
            .state
            .clone()
    }

    pub fn start_single(
        &self,
        app: AppHandle,
        task: SingleTaskRecord,
        goods: GoodsRecord,
        config: AppConfig,
        templates: Vec<TemplateConfig>,
        paths: Arc<AppPaths>,
    ) -> Result<AutomationRunState> {
        self.stop_internal();
        let session_id = format!("single-{}", Uuid::new_v4());
        let state = AutomationRunState {
            session_id: Some(session_id.clone()),
            mode: Some("single".to_string()),
            state: "running".to_string(),
            detail: Some("single automation started".to_string()),
            started_at: Some(now_iso()),
            updated_at: now_iso(),
        };
        self.repo.upsert_runtime_session(&state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, state.clone());
        let manager = self.clone();
        let state_clone = state.clone();
        let stop_requested = Arc::new(AtomicBool::new(false));
        let stop_requested_for_task = stop_requested.clone();
        let handle = async_runtime::spawn(async move {
            let event_manager = manager.clone();
            let event_app = app.clone();
            let emit = move |event: AutomationEvent| {
                let _ = event_manager.handle_event(&event_app, event);
            };
            let request = single_runner::SingleRunRequest {
                task,
                goods,
                config,
                templates,
                paths,
                repo: manager.repo.clone(),
                stop_requested: stop_requested_for_task,
            };
            let result = single_runner::run_single_flow(request, emit, session_id.clone()).await;
            let final_state = AutomationRunState {
                session_id: Some(session_id.clone()),
                mode: Some("single".to_string()),
                state: if result.is_ok() {
                    "completed"
                } else {
                    "failed"
                }
                .to_string(),
                detail: result
                    .err()
                    .map(|error| error.to_string())
                    .or_else(|| Some("single automation finished".to_string())),
                started_at: state_clone.started_at.clone(),
                updated_at: now_iso(),
            };
            let _ = manager.finish_run(&app, final_state);
        });
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state = state.clone();
        control.task = Some(handle);
        control.stop_requested = Some(stop_requested);
        Ok(state)
    }

    pub fn start_multi(
        &self,
        app: AppHandle,
        tasks: Vec<MultiTaskRecord>,
        config: AppConfig,
        templates: Vec<TemplateConfig>,
        paths: Arc<AppPaths>,
    ) -> Result<AutomationRunState> {
        if tasks.is_empty() {
            return Err(anyhow!("no multi tasks configured"));
        }
        self.stop_internal();
        let session_id = format!("multi-{}", Uuid::new_v4());
        let state = AutomationRunState {
            session_id: Some(session_id.clone()),
            mode: Some("multi".to_string()),
            state: "running".to_string(),
            detail: Some("multi automation started".to_string()),
            started_at: Some(now_iso()),
            updated_at: now_iso(),
        };
        self.repo.upsert_runtime_session(&state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, state.clone());
        let manager = self.clone();
        let state_clone = state.clone();
        let stop_requested = Arc::new(AtomicBool::new(false));
        let stop_requested_for_task = stop_requested.clone();
        let handle = async_runtime::spawn(async move {
            let event_manager = manager.clone();
            let event_app = app.clone();
            let emit = move |event: AutomationEvent| {
                let _ = event_manager.handle_event(&event_app, event);
            };
            let request = multi_runner::MultiRunRequest {
                tasks,
                config,
                templates,
                paths,
                repo: manager.repo.clone(),
                stop_requested: stop_requested_for_task,
            };
            let result = multi_runner::run_multi_flow(request, emit, session_id.clone()).await;
            let final_state = AutomationRunState {
                session_id: Some(session_id.clone()),
                mode: Some("multi".to_string()),
                state: if result.is_ok() {
                    "completed"
                } else {
                    "failed"
                }
                .to_string(),
                detail: result
                    .err()
                    .map(|error| error.to_string())
                    .or_else(|| Some("multi automation finished".to_string())),
                started_at: state_clone.started_at.clone(),
                updated_at: now_iso(),
            };
            let _ = manager.finish_run(&app, final_state);
        });
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state = state.clone();
        control.task = Some(handle);
        control.stop_requested = Some(stop_requested);
        Ok(state)
    }

    pub fn stop(&self, app: &AppHandle) -> Result<AutomationRunState> {
        self.stop_internal();
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state.state = "stopped".to_string();
        control.state.updated_at = now_iso();
        control.state.detail = Some("automation stopped by user".to_string());
        self.repo.upsert_runtime_session(&control.state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, control.state.clone());
        Ok(control.state.clone())
    }

    fn stop_internal(&self) {
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        if let Some(stop_requested) = control.stop_requested.as_ref() {
            stop_requested.store(true, Ordering::Relaxed);
        }
        if let Some(handle) = control.task.take() {
            handle.abort();
        }
        control.stop_requested = None;
    }

    fn finish_run(&self, app: &AppHandle, state: AutomationRunState) -> Result<()> {
        self.repo.upsert_runtime_session(&state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, state.clone());
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state = state;
        control.task = None;
        control.stop_requested = None;
        Ok(())
    }

    fn handle_event(&self, app: &AppHandle, event: AutomationEvent) -> Result<()> {
        let scope = format!("automation:{}", event.mode);
        let log = append_log(
            &self.paths.logs_dir,
            Some(event.session_id.clone()),
            event.level.clone(),
            scope,
            event.message.clone(),
        )?;
        let _ = app.emit(AUTOMATION_LOG_EVENT, &log);
        let _ = app.emit(AUTOMATION_PROGRESS_EVENT, &event);
        Ok(())
    }
}
