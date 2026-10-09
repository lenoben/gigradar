//! gigradar desktop shell. The UI never touches files or the network itself: every action is one of the commands
//! below, each of which runs the bundled helper program (the Python CLI) and streams its JSON Lines to the window.

mod sidecar;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager, RunEvent, State};

use sidecar::{Invocation, Outcome, Running};

const DEFAULT_TASK: &str = "gigradar-watch";

/// The Telegram bot token lives here (in memory, this process only) from the moment the user submits it until the
/// app closes. The window never gets it back.
#[derive(Default)]
struct TokenStore(Mutex<Option<String>>);

impl TokenStore {
    fn set(&self, token: String) {
        if let Ok(mut slot) = self.0.lock() {
            *slot = Some(token);
        }
    }

    fn get(&self) -> Option<String> {
        self.0.lock().ok().and_then(|slot| slot.clone())
    }

    fn clear(&self) {
        if let Ok(mut slot) = self.0.lock() {
            *slot = None;
        }
    }
}

struct AppState {
    running: Arc<Running>,
    token: Arc<TokenStore>,
}

fn task_name() -> String {
    let name = std::env::var("GIGRADAR_TASK_NAME").unwrap_or_else(|_| DEFAULT_TASK.to_string());
    if valid_task_name(&name) { name } else { DEFAULT_TASK.to_string() }
}

fn valid_task_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// Where the helper keeps its data (GIGRADAR_HOME, else %LOCALAPPDATA%\gigradar): shown to the user only.
fn app_home() -> String {
    if let Ok(home) = std::env::var("GIGRADAR_HOME") {
        return home;
    }
    let base = std::env::var("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(base).join("gigradar").display().to_string()
}

fn sidecar_exe(app: &AppHandle) -> Result<PathBuf, String> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(dir) = std::env::var("GIGRADAR_SIDECAR_DIR") {
        candidates.push(PathBuf::from(dir).join("gigradar.exe"));
    }
    if let Ok(dir) = app.path().resource_dir() {
        candidates.push(dir.join("sidecar").join("gigradar.exe"));
    }
    candidates.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("dist").join("gigradar").join("gigradar.exe"));
    candidates.into_iter().find(|p| p.is_file()).ok_or_else(|| "no_helper".to_string())
}

fn number(payload: &Value, key: &str, min: i64, max: i64, default: i64) -> Result<i64, String> {
    match payload.get(key) {
        None | Some(Value::Null) => Ok(default),
        Some(v) => v.as_i64().filter(|n| (min..=max).contains(n)).ok_or_else(|| format!("{key} must be {min} to {max}")),
    }
}

fn flag(payload: &Value, key: &str) -> bool {
    payload.get(key).and_then(Value::as_bool).unwrap_or(false)
}

/// The helper's command line for a UI request. Only these commands exist; arguments are built here, never taken
/// from the window as free text, and a secret only ever goes to stdin.
fn invocation_for(name: &str, payload: &Value, task: &str, token: Option<&str>) -> Result<Invocation, String> {
    let plain = |args: Vec<&str>| Invocation { args: args.into_iter().map(String::from).collect(), stdin: None, secrets: vec![] };
    match name {
        "doctor" => {
            let mut args = vec!["doctor", "--task-name", task];
            if flag(payload, "offline") {
                args.push("--offline");
            }
            Ok(plain(args))
        }
        "upwork-check" | "model-download" | "run-once" => Ok(plain(vec![name])),
        "notify-test" => {
            let mut args = vec!["notify-test"];
            if payload.get("channel").and_then(Value::as_str) == Some("toast") {
                args.extend(["--channel", "toast"]);
            }
            Ok(plain(args))
        }
        "jobs" => {
            let limit = number(payload, "limit", 1, 100, 20)?.to_string();
            Ok(plain(vec!["jobs", "--limit", limit.as_str()]))
        }
        "task-register" => {
            let interval = number(payload, "interval", 15, 1440, 30)?.to_string();
            Ok(plain(vec!["task-register", "--task-name", task, "--interval", interval.as_str()]))
        }
        "task-unregister" => Ok(plain(vec!["task-unregister", "--task-name", task])),
        "setup-apply" => {
            let mut answers = payload.get("answers").cloned().ok_or("setup-apply needs answers")?;
            let mut secrets = vec![];
            if answers.get("telegram").is_some() {
                let token = token.ok_or("telegram_token_missing")?;
                answers["telegram"]["bot_token"] = json!(token);
                secrets.push(token.to_string());
            }
            let mut args = vec!["setup-apply".to_string(), "--answers".to_string(), "-".to_string()];
            if flag(payload, "force") {
                args.push("--force".into());
                if flag(payload, "noBackup") {
                    args.push("--no-backup".into());
                }
            }
            Ok(Invocation { args, stdin: Some(answers.to_string()), secrets })
        }
        _ => Err(format!("unknown command {name}")),
    }
}

