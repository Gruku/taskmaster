// User intent: run a whole session (or the rest of it) through a child MCP process the relay pumps both ways - the stdio fallback,
// the Python team bridge, the TASKMASTER_RELAY=python lever - without ever exiting while the child runs, so Claude Code keeps one stable process.
use crate::consts::{secs, CHILD_STOP_WAIT};
use crate::spawn::spawn_hidden;
use std::io::{self, Write};
use std::process::{Child, ChildStdin, ChildStdout, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub struct Delegate {
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
}

/// Start argv hidden with piped stdin/stdout; its stderr is ours, so its notes reach Claude Code's MCP log.
pub fn spawn(argv: &[String]) -> io::Result<(Arc<Delegate>, ChildStdout)> {
    let mut child = spawn_hidden(argv, Stdio::piped(), Stdio::piped(), Stdio::inherit(), &[])?;
    let stdin = child.stdin.take();
    let stdout = child.stdout.take().ok_or_else(|| io::Error::new(io::ErrorKind::Other, "no stdout pipe"))?;
    Ok((Arc::new(Delegate { child: Mutex::new(child), stdin: Mutex::new(stdin) }), stdout))
}

impl Delegate {
    /// One JSON line to the child (a newline is added when missing).
    pub fn write_line(&self, line: &[u8]) -> io::Result<()> {
        let mut guard = self.stdin.lock().unwrap_or_else(|e| e.into_inner());
        let stdin = guard.as_mut().ok_or_else(|| io::Error::new(io::ErrorKind::BrokenPipe, "child stdin closed"))?;
        stdin.write_all(line)?;
        if !line.ends_with(b"\n") {
            stdin.write_all(b"\n")?;
        }
        stdin.flush()
    }

    pub fn close_stdin(&self) {
        *self.stdin.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }

    /// Exit code once the child has ended (polls briefly: its stdout closing normally means it is gone).
    pub fn exit_code(&self, wait: Duration) -> i32 {
        let deadline = Instant::now() + wait;
        loop {
            if let Ok(mut c) = self.child.lock() {
                if let Ok(Some(status)) = c.try_wait() {
                    return status.code().unwrap_or(-1);
                }
            }
            if Instant::now() >= deadline {
                return -1;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    /// Close its stdin, give it CHILD_STOP_WAIT seconds, then kill it. The local server is never touched.
    pub fn shutdown(&self) {
        self.close_stdin();
        let deadline = Instant::now() + secs(CHILD_STOP_WAIT);
        loop {
            if let Ok(mut c) = self.child.lock() {
                if !matches!(c.try_wait(), Ok(None)) {
                    return;
                }
                if Instant::now() >= deadline {
                    let _ = c.kill();
                    let _ = c.wait();
                    return;
                }
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }
}
