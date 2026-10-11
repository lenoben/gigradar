//! Gigradar desktop shell. The UI never touches files or the network itself: every action is one of the commands
//! below, each of which runs the bundled helper program (the Python CLI) and streams its JSON Lines to the window.
//!
//! The app lives in the tray: closing the window hides it, "Quit" in the tray menu ends the app. A click on a
//! notification starts the app (or reaches the running one) with a gigradar:// link; see links.rs.

mod links;
mod sidecar;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::ipc::Channel;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};

use sidecar::{Invocation, Outcome, Running};

/// What a person reads. The same text is in tauri.conf.json (productName, window title), src/lib/strings.ts and
/// gigradar/branding.py; tests/test_branding.py fails when they differ.
const APP_DISPLAY_NAME: &str = "Gigradar";
/// The app's own scheduled task (folder and name are the helper's: gigradar/branding.py).
const DEFAULT_TASK: &str = "checks";
const DATA_DIR_NAME: &str = "Gigradar Data";
/// Tray-started checks use run ids far above the window's counter, so they never collide.
const TRAY_RUN_ID_START: u64 = 1 << 40;
const SECTIONS: [&str; 6] = ["profile", "preferences", "searches", "notifications", "schedule", "storage"];

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
    /// A link that arrived (launch argument, or a second start) and that the window has not taken yet.
    pending_link: Mutex<Option<Value>>,
    /// A tray-started check is running.
    checking: AtomicBool,
    next_tray_run: AtomicU64,
}

fn task_name() -> String {
    let name = std::env::var("GIGRADAR_TASK_NAME").unwrap_or_else(|_| DEFAULT_TASK.to_string());
    if valid_task_name(&name) { name } else { DEFAULT_TASK.to_string() }
}

fn valid_task_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// Where the helper keeps its data (GIGRADAR_HOME, else %LOCALAPPDATA%\Gigradar Data): shown to the user only.
fn app_home() -> String {
    if let Ok(home) = std::env::var("GIGRADAR_HOME") {
        return home;
    }
    let base = std::env::var("LOCALAPPDATA").unwrap_or_default();
    PathBuf::from(base).join(DATA_DIR_NAME).display().to_string()
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

fn text<'a>(payload: &'a Value, key: &str) -> Option<&'a str> {
    payload.get(key).and_then(Value::as_str)
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
        "upwork-check" | "model-download" | "settings-get" => Ok(plain(vec![name])),
        // started by a person (the first check, the tray): quiet hours only pause the scheduled ones
        "run-once" => Ok(plain(vec!["run-once", "--ignore-quiet-hours"])),
        "notify-test" => {
            let mut args = vec!["notify-test"];
            if text(payload, "channel") == Some("toast") {
                args.extend(["--channel", "toast"]);
            }
            Ok(plain(args))
        }
        "jobs" => {
            let limit = number(payload, "limit", 1, 100, 20)?.to_string();
            let mut args = vec!["jobs", "--limit", limit.as_str()];
            if let Some(run) = text(payload, "run") {
                if !links::valid_run(run) {
                    return Err("run is not a time stamp".into());
                }
                args.extend(["--run", run]);
            }
            if payload.get("includeBelow").and_then(|v| v.as_bool()) == Some(true) {
                args.push("--include-below");
            }
            Ok(plain(args))
        }
        "job" => {
            let id = text(payload, "id").filter(|id| links::valid_id(id)).ok_or("id is not a job id")?;
            Ok(plain(vec!["job", "--job-id", id]))
        }
        "label" => {
            let id = text(payload, "id").filter(|id| links::valid_id(id)).ok_or("id is not a job id")?;
            let value = text(payload, "value").filter(|v| matches!(*v, "up" | "down" | "clear")).ok_or("value must be up, down or clear")?;
            Ok(plain(vec!["label", "--job-id", id, "--value", value]))
        }
        // without an interval the helper uses the one saved in the settings
        "task-register" => {
            let mut args = vec!["task-register".to_string(), "--task-name".to_string(), task.to_string()];
            if payload.get("interval").is_some_and(|v| !v.is_null()) {
                args.extend(["--interval".to_string(), number(payload, "interval", 30, 1440, 30)?.to_string()]);
            }
            Ok(Invocation { args, stdin: None, secrets: vec![] })
        }
        "task-unregister" => Ok(plain(vec!["task-unregister", "--task-name", task])),
        "settings-save" => {
            let section = text(payload, "section").filter(|s| SECTIONS.contains(s)).ok_or("unknown settings section")?;
            let mut data = payload.get("data").cloned().ok_or("settings-save needs data")?;
            let mut secrets = vec![];
            // "use the bot that was just connected": the token comes from this process, never from the window
            if data.get("telegram").and_then(|t| t.get("useStoredToken")).and_then(Value::as_bool) == Some(true) {
                let token = token.ok_or("telegram_token_missing")?;
                data["telegram"]["bot_token"] = json!(token);
                secrets.push(token.to_string());
            }
            if let Some(telegram) = data.get_mut("telegram").and_then(Value::as_object_mut) {
                telegram.remove("useStoredToken");
            }
            let mut args = vec!["settings-save", "--section", section];
            if section == "schedule" {
                args.extend(["--task-name", task]);
            }
            let mut inv = plain(args);
            inv.stdin = Some(data.to_string());
            inv.secrets = secrets;
            Ok(inv)
        }
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
    json!({ "home": app_home(), "taskName": task_name(), "displayName": APP_DISPLAY_NAME })
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

