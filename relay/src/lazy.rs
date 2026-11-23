// User intent: the lazy-start half of the relay - answer Claude Code's handshake at once from the shipped handshake, hold or answer calls
// until the helper has found or launched the project's server, and hand the whole session to a child process (stdio server, Python team
// bridge) whenever the local server cannot be had. Ported from tm_bridge.py (_lazy_handle, _boot, _hold, _switch_to_fallback).
use crate::consts::*;
use crate::delegate::{self, Delegate};
use crate::helper::{session_line, Helper, HelperError, Mode};
use crate::relay::*;
use crate::spawn::find_uv;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::process::ChildStdout;
use std::sync::Arc;
use std::time::Duration;

const MIN_INIT: &str = r#"{"capabilities":{"tools":{"listChanged":true}},"serverInfo":{"name":"tm","version":"0"}}"#;
const FB_INIT_ID: &str = "tm-bridge-fb-init";
const TEAM_LATE_NOTE: &str = "team clone detected after initialize; the team server's instructions apply from the next session";

/// One helper per reconnect episode (spec 3, M3), spawned on the first attempt and closed when the episode ends.
pub struct EnsureHelper {
    h: Option<Helper>,
}

impl EnsureHelper {
    pub fn new() -> EnsureHelper {
        EnsureHelper { h: None }
    }

    pub fn finish(&mut self, log: &crate::log::Log) {
        if let Some(h) = self.h.take() {
            h.finish(log.clone());
        }
    }
}

fn endpoint_from_answer(v: &Value) -> Result<Endpoint, Fail> {
    match v.get("kind").and_then(Value::as_str) {
        Some("endpoint") => Endpoint::from_line(v).map_err(Fail::Unavailable),
        Some("unavailable") => Err(Fail::Unavailable(v.get("reason").and_then(Value::as_str).unwrap_or("no reason given").to_string())),
        _ => Err(Fail::Unavailable("relay helper sent a line the relay does not know".into())),
    }
}

impl Shared {
    // -- commands the relay starts
    fn uv_python_argv(&self) -> Result<Vec<String>, String> {
        let uv = find_uv().ok_or("uv not found on PATH")?;
        Ok(vec![
            uv.to_string_lossy().to_string(),
            "run".into(),
            "--no-project".into(),
            "--python".into(),
            ">=3.11".into(),
            "python".into(),
            self.plugin_dir.join("tm_bridge.py").to_string_lossy().to_string(),
        ])
    }

    pub fn helper_argv(&self, ensure_only: bool) -> Result<Vec<String>, String> {
        let mut argv = match test_argv("TASKMASTER_RELAY_HELPER") {
            Some(a) => a,
            None => self.uv_python_argv()?,
        };
        argv.push("--relay-helper".into());
        if ensure_only {
            argv.push("--ensure-only".into());
        }
        Ok(argv)
    }

    pub fn delegate_argv(&self) -> Result<Vec<String>, String> {
        match test_argv("TASKMASTER_BRIDGE_TEST_DELEGATE_ARGV") {
            Some(a) => Ok(a),
            None => self.uv_python_argv(),
        }
    }

    fn fallback_argv(&self) -> Vec<String> {
        if let Some(a) = test_argv("TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV") {
            return a;
        }
        let uv = find_uv().map(|p| p.to_string_lossy().to_string()).unwrap_or_else(|| "uv".to_string());
        vec![uv, "run".into(), self.plugin_dir.join("backlog_server.py").to_string_lossy().to_string()]
    }

