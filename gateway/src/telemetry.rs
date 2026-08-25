//! Logging and optional OTLP tracing.
//!
//! Both are configured once at startup and never fail the process: an exporter
//! that cannot be built is logged and dropped, matching `src/app/tracing.py`,
//! where the whole setup sits in a try/except that downgrades to a warning.
//! Tracing is an observability aid, and a gateway that refuses to start because
//! its collector is down is worse than one that starts without traces.

use crate::config::Settings;

/// Initialise logging, and OTLP tracing when an endpoint is configured.
///
/// Returns a guard that flushes pending spans on drop. Holding it for the
/// process lifetime is what stops the last batch being lost at shutdown.
pub fn setup(settings: &Settings) -> TelemetryGuard {
    use tracing_subscriber::layer::SubscriberExt;
    use tracing_subscriber::util::SubscriberInitExt;

    let level = if settings.debug { "debug" } else { "info" };
    // DEBUG is authoritative for this crate, as it is in logging_config.py.
    // RUST_LOG previously replaced the filter outright, so `DEBUG=true` with a
    // stray `RUST_LOG=warn` in the environment silently produced warn-level
    // logs. It is still honoured for everything else, which is what makes
    // dependency noise tunable without touching application config.
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(level))
        .add_directive(format!("osrm_api_gateway={level}").parse()
            .expect("a literal directive is valid"));

    // APPEND_TO_STDERR mirrors logging_config.py: stderr when set, stdout
    // otherwise. Both deployments capture whichever is chosen -- Docker
    // collects them, and daemon(8) redirects them to the configured logfile.
    let format = tracing_subscriber::fmt::layer()
        .event_format(PythonFormat)
        // Both deployments send this to a file -- Docker collects it, and
        // daemon(8) redirects it to the configured logfile -- and the default
        // is unconditional colour, so the jail's log was being written full of
        // escape sequences.
        .with_ansi(false)
        .with_writer::<fn() -> Box<dyn std::io::Write>>(if settings.append_to_stderr {
            || Box::new(std::io::stderr())
        } else {
            || Box::new(std::io::stdout())
        });

    let provider = build_tracer(settings);
    match &provider {
        Some(provider) => {
            use opentelemetry::trace::TracerProvider as _;
            let layer = tracing_opentelemetry::layer()
                .with_tracer(provider.tracer("osrm-api-gateway"));
            // Without a propagator installed, `inject_context` writes nothing
            // and the engine sees no traceparent, so its spans never join the
            // caller's trace. Python got this from the OTel SDK's default.
            opentelemetry::global::set_text_map_propagator(
                opentelemetry_sdk::propagation::TraceContextPropagator::new());
            tracing_subscriber::registry().with(filter).with(format).with(layer).init();
            tracing::info!(endpoint = %settings.otlp_endpoint, "OTLP tracing enabled");
        }
        None => {
            tracing_subscriber::registry().with(filter).with(format).init();
            if settings.otlp_endpoint.is_empty() {
                tracing::info!("OTLP_ENDPOINT not set, tracing disabled");
            }
        }
    }
    TelemetryGuard { provider }
}

/// `logging_config.py`'s line shape: `asctime [LEVEL] name: message`.
///
/// The timestamp is UTC where Python's `asctime` is local: resolving a local
/// zone needs a tz database this binary deliberately does not carry, and a log
/// that is unambiguous across the jail and the container is worth more than an
/// exact character match with an implementation that no longer runs.
struct PythonFormat;

impl<S, N> tracing_subscriber::fmt::FormatEvent<S, N> for PythonFormat
where
    S: tracing::Subscriber + for<'a> tracing_subscriber::registry::LookupSpan<'a>,
    N: for<'a> tracing_subscriber::fmt::FormatFields<'a> + 'static,
{
    fn format_event(&self, ctx: &tracing_subscriber::fmt::FmtContext<'_, S, N>,
                    mut writer: tracing_subscriber::fmt::format::Writer<'_>,
                    event: &tracing::Event<'_>) -> std::fmt::Result {
        let metadata = event.metadata();
        write!(writer, "{} [{}] {}: ", utc_timestamp(std::time::SystemTime::now()),
               metadata.level(), metadata.target())?;
        ctx.field_format().format_fields(writer.by_ref(), event)?;
        writeln!(writer)
    }
}

