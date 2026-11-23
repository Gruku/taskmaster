// User intent: every number and text the relay shares with tm_bridge.py in one place, so tests/fixtures/relay_vectors.json can pin them to the Python bridge.
// Test-only overrides (TASKMASTER_BRIDGE_TESTING=1) live in Bounds; production always uses the defaults.
use std::time::Duration;

pub const CONNECT_TIMEOUT: f64 = 5.0;
pub const SHORT_TIMEOUT: f64 = 30.0;
pub const MAX_BODY_BYTES: usize = 64 * 1024 * 1024;
pub const MAX_LINE_BYTES: usize = 16 * 1024 * 1024;
pub const KNOWN_PROTOCOLS: [&str; 4] = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"];
pub const RECONNECT_BUDGET: f64 = 45.0;
pub const RECONNECT_BACKOFF_START: f64 = 0.2;
pub const RECONNECT_BACKOFF_CAP: f64 = 2.0;
pub const RETRY_ATTEMPTS: usize = 4;
pub const LIST_HOLD: f64 = 12.0;
pub const HOLD_BOUND: f64 = 180.0;
pub const FALLBACK_FAILURES: usize = 3;
pub const FALLBACK_WINDOW: f64 = 60.0;
pub const GET_BACKOFF: [f64; 6] = [0.2, 0.5, 1.0, 2.0, 3.0, 5.0];
pub const GET_NON200_STEP: f64 = 2.0;
pub const GET_NON200_CAP: f64 = 30.0;
/// Relay-only: how long the client's initialize waits for the helper's line 1 (spec 3.1).
pub const INIT_WAIT: f64 = 2.0;
pub const DRAINING_TEXT: &str = "is draining";
pub const SERVER_NAME: &str = "tm";
pub const LOOPBACK_HOSTS: [&str; 3] = ["127.0.0.1", "localhost", "::1"];
pub const ERROR_BODY_READ: usize = 2048;
pub const ERROR_TEXT_CHARS: usize = 300;
pub const DELETE_TIMEOUT: f64 = 2.0;
pub const CHILD_STOP_WAIT: f64 = 5.0;
pub const HELPER_EXIT_WAIT: f64 = 5.0;
pub const ACCEPT_BOTH: &str = "application/json, text/event-stream";
pub const ACCEPT_STREAM: &str = "text/event-stream";

pub fn secs(s: f64) -> Duration {
    Duration::from_secs_f64(s.max(0.0))
}

pub fn testing() -> bool {
    std::env::var("TASKMASTER_BRIDGE_TESTING").map(|v| v == "1").unwrap_or(false)
}

/// A JSON string-array argv from a test-mode variable; always None outside TASKMASTER_BRIDGE_TESTING=1.
pub fn test_argv(name: &str) -> Option<Vec<String>> {
    if !testing() {
        return None;
    }
    let raw = std::env::var(name).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let list = value.as_array()?;
    let argv: Vec<String> = list.iter().filter_map(|v| v.as_str().map(str::to_string)).collect();
    if argv.is_empty() || argv.len() != list.len() {
        None
    } else {
        Some(argv)
    }
}

/// The bounds a test may shorten through TASKMASTER_BRIDGE_TEST_BOUNDS (seconds; a list for GET_BACKOFF).
#[derive(Clone, Debug)]
pub struct Bounds {
    pub short_timeout: f64,
    pub list_hold: f64,
    pub hold_bound: f64,
    pub reconnect_budget: f64,
    pub fallback_window: f64,
    pub get_backoff: Vec<f64>,
    pub init_wait: f64,
}

impl Default for Bounds {
    fn default() -> Self {
        Bounds {
            short_timeout: SHORT_TIMEOUT,
            list_hold: LIST_HOLD,
            hold_bound: HOLD_BOUND,
            reconnect_budget: RECONNECT_BUDGET,
            fallback_window: FALLBACK_WINDOW,
            get_backoff: GET_BACKOFF.to_vec(),
            init_wait: INIT_WAIT,
        }
    }
}

const BOUND_NAMES: [&str; 10] = [
    "SHORT_TIMEOUT", "LIST_HOLD", "HOLD_BOUND", "RECONNECT_BUDGET", "FALLBACK_WINDOW", "GET_BACKOFF", "LAUNCH_BOUND",
    "DRAIN_WAIT", "SUCCESSOR_WAIT", "INIT_WAIT",
];

impl Bounds {
    pub fn load() -> Result<Bounds, String> {
        let mut b = Bounds::default();
        if !testing() {
            return Ok(b);
        }
        let Ok(raw) = std::env::var("TASKMASTER_BRIDGE_TEST_BOUNDS") else { return Ok(b) };
        b.apply(&raw)?;
        Ok(b)
    }

    pub fn apply(&mut self, raw: &str) -> Result<(), String> {
        let value: serde_json::Value = serde_json::from_str(raw).map_err(|e| format!("bad TASKMASTER_BRIDGE_TEST_BOUNDS: {e}"))?;
        let map = value.as_object().ok_or("bad TASKMASTER_BRIDGE_TEST_BOUNDS: not an object")?;
        for (name, v) in map {
            if !BOUND_NAMES.contains(&name.as_str()) {
                return Err(format!("unknown test bound {name}"));
            }
            match name.as_str() {
                "SHORT_TIMEOUT" => self.short_timeout = v.as_f64().unwrap_or(self.short_timeout),
                "LIST_HOLD" => self.list_hold = v.as_f64().unwrap_or(self.list_hold),
                "HOLD_BOUND" => self.hold_bound = v.as_f64().unwrap_or(self.hold_bound),
                "RECONNECT_BUDGET" => self.reconnect_budget = v.as_f64().unwrap_or(self.reconnect_budget),
                "FALLBACK_WINDOW" => self.fallback_window = v.as_f64().unwrap_or(self.fallback_window),
                "INIT_WAIT" => self.init_wait = v.as_f64().unwrap_or(self.init_wait),
                "GET_BACKOFF" => {
                    if let Some(list) = v.as_array() {
                        let parsed: Vec<f64> = list.iter().filter_map(|x| x.as_f64()).collect();
                        if !parsed.is_empty() {
                            self.get_backoff = parsed;
                        }
                    }
                }
                _ => {} // LAUNCH_BOUND, DRAIN_WAIT, SUCCESSOR_WAIT belong to the helper
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_bound_is_refused_and_known_ones_apply() {
        let mut b = Bounds::default();
        assert!(b.apply(r#"{"SHORT_TIMEOUT": 1, "GET_BACKOFF": [0.1, 0.2], "LAUNCH_BOUND": 3}"#).is_ok());
        assert_eq!(b.short_timeout, 1.0);
        assert_eq!(b.get_backoff, vec![0.1, 0.2]);
        assert_eq!(Bounds::default().apply(r#"{"NOPE": 1}"#), Err("unknown test bound NOPE".to_string()));
    }
}
