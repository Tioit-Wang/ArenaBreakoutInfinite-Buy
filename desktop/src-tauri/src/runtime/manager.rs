use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};

use anyhow::{Result, anyhow};
use tauri::{AppHandle, Emitter};
use tokio::task::JoinHandle;
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
    task: Option<JoinHandle<()>>,
    pause_flag: Option<Arc<AtomicBool>>,
}

#[derive(Clone)]
pub struct AutomationManager {
    repo: Arc<Repository>,
    control: Arc<Mutex<RuntimeControl>>,
}

impl AutomationManager {
    pub fn new(repo: Arc<Repository>) -> Self {
        Self {
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
        let pause_flag = Arc::new(AtomicBool::new(false));
        let state = AutomationRunState {
            session_id: Some(session_id.clone()),
            mode: Some("single".to_string()),
            state: "running".to_string(),
            detail: Some("single automation started".to_string()),
            started_at: Some(now_iso()),
            updated_at: now_iso(),
            can_pause: true,
            can_resume: false,
        };
        self.repo.upsert_runtime_session(&state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, state.clone());
        let manager = self.clone();
        let state_clone = state.clone();
        let pause_clone = pause_flag.clone();
        let handle = tokio::spawn(async move {
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
                pause_flag: pause_clone,
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
                can_pause: false,
                can_resume: false,
            };
            let _ = manager.finish_run(&app, final_state);
        });
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state = state.clone();
        control.task = Some(handle);
        control.pause_flag = Some(pause_flag);
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
        let pause_flag = Arc::new(AtomicBool::new(false));
        let state = AutomationRunState {
            session_id: Some(session_id.clone()),
            mode: Some("multi".to_string()),
            state: "running".to_string(),
            detail: Some("multi automation started".to_string()),
            started_at: Some(now_iso()),
            updated_at: now_iso(),
            can_pause: true,
            can_resume: false,
        };
        self.repo.upsert_runtime_session(&state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, state.clone());
        let manager = self.clone();
        let state_clone = state.clone();
        let pause_clone = pause_flag.clone();
        let handle = tokio::spawn(async move {
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
                pause_flag: pause_clone,
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
                can_pause: false,
                can_resume: false,
            };
            let _ = manager.finish_run(&app, final_state);
        });
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state = state.clone();
        control.task = Some(handle);
        control.pause_flag = Some(pause_flag);
        Ok(state)
    }

    pub fn pause(&self, app: &AppHandle) -> Result<AutomationRunState> {
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state.state = "paused".to_string();
        control.state.updated_at = now_iso();
        control.state.can_pause = false;
        control.state.can_resume = true;
        if let Some(flag) = &control.pause_flag {
            flag.store(true, Ordering::SeqCst);
        }
        self.repo.upsert_runtime_session(&control.state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, control.state.clone());
        Ok(control.state.clone())
    }

    pub fn resume(&self, app: &AppHandle) -> Result<AutomationRunState> {
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        control.state.state = "running".to_string();
        control.state.updated_at = now_iso();
        control.state.can_pause = true;
        control.state.can_resume = false;
        if let Some(flag) = &control.pause_flag {
            flag.store(false, Ordering::SeqCst);
        }
        self.repo.upsert_runtime_session(&control.state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, control.state.clone());
        Ok(control.state.clone())
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
        control.state.can_pause = false;
        control.state.can_resume = false;
        self.repo.upsert_runtime_session(&control.state)?;
        let _ = app.emit(AUTOMATION_STATE_EVENT, control.state.clone());
        Ok(control.state.clone())
    }

    fn stop_internal(&self) {
        let mut control = self
            .control
            .lock()
            .expect("automation control mutex poisoned");
        if let Some(flag) = &control.pause_flag {
            flag.store(false, Ordering::SeqCst);
        }
        control.pause_flag = None;
        if let Some(handle) = control.task.take() {
            handle.abort();
        }
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
        control.pause_flag = None;
        Ok(())
    }

    fn handle_event(&self, app: &AppHandle, event: AutomationEvent) -> Result<()> {
        let scope = format!("automation:{}", event.mode);
        let log = append_log(
            &self.repo,
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