/// Format a `SystemTime` as `YYYY-MM-DD HH:MM:SS,mmm`, as `asctime` does.
fn utc_timestamp(time: std::time::SystemTime) -> String {
    let epoch = time.duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let (seconds, millis) = (epoch.as_secs() as i64, epoch.subsec_millis());
    let (days, rest) = (seconds.div_euclid(86_400), seconds.rem_euclid(86_400));
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02},{millis:03}",
            rest / 3600, (rest % 3600) / 60, rest % 60)
}

/// Days since the Unix epoch to a civil date (Howard Hinnant's algorithm).
///
/// Written out rather than pulled in: a date library for one log line is a
/// dependency the FreeBSD build would have to carry for nothing else.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era = (day_of_era - day_of_era / 1460 + day_of_era / 36_524
                       - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_position = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * month_position + 2) / 5 + 1) as u32;
    let month = if month_position < 10 { month_position + 3 } else { month_position - 9 } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

/// Write the active span's trace context into outgoing request headers.
///
/// This is what `HTTPXClientInstrumentor` did on the Python side. With no
/// propagator installed -- which is the case whenever OTLP is unconfigured --
/// this writes nothing, so it is safe to call unconditionally.
pub fn inject_context(headers: &mut reqwest::header::HeaderMap) {
    use tracing_opentelemetry::OpenTelemetrySpanExt as _;

    let context = tracing::Span::current().context();
    opentelemetry::global::get_text_map_propagator(|propagator| {
        propagator.inject_context(&context, &mut HeaderInjector(headers));
    });
}

/// Adapts a `reqwest` header map to OTel's injector interface.
struct HeaderInjector<'a>(&'a mut reqwest::header::HeaderMap);

impl opentelemetry::propagation::Injector for HeaderInjector<'_> {
    fn set(&mut self, key: &str, value: String) {
        // A header that will not build is dropped rather than panicking: a
        // malformed trace context must not fail the request carrying it.
        if let Ok(name) = reqwest::header::HeaderName::from_bytes(key.as_bytes()) {
            if let Ok(value) = reqwest::header::HeaderValue::from_str(&value) {
                self.0.insert(name, value);
            }
        }
    }
}

/// Build an OTLP span exporter, or nothing at all.
fn build_tracer(settings: &Settings) -> Option<opentelemetry_sdk::trace::TracerProvider> {
    if settings.otlp_endpoint.is_empty() {
        return None;
    }
    // WithExportConfig is what carries with_endpoint onto the builder.
    use opentelemetry_otlp::WithExportConfig as _;
    let exporter = opentelemetry_otlp::SpanExporter::builder()
        .with_http()
        .with_endpoint(settings.otlp_endpoint.clone())
        .build();

    match exporter {
        Ok(exporter) => Some(
            opentelemetry_sdk::trace::TracerProvider::builder()
                .with_batch_exporter(exporter, opentelemetry_sdk::runtime::Tokio)
                .with_resource(opentelemetry_sdk::Resource::new([
                    opentelemetry::KeyValue::new("service.name", settings.app_name.clone()),
                ]))
                .build(),
        ),
        Err(error) => {
            // Deliberately not fatal: see the module docs.
            eprintln!("OTLP exporter unavailable, continuing without tracing: {error}");
            None
        }
    }
}

/// How long shutdown will wait for pending spans before giving up on them.
const FLUSH_BUDGET: std::time::Duration = std::time::Duration::from_secs(2);

/// Flushes pending spans when dropped, under a deadline.
pub struct TelemetryGuard {
    provider: Option<opentelemetry_sdk::trace::TracerProvider>,
}

impl TelemetryGuard {
    /// Flush pending spans, giving up after `FLUSH_BUDGET`.
    ///
    /// `shutdown()` drains the batch processor, and against an unreachable
    /// collector that drain does not finish -- measured at over a minute before
    /// being killed. Since this runs on the way out of `main`, an unbounded wait
    /// would hang SIGTERM: Docker would wait its full stop timeout and then
    /// SIGKILL, and `daemon(8)` would sit on the process. Losing the last batch
    /// of spans is the better trade.
    fn flush(&mut self) {
        let Some(provider) = self.provider.take() else {
            return;
        };
        let (done, wait) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = provider.shutdown();
            let _ = done.send(());
        });
        if wait.recv_timeout(FLUSH_BUDGET).is_err() {
            eprintln!("telemetry: collector unreachable, exiting without flushing spans");
        }
    }
}

