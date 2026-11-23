// User intent: pin every constant, encoding and parser the relay shares with tm_bridge.py to tests/fixtures/relay_vectors.json,
// which the Python side asserts too, so the two relays cannot drift apart without a test failing.
use crate::consts::*;
use crate::http;
use crate::sse;
use serde_json::Value;
use std::io::{Cursor, Read, Write};
use std::net::TcpListener;

fn vectors() -> Value {
    serde_json::from_str(include_str!("../../tests/fixtures/relay_vectors.json")).expect("relay_vectors.json parses")
}

#[test]
fn constants_equal_the_shared_vectors() {
    let v = vectors();
    assert_eq!(v["format"], 1);
    let c = &v["constants"];
    let num = |name: &str| c[name].as_f64().unwrap_or_else(|| panic!("constant {name} missing"));
    assert_eq!(num("CONNECT_TIMEOUT"), CONNECT_TIMEOUT);
    assert_eq!(num("SHORT_TIMEOUT"), SHORT_TIMEOUT);
    assert_eq!(num("MAX_BODY_BYTES"), MAX_BODY_BYTES as f64);
    assert_eq!(num("MAX_LINE_BYTES"), MAX_LINE_BYTES as f64);
    assert_eq!(num("RECONNECT_BUDGET"), RECONNECT_BUDGET);
    assert_eq!(num("RECONNECT_BACKOFF_START"), RECONNECT_BACKOFF_START);
    assert_eq!(num("RECONNECT_BACKOFF_CAP"), RECONNECT_BACKOFF_CAP);
    assert_eq!(num("RETRY_ATTEMPTS"), RETRY_ATTEMPTS as f64);
    assert_eq!(num("LIST_HOLD"), LIST_HOLD);
    assert_eq!(num("HOLD_BOUND"), HOLD_BOUND);
    assert_eq!(num("FALLBACK_FAILURES"), FALLBACK_FAILURES as f64);
    assert_eq!(num("FALLBACK_WINDOW"), FALLBACK_WINDOW);
    assert_eq!(num("GET_NON200_STEP"), GET_NON200_STEP);
    assert_eq!(num("GET_NON200_CAP"), GET_NON200_CAP);
    let protocols: Vec<&str> = c["KNOWN_PROTOCOLS"].as_array().unwrap().iter().map(|p| p.as_str().unwrap()).collect();
    assert_eq!(protocols, KNOWN_PROTOCOLS.to_vec());
    let backoff: Vec<f64> = c["GET_BACKOFF"].as_array().unwrap().iter().map(|p| p.as_f64().unwrap()).collect();
    assert_eq!(backoff, GET_BACKOFF.to_vec());
    assert_eq!(v["init_wait"].as_f64().unwrap(), INIT_WAIT);
}

#[test]
fn cwd_encodings_equal_the_vectors() {
    for pair in vectors()["cwd"].as_array().unwrap() {
        let (path, encoded) = (pair[0].as_str().unwrap(), pair[1].as_str().unwrap());
        assert_eq!(http::encode_cwd(path), encoded, "cwd {path:?}");
    }
}

#[test]
fn sse_cases_decode_to_the_vector_events() {
    for (i, case) in vectors()["sse"].as_array().unwrap().iter().enumerate() {
        let raw: String = case["chunks"].as_array().unwrap().iter().map(|c| c.as_str().unwrap()).collect();
        let mut got: Vec<Value> = Vec::new();
        sse::parse_stream(&mut Cursor::new(raw.into_bytes()), MAX_LINE_BYTES, &mut |v| got.push(v)).unwrap();
        let want: Vec<Value> = case["events"].as_array().unwrap().clone();
        assert_eq!(got, want, "sse case {i}");
    }
}

#[test]
fn chunked_cases_read_back_the_vector_body_or_fail() {
    for (i, case) in vectors()["chunked"].as_array().unwrap().iter().enumerate() {
        let raw = case["raw"].as_str().unwrap().as_bytes().to_vec();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut s, _)) = listener.accept() {
                let mut buf = [0u8; 4096];
                let _ = s.read(&mut buf);
                let _ = s.write_all(&raw);
            }
        });
        let req = http::Request { host: "127.0.0.1", port, verb: "GET", path: "/", headers: vec![], body: None, read_timeout: Some(std::time::Duration::from_secs(5)) };
        let mut resp = http::open(&req, std::time::Duration::from_secs(5)).ok().expect("connect");
        let body = resp.read_capped(MAX_BODY_BYTES);
        match (case.get("body"), case.get("error")) {
            (Some(want), _) => assert_eq!(String::from_utf8(body.expect("body reads")).unwrap(), want.as_str().unwrap(), "chunked case {i}"),
            (_, Some(_)) => assert!(body.is_err(), "chunked case {i} must fail"),
            _ => panic!("chunked case {i} has neither body nor error"),
        }
    }
}
