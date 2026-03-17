use std::time::Duration;

use anyhow::Result;
use tokio::time::sleep;

use crate::app::types::{AutomationEvent, MultiTaskRecord};

#[derive(Debug, Clone)]
pub enum MultiRunnerState {
    Preparing,
    OpeningRecentPurchases,
    OpeningFavorites,
    CachingCards,
    BatchReadingPrices,
    Purchasing,
    Completed,
}

pub async fn run_multi_flow(
    tasks: &[MultiTaskRecord],
    mut emit: impl FnMut(AutomationEvent) + Send + 'static,
    session_id: String,
) -> Result<()> {
    let total = tasks.len().max(1);
    let mode = "multi".to_string();
    let steps = [
        (MultiRunnerState::Preparing, "初始化多商品运行上下文"),
        (MultiRunnerState::OpeningRecentPurchases, "点击最近购买"),
        (MultiRunnerState::OpeningFavorites, "点击我的收藏"),
        (MultiRunnerState::CachingCards, "首轮缓存卡片坐标"),
        (MultiRunnerState::BatchReadingPrices, "批量 OCR 读取价格"),
        (MultiRunnerState::Purchasing, "执行购买与失败回退"),
        (MultiRunnerState::Completed, "多商品流程骨架运行完成"),
    ];
    for (index, (_state, message)) in steps.iter().enumerate() {
        emit(AutomationEvent::log(
            session_id.clone(),
            mode.clone(),
            "info",
            format!("{message}，当前任务数 {total}"),
            Some((*message).to_string()),
            Some(((index + 1) as f64) / (steps.len() as f64)),
        ));
        sleep(Duration::from_millis(140)).await;
    }
    Ok(())
}
