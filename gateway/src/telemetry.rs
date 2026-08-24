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
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(level));

    // APPEND_TO_STDERR mirrors logging_config.py: stderr when set, stdout
    // otherwise. Both deployments capture whichever is chosen -- Docker
    // collects them, and daemon(8) redirects them to the configured logfile.
    let format = tracing_subscriber::fmt::layer()
        .with_target(true)
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
