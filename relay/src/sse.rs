// User intent: turn a server response body into MCP messages exactly as tm_bridge.py's Bridge.events does (JSON document, or SSE
// parsed as it arrives), so a message reaches Claude Code the moment its event ends.
use crate::http::read_line_capped;
use serde_json::Value;
use std::io::{self, BufRead};

/// A JSON document (or batch array) as messages; undecodable text yields nothing.
pub fn decode(text: &str) -> Vec<Value> {
    match serde_json::from_str::<Value>(text) {
        Ok(Value::Array(items)) => items,
        Ok(other) => vec![other],
        Err(_) => Vec::new(),
    }
}

#[derive(Default)]
pub struct Decoder {
    data: Vec<String>,
}

impl Decoder {
    pub fn feed_line(&mut self, line: &str) -> Vec<Value> {
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            if self.data.is_empty() {
                return Vec::new();
            }
            let text = self.data.join("\n");
            self.data.clear();
            return decode(&text);
        }
        if let Some(rest) = line.strip_prefix("data:") {
            self.data.push(rest.trim_start_matches(' ').to_string());
        }
        Vec::new()
    }

    pub fn finish(&mut self) -> Vec<Value> {
        if self.data.is_empty() {
            return Vec::new();
        }
        let text = self.data.join("\n");
        self.data.clear();
        decode(&text)
    }
}

/// Parse an SSE stream line by line, calling f for each message as soon as its event ends.
pub fn parse_stream<R: BufRead + ?Sized>(r: &mut R, line_cap: usize, f: &mut dyn FnMut(Value)) -> io::Result<()> {
    let mut dec = Decoder::default();
    loop {
        match read_line_capped(r, line_cap)? {
            None => {
                for v in dec.finish() {
                    f(v);
                }
                return Ok(());
            }
            Some(raw) => {
                let text = String::from_utf8(raw).map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid utf-8 in an event"))?;
                for v in dec.feed_line(&text) {
                    f(v);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn events(raw: &str) -> Vec<Value> {
        let mut out = Vec::new();
        parse_stream(&mut io::Cursor::new(raw.as_bytes().to_vec()), 1 << 20, &mut |v| out.push(v)).unwrap();
        out
    }

    #[test]
    fn crlf_multiline_data_and_event_lines() {
        let got = events("event: message\r\ndata: {\"a\":\r\ndata: 1}\r\n\r\ndata: [{\"b\":2},{\"c\":3}]\r\n\r\n");
        assert_eq!(got.len(), 3);
        assert_eq!(got[0]["a"], 1);
    }

    #[test]
    fn a_final_event_without_a_blank_line_is_still_delivered() {
        assert_eq!(events("data: {\"x\":1}").len(), 1);
    }
}
