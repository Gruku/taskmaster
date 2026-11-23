// User intent: the ONE place the relay starts a child process, so every child is hidden (no console window, ever: R4/R9),
// and the relay's own stdio pipes never leak into helpers (a helper mid-launch must not hold Claude Code's pipes open).
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

#[cfg(windows)]
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Start argv with no window; stdio as given; extra environment on top of ours.
pub fn spawn_hidden(argv: &[String], stdin: Stdio, stdout: Stdio, stderr: Stdio, envs: &[(&str, &str)]) -> io::Result<Child> {
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..]).stdin(stdin).stdout(stdout).stderr(stderr);
    for (k, v) in envs {
        cmd.env(k, v);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn()
}

/// uv on PATH, in PATH order: uv.exe only on Windows (no PATHEXT, never the relay's own directory or the cwd first); execvp rules on POSIX.
pub fn find_uv() -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let name = if cfg!(windows) { "uv.exe" } else { "uv" };
    for dir in std::env::split_paths(&path) {
        if dir.as_os_str().is_empty() {
            continue;
        }
        let candidate = dir.join(name);
        if is_runnable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

#[cfg(windows)]
fn is_runnable(p: &Path) -> bool {
    p.is_file()
}

#[cfg(not(windows))]
fn is_runnable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    p.metadata().map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

/// The extended-length prefix canonicalize adds on Windows, removed so child argv and paths look like the ones Python builds.
pub fn strip_verbatim(p: &Path) -> PathBuf {
    let s = p.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        return PathBuf::from(rest);
    }
    p.to_path_buf()
}

#[cfg(windows)]
mod win {
    extern "system" {
        pub fn GetStdHandle(n: u32) -> isize;
        pub fn SetHandleInformation(h: isize, mask: u32, flags: u32) -> i32;
        pub fn SetErrorMode(mode: u32) -> u32;
    }
}

/// Windows: a crash (panic = abort) must end quietly, never in a "stopped working" / Windows Error Reporting window (rulings R4, R9).
pub fn suppress_crash_dialog() {
    #[cfg(windows)]
    unsafe {
        // SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
        win::SetErrorMode(0x0001 | 0x0002);
    }
}

/// Windows: clear HANDLE_FLAG_INHERIT on one handle (so a child created with bInheritHandles never gets it).
#[cfg(windows)]
pub fn clear_inherit(handle: isize) {
    if handle != 0 && handle != -1 {
        unsafe {
            win::SetHandleInformation(handle, 1, 0);
        }
    }
}

/// Windows: the relay's stdin, stdout and stderr are Claude Code's pipes; none may be inherited by a helper chain.
/// (Stdio::inherit duplicates an inheritable copy for the one child that is meant to share stderr.)
pub fn protect_std_handles() {
    #[cfg(windows)]
    unsafe {
        for id in [0xFFFF_FFF6u32, 0xFFFF_FFF5, 0xFFFF_FFF4] {
            clear_inherit(win::GetStdHandle(id));
        }
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::io::Read;

    extern "system" {
        fn CreatePipe(r: *mut isize, w: *mut isize, attrs: *const u8, size: u32) -> i32;
        fn GetHandleInformation(h: isize, flags: *mut u32) -> i32;
        fn GetConsoleWindow() -> isize;
    }

    #[test]
    fn child_probe() {
        // Runs only as the child of the tests below.
        if let Ok(h) = std::env::var("TM_RELAY_PROBE_HANDLE") {
            let h: isize = h.parse().unwrap();
            let mut flags = 0u32;
            let held = unsafe { GetHandleInformation(h, &mut flags) } != 0;
            println!("PROBE held={} console_window={}", held, unsafe { GetConsoleWindow() });
        }
    }

    fn run_child(handle: isize) -> String {
        let exe = std::env::current_exe().unwrap().to_string_lossy().to_string();
        let argv = vec![exe, "--exact".to_string(), "spawn::tests::child_probe".to_string(), "--nocapture".to_string()];
        let mut child = spawn_hidden(&argv, Stdio::null(), Stdio::piped(), Stdio::null(), &[("TM_RELAY_PROBE_HANDLE", &handle.to_string())]).unwrap();
        let mut out = String::new();
        child.stdout.take().unwrap().read_to_string(&mut out).unwrap();
        child.wait().unwrap();
        out
    }

    #[test]
    fn hidden_children_have_no_console_window() {
        assert!(run_child(0).contains("console_window=0"), "CREATE_NO_WINDOW must leave the child without a console window");
    }

    #[test]
    fn a_child_holds_no_handle_that_had_its_inherit_flag_cleared() {
        let (mut r, mut w) = (0isize, 0isize);
        assert!(unsafe { CreatePipe(&mut r, &mut w, std::ptr::null(), 0) } != 0);
        unsafe { win::SetHandleInformation(r, 1, 1) }; // control: inheritable, so a child sees it
        assert!(run_child(r).contains("held=true"), "control: an inheritable handle must reach the child");
        clear_inherit(r);
        assert!(run_child(r).contains("held=false"), "a handle with the inherit flag cleared must not reach the child");
    }

    #[test]
    fn verbatim_prefix_is_stripped() {
        assert_eq!(strip_verbatim(Path::new(r"\\?\C:\a\b")), PathBuf::from(r"C:\a\b"));
        assert_eq!(strip_verbatim(Path::new(r"\\?\UNC\srv\share\x")), PathBuf::from(r"\\srv\share\x"));
        assert_eq!(strip_verbatim(Path::new(r"C:\plain")), PathBuf::from(r"C:\plain"));
    }
}
