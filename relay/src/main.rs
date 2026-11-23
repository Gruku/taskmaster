// User intent: bin/tm-relay[.exe] - the plugin's per-session "tm" MCP entry as a tiny compiled relay (GUI subsystem on Windows so it can never
// own a console), keeping every behaviour of tm_bridge.py while Claude Code pays ~1 MB per session instead of 58 MB (rulings R2, R4, R7-R9).
#![cfg_attr(all(windows, not(test)), windows_subsystem = "windows")]

mod consts;
mod delegate;
mod helper;
mod http;
mod lazy;
mod log;
mod relay;
mod spawn;
mod sse;
#[cfg(test)]
mod vectors;

use consts::Bounds;
use serde_json::{json, Value};
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io::{BufRead, Write};
use std::sync::Arc;

const SOURCE_DIGEST: &str = match option_env!("TM_RELAY_SOURCE_DIGEST") {
    Some(d) => d,
    None => "unknown",
};

/// 16 hex digits from the OS-seeded hasher: a nonce and a session discriminator, not a secret.
fn random_hex() -> String {
    let mut h = RandomState::new().build_hasher();
    h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
    h.write_u32(std::process::id());
    format!("{:016x}", h.finish())
}

/// The parent of the directory holding the executable (<plugin>/bin/tm-relay[.exe]); no override. Fails closed: None unless the result is an
/// absolute directory holding tm_bridge.py, so a relay that cannot find itself never touches cwd-relative files.
fn plugin_dir() -> Option<std::path::PathBuf> {
    let exe = std::env::current_exe().ok()?;
    plugin_dir_from(&exe, &|p| p.canonicalize().ok(), &|d| d.join("tm_bridge.py").is_file())
}

fn plugin_dir_from(
    exe: &std::path::Path,
    canon: &dyn Fn(&std::path::Path) -> Option<std::path::PathBuf>,
    has_bridge: &dyn Fn(&std::path::Path) -> bool,
) -> Option<std::path::PathBuf> {
    let resolved = canon(exe).unwrap_or_else(|| exe.to_path_buf());
    let resolved = spawn::strip_verbatim(&resolved);
    let dir = resolved.parent()?.parent()?.to_path_buf();
    (dir.is_absolute() && has_bridge(&dir)).then_some(dir)
}

#[cfg(test)]
mod plugin_dir_tests {
    use super::plugin_dir_from;
    use std::path::{Path, PathBuf};

    #[test]
    fn canonicalize_failure_falls_back_to_the_absolute_exe_path() {
        let exe = if cfg!(windows) { r"C:\p\bin\tm-relay.exe" } else { "/p/bin/tm-relay" };
        let got = plugin_dir_from(Path::new(exe), &|_| None, &|_| true);
        assert_eq!(got, Some(PathBuf::from(if cfg!(windows) { r"C:\p" } else { "/p" })));
    }

    #[test]
    fn relative_or_empty_exe_path_fails_closed() {
        assert_eq!(plugin_dir_from(Path::new(""), &|_| None, &|_| true), None);
        assert_eq!(plugin_dir_from(Path::new("tm-relay"), &|_| None, &|_| true), None);
        assert_eq!(plugin_dir_from(Path::new("bin/tm-relay"), &|_| None, &|_| true), None);
    }

    #[test]
    fn a_dir_without_tm_bridge_py_fails_closed() {
        let exe = if cfg!(windows) { r"C:\p\bin\tm-relay.exe" } else { "/p/bin/tm-relay" };
        assert_eq!(plugin_dir_from(Path::new(exe), &|_| None, &|_| false), None);
    }
}

fn main() {
    std::process::exit(run());
}

