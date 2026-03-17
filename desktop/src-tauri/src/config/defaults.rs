use crate::app::types::{AppConfig, TemplateConfig, now_iso};

const DEFAULT_TEMPLATE_DEFS: &[(&str, &str, &str, f64)] = &[
    ("btn_launch", "启动按钮", "launcher", 0.85),
    ("btn_settings", "设置按钮", "launcher", 0.85),
    ("btn_exit", "退出按钮", "launcher", 0.85),
    ("btn_exit_confirm", "退出确认按钮", "launcher", 0.85),
    ("home_indicator", "首页标识模板", "navigation", 0.85),
    ("market_indicator", "市场标识模板", "navigation", 0.85),
    ("btn_home", "首页按钮", "navigation", 0.85),
    ("btn_market", "市场按钮", "navigation", 0.85),
    ("input_search", "市场搜索栏", "search", 0.85),
    ("btn_search", "市场搜索按钮", "search", 0.85),
    ("btn_buy", "购买按钮", "detail", 0.88),
    ("buy_ok", "购买成功", "detail", 0.90),
    ("buy_fail", "购买失败", "detail", 0.90),
    ("btn_close", "商品关闭位置", "detail", 0.85),
    ("btn_refresh", "刷新按钮", "detail", 0.85),
    ("btn_back", "返回按钮", "detail", 0.85),
    ("btn_max", "数量最大按钮", "detail", 0.85),
    ("qty_minus", "数量-", "detail", 0.85),
    ("qty_plus", "数量+", "detail", 0.85),
    ("penalty_warning", "处罚识别模板", "detail", 0.90),
    ("btn_penalty_confirm", "处罚确认按钮", "detail", 0.90),
    ("recent_purchases_tab", "最近购买模板", "multi", 0.85),
    ("favorites_tab", "我的收藏模板", "multi", 0.85),
];

pub fn default_config() -> AppConfig {
    AppConfig::default()
}

pub fn default_templates() -> Vec<TemplateConfig> {
    let now = now_iso();
    DEFAULT_TEMPLATE_DEFS
        .iter()
        .map(|(slug, name, kind, confidence)| TemplateConfig {
            id: format!("tpl-{}", slug),
            slug: (*slug).to_string(),
            name: (*name).to_string(),
            kind: (*kind).to_string(),
            path: format!("images/templates/{}.png", slug),
            confidence: *confidence,
            notes: None,
            created_at: now.clone(),
            updated_at: now.clone(),
        })
        .collect()
}
