mod app;
mod automation;
mod commands;
mod config;
mod legacy;
mod runtime;
mod storage;

use anyhow::Context;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_log::{Target, TargetKind};
use url::Url;

use crate::app::state::AppState;

pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::new()
                .targets([
                    Target::new(TargetKind::Stdout),
                    Target::new(TargetKind::LogDir { file_name: None }),
                    Target::new(TargetKind::Webview),
                ])
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            let state =
                AppState::bootstrap(app.handle()).context("failed to bootstrap app state")?;
            app.manage(state);
            create_main_window(app).context("failed to create main window")?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bootstrap::bootstrap,
            commands::config::config_get,
            commands::config::config_save,
            commands::goods::goods_list,
            commands::goods::goods_save,
            commands::goods::goods_delete,
            commands::tasks::single_tasks_list,
            commands::tasks::single_tasks_save,
            commands::tasks::single_tasks_reorder,
            commands::tasks::single_tasks_delete,
            commands::tasks::multi_tasks_list,
            commands::tasks::multi_tasks_save,
            commands::tasks::multi_tasks_reorder,
            commands::tasks::multi_tasks_delete,
            commands::templates::templates_list,
            commands::templates::templates_save,
            commands::templates::templates_test,
            commands::templates::templates_validate_file,
            commands::templates::templates_probe_match,
            commands::templates::templates_import_image,
            commands::templates::templates_capture_region,
            commands::templates::templates_capture_interactive,
            commands::templates::goods_import_image,
            commands::templates::goods_capture_card_image,
            commands::templates::goods_capture_card_interactive,
            commands::history::history_query_prices,
            commands::history::history_query_purchases,
            commands::history::history_query_summary,
            commands::history::history_query_item_price_trend,
            commands::legacy::legacy_scan,
            commands::legacy::legacy_import,
            commands::runtime::automation_start_single,
            commands::runtime::automation_start_multi,
            commands::runtime::automation_stop,
            commands::runtime::ocr_status,
            commands::runtime::ocr_start,
            commands::runtime::ocr_stop,
            commands::runtime::ocr_restart,
            commands::runtime::automation_probe_click,
            commands::runtime::automation_probe_type_text,
        ])
        .run(tauri::generate_context!())
        .expect("error while running ArenaBuyer Desktop");
}

fn create_main_window(app: &mut tauri::App) -> anyhow::Result<()> {
    let url = resolve_main_window_url().context("failed to resolve main window url")?;
    WebviewWindowBuilder::new(app, "main", url)
        .title("ArenaBuyer Desktop")
        .decorations(false)
        .inner_size(1440.0, 920.0)
        .min_inner_size(1180.0, 760.0)
        .resizable(true)
        .build()
        .context("failed to build main window")?;
    Ok(())
}

fn resolve_main_window_url() -> anyhow::Result<WebviewUrl> {
    const DEFAULT_DEV_URL: &str = "http://127.0.0.1:1420";

    if cfg!(debug_assertions) {
        if dev_server_reachable(DEFAULT_DEV_URL) {
            return Ok(WebviewUrl::External(
                Url::parse(DEFAULT_DEV_URL).context("invalid default dev url")?,
            ));
        }

        if let Ok(custom_url) = std::env::var("TAURI_DEV_SERVER_URL") {
            if !custom_url.trim().is_empty() && dev_server_reachable(&custom_url) {
                return Ok(WebviewUrl::External(
                    Url::parse(&custom_url).context("invalid TAURI_DEV_SERVER_URL")?,
                ));
            }
        }
    }

    eprintln!(
        "ArenaBuyer Desktop: dev server is unavailable, falling back to bundled frontend assets."
    );
    Ok(WebviewUrl::App("index.html".into()))
}

fn dev_server_reachable(url: &str) -> bool {
    let Ok(parsed) = Url::parse(url) else {
        return false;
    };
    let host = parsed.host_str().unwrap_or("127.0.0.1");
    let port = parsed.port_or_known_default().unwrap_or(80);
    let Ok(addresses) = std::net::ToSocketAddrs::to_socket_addrs(&(host, port)) else {
        return false;
    };
    addresses.into_iter().any(|addr| {
        std::net::TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(300)).is_ok()
    })
}
