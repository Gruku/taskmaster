// User intent: the relay's own tiny HTTP/1.1 client for the loopback tm server (no TLS, no async runtime): Content-Length and chunked
// bodies, every resolved address tried in order, one connection per request, so the relay stays ~1 MB resident.
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub enum OpenError {
    /// Nothing was sent: the connect failed on every address.
    Unsent(String),
    /// The request may have reached the server.
    Ambiguous(String),
}

pub struct Request<'a> {
    pub host: &'a str,
    pub port: u16,
    pub verb: &'a str,
    pub path: &'a str,
    pub headers: Vec<(String, String)>,
    pub body: Option<&'a [u8]>,
    pub read_timeout: Option<Duration>,
}

fn invalid(msg: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, msg.to_string())
}

pub fn read_line_capped<R: BufRead + ?Sized>(r: &mut R, cap: usize) -> io::Result<Option<Vec<u8>>> {
    let mut out: Vec<u8> = Vec::new();
    loop {
        let buf = r.fill_buf()?;
        if buf.is_empty() {
            return Ok(if out.is_empty() { None } else { Some(out) });
        }
        match buf.iter().position(|&b| b == b'\n') {
            Some(i) => {
                out.extend_from_slice(&buf[..=i]);
                r.consume(i + 1);
                if out.len() > cap {
                    return Err(invalid("a line from the server is too long"));
                }
                return Ok(Some(out));
            }
            None => {
                let n = buf.len();
                out.extend_from_slice(buf);
                r.consume(n);
                if out.len() > cap {
                    return Err(invalid("a line from the server is too long"));
                }
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Mode {
    Length,
    Chunked,
    UntilClose,
}

/// A response body with the transfer coding removed.
pub struct BodyReader<R: Read> {
    inner: BufReader<R>,
    mode: Mode,
    left: u64,
    done: bool,
}

impl<R: Read> BodyReader<R> {
    pub fn new(inner: BufReader<R>, mode: Mode, length: u64) -> Self {
        BodyReader { inner, mode, left: length, done: false }
    }

    fn next_chunk_size(&mut self) -> io::Result<u64> {
        let line = read_line_capped(&mut self.inner, 4096)?.ok_or_else(|| invalid("IncompleteRead: chunk size missing"))?;
        let text = String::from_utf8_lossy(&line);
        let size_text = text.trim().split(';').next().unwrap_or("").trim();
        u64::from_str_radix(size_text, 16).map_err(|_| invalid("bad chunk size"))
    }

    fn eat_crlf(&mut self) -> io::Result<()> {
        let line = read_line_capped(&mut self.inner, 4096)?.ok_or_else(|| invalid("IncompleteRead: chunk terminator missing"))?;
        if line.iter().all(|&b| b == b'\r' || b == b'\n') {
            Ok(())
        } else {
            Err(invalid("bad chunk terminator"))
        }
    }
}

impl<R: Read> Read for BodyReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.done || buf.is_empty() {
            return Ok(0);
        }
        match self.mode {
            Mode::Length => {
                if self.left == 0 {
                    self.done = true;
                    return Ok(0);
                }
                let want = buf.len().min(self.left as usize);
                let n = self.inner.read(&mut buf[..want])?;
                if n == 0 {
                    return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "IncompleteRead"));
                }
                self.left -= n as u64;
                Ok(n)
            }
            Mode::Chunked => {
                if self.left == 0 {
                    let size = self.next_chunk_size()?;
                    if size == 0 {
                        loop {
                            // trailers, then the blank line
                            match read_line_capped(&mut self.inner, 65536)? {
                                None => break,
                                Some(l) if l.iter().all(|&b| b == b'\r' || b == b'\n') => break,
                                Some(_) => {}
                            }
                        }
                        self.done = true;
                        return Ok(0);
                    }
                    self.left = size;
                }
                let want = buf.len().min(self.left as usize);
                let n = self.inner.read(&mut buf[..want])?;
                if n == 0 {
                    return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "IncompleteRead"));
                }
                self.left -= n as u64;
                if self.left == 0 {
                    self.eat_crlf()?;
                }
                Ok(n)
            }
            Mode::UntilClose => {
                let n = self.inner.read(buf)?;
                if n == 0 {
                    self.done = true;
                }
                Ok(n)
            }
        }
    }
}

pub type Body = BufReader<BodyReader<TcpStream>>;

pub struct Response {
    pub status: u16,
    headers: Vec<(String, String)>,
    body: Body,
}

impl Response {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }

    pub fn body(&mut self) -> &mut Body {
        &mut self.body
    }

    /// The whole body, an error when it is larger than cap.
    pub fn read_capped(&mut self, cap: usize) -> io::Result<Vec<u8>> {
        let mut out = Vec::new();
        (&mut self.body).take(cap as u64 + 1).read_to_end(&mut out)?;
        if out.len() > cap {
            return Err(invalid("a response from the server is too large"));
        }
        Ok(out)
    }

    /// At most n bytes of the body (an error text), never more.
    pub fn read_up_to(&mut self, n: usize) -> io::Result<Vec<u8>> {
        let mut out = Vec::new();
        (&mut self.body).take(n as u64).read_to_end(&mut out)?;
        Ok(out)
    }

    /// Read and discard the rest of the body.
    pub fn drain(&mut self) -> io::Result<()> {
        io::copy(&mut (&mut self.body).take(crate::consts::MAX_BODY_BYTES as u64), &mut io::sink())?;
        Ok(())
    }
}

fn host_header(host: &str, port: u16) -> String {
    if host.contains(':') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    }
}

fn clean(v: &str) -> String {
    v.chars().filter(|c| *c != '\r' && *c != '\n').collect()
}

