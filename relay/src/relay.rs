// User intent: the Rust port of tm_bridge.py's Bridge - one stdio MCP session relayed to the project's shared local server with
// the same retry rule, wording, reconnect and replay, so nothing the reviews added regresses while the per-session cost drops to ~1 MB.
use crate::consts::*;
use crate::delegate::Delegate;
use crate::http::{self, OpenError, Request, Response};
use crate::log::Log;
use crate::sse;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::io::{self, Write};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::time::{Duration, Instant};

pub fn lk<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

pub enum Fail {
    /// Provably never reached the server.
    Unsent(String),
    /// May have been received.
    Ambiguous(String),
    /// No usable local server this time (the text says why).
    Unavailable(String),
    Io(String),
}

impl Fail {
    pub fn text(&self) -> String {
        match self {
            Fail::Unsent(s) | Fail::Ambiguous(s) | Fail::Unavailable(s) | Fail::Io(s) => s.clone(),
        }
    }
}

impl From<io::Error> for Fail {
    fn from(e: io::Error) -> Fail {
        Fail::Io(e.to_string())
    }
}

/// Where the local server is. The token never leaves this struct (no Debug impl on purpose).
pub struct Endpoint {
    pub host: String,
    pub port: u16,
    pub path: String,
    pub token: String,
    pub build: Option<Value>,
    pub url: String,
}

impl Endpoint {
    /// From the helper's endpoint line. The relay sends the token, so it refuses a non-loopback host itself.
    pub fn from_line(v: &Value) -> Result<Endpoint, String> {
        let url = v.get("url").and_then(Value::as_str).ok_or("the helper's endpoint line has no url")?;
        let token = v.get("token").and_then(Value::as_str).ok_or("the helper's endpoint line has no token")?;
        let (host, port, path) = parse_url(url)?;
        if !LOOPBACK_HOSTS.contains(&host.as_str()) {
            return Err(format!("the local server record names a non-loopback host: {host:?}"));
        }
        let build = v.get("build").filter(|b| b.is_object()).cloned();
        Ok(Endpoint { host, port, path, token: token.to_string(), build, url: url.to_string() })
    }
}

pub fn parse_url(url: &str) -> Result<(String, u16, String), String> {
    let rest = url.strip_prefix("http://").ok_or_else(|| format!("unsupported server url {url:?}"))?;
    let (authority, tail) = match rest.find(['/', '?', '#']) {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, ""),
    };
    let path = tail.split(['?', '#']).next().unwrap_or("");
    let path = if path.is_empty() { "/mcp".to_string() } else { path.to_string() };
    let authority = authority.rsplit('@').next().unwrap_or(authority);
    let (host, port) = if let Some(inner) = authority.strip_prefix('[') {
        let (h, after) = inner.split_once(']').ok_or("bad IPv6 host")?;
        (h.to_string(), after.strip_prefix(':').and_then(|p| p.parse::<u16>().ok()))
    } else if let Some((h, p)) = authority.rsplit_once(':') {
        (h.to_string(), p.parse::<u16>().ok())
    } else {
        (authority.to_string(), None)
    };
    Ok((host.to_ascii_lowercase(), port.filter(|p| *p != 0).unwrap_or(80), path))
}

