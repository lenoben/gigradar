//! Runs the packaged helper program (the Python CLI) and streams its JSON Lines.
//!
//! The helper prints `{"event": ...}` progress lines and then one result line (it has an `ok` key). Stdout is
//! parsed line by line; events are handed to the caller as they arrive, the result is returned. Whatever the helper
//! prints has the secrets of this call replaced by `<redacted>` before anything is parsed or forwarded, so even a
//! helper that leaked a token could not pass it on. Nothing here writes to a log or to the console.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use serde_json::Value;

pub const REDACTED: &str = "<redacted>";
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// What to run: arguments (never secrets), text for stdin (may hold a secret), and the secrets to scrub from output.
pub struct Invocation {
    pub args: Vec<String>,
    pub stdin: Option<String>,
    pub secrets: Vec<String>,
}

/// `result` is None when the helper ended without a result line (a crash); then only the exit code is known.
#[derive(Serialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Outcome {
    pub exit_code: Option<i32>,
    pub result: Option<Value>,
}

/// The helpers that are running, so a run can be cancelled and all of them stopped when the app closes.
#[derive(Default)]
pub struct Running {
    children: Mutex<HashMap<u64, Arc<Mutex<Child>>>>,
}

impl Running {
    fn add(&self, id: u64, child: Arc<Mutex<Child>>) {
        if let Ok(mut map) = self.children.lock() {
            map.insert(id, child);
        }
    }

    fn remove(&self, id: u64) {
        if let Ok(mut map) = self.children.lock() {
            map.remove(&id);
        }
    }

    pub fn kill(&self, id: u64) {
        let child = self.children.lock().ok().and_then(|map| map.get(&id).cloned());
        if let Some(child) = child {
            if let Ok(mut child) = child.lock() {
                let _ = child.kill();
            }
        }
    }

    pub fn kill_all(&self) {
        let ids: Vec<u64> = self.children.lock().map(|map| map.keys().copied().collect()).unwrap_or_default();
        for id in ids {
            self.kill(id);
        }
    }
}

/// Replaces every secret in `text` with `<redacted>`. Empty secrets are ignored (they would match everywhere).
pub fn scrub(text: &str, secrets: &[String]) -> String {
    let mut out = text.to_string();
    for secret in secrets.iter().filter(|s| !s.is_empty()) {
        out = out.replace(secret.as_str(), REDACTED);
    }
    out
}