async fn run_blocking(
    app: &AppHandle,
    state: &AppState,
    invocation: Invocation,
    run_id: u64,
    on_event: Channel<Value>,
) -> Result<Outcome, String> {
    let program = sidecar_exe(app)?;
    let running = state.running.clone();
    tauri::async_runtime::spawn_blocking(move || {
        sidecar::run(&program, &invocation, &[], &running, run_id, |event| {
            let _ = on_event.send(event);
        })
    })
    .await
    .map_err(|_| "the helper task failed".to_string())?
}

#[tauri::command]
fn app_info() -> Value {
    json!({ "home": app_home(), "taskName": task_name() })
}

#[tauri::command]
async fn run_command(
    app: AppHandle,
    state: State<'_, AppState>,
    name: String,
    payload: Option<Value>,
    run_id: u64,
    on_event: Channel<Value>,
) -> Result<Outcome, String> {
    let payload = payload.unwrap_or(Value::Null);
    let token = state.token.get();
    let invocation = invocation_for(&name, &payload, &task_name(), token.as_deref())?;
    run_blocking(&app, &state, invocation, run_id, on_event).await
}

/// `token` is Some on the first try; None reuses the token already given (the second try, after /start).
#[tauri::command]
async fn telegram_connect(
    app: AppHandle,
    state: State<'_, AppState>,
    token: Option<String>,
    run_id: u64,
    on_event: Channel<Value>,
) -> Result<Outcome, String> {
    if let Some(token) = token {
        state.token.set(token);
    }
    let token = state.token.get().ok_or("telegram_token_missing")?;
    let invocation = Invocation {
        args: vec!["telegram-connect".into()],
        stdin: Some(format!("{token}\n")),
        secrets: vec![token],
    };
    let outcome = run_blocking(&app, &state, invocation, run_id, on_event).await?;
    let bad = outcome
        .result
        .as_ref()
        .and_then(|r| r.pointer("/error/code"))
        .and_then(Value::as_str)
        .is_some_and(|code| code == "bad_token" || code == "no_token");
    if bad {
        state.token.clear();
    }
    Ok(outcome)
}

#[tauri::command]
fn close_app(app: AppHandle) {
    // the scheduled task keeps running without the window
    app.exit(0);
}

