// User intent: a std-only stand-in for tm-relay.exe so the entry and self-heal tests can force every --version outcome (runs, slow, odd output, exit code) without the real relay.
// Mode comes from env TM_STANDIN: unset | sleep:N | odd | exit:N | echo. TM_STANDIN_MARK=<file> appends a line when the stand-in starts (a completion signal for "left alone" tests).
use std::io::{Read, Write};
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mode = std::env::var("TM_STANDIN").unwrap_or_default();
    if let Ok(p) = std::env::var("TM_STANDIN_MARK") {
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(p) {
            let _ = writeln!(f, "ran");
        }
    }
    let version = args.first().map(|a| a == "--version").unwrap_or(false);
    if let Some(n) = mode.strip_prefix("sleep:") {
        std::thread::sleep(std::time::Duration::from_secs(n.parse().unwrap_or(0)));
    }
    if let Some(n) = mode.strip_prefix("exit:") {
        std::process::exit(n.parse().unwrap_or(1));
    }
    if mode == "odd" {
        println!("hello world, not a version line");
        return;
    }
    if version || mode != "echo" {
        println!("tm-relay 0.0.0 standin");
        return;
    }
    let mut s = String::new();
    let _ = std::io::stdin().read_to_string(&mut s);
    let mut o = std::io::stdout();
    let _ = writeln!(o, "pid={} args={} stdin={}", std::process::id(), args.join(" "), s);
}