/// Runs `program`, calls `on_event` for every progress line, and returns the exit code and the result line.
pub fn run<F: FnMut(Value)>(
    program: &Path,
    invocation: &Invocation,
    envs: &[(String, String)],
    running: &Running,
    run_id: u64,
    mut on_event: F,
) -> Result<Outcome, String> {
    let mut command = Command::new(program);
    command
        .args(&invocation.args)
        .envs(envs.iter().map(|(k, v)| (k, v)))
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(if invocation.stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    // The message names only the program path: never the arguments or the input.
    let mut child = command.spawn().map_err(|e| format!("could not start {}: {}", program.display(), e.kind()))?;
    let stdout = child.stdout.take().ok_or("no output pipe")?;
    if let (Some(text), Some(mut stdin)) = (invocation.stdin.as_ref(), child.stdin.take()) {
        let _ = stdin.write_all(text.as_bytes());
        // dropped here: the helper sees end-of-input
    }
    let child = Arc::new(Mutex::new(child));
    running.add(run_id, child.clone());

    let mut result: Option<Value> = None;
    for line in BufReader::new(stdout).lines() {
        let Ok(line) = line else { break };
        let clean = scrub(&line, &invocation.secrets);
        let Ok(value) = serde_json::from_str::<Value>(&clean) else { continue }; // tqdm bars, stray text
        match value {
            Value::Object(ref map) if map.contains_key("ok") => result = Some(value),
            Value::Object(_) => on_event(value),
            _ => {}
        }
    }
    let status = child.lock().map_err(|_| "helper lock poisoned".to_string())?.wait();
    running.remove(run_id);
    let exit_code = status.ok().and_then(|s| s.code());
    Ok(Outcome { exit_code, result })
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "987654321:AAcanaryCANARYcanaryCANARYcanary_-xy";

    #[test]
    fn scrub_replaces_every_occurrence_and_ignores_empty_secrets() {
        let secrets = vec![TOKEN.to_string(), String::new()];
        let text = format!("a {TOKEN} b {TOKEN}");
        assert_eq!(scrub(&text, &secrets), "a <redacted> b <redacted>");
        assert_eq!(scrub("nothing here", &secrets), "nothing here");
    }

    #[test]
    fn the_rust_sources_never_print_or_log() {
        // built at run time so this test does not match itself
        let needles = [["print", "ln!("].concat(), ["eprint", "ln!("].concat(), ["dbg", "!("].concat(), ["log", "::"].concat(), ["trac", "ing::"].concat()];
        for (name, source) in [("sidecar.rs", include_str!("sidecar.rs")), ("lib.rs", include_str!("lib.rs")), ("main.rs", include_str!("main.rs"))] {
            for needle in &needles {
                // this file's own test code mentions the needles only through the array above
                let count = source.matches(needle.as_str()).count();
                assert_eq!(count, 0, "{name} contains {needle}");
            }
        }
    }

    #[cfg(windows)]
    mod windows_helpers {
        use super::*;
        use std::fs;
        use std::time::{Duration, Instant};

        fn script(name: &str, body: &str) -> std::path::PathBuf {
            let dir = std::env::temp_dir().join(format!("gigradar-sidecar-test-{}", std::process::id()));
            fs::create_dir_all(&dir).unwrap();
            let path = dir.join(name);
            fs::write(&path, body).unwrap();
            path
        }

        fn invocation(stdin: Option<&str>, secrets: &[&str]) -> Invocation {
            Invocation { args: vec![], stdin: stdin.map(String::from), secrets: secrets.iter().map(|s| s.to_string()).collect() }
        }

        #[test]
        fn events_are_streamed_the_result_is_returned_and_a_leaked_token_is_scrubbed() {
            // a helper that misbehaves: it echoes the token it was given on stdin into its output
            let program = script(
                "leaky.cmd",
                "@echo off\r\nset /p tok=\r\necho {\"event\":\"progress\",\"note\":\"%tok%\"}\r\necho not json at all %tok%\r\necho {\"ok\":true,\"command\":\"x\",\"leak\":\"%tok%\"}\r\nexit /b 0\r\n",
            );
            let running = Running::default();
            let mut events = Vec::new();
            let outcome = run(&program, &invocation(Some(&format!("{TOKEN}\n")), &[TOKEN]), &[], &running, 1, |e| events.push(e)).unwrap();
            assert_eq!(outcome.exit_code, Some(0));
            assert_eq!(events.len(), 1);
            let everything = format!("{:?} {:?}", events, outcome);
            assert!(!everything.contains(TOKEN), "the token reached the caller");
            assert!(everything.contains(REDACTED));
            assert_eq!(outcome.result.unwrap()["ok"], true);
        }

        #[test]
        fn a_helper_that_dies_without_a_result_reports_only_its_exit_code() {
            let program = script("crash.cmd", "@echo off\r\nexit /b 3\r\n");
            let outcome = run(&program, &invocation(None, &[]), &[], &Running::default(), 2, |_| {}).unwrap();
            assert_eq!((outcome.exit_code, outcome.result.is_none()), (Some(3), true));
        }

        #[test]
        fn a_missing_program_is_an_error_that_names_no_arguments() {
            let secret_arg = Invocation { args: vec![TOKEN.to_string()], stdin: None, secrets: vec![] };
            let error = run(Path::new("C:/definitely/not/here.exe"), &secret_arg, &[], &Running::default(), 3, |_| {}).unwrap_err();
            assert!(!error.contains(TOKEN));
        }

        #[test]
        fn a_run_can_be_cancelled() {
            // powershell itself sleeps (no grandchild that would keep the output pipe open)
            let sleeper = Invocation { args: vec!["-NoProfile".into(), "-Command".into(), "Start-Sleep -Seconds 40".into()], stdin: None, secrets: vec![] };
            let running = Arc::new(Running::default());
            let killer = running.clone();
            let started = Instant::now();
            let handle = std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(1500));
                killer.kill(4);
            });
            let outcome = run(Path::new("powershell.exe"), &sleeper, &[], &running, 4, |_| {}).unwrap();
            handle.join().unwrap();
            assert!(started.elapsed() < Duration::from_secs(20), "kill did not stop the helper");
            assert!(outcome.result.is_none());
        }
    }
}