pub fn open(req: &Request, connect_timeout: Duration) -> Result<Response, OpenError> {
    let addrs: Vec<_> = match (req.host, req.port).to_socket_addrs() {
        Ok(a) => a.collect(),
        Err(e) => return Err(OpenError::Unsent(e.to_string())),
    };
    let mut last = String::from("no address to connect to");
    let mut stream = None;
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, connect_timeout) {
            Ok(s) => {
                stream = Some(s);
                break;
            }
            Err(e) => last = e.to_string(),
        }
    }
    let Some(stream) = stream else { return Err(OpenError::Unsent(last)) };
    let _ = stream.set_nodelay(true);
    let _ = stream.set_read_timeout(req.read_timeout);
    let _ = stream.set_write_timeout(req.read_timeout);
    let mut head = format!(
        "{} {} HTTP/1.1\r\nHost: {}\r\nAccept-Encoding: identity\r\nConnection: close\r\n",
        req.verb, req.path, host_header(req.host, req.port)
    );
    for (k, v) in &req.headers {
        head.push_str(&format!("{}: {}\r\n", clean(k), clean(v)));
    }
    if let Some(body) = req.body {
        head.push_str(&format!("Content-Length: {}\r\n", body.len()));
    }
    head.push_str("\r\n");
    let amb = |e: io::Error| OpenError::Ambiguous(e.to_string());
    let mut w = &stream;
    match req.body {
        // one segment for ordinary messages, so a server that reads the request once sees all of it
        Some(body) if body.len() <= 65536 => {
            let mut all = head.into_bytes();
            all.extend_from_slice(body);
            w.write_all(&all).map_err(amb)?;
        }
        Some(body) => {
            w.write_all(head.as_bytes()).map_err(amb)?;
            w.write_all(body).map_err(amb)?;
        }
        None => w.write_all(head.as_bytes()).map_err(amb)?,
    }
    w.flush().map_err(amb)?;
    read_response(stream, req.verb).map_err(|e| OpenError::Ambiguous(e.to_string()))
}

fn read_response(stream: TcpStream, verb: &str) -> io::Result<Response> {
    let mut reader = BufReader::with_capacity(8192, stream);
    let mut status;
    let mut headers: Vec<(String, String)>;
    loop {
        let line = read_line_capped(&mut reader, 65536)?.ok_or_else(|| invalid("RemoteDisconnected"))?;
        let text = String::from_utf8_lossy(&line).trim().to_string();
        let mut parts = text.split_whitespace();
        let version = parts.next().unwrap_or("");
        if !version.starts_with("HTTP/") {
            return Err(invalid("BadStatusLine"));
        }
        status = parts.next().and_then(|s| s.parse::<u16>().ok()).ok_or_else(|| invalid("BadStatusLine"))?;
        headers = Vec::new();
        loop {
            let l = read_line_capped(&mut reader, 65536)?.ok_or_else(|| invalid("IncompleteRead: headers"))?;
            let t = String::from_utf8_lossy(&l).trim_end_matches(['\r', '\n']).to_string();
            if t.is_empty() {
                break;
            }
            if headers.len() >= 100 {
                return Err(invalid("too many headers"));
            }
            if let Some((k, v)) = t.split_once(':') {
                headers.push((k.trim().to_string(), v.trim().to_string()));
            }
        }
        if status != 100 {
            break;
        }
    }
    let find = |name: &str| headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.clone());
    let (mode, length) = if verb == "HEAD" || status == 204 || status == 304 || (100..200).contains(&status) {
        (Mode::Length, 0)
    } else if find("Transfer-Encoding").map(|v| v.to_ascii_lowercase().contains("chunked")).unwrap_or(false) {
        (Mode::Chunked, 0)
    } else if let Some(n) = find("Content-Length").and_then(|v| v.parse::<u64>().ok()) {
        (Mode::Length, n)
    } else {
        (Mode::UntilClose, 0)
    };
    let body = BufReader::with_capacity(8192, BodyReader::new(reader, mode, length));
    Ok(Response { status, headers, body })
}

/// Percent-encode like Python's urllib.parse.quote(path, safe="/:") (the X-Taskmaster-Cwd header value).
pub fn encode_cwd(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' | b'/' | b':' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Decode a complete chunked body (the vectors' raw text).
#[cfg(test)]
pub fn decode_chunked(raw: &[u8]) -> io::Result<Vec<u8>> {
    let mut reader = BodyReader::new(BufReader::new(io::Cursor::new(raw.to_vec())), Mode::Chunked, 0);
    let mut out = Vec::new();
    reader.read_to_end(&mut out)?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunked_bodies_decode() {
        assert_eq!(decode_chunked(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n").unwrap(), b"Wikipedia");
        assert_eq!(decode_chunked(b"3;ext=1\r\nabc\r\n0\r\nTrailer: x\r\n\r\n").unwrap(), b"abc");
    }

    #[test]
    fn truncated_chunked_body_is_an_error() {
        assert!(decode_chunked(b"5\r\nabc").is_err());
        assert!(decode_chunked(b"zz\r\nabc\r\n").is_err());
    }

    #[test]
    fn cwd_encoding_is_python_quote_with_slash_and_colon_safe() {
        assert_eq!(encode_cwd("C:\\Users\\a b#c"), "C:%5CUsers%5Ca%20b%23c");
        assert_eq!(encode_cwd("/home/x y/é"), "/home/x%20y/%C3%A9");
    }

    #[test]
    fn long_lines_are_refused() {
        let data = vec![b'a'; 100];
        let mut r = BufReader::new(io::Cursor::new(data));
        assert!(read_line_capped(&mut r, 50).is_err());
    }
}