impl Drop for TelemetryGuard {
    fn drop(&mut self) {
        self.flush();
    }
}

#[cfg(test)]
mod tests {
    /// Pinned against Python's `asctime` for known instants.
    #[test]
    fn the_timestamp_matches_pythons_asctime_shape() {
        let at = |secs: u64, millis: u32| utc_timestamp(
            std::time::UNIX_EPOCH + std::time::Duration::new(secs, millis * 1_000_000));
        assert_eq!(at(0, 0), "1970-01-01 00:00:00,000");
        // 2026-08-25T17:31:23Z, the instant this formatter replaced the default.
        assert_eq!(at(1_787_679_083, 28), "2026-08-25 17:31:23,028");
        // Leap day, and the year-boundary case the civil-date maths gets wrong
        // if January and February are not carried back into the prior year.
        assert_eq!(at(1_709_164_800, 0), "2024-02-29 00:00:00,000");
        assert_eq!(at(1_735_689_599, 999), "2024-12-31 23:59:59,999");
    }

    /// The regression that matters: an OTLP endpoint could be configured and
    /// correctly connected and the collector would still see nothing, because
    /// no span was ever created and no context ever propagated.
    #[test]
    fn an_active_span_propagates_a_traceparent_upstream() {
        use tracing_subscriber::layer::SubscriberExt as _;

        opentelemetry::global::set_text_map_propagator(
            opentelemetry_sdk::propagation::TraceContextPropagator::new());
        // No exporter: span contexts are still real, which is all the
        // propagator needs, and nothing has to be running to receive them.
        let provider = opentelemetry_sdk::trace::TracerProvider::builder().build();
        let tracer = {
            use opentelemetry::trace::TracerProvider as _;
            provider.tracer("test")
        };
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(tracer));

        let mut headers = reqwest::header::HeaderMap::new();
        tracing::subscriber::with_default(subscriber, || {
            let span = tracing::info_span!("http.client");
            let _entered = span.enter();
            inject_context(&mut headers);
        });

        let traceparent = headers.get("traceparent")
            .expect("an active span must inject a traceparent")
            .to_str().expect("header is ASCII");
        // W3C: version-traceid-spanid-flags, with non-zero ids.
        let parts: Vec<&str> = traceparent.split('-').collect();
        assert_eq!(parts.len(), 4, "malformed traceparent: {traceparent}");
        assert_eq!(parts[1].len(), 32, "trace id: {traceparent}");
        assert_eq!(parts[2].len(), 16, "span id: {traceparent}");
        assert_ne!(parts[1], "0".repeat(32), "trace id must not be all zeroes");
    }

    /// With no propagator context available there is nothing to inject, and the
    /// call must stay harmless rather than writing a malformed header.
    #[test]
    fn injection_without_an_active_span_writes_nothing_usable() {
        let mut headers = reqwest::header::HeaderMap::new();
        inject_context(&mut headers);
        assert!(headers.get("traceparent").is_none_or(|value| {
            value.to_str().is_ok_and(|v| v.contains(&"0".repeat(32)))
        }), "an inactive context must not fabricate a live trace id");
    }

    use super::*;
    use std::time::Instant;

    fn settings_with(endpoint: &str) -> Settings {
        let mut settings = Settings::from_env();
        settings.otlp_endpoint = endpoint.to_string();
        settings
    }

    #[test]
    fn no_endpoint_means_no_tracer() {
        assert!(build_tracer(&settings_with("")).is_none());
    }

    #[test]
    fn a_disabled_guard_drops_immediately() {
        let mut guard = TelemetryGuard { provider: None };
        let started = Instant::now();
        guard.flush();
        assert!(started.elapsed() < std::time::Duration::from_millis(100));
    }

    /// The hazard this bound exists for: against an unreachable collector the
    /// drain never completes, and an unbounded wait would hang SIGTERM.
    #[tokio::test(flavor = "multi_thread")]
    async fn shutdown_gives_up_on_an_unreachable_collector() {
        let provider = build_tracer(&settings_with("http://127.0.0.1:4318"));
        assert!(provider.is_some(), "an unreachable collector must still start");

        let mut guard = TelemetryGuard { provider };
        let started = Instant::now();
        guard.flush();
        let waited = started.elapsed();
        assert!(waited < FLUSH_BUDGET + std::time::Duration::from_secs(1),
                "shutdown took {waited:?}, past its budget");
    }
}
