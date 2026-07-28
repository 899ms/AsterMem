mod sidecar;
mod tray;

use log::info;
use tauri::Manager;

fn main() {
    env_logger::init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            info!("AsterMem Desktop starting...");

            let port = portpicker::pick_unused_port_in_range(8000, 9000)
                .unwrap_or(8768);

            let data_dir = app
                .path()
                .app_data_dir()
                .expect("failed to resolve app data dir");
            std::fs::create_dir_all(&data_dir).ok();

            info!("Using port {port}, data dir: {}", data_dir.display());

            let child = sidecar::spawn_server(app.handle(), port, &data_dir)?;
            app.manage(sidecar::SidecarState::new(child, port));

            tray::create_tray(app)?;

            let window = app.get_webview_window("main")
                .expect("main window not found");
            let url = format!("http://localhost:{port}");
            window.eval(&format!("window.location.replace('{url}')")).ok();

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Hide to tray instead of quitting
                window.hide().unwrap_or_default();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running AsterMem");
}