    // -- the helper, per reconnect attempt
    pub fn ensure(&self, eh: &mut EnsureHelper) -> Result<Endpoint, Fail> {
        if eh.h.as_ref().map(|h| !h.alive()).unwrap_or(true) {
            eh.h = None;
            let argv = self.helper_argv(true).map_err(Fail::Unavailable)?;
            let h = Helper::spawn(&argv, &self.discriminator).map_err(|e| Fail::Unavailable(format!("cannot start the relay helper: {e}")))?;
            eh.h = Some(h);
        }
        let _env = lk(&self.env_lock); // a launch's environment build runs inside this call: nothing of ours starts another meanwhile
        let Some(h) = eh.h.as_mut() else { return Err(Fail::Unavailable("relay helper missing".into())) };
        if h.send(r#"{"v":1,"op":"ensure"}"#).is_err() {
            eh.finish(&self.log);
            return Err(Fail::Unavailable("relay helper exited before answering".into()));
        }
        match h.next() {
            Ok(v) => endpoint_from_answer(&v),
            Err(e) => {
                self.log.once(&e.text());
                eh.finish(&self.log);
                Err(Fail::Unavailable(e.text()))
            }
        }
    }

    // -- shipped handshake
    fn shipped_handshake(&self) -> Option<(Value, Vec<Value>)> {
        let mut g = lk(&self.shipped);
        if g.is_none() {
            let loaded = std::fs::read_to_string(self.plugin_dir.join("tm_bridge_handshake.json"))
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|data| {
                    let init = data.get("initialize").filter(|v| v.is_object())?.clone();
                    let tools = data.get("tools").and_then(Value::as_array)?.clone();
                    Some((init, tools))
                });
            *g = Some(loaded);
        }
        g.clone().flatten()
    }

    // -- boot
    pub fn no_helper(&self, reason: &str) {
        self.log.once(&format!("cannot start the relay helper: {reason}"));
        let mut st = lk(&self.st);
        st.line1 = Line1::Gone;
        st.boot_error = Some(reason.to_string());
        st.uv_missing = true;
        st.upstream_ready = true;
        drop(st);
        self.cv.notify_all();
    }

    fn set_line1(&self, line: Line1) {
        lk(&self.st).line1 = line;
        self.cv.notify_all();
    }

    pub fn set_boot_error(self: &Arc<Self>, reason: &str, kind: Kind, startup: bool) {
        let enter = {
            let mut st = lk(&self.st);
            st.boot_error = Some(reason.to_string());
            st.pending = kind;
            st.pending_startup = startup;
            st.upstream_ready = true;
            st.inited_msg.is_some() && st.fallback.is_none() && !st.uv_missing
        };
        self.cv.notify_all();
        if enter {
            self.enter_fallback();
        }
    }

    pub fn enter_fallback(self: &Arc<Self>) {
        let (reason, kind, startup) = {
            let st = lk(&self.st);
            if st.fallback.is_some() || st.uv_missing {
                return;
            }
            (st.boot_error.clone().unwrap_or_default(), st.pending, st.pending_startup)
        };
        if let Err(e) = self.switch_to_delegate(kind, &reason, startup) {
            self.log.once(&format!("could not start the child server: {e}"));
        }
    }

    /// The whole rest of the session goes to a child process (the stdio server or the Python bridge): spawn it, replay the
    /// client's initialize + initialized into it (its answer swallowed), tell the client the tool list may have changed, and pump.
    pub fn switch_to_delegate(self: &Arc<Self>, kind: Kind, reason: &str, startup: bool) -> Result<(), String> {
        let _fb = lk(&self.fb_lock);
        if lk(&self.st).fallback.is_some() {
            return Ok(());
        }
        let argv = match kind {
            Kind::Fallback => self.fallback_argv(),
            Kind::Python => self.delegate_argv()?,
        };
        let (d, stdout) = {
            let _env = lk(&self.env_lock); // waits for a launch's environment build to finish instead of racing it
            delegate::spawn(&argv).map_err(|e| format!("cannot start {}: {e}", argv[0]))?
        };
        match (kind, startup) {
            (Kind::Fallback, true) => self.log.once(&format!("using the per-session stdio server ({reason})")),
            (Kind::Fallback, false) => self.log.once(&format!("the local server is lost and could not be relaunched ({reason}); switching to the per-session stdio server")),
            (Kind::Python, _) => self.log.once(reason),
        }
        let (init_msg, inited_msg) = {
            let st = lk(&self.st);
            (st.init_msg.clone(), st.inited_msg.clone())
        };
        let mut reader = BufReader::new(stdout);
        if let Some(mut init) = init_msg {
            init["id"] = json!(FB_INIT_ID);
            let line = serde_json::to_vec(&init).unwrap_or_default();
            d.write_line(&line).map_err(|e| e.to_string())?;
            loop {
                let mut buf = Vec::new();
                match reader.read_until(b'\n', &mut buf) {
                    Ok(0) | Err(_) => return Err("the child server exited during startup".to_string()),
                    Ok(_) => {}
                }
                if serde_json::from_slice::<Value>(&buf).ok().and_then(|v| v.get("id").cloned()) == Some(json!(FB_INIT_ID)) {
                    break;
                }
            }
            if let Some(inited) = inited_msg {
                let line = serde_json::to_vec(&inited).unwrap_or_default();
                let _ = d.write_line(&line);
                self.emit_list_changed();
            }
        }
        {
            lk(&self.st).fallback = Some(d.clone());
        }
        self.cv.notify_all();
        self.start_pump(d, reader);
        Ok(())
    }

    /// Child stdout to ours, line by line; when the child ends the session has no server, so the relay ends too.
    pub fn start_pump(self: &Arc<Self>, d: Arc<Delegate>, mut reader: BufReader<ChildStdout>) {
        let me = self.clone();
        let _ = std::thread::Builder::new().stack_size(512 * 1024).spawn(move || {
            loop {
                let mut buf = Vec::new();
                match reader.read_until(b'\n', &mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {
                        if !buf.ends_with(b"\n") {
                            buf.push(b'\n');
                        }
                        me.write_out(&buf);
                    }
                }
            }
            if me.closed() {
                return;
            }
            let code = d.exit_code(Duration::from_secs(2));
            std::process::exit(if code < 0 { 1 } else { code });
        });
    }

    /// Team clone seen before the client's initialize was answered: the Python bridge answers it (the team server's instructions
    /// and capabilities), the relay forwards the client's own line unchanged and becomes a pure pump.
    fn team_forward(self: &Arc<Self>, raw: &[u8]) -> bool {
        let argv = match self.delegate_argv() {
            Ok(a) => a,
            Err(e) => {
                self.log.once(&format!("team clone but the Python bridge cannot start: {e}"));
                return false;
            }
        };
        let (d, stdout) = match delegate::spawn(&argv) {
            Ok(x) => x,
            Err(e) => {
                self.log.once(&format!("team clone but the Python bridge cannot start: {e}"));
                return false;
            }
        };
        if d.write_line(raw).is_err() {
            return false;
        }
        {
            let mut st = lk(&self.st);
            st.fallback = Some(d.clone());
            st.pump = true;
            st.upstream_ready = true;
        }
        self.cv.notify_all();
        self.start_pump(d, BufReader::new(stdout));
        true
    }

    /// The client's initialize waits for the helper's line 1 up to INIT_WAIT (spec 3.1); true when a team clone took the line.
    pub fn init_decision(self: &Arc<Self>, raw: &[u8]) -> bool {
        let st = lk(&self.st);
        if st.pump {
            return false;
        }
        let (st, _) = self.cv.wait_timeout_while(st, secs(self.b.init_wait), |s| matches!(s.line1, Line1::Pending)).unwrap_or_else(|e| e.into_inner());
        let team = matches!(st.line1, Line1::Team) && st.fallback.is_none();
        drop(st);
        team && self.team_forward(raw)
    }

    /// Line 1 said team after the client's initialize was answered from the shipped handshake: delegate with replay.
    fn team_late(&self, st: &mut State) -> bool {
        if st.init_msg.is_some() && st.fallback.is_none() && st.boot_error.is_none() && matches!(st.line1, Line1::Team) {
            st.boot_error = Some(TEAM_LATE_NOTE.to_string());
            st.pending = Kind::Python;
            st.pending_startup = false;
            st.upstream_ready = true;
            return true;
        }
        false
    }

    /// The boot thread: the helper's lines decide what this session is.
    pub fn boot(self: Arc<Self>, mut helper: Helper) {
        let first = helper.next().and_then(|v| session_line(&v).ok_or_else(|| HelperError::Protocol("relay helper's first line is not a session line".into())));
        let line = match first {
            Ok(l) => l,
            Err(e) => {
                self.log.once(&e.text());
                helper.finish(self.log.clone());
                self.set_line1(Line1::Gone);
                self.set_boot_error(&e.text(), Kind::Python, true); // the Python bridge re-decides everything itself
                return;
            }
        };
        match line.mode {
            Mode::Fallback => {
                self.set_line1(Line1::Fallback);
                helper.finish(self.log.clone());
                self.set_boot_error(line.reason.as_deref().unwrap_or("local server not used"), Kind::Fallback, true);
            }
            Mode::Team => {
                self.set_line1(Line1::Team);
                helper.finish(self.log.clone());
                let late = {
                    let mut st = lk(&self.st);
                    let late = self.team_late(&mut st);
                    let enter = late && st.inited_msg.is_some();
                    (late, enter)
                };
                if late.0 {
                    self.cv.notify_all();
                    self.log.once(TEAM_LATE_NOTE);
                    if late.1 {
                        self.enter_fallback();
                    }
                }
            }
            Mode::Local => {
                self.set_line1(Line1::Local);
                self.boot_local(helper);
            }
        }
    }

    fn boot_local(self: &Arc<Self>, mut helper: Helper) {
        let answer = {
            let _env = lk(&self.env_lock); // held while the helper settles or launches the server
            helper.next()
        };
        let endpoint = match answer {
            Err(e) => {
                self.log.once(&e.text());
                helper.finish(self.log.clone());
                self.set_boot_error(&e.text(), Kind::Fallback, true);
                return;
            }
            Ok(v) => match endpoint_from_answer(&v) {
                Ok(ep) => ep,
                Err(f) => {
                    helper.finish(self.log.clone());
                    self.set_boot_error(&f.text(), Kind::Fallback, true);
                    return;
                }
            },
        };
        helper.finish(self.log.clone());
        let url = endpoint.url.clone();
        {
            let mut st = lk(&self.st);
            st.server_build = endpoint.build.clone();
            st.endpoint = Some(Arc::new(endpoint));
        }
        self.log.note(&format!("using {url} as session {} nonce {}", self.discriminator, self.nonce));
        {
            let mut st = lk(&self.st);
            if st.init_msg.is_none() {
                // no client handshake yet: the normal path takes it from here
                st.upstream_ready = true;
                drop(st);
                self.cv.notify_all();
                return;
            }
        }
        let result = match self.replay() {
            Ok(r) => r,
            Err(e) => {
                // draining / provably unsent / ambiguous: re-run discovery and reconnect like the eager bridge; only a genuine
                // inability to get a server within the bound falls back
                self.log.note(&format!("initialize replay failed, reconnecting: {}", e.text()));
                let epoch = self.epoch();
                if !self.reconnect(epoch, None) {
                    self.set_boot_error(&e.text(), Kind::Fallback, true);
                    return;
                }
                let mut st = lk(&self.st);
                if st.fallback.is_some() {
                    st.upstream_ready = true;
                    drop(st);
                    self.cv.notify_all();
                    return;
                }
                st.epoch = st.epoch.saturating_sub(1); // reconnect() already counted the new session; counted once below
                st.last_init_result.clone()
            }
        };
        let need_inited = {
            let mut st = lk(&self.st);
            let need = !st.inited_upstream;
            st.inited_upstream = true;
            need.then(|| st.inited_msg.clone())
        };
        if let Some(inited) = need_inited {
            let msg = inited.unwrap_or_else(|| json!({"jsonrpc": "2.0", "method": "notifications/initialized"}));
            if let Err(e) = self.post_notification(&msg) {
                self.log.note(&format!("background start failed: {}", e.text()));
                self.set_boot_error(&e.text(), Kind::Fallback, true);
                return;
            }
        }
        lk(&self.st).epoch += 1;
        self.start_stream();
        lk(&self.st).upstream_ready = true;
        self.cv.notify_all();
        self.reconcile(result);
    }

    /// Upstream is up: tell the client when what it was served (shipped handshake) differs from the live server.
    fn reconcile(self: &Arc<Self>, live_init: Option<Value>) {
        {
            let st = lk(&self.st);
            let _ = self.cv.wait_timeout_while(st, Duration::from_secs(5), |s| s.inited_msg.is_none() && !s.closed);
        }
        let tools = match self.upstream_tools() {
            Ok(t) => t,
            Err(e) => {
                self.log.note(&format!("could not list tools to reconcile the handshake: {}", e.text()));
                None
            }
        };
        let (served_init, served_tools) = {
            let mut st = lk(&self.st);
            (st.served_init.take(), st.served_tools.take())
        };
        *lk(&self.shipped) = Some(None); // the shipped handshake is not needed again: give the memory back
        let live = live_init.unwrap_or(Value::Null);
        let served = served_init.clone().unwrap_or(Value::Null);
        let instructions_differ = served.get("instructions") != live.get("instructions");
        let tools_differ = matches!((&served_tools, &tools), (Some(a), Some(b)) if a != b);
        if (instructions_differ || tools_differ) && served_init.is_some() {
            if instructions_differ {
                self.log.note("the live instructions differ from the served ones; clients read them at initialize, so the next session gets them");
            }
            self.emit_list_changed();
        }
    }

    // -- the lazy answers
    /// True when the message was dealt with here (answered from the shipped handshake, or held then handed to the fallback).
    pub fn lazy_handle(self: &Arc<Self>, message: &Value) -> bool {
        let method = message.get("method").and_then(Value::as_str);
        let mid = message.get("id").filter(|v| !v.is_null());
        if method == Some("notifications/cancelled") {
            let mut st = lk(&self.st);
            if let Some(rid) = message.pointer("/params/requestId").filter(|v| !v.is_null()) {
                let key = id_key(rid);
                if st.held.contains(&key) {
                    st.cancelled.insert(key); // still held: drop it; MCP says a cancelled request gets no answer
                    return true;
                }
            }
            return false;
        }
        {
            let mut st = lk(&self.st);
            if st.upstream_ready && st.boot_error.is_none() {
                if method == Some("notifications/initialized") && st.inited_upstream {
                    st.inited_msg = Some(message.clone());
                    return true;
                }
                return false;
            }
            if st.fallback.is_some() {
                return false;
            }
        }
        if let (Some("initialize"), Some(mid)) = (method, mid) {
            let shipped = self.shipped_handshake();
            let mut result: Value = match &shipped {
                Some((init, _)) => init.clone(),
                None => serde_json::from_str(MIN_INIT).unwrap_or(Value::Null),
            };
            let tools = shipped.map(|(_, t)| t);
            let asked = message.pointer("/params/protocolVersion").and_then(Value::as_str);
            // echo only a version the server family speaks; anything else is answered with ours and the client decides (cold M4)
            let proto = match asked {
                Some(a) if KNOWN_PROTOCOLS.contains(&a) => a.to_string(),
                _ => result.get("protocolVersion").and_then(Value::as_str).unwrap_or("2025-06-18").to_string(),
            };
            result["protocolVersion"] = json!(proto);
            let late_team = {
                let mut st = lk(&self.st);
                if st.upstream_ready && st.boot_error.is_none() {
                    return false;
                }
                st.init_msg = Some(message.clone());
                st.served_init = Some(result.clone());
                st.served_tools = tools;
                self.team_late(&mut st)
            };
            self.emit(&json!({"jsonrpc": "2.0", "id": mid, "result": result}));
            if late_team {
                self.cv.notify_all();
                self.log.once(TEAM_LATE_NOTE);
            }
            return true;
        }
        if let (Some("ping"), Some(mid)) = (method, mid) {
            self.emit(&json!({"jsonrpc": "2.0", "id": mid, "result": {}}));
            return true;
        }
        if let (Some("server/discover"), Some(mid)) = (method, mid) {
            // Claude Code probes the modern protocol first; the server answers method-not-found and the client uses initialize.
            self.emit(&json!({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}));
            return true;
        }
        let boot_ok = lk(&self.st).boot_error.is_none();
        if let (Some(m @ ("prompts/list" | "resources/list" | "resources/templates/list")), Some(mid), true) = (method, mid, boot_ok) {
            let result = match m {
                "prompts/list" => json!({"prompts": []}),
                "resources/list" => json!({"resources": []}),
                _ => json!({"resourceTemplates": []}),
            };
            self.emit(&json!({"jsonrpc": "2.0", "id": mid, "result": result}));
            return true;
        }
        if method == Some("notifications/initialized") {
            let enter = {
                let mut st = lk(&self.st);
                st.inited_msg = Some(message.clone());
                st.boot_error.is_some() && st.fallback.is_none() && !st.uv_missing
            };
            if enter {
                self.enter_fallback();
            }
            return true;
        }
        if let (Some("tools/list"), Some(mid), true) = (method, mid, boot_ok) {
            let served = lk(&self.st).served_tools.clone();
            if let Some(tools) = served {
                self.emit(&json!({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}));
                return true;
            }
            if !self.wait_ready(self.b.list_hold) {
                // Claude Code gives up on a request after ~15-20 s and then drops the connection when the late answer arrives
                // (live cold-start run), so answer now and let list_changed bring the real list.
                lk(&self.st).served_tools = Some(Vec::new());
                self.emit(&json!({"jsonrpc": "2.0", "id": mid, "result": {"tools": []}}));
                return true;
            }
        }
        // everything else waits for the upstream session
        self.log.note(&format!("holding {} (id {}) until the upstream session is ready", method.unwrap_or(""), mid.map(|m| m.to_string()).unwrap_or_else(|| "null".into())));
        let held = method == Some("tools/call") && mid.is_some();
        let key = mid.map(id_key);
        if held {
            if let Some(k) = &key {
                lk(&self.st).held.insert(k.clone());
            }
        }
        let handled = self.hold(message, method, mid);
        if held {
            if let Some(k) = &key {
                let mut st = lk(&self.st);
                st.held.remove(k);
                st.cancelled.remove(k);
            }
        }
        handled
    }

    fn was_cancelled(&self, mid: Option<&Value>) -> bool {
        // a cancelled held call gets no answer on any branch (MCP; and a late answer to an abandoned id makes Claude Code drop the connection)
        mid.map(|m| lk(&self.st).cancelled.contains(&id_key(m))).unwrap_or(false)
    }

    fn hold(self: &Arc<Self>, message: &Value, method: Option<&str>, mid: Option<&Value>) -> bool {
        // a tool call may wait long; any other request must be answered before the client gives up on it
        let bound = if method == Some("tools/call") { self.b.hold_bound } else { self.b.list_hold };
        let name = message.pointer("/params/name").and_then(Value::as_str).or(method).unwrap_or("").to_string();
        let answerable = mid.is_some() && method.is_some();
        if !self.wait_ready(bound) {
            if answerable && !self.was_cancelled(mid) {
                if let Some(m) = mid {
                    self.emit_error(m, &may_have_committed_text(&name, true, "local"));
                }
            }
            return true;
        }
        let (boot_error, uv_missing) = {
            let st = lk(&self.st);
            (st.boot_error.clone(), st.uv_missing)
        };
        if let Some(reason) = boot_error {
            if uv_missing {
                if answerable && !self.was_cancelled(mid) {
                    if let Some(m) = mid {
                        self.emit_error(m, &format!("taskmaster bridge error: {reason}"));
                    }
                }
                return true;
            }
            self.enter_fallback();
            if self.fallback().is_none() {
                if answerable && !self.was_cancelled(mid) {
                    if let Some(m) = mid {
                        self.emit_error(m, &may_have_committed_text(&name, true, "local"));
                    }
                }
                return true;
            }
        }
        let mut st = lk(&self.st); // last look: a cancel that landed while held wins; after this it is forwarded
        if let Some(m) = mid {
            let key = id_key(m);
            if st.cancelled.contains(&key) {
                return true;
            }
            st.held.remove(&key);
        }
        false
    }
}
