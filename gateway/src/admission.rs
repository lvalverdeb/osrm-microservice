//! Admission control for the solve endpoints.
//!
//! Two bounds, doing different jobs. `VRP_MAX_CONCURRENCY` caps how many solves
//! run at once, because peak memory is stops x concurrent solves. `VRP_QUEUE_
//! TIMEOUT` caps how long a request waits for one of those slots.
//!
//! Waiting is usually the right thing: a solve takes well under a second, so a
//! queued request is often served rather than shed. But with the queue timeout
//! as the only bound, sustained overload makes every rejected caller pay the
//! full timeout before hearing no -- measured at p50 10,008 ms against a
//! 10-second timeout on the jail. That is the worst of both: the caller waits,
//! and still fails.
//!
//! So a third bound, `VRP_MAX_QUEUE_DEPTH`: past that many waiters, reject
//! immediately instead of joining a queue that is not draining. A modest queue
//! still waits, and a runaway one is refused in microseconds. Set it to 0 to
//! restore the wait-only behaviour.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::{OwnedSemaphorePermit, Semaphore};

/// Why a request was refused a slot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejected {
    /// The queue was already deeper than `VRP_MAX_QUEUE_DEPTH`; no wait.
    QueueTooDeep,
    /// Waited `VRP_QUEUE_TIMEOUT` without a slot coming free.
    TimedOut,
}

pub struct AdmissionGate {
    slots: Arc<Semaphore>,
    waiting: AtomicUsize,
    queue_timeout: Duration,
    /// 0 disables the depth check, leaving the timeout as the only bound.
    max_queue_depth: usize,
}

/// Keeps the waiter count honest across every exit path, including cancellation
/// -- an axum handler whose client disconnects is dropped mid-await, and a
/// hand-rolled decrement would leak a waiter every time that happened.
struct WaitingGuard<'a>(&'a AtomicUsize);

impl Drop for WaitingGuard<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}

impl AdmissionGate {
    pub fn new(concurrency: usize, queue_timeout_seconds: f64, max_queue_depth: usize) -> Self {
        Self {
            slots: Arc::new(Semaphore::new(concurrency.max(1))),
            waiting: AtomicUsize::new(0),
            queue_timeout: Duration::from_secs_f64(queue_timeout_seconds.max(0.0)),
            max_queue_depth,
        }
    }

    /// Take a solve slot, or say why not.
    pub async fn enter(&self) -> Result<OwnedSemaphorePermit, Rejected> {
        if self.max_queue_depth > 0 && self.waiting.load(Ordering::Relaxed) >= self.max_queue_depth
        {
            return Err(Rejected::QueueTooDeep);
        }

        self.waiting.fetch_add(1, Ordering::Relaxed);
        let _guard = WaitingGuard(&self.waiting);

        let slots = Arc::clone(&self.slots);
        match tokio::time::timeout(self.queue_timeout, slots.acquire_owned()).await {
            Ok(Ok(permit)) => Ok(permit),
            // The semaphore is never closed, so a inner error cannot occur in
            // practice; treating it as a timeout keeps the caller's contract.
            _ => Err(Rejected::TimedOut),
        }
    }

    /// How many requests are currently waiting for a slot.
    pub fn waiting(&self) -> usize {
        self.waiting.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    fn gate(concurrency: usize, timeout: f64, depth: usize) -> AdmissionGate {
        AdmissionGate::new(concurrency, timeout, depth)
    }

    #[tokio::test]
    async fn a_free_slot_is_taken_immediately() {
        let gate = gate(1, 10.0, 4);
        assert!(gate.enter().await.is_ok());
    }

    #[tokio::test]
    async fn the_waiter_count_returns_to_zero() {
        let gate = gate(1, 10.0, 4);
        let permit = gate.enter().await.expect("free slot");
        drop(permit);
        assert_eq!(gate.waiting(), 0);
    }

    /// The behaviour this module exists for: past the depth bound, refuse now
    /// rather than after the full timeout.
    #[tokio::test]
    async fn a_deep_queue_is_refused_without_waiting() {
        // One slot, held; depth 1, so the second waiter is refused outright.
        let gate = Arc::new(gate(1, 30.0, 1));
        let _held = gate.enter().await.expect("first takes the only slot");

        let waiter = {
            let gate = Arc::clone(&gate);
            tokio::spawn(async move { gate.enter().await })
        };
        // Let the waiter register itself before measuring.
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert_eq!(gate.waiting(), 1, "the queued request should be counted");

        let started = Instant::now();
        let refused = gate.enter().await;
        let elapsed = started.elapsed();

        assert_eq!(refused.err(), Some(Rejected::QueueTooDeep));
        assert!(elapsed < Duration::from_millis(100),
                "refusal took {elapsed:?}; it should not wait at all");
        waiter.abort();
    }

    /// A shallow queue still waits, because waiting usually pays off.
    #[tokio::test]
    async fn a_shallow_queue_waits_and_is_served() {
        let gate = Arc::new(gate(1, 5.0, 4));
        let permit = gate.enter().await.expect("first takes the only slot");

        let waiter = {
            let gate = Arc::clone(&gate);
            tokio::spawn(async move { gate.enter().await })
        };
        tokio::time::sleep(Duration::from_millis(50)).await;
        drop(permit); // the slot frees while the request is queued

        assert!(waiter.await.expect("task completes").is_ok(),
                "a queued request should be served once a slot frees");
    }

    #[tokio::test]
    async fn waiting_past_the_timeout_is_a_timeout_not_a_depth_refusal() {
        let gate = gate(1, 0.1, 4);
        let _held = gate.enter().await.expect("first takes the only slot");
        assert_eq!(gate.enter().await.err(), Some(Rejected::TimedOut));
    }

    /// Zero restores the previous behaviour, so the change can be turned off
    /// without redeploying a different binary.
    #[tokio::test]
    async fn zero_depth_disables_the_check() {
        let gate = gate(1, 0.1, 0);
        let _held = gate.enter().await.expect("first takes the only slot");
        // Would be QueueTooDeep with any positive depth; here it waits out the
        // timeout instead.
        assert_eq!(gate.enter().await.err(), Some(Rejected::TimedOut));
    }

    #[tokio::test]
    async fn a_cancelled_waiter_does_not_leak_a_queue_slot() {
        let gate = Arc::new(gate(1, 30.0, 4));
        let _held = gate.enter().await.expect("first takes the only slot");

        let waiter = {
            let gate = Arc::clone(&gate);
            tokio::spawn(async move { gate.enter().await })
        };
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert_eq!(gate.waiting(), 1);

        waiter.abort();
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert_eq!(gate.waiting(), 0,
                   "a client that disconnects mid-wait must not hold a queue slot forever");
    }
}