#[tauri::command]
fn cancel_command(state: State<'_, AppState>, run_id: u64) {
    state.running.kill(run_id);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = AppState { running: Arc::new(Running::default()), token: Arc::new(TokenStore::default()) };
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![app_info, run_command, telegram_connect, cancel_command, close_app])
        .build(tauri::generate_context!())
        .expect("failed to build the app");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<AppState>().running.kill_all();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "987654321:AAcanaryCANARYcanaryCANARYcanary_-xy";

    fn args(name: &str, payload: Value) -> Vec<String> {
        invocation_for(name, &payload, "gigradar-app-test", Some(TOKEN)).unwrap().args
    }

    #[test]
    fn simple_commands_map_to_the_helpers_commands() {
        assert_eq!(args("doctor", json!({"offline": true})), ["doctor", "--task-name", "gigradar-app-test", "--offline"]);
        assert_eq!(args("doctor", Value::Null), ["doctor", "--task-name", "gigradar-app-test"]);
        assert_eq!(args("upwork-check", Value::Null), ["upwork-check"]);
        assert_eq!(args("model-download", json!({})), ["model-download"]);
        assert_eq!(args("run-once", json!({})), ["run-once"]);
        assert_eq!(args("notify-test", json!({"channel": "toast"})), ["notify-test", "--channel", "toast"]);
        assert_eq!(args("jobs", json!({"limit": 5})), ["jobs", "--limit", "5"]);
        assert_eq!(args("task-register", json!({"interval": 30})), ["task-register", "--task-name", "gigradar-app-test", "--interval", "30"]);
        assert_eq!(args("task-unregister", Value::Null), ["task-unregister", "--task-name", "gigradar-app-test"]);
    }

    #[test]
    fn unknown_commands_and_out_of_range_numbers_are_refused() {
        assert!(invocation_for("rm -rf", &Value::Null, "t", None).is_err());
        assert!(invocation_for("jobs", &json!({"limit": 0}), "t", None).is_err());
        assert!(invocation_for("jobs", &json!({"limit": "5; calc"}), "t", None).is_err());
        assert!(invocation_for("task-register", &json!({"interval": 5}), "t", None).is_err());
    }

    #[test]
    fn task_names_are_checked() {
        assert!(valid_task_name("gigradar-app-test"));
        assert!(!valid_task_name("bad name"));
        assert!(!valid_task_name("a;b"));
        assert!(!valid_task_name(""));
    }

    #[test]
    fn setup_apply_sends_the_token_on_stdin_only() {
        let payload = json!({"answers": {"searches": [], "telegram": {"chat_id": "42"}}, "force": true, "noBackup": true});
        let inv = invocation_for("setup-apply", &payload, "t", Some(TOKEN)).unwrap();
        assert_eq!(inv.args, ["setup-apply", "--answers", "-", "--force", "--no-backup"]);
        let stdin: Value = serde_json::from_str(inv.stdin.as_ref().unwrap()).unwrap();
        assert_eq!(stdin["telegram"]["bot_token"], TOKEN);
        assert_eq!(stdin["telegram"]["chat_id"], "42");
        assert_eq!(inv.secrets, [TOKEN]);
    }

    #[test]
    fn no_command_line_ever_holds_the_token() {
        let commands = ["doctor", "upwork-check", "model-download", "run-once", "notify-test", "jobs", "task-register", "task-unregister"];
        for name in commands {
            let inv = invocation_for(name, &json!({}), "t", Some(TOKEN)).unwrap();
            assert!(inv.args.iter().all(|a| !a.contains(TOKEN)), "{name}");
            assert!(inv.stdin.is_none() && inv.secrets.is_empty(), "{name}");
        }
        let payload = json!({"answers": {"searches": [], "telegram": {"chat_id": "42"}}});
        let inv = invocation_for("setup-apply", &payload, "t", Some(TOKEN)).unwrap();
        assert!(inv.args.iter().all(|a| !a.contains(TOKEN)));
    }

    #[test]
    fn telegram_answers_without_a_known_token_are_refused() {
        let payload = json!({"answers": {"searches": [], "telegram": {"chat_id": "42"}}});
        assert_eq!(invocation_for("setup-apply", &payload, "t", None).err().as_deref(), Some("telegram_token_missing"));
        let payload = json!({"answers": {"searches": []}});
        assert!(invocation_for("setup-apply", &payload, "t", None).is_ok());
    }

    #[test]
    fn the_token_store_holds_and_forgets() {
        let store = TokenStore::default();
        assert!(store.get().is_none());
        store.set(TOKEN.to_string());
        assert_eq!(store.get().as_deref(), Some(TOKEN));
        store.clear();
        assert!(store.get().is_none());
    }
}
