// User intent: talk to the transient Python helper (tm_bridge.py --relay-helper, protocol v1) that owns every policy decision, so
// the relay never duplicates discovery, skew, launch or private-file rules; and make sure a finished helper never lingers (R2).
use crate::consts::{secs, HELPER_EXIT_WAIT};
use crate::log::Log;
use crate::spawn::spawn_hidden;
use serde_json::Value;
use std::io::{self, BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub enum Ev {
    Line(Value),
    Garbage,
    Eof,
}

pub enum HelperError {
    Exited(i32),
    Protocol(String),
}

impl HelperError {
    pub fn text(&self) -> String {
        match self {
            HelperError::Exited(code) => format!("relay helper exited (code {code}) before answering"),
            HelperError::Protocol(why) => why.clone(),
        }
    }
}

pub struct Helper {
    child: Arc<Mutex<Child>>,
    stdin: Option<ChildStdin>,
    rx: Receiver<Ev>,
}

impl Helper {
    pub fn spawn(argv: &[String], discriminator: &str) -> io::Result<Helper> {
        let mut child = spawn_hidden(argv, Stdio::piped(), Stdio::piped(), Stdio::inherit(), &[("TASKMASTER_BRIDGE_DISCRIMINATOR", discriminator)])?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take();
        let (tx, rx) = channel();
        std::thread::Builder::new().stack_size(256 * 1024).spawn(move || {
            if let Some(out) = stdout {
                let mut reader = BufReader::new(out);
                loop {
                    let mut line = String::new();
                    match reader.read_line(&mut line) {
                        Ok(0) | Err(_) => break,
                        Ok(_) => {
                            let t = line.trim();
                            if t.is_empty() {
                                continue;
                            }
                            let ev = match serde_json::from_str::<Value>(t) {
                                Ok(v) => Ev::Line(v),
                                Err(_) => Ev::Garbage,
                            };
                            if tx.send(ev).is_err() {
                                return;
                            }
                        }
                    }
                }
            }
            let _ = tx.send(Ev::Eof);
        })?;
        Ok(Helper { child: Arc::new(Mutex::new(child)), stdin, rx })
    }

    /// The next line the helper prints, a protocol error for a line that is not v1, or the exit code when it ends first.
    pub fn next(&mut self) -> Result<Value, HelperError> {
        match self.rx.recv() {
            Ok(Ev::Line(v)) => parse_v1(v),
            Ok(Ev::Garbage) => Err(HelperError::Protocol("relay helper printed a line that is not JSON".into())),
            Ok(Ev::Eof) | Err(_) => Err(HelperError::Exited(self.exit_code())),
        }
    }

    fn exit_code(&self) -> i32 {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if let Ok(mut c) = self.child.lock() {
                match c.try_wait() {
                    Ok(Some(status)) => return status.code().unwrap_or(-1),
                    Ok(None) => {}
                    Err(_) => return -1,
                }
            }
            if Instant::now() >= deadline {
                return -1;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    pub fn alive(&self) -> bool {
        self.child.lock().map(|mut c| matches!(c.try_wait(), Ok(None))).unwrap_or(false)
    }

    pub fn send(&mut self, line: &str) -> io::Result<()> {
        let stdin = self.stdin.as_mut().ok_or_else(|| io::Error::new(io::ErrorKind::BrokenPipe, "helper stdin closed"))?;
        stdin.write_all(line.as_bytes())?;
        stdin.write_all(b"\n")?;
        stdin.flush()
    }

    /// The last line it needed is in: close its stdin and reap it; a helper still alive HELPER_EXIT_WAIT later is killed.
    pub fn finish(mut self, log: Log) {
        self.stdin = None;
        let child = self.child.clone();
        let _ = std::thread::Builder::new().stack_size(256 * 1024).spawn(move || {
            let deadline = Instant::now() + secs(HELPER_EXIT_WAIT);
            loop {
                if let Ok(mut c) = child.lock() {
                    if !matches!(c.try_wait(), Ok(None)) {
                        return;
                    }
                    if Instant::now() >= deadline {
                        let _ = c.kill();
                        let _ = c.wait();
                        log.once("relay helper did not exit; stopped");
                        return;
                    }
                }
                std::thread::sleep(Duration::from_millis(25));
            }
        });
    }
}

pub fn parse_v1(v: Value) -> Result<Value, HelperError> {
    match v.get("v").and_then(Value::as_i64) {
        Some(1) => Ok(v),
        other => Err(HelperError::Protocol(format!("relay helper speaks protocol {other:?}, the relay needs 1"))),
    }
}

/// What line 1 of the helper says about this session.
#[derive(Clone, Debug, PartialEq)]
pub enum Mode {
    Local,
    Team,
    Fallback,
}

pub struct SessionLine {
    pub mode: Mode,
    pub reason: Option<String>,
}

pub fn session_line(v: &Value) -> Option<SessionLine> {
    if v.get("kind").and_then(Value::as_str) != Some("session") {
        return None;
    }
    let mode = match v.get("mode").and_then(Value::as_str)? {
        "local" => Mode::Local,
        "team" => Mode::Team,
        "fallback" => Mode::Fallback,
        _ => return None,
    };
    Some(SessionLine { mode, reason: v.get("reason").and_then(Value::as_str).map(str::to_string) })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_v1_lines_pass() {
        assert!(parse_v1(json!({"v": 1, "kind": "session"})).is_ok());
        assert!(parse_v1(json!({"v": 2})).is_err());
        assert!(parse_v1(json!({"kind": "session"})).is_err());
    }

    #[test]
    fn session_line_modes() {
        let l = session_line(&json!({"v":1,"kind":"session","mode":"fallback","reason":"TASKMASTER_LOCAL_SERVER=0"})).unwrap();
        assert_eq!(l.mode, Mode::Fallback);
        assert_eq!(l.reason.as_deref(), Some("TASKMASTER_LOCAL_SERVER=0"));
        assert!(session_line(&json!({"v":1,"kind":"session","mode":"weird"})).is_none());
        assert!(session_line(&json!({"v":1,"kind":"endpoint"})).is_none());
    }
}