pub fn may_have_committed_text(tool: &str, sent_known: bool, place: &str) -> String {
    // The first line matches the mods' "not connected" matcher (mods/taskmaster-mods/hooks/tm.ts NOT_CONNECTED), so a restart blip shows as "connecting".
    let head = format!("no connected MCP tool \"{tool}\" on a server named \"{SERVER_NAME}\"");
    if sent_known {
        format!("{head}: the {place} taskmaster server could not be reached; this call was not sent")
    } else {
        format!("{head}: the connection to the {place} taskmaster server was lost while this call ran; the call may have committed, check its effect before repeating it")
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Kind {
    /// The per-session stdio server (backlog_server.py).
    Fallback,
    /// The Python bridge in full mode (team clones, a helper that is gone).
    Python,
}

pub enum Line1 {
    Pending,
    Local,
    Team,
    Fallback,
    /// The helper never answered with a usable line 1 (exited, wrong protocol, could not start).
    Gone,
}

pub struct State {
    pub line1: Line1,
    pub endpoint: Option<Arc<Endpoint>>,
    pub sid: Option<String>,
    pub protocol: Option<String>,
    pub epoch: u64,
    pub init_msg: Option<Value>,
    pub inited_msg: Option<Value>,
    pub server_build: Option<Value>,
    pub replays: u64,
    pub stream_started: bool,
    pub stream_wake: bool,
    pub failures: Vec<Instant>,
    pub fallback: Option<Arc<Delegate>>,
    /// Every client line goes straight to this delegate (team clone, TASKMASTER_RELAY=python).
    pub pump: bool,
    pub served_init: Option<Value>,
    pub served_tools: Option<Vec<Value>>,
    pub inited_upstream: bool,
    pub held: HashSet<String>,
    pub cancelled: HashSet<String>,
    pub boot_error: Option<String>,
    pub pending: Kind,
    pub pending_startup: bool,
    pub uv_missing: bool,
    pub upstream_ready: bool,
    pub last_init_result: Option<Value>,
    pub closed: bool,
}

impl State {
    fn new() -> State {
        State {
            line1: Line1::Pending,
            endpoint: None,
            sid: None,
            protocol: None,
            epoch: 0,
            init_msg: None,
            inited_msg: None,
            server_build: None,
            replays: 0,
            stream_started: false,
            stream_wake: false,
            failures: Vec::new(),
            fallback: None,
            pump: false,
            served_init: None,
            served_tools: None,
            inited_upstream: false,
            held: HashSet::new(),
            cancelled: HashSet::new(),
            boot_error: None,
            pending: Kind::Fallback,
            pending_startup: true,
            uv_missing: false,
            upstream_ready: false,
            last_init_result: None,
            closed: false,
        }
    }
}

pub struct Shared {
    pub st: Mutex<State>,
    pub cv: Condvar,
    pub out: Mutex<Box<dyn Write + Send>>,
    pub rlock: Mutex<()>,
    pub env_lock: Mutex<()>,
    pub fb_lock: Mutex<()>,
    pub discriminator: String,
    pub nonce: String,
    pub cwd: String,
    pub plugin_dir: PathBuf,
    pub log: Log,
    pub b: Bounds,
    pub shipped: Mutex<Option<Option<(Value, Vec<Value>)>>>,
}

pub enum Next {
    Done,
    Reconnect,
    Ambiguous,
}

pub fn id_key(v: &Value) -> String {
    v.to_string()
}

impl Shared {
    pub fn new(discriminator: String, nonce: String, cwd: String, plugin_dir: PathBuf, log: Log, b: Bounds) -> Arc<Shared> {
        Shared::with_out(discriminator, nonce, cwd, plugin_dir, log, b, Box::new(io::stdout()))
    }

    pub fn with_out(discriminator: String, nonce: String, cwd: String, plugin_dir: PathBuf, log: Log, b: Bounds, out: Box<dyn Write + Send>) -> Arc<Shared> {
        Arc::new(Shared {
            st: Mutex::new(State::new()),
            cv: Condvar::new(),
            out: Mutex::new(out),
            rlock: Mutex::new(()),
            env_lock: Mutex::new(()),
            fb_lock: Mutex::new(()),
            discriminator,
            nonce,
            cwd,
            plugin_dir,
            log,
            b,
            shipped: Mutex::new(None),
        })
    }

    // -- output
    pub fn write_out(&self, bytes: &[u8]) {
        let mut o = lk(&self.out);
        let _ = o.write_all(bytes);
        let _ = o.flush();
    }

    pub fn emit(&self, message: &Value) {
        if let Ok(mut line) = serde_json::to_vec(message) {
            line.push(b'\n');
            self.write_out(&line);
        }
    }

    pub fn emit_error(&self, mid: &Value, text: &str) {
        self.emit(&json!({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": text}}));
    }

    pub fn emit_list_changed(&self) {
        self.emit(&json!({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}));
    }

    // -- small state accessors
    pub fn closed(&self) -> bool {
        lk(&self.st).closed
    }

    pub fn epoch(&self) -> u64 {
        lk(&self.st).epoch
    }

    pub fn fallback(&self) -> Option<Arc<Delegate>> {
        lk(&self.st).fallback.clone()
    }

    pub fn wait_ready(&self, timeout: f64) -> bool {
        let st = lk(&self.st);
        let (st, _) = self.cv.wait_timeout_while(st, secs(timeout), |s| !s.upstream_ready).unwrap_or_else(|e| e.into_inner());
        st.upstream_ready
    }

    // -- HTTP
    pub fn open(&self, verb: &str, body: Option<&[u8]>, read_timeout: Option<Duration>, sid: Option<&str>, accept: &str) -> Result<Response, Fail> {
        let (ep, protocol) = {
            let st = lk(&self.st);
            (st.endpoint.clone(), st.protocol.clone())
        };
        let Some(ep) = ep else { return Err(Fail::Unsent("no server endpoint yet".into())) };
        let mut headers = vec![
            ("Authorization".to_string(), format!("Bearer {}", ep.token)),
            ("Accept".to_string(), accept.to_string()),
            ("X-Taskmaster-Session".to_string(), self.discriminator.clone()),
            ("X-Taskmaster-Session-Nonce".to_string(), self.nonce.clone()),
            ("X-Taskmaster-Cwd".to_string(), http::encode_cwd(&self.cwd)),
        ];
        if body.is_some() {
            headers.push(("Content-Type".into(), "application/json".into()));
        }
        if let Some(sid) = sid {
            headers.push(("Mcp-Session-Id".into(), sid.to_string()));
            if let Some(p) = protocol.filter(|p| !p.is_empty()) {
                headers.push(("MCP-Protocol-Version".into(), p));
            }
        }
        let req = Request { host: &ep.host, port: ep.port, verb, path: &ep.path, headers, body, read_timeout };
        http::open(&req, secs(CONNECT_TIMEOUT)).map_err(|e| match e {
            OpenError::Unsent(m) => Fail::Unsent(m),
            OpenError::Ambiguous(m) => Fail::Ambiguous(m),
        })
    }

    /// Messages of a response body: one JSON document or SSE parsed as it arrives; non-object items are skipped.
    pub fn events(resp: &mut Response, f: &mut dyn FnMut(&Value)) -> io::Result<()> {
        let ctype = resp.header("Content-Type").unwrap_or("").to_string();
        let mut deliver = |v: Value| {
            if v.is_object() {
                f(&v);
            }
        };
        if ctype.contains("text/event-stream") {
            sse::parse_stream(resp.body(), MAX_LINE_BYTES, &mut deliver)
        } else {
            let raw = resp.read_capped(MAX_BODY_BYTES)?;
            let text = String::from_utf8(raw).map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid utf-8 in a response"))?;
            if !text.trim().is_empty() {
                for v in sse::decode(&text) {
                    deliver(v);
                }
            }
            Ok(())
        }
    }

    // -- requests
    pub fn handle(self: &Arc<Self>, message: &Value, raw: Option<&[u8]>) {
        if self.lazy_handle(message) {
            return;
        }
        let method = message.get("method").and_then(Value::as_str);
        let mid = message.get("id").filter(|v| !v.is_null());
        let is_request = mid.is_some() && method.is_some();
        let is_call = method == Some("tools/call");
        let tool = if is_call { message.pointer("/params/name").and_then(Value::as_str) } else { None };
        let timeout = if is_call { None } else { Some(secs(self.b.short_timeout)) };
        let body: Vec<u8> = match raw {
            Some(r) => r.to_vec(),
            None => serde_json::to_vec(message).unwrap_or_default(),
        };
        let mut ambiguous = false;
        for _ in 0..RETRY_ATTEMPTS {
            if let Some(fb) = self.fallback() {
                let _g = lk(&self.fb_lock);
                let _ = fb.write_line(&body);
                return;
            }
            let epoch = self.epoch();
            let sid = if method == Some("initialize") { None } else { lk(&self.st).sid.clone() };
            match self.attempt(message, method, mid, is_request, is_call, tool, &body, timeout, sid.as_deref()) {
                Next::Done => return,
                Next::Reconnect => {
                    if self.reconnect(epoch, None) {
                        continue;
                    }
                    break;
                }
                Next::Ambiguous => {
                    ambiguous = true;
                    break;
                }
            }
        }
        if ambiguous {
            self.background_reconnect();
        }
        if is_request {
            if let Some(mid) = mid {
                self.emit_error(mid, &may_have_committed_text(tool.or(method).unwrap_or(""), !ambiguous, "local"));
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn attempt(self: &Arc<Self>, message: &Value, method: Option<&str>, mid: Option<&Value>, is_request: bool, is_call: bool, tool: Option<&str>, body: &[u8], timeout: Option<Duration>, sid: Option<&str>) -> Next {
        let method_name = method.unwrap_or("");
        let mut resp = match self.open("POST", Some(body), timeout, sid, ACCEPT_BOTH) {
            Ok(r) => r,
            Err(Fail::Unsent(_)) => return Next::Reconnect,
            Err(other) => {
                self.log.note(&format!("{method_name}: connection lost after send: {}", other.text()));
                return if is_call { Next::Ambiguous } else { Next::Reconnect };
            }
        };
        match self.consume(&mut resp, message, method, mid, is_request, is_call, tool) {
            Ok(next) => next,
            Err(e) => {
                self.log.note(&format!("{method_name}: response lost: {e}"));
                if is_call { Next::Ambiguous } else { Next::Reconnect }
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn consume(self: &Arc<Self>, resp: &mut Response, message: &Value, method: Option<&str>, mid: Option<&Value>, is_request: bool, is_call: bool, tool: Option<&str>) -> io::Result<Next> {
        let status = resp.status;
        if status == 404 && method != Some("initialize") {
            resp.drain()?; // "Session not found", answered before dispatch
            return Ok(Next::Reconnect);
        }
        if status == 202 {
            resp.drain()?;
            self.after_forward(message);
            return Ok(Next::Done);
        }
        if status >= 400 {
            let raw = resp.read_up_to(ERROR_BODY_READ)?;
            let text: String = String::from_utf8_lossy(&raw).chars().take(ERROR_TEXT_CHARS).collect();
            if status == 503 && text.contains(DRAINING_TEXT) {
                return Ok(Next::Reconnect); // refused before dispatch: the request never ran
            }
            if is_request {
                if let Some(mid) = mid {
                    if (status == 502 || status == 504) && is_call {
                        self.emit_error(mid, &may_have_committed_text(tool.or(method).unwrap_or(""), false, "local"));
                    } else {
                        self.emit_error(mid, &format!("taskmaster server answered HTTP {status}: {text}"));
                    }
                }
            }
            return Ok(Next::Done);
        }
        self.after_response(message, resp)?;
        self.after_forward(message);
        Ok(Next::Done)
    }

    fn after_response(&self, message: &Value, resp: &mut Response) -> io::Result<()> {
        let method = message.get("method").and_then(Value::as_str);
        let mid = message.get("id").filter(|v| !v.is_null());
        let sid = resp.header("Mcp-Session-Id").map(str::to_string);
        let mut final_seen = false;
        let result = Shared::events(resp, &mut |item| {
            if method == Some("initialize") && item.get("id") == mid && item.get("result").map(Value::is_object).unwrap_or(false) {
                let mut st = lk(&self.st);
                st.protocol = item.pointer("/result/protocolVersion").and_then(Value::as_str).map(str::to_string);
                if let Some(s) = &sid {
                    st.sid = Some(s.clone()); // recorded before the result reaches the client: its next messages need the session id
                }
                st.init_msg = Some(message.clone());
                st.epoch += 1;
            }
            if item.get("id") == mid && (item.get("result").is_some() || item.get("error").is_some()) {
                final_seen = true;
            }
            self.emit(item);
        });
        if let Err(e) = result {
            if !final_seen {
                return Err(e); // a stream that fails after the answer went out must not answer or retry it again
            }
        }
        if mid.is_some() && method.is_some() && !final_seen {
            return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "response ended without an answer"));
        }
        Ok(())
    }

    fn after_forward(self: &Arc<Self>, message: &Value) {
        if message.get("method").and_then(Value::as_str) == Some("notifications/initialized") {
            lk(&self.st).inited_msg = Some(message.clone());
            self.start_stream();
        }
    }

    // -- (re)connect
    pub fn background_reconnect(self: &Arc<Self>) {
        let epoch = self.epoch();
        let me = self.clone();
        let _ = std::thread::Builder::new().stack_size(512 * 1024).spawn(move || {
            me.reconnect(epoch, Some(me.b.reconnect_budget));
        });
    }

    /// Re-establish the upstream session (eagerly, with backoff); true when a session newer than seen_epoch exists.
    pub fn reconnect(self: &Arc<Self>, seen_epoch: u64, budget: Option<f64>) -> bool {
        let _rl = lk(&self.rlock);
        {
            let st = lk(&self.st);
            if st.epoch != seen_epoch || st.fallback.is_some() {
                return true;
            }
        }
        let deadline = Instant::now() + secs(budget.unwrap_or(self.b.reconnect_budget));
        let mut delay = RECONNECT_BACKOFF_START;
        let mut helper = crate::lazy::EnsureHelper::new();
        loop {
            if self.closed() {
                return false;
            }
            match self.try_once(&mut helper) {
                Ok(()) => {
                    {
                        let mut st = lk(&self.st);
                        st.epoch += 1;
                        st.stream_wake = true;
                    }
                    self.cv.notify_all();
                    helper.finish(&self.log);
                    return true;
                }
                Err(e) => {
                    self.log.note(&format!("reconnect failed: {}", e.text()));
                    if matches!(e, Fail::Unavailable(_)) && self.count_failure() {
                        helper.finish(&self.log);
                        return match self.switch_to_delegate(Kind::Fallback, &e.text(), false) {
                            Ok(()) => true,
                            Err(why) => {
                                self.log.note(&format!("switching to the stdio server failed: {why}"));
                                false
                            }
                        };
                    }
                    if Instant::now() >= deadline {
                        helper.finish(&self.log);
                        return false;
                    }
                    std::thread::sleep(secs(delay));
                    delay = (delay * 2.0).min(RECONNECT_BACKOFF_CAP);
                }
            }
        }
    }

    fn try_once(&self, helper: &mut crate::lazy::EnsureHelper) -> Result<(), Fail> {
        let ep = self.ensure(helper)?;
        lk(&self.st).endpoint = Some(Arc::new(ep));
        self.replay()?;
        Ok(())
    }

    fn count_failure(&self) -> bool {
        let now = Instant::now();
        let window = secs(self.b.fallback_window);
        let mut st = lk(&self.st);
        st.failures.retain(|t| now.duration_since(*t) < window);
        st.failures.push(now);
        st.failures.len() >= FALLBACK_FAILURES
    }

    /// Replay the cached initialize + initialized on a fresh upstream session; list_changed when the build changed.
    /// Returns the upstream's initialize result (None when there was nothing to replay).
    pub fn replay(&self) -> Result<Option<Value>, Fail> {
        let (previous, old_sid, init_msg, inited_msg, endpoint) = {
            let st = lk(&self.st);
            (st.server_build.clone(), st.sid.clone(), st.init_msg.clone(), st.inited_msg.clone(), st.endpoint.clone())
        };
        let endpoint_build = endpoint.as_ref().and_then(|e| e.build.clone());
        let Some(init_msg) = init_msg else {
            let mut st = lk(&self.st);
            st.sid = None;
            st.server_build = endpoint_build;
            return Ok(None);
        };
        let n = {
            let mut st = lk(&self.st);
            st.replays += 1;
            st.replays
        };
        let mut message = init_msg;
        let rid = format!("tm-bridge-reinit-{n}");
        message["id"] = json!(rid);
        let body = serde_json::to_vec(&message).unwrap_or_default();
        let mut resp = self.open("POST", Some(&body), Some(secs(self.b.short_timeout)), None, ACCEPT_BOTH)?;
        let sid = resp.header("Mcp-Session-Id").map(str::to_string);
        let mut init_result: Option<Value> = None;
        Shared::events(&mut resp, &mut |item| {
            if item.get("id").and_then(Value::as_str) == Some(rid.as_str()) {
                if let Some(r) = item.get("result").filter(|r| r.is_object()) {
                    init_result = Some(r.clone());
                }
            }
        })
        .map_err(|e| Fail::Ambiguous(e.to_string()))?;
        let status = resp.status;
        drop(resp);
        let sid = match (status, sid) {
            (200, Some(sid)) => sid,
            _ => return Err(Fail::Unsent(format!("initialize replay answered HTTP {status}"))),
        };
        {
            let mut st = lk(&self.st);
            st.last_init_result = init_result.clone();
            if let Some(p) = init_result.as_ref().and_then(|r| r.get("protocolVersion")).and_then(Value::as_str) {
                st.protocol = Some(p.to_string());
            }
        }
        if let Some(inited) = inited_msg.clone() {
            let body = serde_json::to_vec(&inited).unwrap_or_default();
            let mut resp = self.open("POST", Some(&body), Some(secs(self.b.short_timeout)), Some(&sid), ACCEPT_BOTH)?;
            resp.drain().map_err(|e| Fail::Ambiguous(e.to_string()))?;
            lk(&self.st).inited_upstream = true;
        }
        {
            lk(&self.st).sid = Some(sid.clone());
        }
        if let Some(old) = old_sid.filter(|o| *o != sid) {
            self.drop_session(&old);
        }
        let changed = {
            let mut st = lk(&self.st);
            st.server_build = endpoint_build.clone();
            previous != st.server_build && st.inited_msg.is_some()
        };
        if changed {
            self.emit_list_changed();
        }
        Ok(init_result)
    }

    /// End a replaced upstream session (best effort): the local profile keeps idle sessions, so one per reconnect would pile up.
    fn drop_session(&self, sid: &str) {
        if let Ok(mut resp) = self.open("DELETE", None, Some(secs(DELETE_TIMEOUT)), Some(sid), ACCEPT_BOTH) {
            let _ = resp.drain();
        }
    }

    pub fn post_notification(&self, message: &Value) -> Result<(), Fail> {
        let sid = lk(&self.st).sid.clone();
        let body = serde_json::to_vec(message).unwrap_or_default();
        let mut resp = self.open("POST", Some(&body), Some(secs(self.b.short_timeout)), sid.as_deref(), ACCEPT_BOTH)?;
        resp.drain()?;
        Ok(())
    }

    pub fn upstream_tools(&self) -> Result<Option<Vec<Value>>, Fail> {
        let sid = lk(&self.st).sid.clone();
        let message = json!({"jsonrpc": "2.0", "id": "tm-bridge-tools", "method": "tools/list", "params": {}});
        let body = serde_json::to_vec(&message).unwrap_or_default();
        let mut resp = self.open("POST", Some(&body), Some(secs(self.b.short_timeout)), sid.as_deref(), ACCEPT_BOTH)?;
        let mut tools: Option<Vec<Value>> = None;
        Shared::events(&mut resp, &mut |item| {
            if tools.is_none() && item.get("id").and_then(Value::as_str) == Some("tm-bridge-tools") {
                if let Some(t) = item.pointer("/result/tools").and_then(Value::as_array) {
                    tools = Some(t.clone());
                }
            }
        })?;
        Ok(tools)
    }

    // -- the GET stream (the relay's liveness; also carries server notifications)
    pub fn start_stream(self: &Arc<Self>) {
        {
            let mut st = lk(&self.st);
            if st.stream_started {
                return;
            }
            st.stream_started = true;
        }
        let me = self.clone();
        let _ = std::thread::Builder::new().stack_size(512 * 1024).spawn(move || me.stream_loop());
    }

    fn backoff(&self, failures: usize) -> Duration {
        let list = &self.b.get_backoff;
        secs(list[failures.min(list.len()).max(1) - 1])
    }

    fn closed_wait(&self, timeout: Duration) {
        let st = lk(&self.st);
        let _ = self.cv.wait_timeout_while(st, timeout, |s| !s.closed);
    }

    fn stream_loop(self: &Arc<Self>) {
        let mut failures: usize = 0;
        loop {
            let (epoch, sid, stop) = {
                let st = lk(&self.st);
                (st.epoch, st.sid.clone(), st.closed || st.fallback.is_some())
            };
            if stop {
                return;
            }
            let Some(sid) = sid else {
                let st = lk(&self.st);
                let (mut st, _) = self.cv.wait_timeout_while(st, Duration::from_secs(1), |s| !s.stream_wake && !s.closed).unwrap_or_else(|e| e.into_inner());
                st.stream_wake = false;
                continue;
            };
            let mut resp = match self.open("GET", None, None, Some(&sid), ACCEPT_STREAM) {
                Ok(r) => r,
                Err(_) => {
                    failures += 1;
                    self.reconnect(epoch, None);
                    std::thread::sleep(self.backoff(failures));
                    continue;
                }
            };
            if resp.status == 404 {
                let _ = resp.drain();
                self.reconnect(epoch, None);
                continue;
            }
            if resp.status != 200 {
                let _ = resp.drain();
                failures += 1;
                self.closed_wait(secs((GET_NON200_STEP * failures as f64).min(GET_NON200_CAP))); // e.g. 405: this server offers no stream
                continue;
            }
            failures = 0;
            let result = Shared::events(&mut resp, &mut |item| {
                if item.get("id").and_then(Value::as_str).map(|s| s.starts_with("tm-bridge-")).unwrap_or(false) {
                    return;
                }
                self.emit(item);
            });
            drop(resp);
            if self.closed() {
                continue;
            }
            match result {
                Ok(()) => {
                    self.log.note("upstream stream ended; reconnecting");
                    self.reconnect(epoch, None);
                }
                Err(_) => {
                    failures += 1;
                    self.reconnect(epoch, None);
                    std::thread::sleep(self.backoff(failures));
                }
            }
        }
    }

    // -- end of session
    /// stdin closed: end our upstream session (best effort) and stop; the server is never killed.
    pub fn close(&self) {
        let (fallback, sid, has_endpoint) = {
            let mut st = lk(&self.st);
            st.closed = true;
            (st.fallback.clone(), st.sid.clone(), st.endpoint.is_some())
        };
        self.cv.notify_all();
        if let Some(fb) = fallback {
            fb.shutdown();
            return;
        }
        if let (Some(sid), true) = (sid, has_endpoint) {
            if let Ok(mut resp) = self.open("DELETE", None, Some(secs(DELETE_TIMEOUT)), Some(&sid), ACCEPT_BOTH) {
                let _ = resp.drain();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Sink(Arc<Mutex<Vec<u8>>>);
    impl Write for Sink {
        fn write(&mut self, b: &[u8]) -> io::Result<usize> {
            lk(&self.0).extend_from_slice(b);
            Ok(b.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    /// A relay with a ready upstream at 127.0.0.1:port, a helper that fails at once (so a reconnect cannot succeed) and output captured.
    fn relay_at(port: u16) -> (Arc<Shared>, Arc<Mutex<Vec<u8>>>) {
        std::env::set_var("TASKMASTER_BRIDGE_TESTING", "1");
        let helper = if cfg!(windows) { r#"["cmd","/c","exit 1"]"# } else { r#"["false"]"# };
        std::env::set_var("TASKMASTER_RELAY_HELPER", helper);
        let out = Arc::new(Mutex::new(Vec::new()));
        let b = Bounds { reconnect_budget: 0.2, short_timeout: 1.0, ..Bounds::default() };
        let shared = Shared::with_out("sess".into(), "nonce".into(), "C:\\x y".into(), PathBuf::new(), Log::new(None), b, Box::new(Sink(out.clone())));
        {
            let mut st = lk(&shared.st);
            st.upstream_ready = true;
            st.sid = Some("sid-1".into());
            st.endpoint = Some(Arc::new(Endpoint { host: "127.0.0.1".into(), port, path: "/mcp".into(), token: "tok".into(), build: None, url: format!("http://127.0.0.1:{port}/mcp") }));
        }
        (shared, out)
    }

    /// Serves each connection with the next canned reply (None = close without answering); counts requests and keeps their text.
    fn server(replies: Vec<Option<&'static str>>) -> (u16, Arc<AtomicUsize>, Arc<Mutex<Vec<String>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let count = Arc::new(AtomicUsize::new(0));
        let seen = Arc::new(Mutex::new(Vec::new()));
        let (c, sn) = (count.clone(), seen.clone());
        std::thread::spawn(move || {
            for reply in replies {
                let Ok((mut s, _)) = listener.accept() else { return };
                let mut buf = vec![0u8; 8192];
                let n = s.read(&mut buf).unwrap_or(0);
                lk(&sn).push(String::from_utf8_lossy(&buf[..n]).to_string());
                c.fetch_add(1, Ordering::SeqCst);
                if let Some(r) = reply {
                    let _ = s.write_all(r.as_bytes());
                }
            }
        });
        (port, count, seen)
    }

    fn call(id: u64) -> Value {
        json!({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": "backlog_x", "arguments": {}}})
    }

    fn output(out: &Arc<Mutex<Vec<u8>>>) -> String {
        String::from_utf8_lossy(&lk(out)).to_string()
    }

    #[test]
    fn a_tool_call_cut_after_send_is_not_retried_and_may_have_committed() {
        let (port, count, _) = server(vec![None, None, None]);
        let (shared, out) = relay_at(port);
        shared.handle(&call(1), None);
        assert_eq!(count.load(Ordering::SeqCst), 1, "a call cut after send must never be sent twice");
        let text = output(&out);
        assert!(text.contains("the call may have committed"), "{text}");
        assert!(text.contains(r#""id":1"#));
    }

    #[test]
    fn an_unreachable_server_fails_the_call_as_not_sent() {
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port(); // closed again at once
        let (shared, out) = relay_at(port);
        shared.handle(&call(2), None);
        assert!(output(&out).contains("could not be reached; this call was not sent"), "{}", output(&out));
    }

    #[test]
    fn an_http_error_is_returned_not_retried() {
        let (port, count, _) = server(vec![Some("HTTP/1.1 500 Internal Server Error\r\nContent-Length: 4\r\nConnection: close\r\n\r\nboom")]);
        let (shared, out) = relay_at(port);
        shared.handle(&call(3), None);
        assert_eq!(count.load(Ordering::SeqCst), 1);
        assert!(output(&out).contains("taskmaster server answered HTTP 500: boom"), "{}", output(&out));
    }

    #[test]
    fn a_proxy_502_on_a_call_may_have_committed() {
        let (port, _, _) = server(vec![Some("HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")]);
        let (shared, out) = relay_at(port);
        shared.handle(&call(4), None);
        assert!(output(&out).contains("the call may have committed"));
    }

    #[test]
    fn sse_and_json_answers_are_forwarded_and_headers_carry_the_session() {
        let sse = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n2e\r\ndata: {\"jsonrpc\":\"2.0\",\"id\":5,\"result\":{}}\r\n\r\n\r\n0\r\n\r\n";
        let (port, _, seen) = server(vec![Some(sse)]);
        let (shared, out) = relay_at(port);
        shared.handle(&call(5), None);
        assert!(output(&out).contains(r#""id":5"#) && output(&out).contains(r#""result":{}"#), "{}", output(&out));
        let req = lk(&seen)[0].to_lowercase();
        for want in ["authorization: bearer tok", "x-taskmaster-session: sess", "x-taskmaster-session-nonce: nonce", "x-taskmaster-cwd: c:%5cx%20y", "mcp-session-id: sid-1"] {
            assert!(req.contains(want), "missing {want} in {req}");
        }
        assert!(!req.contains("mcp-protocol-version"), "no protocol is known yet");
    }

    #[test]
    fn a_202_for_a_notification_answers_nothing() {
        let (port, _, _) = server(vec![Some("HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n")]);
        let (shared, out) = relay_at(port);
        shared.handle(&json!({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}), None);
        assert_eq!(output(&out), "");
    }

    #[test]
    fn urls_parse_like_urlsplit() {
        assert_eq!(parse_url("http://127.0.0.1:8123/mcp").unwrap(), ("127.0.0.1".into(), 8123, "/mcp".into()));
        assert_eq!(parse_url("http://[::1]:9/x?y=1").unwrap(), ("::1".into(), 9, "/x".into()));
        assert_eq!(parse_url("http://LOCALHOST").unwrap(), ("localhost".into(), 80, "/mcp".into()));
        assert!(parse_url("https://127.0.0.1/mcp").is_err());
    }

    #[test]
    fn a_non_loopback_endpoint_is_refused() {
        let line = json!({"url": "http://10.0.0.5:80/mcp", "token": "t"});
        assert!(Endpoint::from_line(&line).is_err());
        let ok = json!({"url": "http://localhost:80/mcp", "token": "t"});
        assert!(Endpoint::from_line(&ok).is_ok());
    }

    #[test]
    fn wording_matches_the_mods_fixture_shape() {
        let t = may_have_committed_text("backlog_x", false, "local");
        assert!(t.starts_with("no connected MCP tool \"backlog_x\" on a server named \"tm\": the connection to the local taskmaster server was lost while this call ran; the call may have committed"));
        assert!(may_have_committed_text("t", true, "local").ends_with("could not be reached; this call was not sent"));
    }
}
