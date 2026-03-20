use std::path::Path;

use crate::app::types::{AppConfig, RuntimePreflightStatus};

fn validate_executable_path(raw: &str, missing_message: &str, invalid_message: &str) -> (bool, String) {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return (false, missing_message.to_string());
    }
    let path = Path::new(trimmed);
    if path.is_file() {
        return (true, format!("已配置：{}", path.display()));
    }
    (false, invalid_message.to_string())
}

pub fn build_runtime_preflight(config: &AppConfig) -> RuntimePreflightStatus {
    let (launcher_ready, launcher_message) = validate_executable_path(
        &config.game.exe_path,
        "请先配置有效的启动器路径",
        "启动器路径无效，请重新选择 launcher.exe",
    );
    let (umi_ready, umi_message) = validate_executable_path(
        &config.umi_ocr.exe_path,
        "请先配置有效的 Umi-OCR 路径",
        "Umi-OCR 路径无效，请重新选择 Umi-OCR.exe",
    );
    RuntimePreflightStatus {
        launcher_ready,
        launcher_message,
        umi_ready,
        umi_message,
    }
}

pub fn validate_automation_start(config: &AppConfig) -> Result<(), String> {
    let preflight = build_runtime_preflight(config);
    if !preflight.launcher_ready {
        return Err(preflight.launcher_message);
    }
    if !preflight.umi_ready {
        return Err(preflight.umi_message);
    }
    Ok(())
}

pub fn validate_ocr_start(config: &AppConfig) -> Result<(), String> {
    let preflight = build_runtime_preflight(config);
    if !preflight.umi_ready {
        return Err(preflight.umi_message);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;

    use super::{build_runtime_preflight, validate_automation_start, validate_ocr_start};
    use crate::app::types::AppConfig;

    fn temp_file_path(label: &str) -> PathBuf {
        let unique = format!(
            "arena-buyer-runtime-check-{label}-{}.exe",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("system clock before unix epoch")
                .as_nanos()
        );
        std::env::temp_dir().join(unique)
    }

    fn create_temp_exe(label: &str) -> PathBuf {
        let path = temp_file_path(label);
        fs::write(&path, b"test").expect("failed to create temp exe");
        path
    }

    #[test]
    fn automation_start_requires_launcher_path() {
        let config = AppConfig::default();
        let error = validate_automation_start(&config).expect_err("launcher path should be required");
        assert_eq!(error, "请先配置有效的启动器路径");
    }

    #[test]
    fn automation_start_requires_umi_path() {
        let mut config = AppConfig::default();
        let launcher = create_temp_exe("launcher");
        config.game.exe_path = launcher.display().to_string();
        let error = validate_automation_start(&config).expect_err("umi path should be required");
        assert_eq!(error, "请先配置有效的 Umi-OCR 路径");
        let _ = fs::remove_file(launcher);
    }

    #[test]
    fn ocr_start_requires_valid_umi_path() {
        let mut config = AppConfig::default();
        config.umi_ocr.exe_path = temp_file_path("missing-umi").display().to_string();
        let error = validate_ocr_start(&config).expect_err("missing umi file should fail");
        assert_eq!(error, "Umi-OCR 路径无效，请重新选择 Umi-OCR.exe");
    }

    #[test]
    fn valid_paths_pass_preflight() {
        let mut config = AppConfig::default();
        let launcher = create_temp_exe("launcher-ok");
        let umi = create_temp_exe("umi-ok");
        config.game.exe_path = launcher.display().to_string();
        config.umi_ocr.exe_path = umi.display().to_string();

        let preflight = build_runtime_preflight(&config);
        assert!(preflight.launcher_ready);
        assert!(preflight.umi_ready);
        assert!(validate_automation_start(&config).is_ok());
        assert!(validate_ocr_start(&config).is_ok());

        let _ = fs::remove_file(launcher);
        let _ = fs::remove_file(umi);
    }
}
