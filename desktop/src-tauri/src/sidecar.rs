use std::path::Path;
use std::sync::Mutex;

use log::{error, info};
use tauri::AppHandle;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

pub struct SidecarState {
    child: Mutex<Option<CommandChild>>,
    port: u16,
    data_dir: String,
}

impl SidecarState {
    pub fn new(child: CommandChild, port: u16, data_dir: &Path) -> Self {
        Self {
            child: Mutex::new(Some(child)),
            port,
            data_dir: data_dir.to_string_lossy().into_owned(),
        }
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn kill(&self) {
        if let Some(child) = self.child.lock().unwrap().take() {
            info!("Stopping astermem-server...");
            child.kill().ok();
        }
    }

    pub fn restart(&self, app: &AppHandle) {
        self.kill();
        match spawn_server_inner(app, self.port, Path::new(&self.data_dir)) {
            Ok(child) => {
                *self.child.lock().unwrap() = Some(child);
                info!("astermem-server restarted on port {}", self.port);
            }
            Err(e) => error!("Failed to restart sidecar: {e}"),
        }
    }
}

pub fn spawn_server(
    app: &AppHandle,
    port: u16,
    data_dir: &Path,
) -> Result<CommandChild, Box<dyn std::error::Error>> {
    spawn_server_inner(app, port, data_dir)
}

fn spawn_server_inner(
    app: &AppHandle,
    port: u16,
    data_dir: &Path,
) -> Result<CommandChild, Box<dyn std::error::Error>> {
    let (mut rx, child) = app
        .shell()
        .sidecar("astermem-server")?
        .args([
            "--port",
            &port.to_string(),
            "--data-dir",
            &data_dir.to_string_lossy(),
        ])
        .spawn()?;

    // Log sidecar output in background
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    info!("[server] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    error!("[server] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    info!("astermem-server exited: {:?}", payload.code);
                    break;
                }
                _ => {}
            }
        }
    });

    info!("astermem-server spawned on port {port}");
    Ok(child)
}
