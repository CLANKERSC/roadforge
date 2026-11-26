//! Desktop shell. Starts the Python worker as a child process, reads the one JSON line
//! it prints ({"port": ..., "token": ...}) and hands that to the UI via `worker_info`.
//! The worker exits by itself when this app closes (it watches its stdin).

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{Manager, RunEvent, State};

#[derive(Clone, Serialize)]
struct WorkerInfo {
    url: String,
    token: String,
}

#[derive(Deserialize)]
struct StartupLine {
    port: u16,
    token: String,
}

#[derive(Default)]
struct WorkerState {
    info: Mutex<Option<Result<WorkerInfo, String>>>,
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn worker_info(state: State<'_, WorkerState>) -> Result<WorkerInfo, String> {
    match &*state.info.lock().unwrap() {
        Some(Ok(info)) => Ok(info.clone()),
        Some(Err(e)) => Err(e.clone()),
        None => Err("worker is still starting".into()),
    }
}

/// In development: run `python -m forge_worker` from the repo's worker/ folder.
/// In the installed app: run the bundled `forge-worker` executable next to our own exe.
fn worker_command() -> Result<Command, String> {
    if cfg!(debug_assertions) {
        let worker_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../worker");
        let venv_python = if cfg!(windows) {
            worker_dir.join(".venv/Scripts/python.exe")
        } else {
            worker_dir.join(".venv/bin/python")
        };
        let python = std::env::var("FORGE_PYTHON").map(PathBuf::from).unwrap_or_else(|_| {
            if venv_python.exists() {
                venv_python
            } else {
                PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
            }
        });
        let mut cmd = Command::new(python);
        cmd.args(["-m", "forge_worker", "--exit-on-stdin-close"]).current_dir(worker_dir);
        Ok(cmd)
    } else {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let dir = exe.parent().ok_or("no exe dir")?;
        let name = if cfg!(windows) { "forge-worker.exe" } else { "forge-worker" };
        let mut cmd = Command::new(dir.join(name));
        cmd.arg("--exit-on-stdin-close");
        Ok(cmd)
    }
}

fn start_worker(app: &tauri::AppHandle) {
    let state = app.state::<WorkerState>();
    let result = (|| -> Result<(Child, std::process::ChildStdout), String> {
        let mut cmd = worker_command()?;
        cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = cmd.spawn().map_err(|e| format!("could not start worker: {e}"))?;
        let stdout = child.stdout.take().ok_or("no worker stdout")?;
        Ok((child, stdout))
    })();

    let (child, stdout) = match result {
        Ok(v) => v,
        Err(e) => {
            *state.info.lock().unwrap() = Some(Err(e));
            return;
        }
    };
    *state.child.lock().unwrap() = Some(child);

    let handle = app.clone();
    std::thread::spawn(move || {
        let mut lines = BufReader::new(stdout).lines();
        let info = match lines.next() {
            Some(Ok(line)) => serde_json::from_str::<StartupLine>(&line)
                .map(|s| WorkerInfo { url: format!("http://127.0.0.1:{}", s.port), token: s.token })
                .map_err(|e| format!("bad worker startup line: {e}")),
            _ => Err("worker exited before it was ready".into()),
        };
        *handle.state::<WorkerState>().info.lock().unwrap() = Some(info);
        for _ in lines {} // keep draining so the worker never blocks on a full pipe
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(WorkerState::default())
        .invoke_handler(tauri::generate_handler![worker_info])
        .setup(|app| {
            start_worker(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<WorkerState>().child.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
