//! Python-compatible float formatting.
//!
//! Three places need a float spelled exactly as Python spells it: the cache key
//! (`json.dumps`), the upstream query string (httpx stringifies values with
//! `str()`), and the coordinate path segment (`f"{lon},{lat}"`). All three go
//! through Python's `repr`, which differs from Rust's `Display` in two ways
//! that matter -- `repr(9.0)` is `"9.0"` where Rust writes `"9"`, and
//! `repr(1e-7)` is `"1e-07"` where Rust writes `"1e-7"`.
//!
//! Getting this wrong is quiet: OSRM accepts either spelling, so the only
//! symptom is a cache key that never matches the Python gateway's.

/// Format a float the way Python's `repr` does.
///
/// Python switches to exponent form below 1e-4 and at or above 1e16, pads the
/// exponent to two digits and always signs it; Rust's `Display` never uses
/// exponent form and drops the trailing `.0`.
pub fn python_float(v: f64) -> String {
    if v.is_nan() {
        return "NaN".to_string();
    }
    if v.is_infinite() {
        return if v > 0.0 { "Infinity".into() } else { "-Infinity".into() };
    }
    let abs = v.abs();
    if abs != 0.0 && (abs < 1e-4 || abs >= 1e16) {
        return python_exponential(v);
    }
    let s = format!("{v}");
    if s.contains('.') {
        s
    } else {
        format!("{s}.0")
    }
}

/// Render exponent form as Python does: signed, at least two digits.
fn python_exponential(v: f64) -> String {
    let formatted = format!("{v:e}");
    let (mantissa, exponent) = formatted.split_once('e').expect("{:e} always emits an exponent");
    let (sign, digits) = match exponent.strip_prefix('-') {
        Some(digits) => ('-', digits),
        None => ('+', exponent),
    };
    format!("{mantissa}e{sign}{digits:0>2}")
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integral_floats_keep_their_decimal_point() {
        // The trap: Rust's Display writes "9", Python writes "9.0", and a
        // coordinate of exactly 9.0 is entirely plausible.
        assert_eq!(python_float(9.0), "9.0");
        assert_eq!(python_float(50.0), "50.0");
        assert_eq!(python_float(0.0), "0.0");
        assert_eq!(python_float(-84.0), "-84.0");
    }

    #[test]
    fn ordinary_floats_round_trip() {
        assert_eq!(python_float(0.1), "0.1");
        assert_eq!(python_float(1.5), "1.5");
        assert_eq!(python_float(-84.090271), "-84.090271");
        assert_eq!(python_float(9.928567), "9.928567");
    }

    #[test]
    fn extreme_floats_use_python_exponent_form() {
        assert_eq!(python_float(1e-7), "1e-07");
        assert_eq!(python_float(1e16), "1e+16");
        assert_eq!(python_float(1.5e-7), "1.5e-07");
    }

    #[test]
    fn non_finite_values_use_python_spellings() {
        assert_eq!(python_float(f64::INFINITY), "Infinity");
        assert_eq!(python_float(f64::NEG_INFINITY), "-Infinity");
        assert_eq!(python_float(f64::NAN), "NaN");
    }
}