/// The user gave up changing the bot: forget the token that was held for it.
#[tauri::command]
fn forget_token(state: State<'_, AppState>) {
    state.token.clear();
}

/// The link the app was started with (or a second start passed on), once.
#[tauri::command]
fn take_pending_link(state: State<'_, AppState>) -> Option<Value> {
    state.pending_link.lock().ok().and_then(|mut slot| slot.take())
}

/// The first time the window is closed the user is told (once, ever) that the app stays in the tray.
const HINT_FILE: &str = "tray_hint_shown";

/// True the first time it is called for this data folder, false afterwards.
fn first_close(home: &std::path::Path) -> bool {
    let marker = home.join(HINT_FILE);
    if marker.exists() {
        return false;
    }
    let _ = std::fs::create_dir_all(home);
    std::fs::write(&marker, "shown").is_ok()
}

/// Close request (the X or the Close button): hint once, then the window goes away. The app stays in the tray and the
/// scheduled checks run as before.
fn request_close(app: &AppHandle, hint_done: bool) {
    if !hint_done && first_close(&PathBuf::from(app_home())) {
        let _ = app.emit("close-hint", ());
        return;
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
}

/// "Close" in the window; `hint_done` is true when the user has just seen the hint and pressed OK.
#[tauri::command]
fn close_app(app: AppHandle, hint_done: bool) {
    request_close(&app, hint_done);
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn cancel_command(state: State<'_, AppState>, run_id: u64) {
    state.running.kill(run_id);
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// A link arrived: remember it, bring the window up and tell it to look.
fn deliver_link(app: &AppHandle, link: links::Link) {
    if let Some(state) = app.try_state::<AppState>() {
        if let Ok(mut slot) = state.pending_link.lock() {
            *slot = Some(link.to_value());
        }
    }
    show_main(app);
    let _ = app.emit("link-pending", ());
}

/// Tray: "Run check now". One at a time; the window (if open) is told when it is over.
fn run_check_now(app: &AppHandle) {
    let state = app.state::<AppState>();
    if state.checking.swap(true, Ordering::SeqCst) {
        return;
    }
    let run_id = state.next_tray_run.fetch_add(1, Ordering::SeqCst);
    let running = state.running.clone();
    let handle = app.clone();
    let _ = app.emit("check-started", ());
    set_tray_tip(app, &format!("{APP_DISPLAY_NAME}: checking Upwork…"));
    tauri::async_runtime::spawn_blocking(move || {
        let outcome = sidecar_exe(&handle).and_then(|program| {
            let invocation = invocation_for("run-once", &Value::Null, &task_name(), None)?;
            sidecar::run(&program, &invocation, &[], &running, run_id, |_| {})
        });
        let tip = match &outcome {
            Ok(done) if done.exit_code == Some(0) => format!("{APP_DISPLAY_NAME}: last check finished"),
            Ok(done) if done.exit_code == Some(75) => format!("{APP_DISPLAY_NAME}: Upwork needs a quick check"),
            _ => format!("{APP_DISPLAY_NAME}: the last check did not finish"),
        };
        set_tray_tip(&handle, &tip);
        handle.state::<AppState>().checking.store(false, Ordering::SeqCst);
        let _ = handle.emit("check-finished", outcome.ok().and_then(|o| o.exit_code));
    });
}

fn set_tray_tip(app: &AppHandle, tip: &str) {
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(tip));
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", format!("Open {APP_DISPLAY_NAME}"), true, None::<&str>)?;
    let check = MenuItem::with_id(app, "check", "Run check now", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &check, &quit])?;
    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip(APP_DISPLAY_NAME)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main(app),
            "check" => run_check_now(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = AppState {
        running: Arc::new(Running::default()),
        token: Arc::new(TokenStore::default()),
        pending_link: Mutex::new(links::find_link(std::env::args().skip(1)).map(|link| link.to_value())),
        checking: AtomicBool::new(false),
        next_tray_run: AtomicU64::new(TRAY_RUN_ID_START),
    };
    let app = tauri::Builder::default()
        // a second start (a notification click while the app runs) hands its link to the first and exits
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| match links::find_link(argv) {
            Some(link) => deliver_link(app, link),
            None => show_main(app),
        }))
        .plugin(tauri_plugin_opener::init())
        .manage(state)
        .setup(|app| {
            build_tray(app.handle())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                request_close(window.app_handle(), false);
            }
        })
        .invoke_handler(tauri::generate_handler![
            app_info,
            run_command,
            telegram_connect,
            forget_token,
            take_pending_link,
            cancel_command,
            close_app,
            quit_app
        ])
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
        assert_eq!(args("settings-get", json!({})), ["settings-get"]);
        assert_eq!(args("run-once", json!({})), ["run-once", "--ignore-quiet-hours"]);
        assert_eq!(args("notify-test", json!({"channel": "toast"})), ["notify-test", "--channel", "toast"]);
        assert_eq!(args("jobs", json!({"limit": 5})), ["jobs", "--limit", "5"]);
        assert_eq!(args("jobs", json!({"limit": 5, "includeBelow": true})), ["jobs", "--limit", "5", "--include-below"]);
        assert_eq!(args("jobs", json!({"limit": 5, "includeBelow": "yes"})), ["jobs", "--limit", "5"]);
        assert_eq!(args("task-register", json!({})), ["task-register", "--task-name", "gigradar-app-test"]);
        assert_eq!(args("task-register", json!({"interval": 60})), ["task-register", "--task-name", "gigradar-app-test", "--interval", "60"]);
        assert_eq!(args("task-unregister", Value::Null), ["task-unregister", "--task-name", "gigradar-app-test"]);
    }

    #[test]
    fn jobs_job_and_label_take_only_valid_ids_runs_and_values() {
        assert_eq!(
            args("jobs", json!({"limit": 20, "run": "2026-10-09T10:00:00+00:00"})),
            ["jobs", "--limit", "20", "--run", "2026-10-09T10:00:00+00:00"]
        );
        assert_eq!(args("job", json!({"id": "~01abc"})), ["job", "--job-id", "~01abc"]);
        assert_eq!(args("label", json!({"id": "~01abc", "value": "down"})), ["label", "--job-id", "~01abc", "--value", "down"]);
        for (name, payload) in [
            ("jobs", json!({"run": "now; calc"})),
            ("job", json!({"id": "~01 --config x"})),
            ("job", json!({})),
            ("label", json!({"id": "~01", "value": "maybe"})),
            ("label", json!({"id": "01", "value": "up"})),
        ] {
            assert!(invocation_for(name, &payload, "t", None).is_err(), "{name} {payload}");
        }
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
    fn settings_save_sends_the_section_data_on_stdin() {
        let payload = json!({"section": "schedule", "data": {"interval_minutes": 60, "quiet_start": null, "quiet_end": null}});
        let inv = invocation_for("settings-save", &payload, "t", None).unwrap();
        assert_eq!(inv.args, ["settings-save", "--section", "schedule", "--task-name", "t"]);
        let stdin: Value = serde_json::from_str(inv.stdin.as_ref().unwrap()).unwrap();
        assert_eq!(stdin["interval_minutes"], 60);
        let other = invocation_for("settings-save", &json!({"section": "profile", "data": {"markdown": "## A\nx"}}), "t", None).unwrap();
        assert_eq!(other.args, ["settings-save", "--section", "profile"]);
        for bad in [json!({"section": "all", "data": {}}), json!({"section": "profile"}), json!({"data": {}})] {
            assert!(invocation_for("settings-save", &bad, "t", None).is_err(), "{bad}");
        }
    }

    #[test]
    fn settings_save_adds_the_stored_token_only_when_asked_and_only_on_stdin() {
        let data = json!({"toast": true, "telegram": {"enabled": true, "chat_id": "42", "bot_username": "b", "useStoredToken": true}});
        let payload = json!({"section": "notifications", "data": data});
        let inv = invocation_for("settings-save", &payload, "t", Some(TOKEN)).unwrap();
        assert!(inv.args.iter().all(|a| !a.contains(TOKEN)));
        let stdin: Value = serde_json::from_str(inv.stdin.as_ref().unwrap()).unwrap();
        assert_eq!(stdin["telegram"]["bot_token"], TOKEN);
        assert!(stdin["telegram"].get("useStoredToken").is_none());
        assert_eq!(inv.secrets, [TOKEN]);
        assert_eq!(invocation_for("settings-save", &payload, "t", None).err().as_deref(), Some("telegram_token_missing"));
        // not asked: an existing token is left alone and nothing is added
        let keep = json!({"section": "notifications", "data": {"toast": true, "telegram": {"enabled": true}}});
        let inv = invocation_for("settings-save", &keep, "t", Some(TOKEN)).unwrap();
        assert!(!inv.stdin.as_ref().unwrap().contains(TOKEN) && inv.secrets.is_empty());
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
        let commands = ["doctor", "upwork-check", "model-download", "run-once", "notify-test", "jobs", "task-register", "task-unregister", "settings-get"];
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

    #[test]
    fn the_tray_hint_is_shown_once_per_data_folder() {
        let dir = std::env::temp_dir().join(format!("gigradar-hint-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert!(first_close(&dir));
        assert!(!first_close(&dir));
        assert!(!first_close(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_display_name_matches_the_window_title_and_product_name() {
        let conf: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(conf["productName"], APP_DISPLAY_NAME);
        assert_eq!(conf["app"]["windows"][0]["title"], APP_DISPLAY_NAME);
        assert_eq!(DATA_DIR_NAME, "Gigradar Data");
    }
}
