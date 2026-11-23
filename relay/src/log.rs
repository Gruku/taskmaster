// User intent: the relay's notes reach Claude Code's MCP log on stderr (once each) and the optional TASKMASTER_BRIDGE_LOG file,
// with the "tm-bridge: " prefix the tests and the mods expect; the operator token never goes through here.
use std::collections::HashSet;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

struct Inner {
    path: Option<PathBuf>,
    seen: Mutex<HashSet<String>>,
}

#[derive(Clone)]
pub struct Log(Arc<Inner>);

impl Log {
    pub fn new(path: Option<String>) -> Log {
        let path = path.filter(|p| !p.is_empty()).map(PathBuf::from);
        Log(Arc::new(Inner { path, seen: Mutex::new(HashSet::new()) }))
    }

    /// Stderr and the file, but only the first time this text is seen.
    pub fn once(&self, text: &str) {
        {
            let mut seen = self.0.seen.lock().unwrap_or_else(|e| e.into_inner());
            if !seen.insert(text.to_string()) {
                return;
            }
        }
        let _ = writeln!(std::io::stderr(), "tm-bridge: {text}");
        self.to_file(text);
    }

    /// The debug file only.
    pub fn note(&self, text: &str) {
        self.to_file(text);
    }

    fn to_file(&self, text: &str) {
        let Some(path) = &self.0.path else { return };
        let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) % 86400;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(f, "{:02}:{:02}:{:02} tm-bridge: {text}", secs / 3600, secs % 3600 / 60, secs % 60);
        }
    }
}
