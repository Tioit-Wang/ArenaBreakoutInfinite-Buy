pub fn parse_price_text(text: &str) -> Option<i64> {
    if text.trim().is_empty() {
        return None;
    }
    let source = text.trim().to_uppercase().replace(',', "").replace(' ', "");
    let mut numeric = String::new();
    let mut suffix = None;
    for ch in source.chars() {
        if ch.is_ascii_digit() || ch == '.' {
            numeric.push(ch);
        } else if matches!(ch, 'K' | 'M') {
            suffix = Some(ch);
            break;
        }
    }
    if numeric.is_empty() {
        let digits: String = source.chars().filter(|ch| ch.is_ascii_digit()).collect();
        return digits.parse::<i64>().ok();
    }
    let mut value = numeric.parse::<f64>().ok()?;
    match suffix {
        Some('K') => value *= 1_000.0,
        Some('M') => value *= 1_000_000.0,
        _ => {}
    }
    Some(value.round() as i64)
}

pub fn price_with_premium(price: i64, premium_pct: f64) -> i64 {
    let factor = 1.0 + (premium_pct / 100.0);
    ((price as f64) * factor).round() as i64
}

#[cfg(test)]
mod tests {
    use super::{parse_price_text, price_with_premium};

    #[test]
    fn parses_price_suffixes() {
        assert_eq!(parse_price_text("12.5k"), Some(12_500));
        assert_eq!(parse_price_text("1.2M"), Some(1_200_000));
        assert_eq!(parse_price_text("12,345"), Some(12_345));
    }

    #[test]
    fn computes_premium_threshold() {
        assert_eq!(price_with_premium(10_000, 2.0), 10_200);
    }
}