fn run() -> i32 {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("--version") {
        let _ = writeln!(std::io::stdout(), "tm-relay {} {}", env!("CARGO_PKG_VERSION"), SOURCE_DIGEST);
        return 0;
    }
    spawn::suppress_crash_dialog();
    spawn::protect_std_handles();
    let bounds = match Bounds::load() {
        Ok(b) => b,
        Err(e) => {
            let _ = writeln!(std::io::stderr(), "tm-bridge: {e}");
            return 2;
        }
    };
    let log = log::Log::new(std::env::var("TASKMASTER_BRIDGE_LOG").ok());
    let session = std::env::var("CLAUDE_CODE_SESSION_ID").ok().filter(|s| !s.is_empty());
    let discriminator = session.clone().unwrap_or_else(|| format!("bridge-{}", random_hex()));
    let cwd = std::env::current_dir().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
    let Some(plugin_dir) = plugin_dir() else {
        log.note("cannot locate the plugin directory (<plugin>/bin/tm-relay with tm_bridge.py beside bin/); exiting so tm shows as failed");
        let _ = writeln!(std::io::stderr(), "tm-relay: cannot locate the plugin directory; not running");
        return 2;
    };
    let shared = relay::Shared::new(discriminator.clone(), random_hex(), cwd, plugin_dir, log.clone(), bounds);
    log.note(&format!(
        "discriminator {discriminator} from {}",
        if session.is_some() { "CLAUDE_CODE_SESSION_ID" } else { "a random id (no CLAUDE_CODE_SESSION_ID)" }
    ));

    if std::env::var("TASKMASTER_RELAY").map(|v| v == "python").unwrap_or(false) {
        // emergency lever: the whole session goes to the Python bridge, nothing replayed (initialize has not arrived yet)
        match shared.delegate_argv().map_err(|e| e.to_string()).and_then(|a| delegate::spawn(&a).map_err(|e| e.to_string())) {
            Ok((d, stdout)) => {
                {
                    let mut st = relay::lk(&shared.st);
                    st.fallback = Some(d.clone());
                    st.pump = true;
                    st.upstream_ready = true;
                }
                shared.start_pump(d, std::io::BufReader::new(stdout));
            }
            Err(e) => {
                let _ = writeln!(std::io::stderr(), "tm-bridge: cannot start the Python bridge: {e}");
                return 1;
            }
        }
    } else {
        match shared.helper_argv(false) {
            Ok(argv) => match helper::Helper::spawn(&argv, &discriminator) {
                Ok(h) => {
                    let boot = shared.clone();
                    let _ = std::thread::Builder::new().stack_size(512 * 1024).spawn(move || boot.boot(h));
                }
                Err(e) => shared.no_helper(&format!("cannot start {}: {e}", argv[0])),
            },
            Err(e) => shared.no_helper(&e),
        }
    }
    serve(&shared);
    shared.close();
    0
}

/// stdin framing: newline JSON, blank lines skipped, a parse error answered, a batch handled message by message, one thread per message.
fn serve(shared: &Arc<relay::Shared>) {
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let mut buf: Vec<u8> = Vec::new();
    loop {
        buf.clear();
        match input.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => return,
            Ok(_) => {}
        }
        let line = buf.trim_ascii();
        if line.is_empty() {
            continue;
        }
        let pump = relay::lk(&shared.st).pump;
        if pump {
            if let Some(d) = shared.fallback() {
                let _ = d.write_line(line);
            }
            continue;
        }
        let parsed: Value = match serde_json::from_slice(line) {
            Ok(v) => v,
            Err(_) => {
                shared.emit(&json!({"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "Parse error"}}));
                continue;
            }
        };
        let (messages, single) = match parsed {
            Value::Array(items) => (items, false),
            other => (vec![other], true),
        };
        for message in messages {
            if !message.is_object() {
                continue;
            }
            let raw = single.then(|| line.to_vec());
            if single && message.get("method").and_then(Value::as_str) == Some("initialize") && message.get("id").map(|i| !i.is_null()).unwrap_or(false) {
                if shared.init_decision(line) {
                    break; // a team clone took this line and every later one
                }
            }
            let me = shared.clone();
            let _ = std::thread::Builder::new().stack_size(512 * 1024).spawn(move || me.handle(&message, raw.as_deref()));
        }
    }
}
