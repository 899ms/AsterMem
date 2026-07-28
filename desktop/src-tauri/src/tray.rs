use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    App, Manager,
};

pub fn create_tray(app: &App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show Window", true, None::<&str>)?;
    let copy_url = MenuItem::with_id(app, "copy_url", "Copy API URL", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart Service", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&show, &copy_url, &restart, &quit])?;

    TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .tooltip("AsterMem")
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(window) = app.get_webview_window("main") {
                    window.show().unwrap_or_default();
                    window.set_focus().unwrap_or_default();
                }
            }
        })
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    window.show().unwrap_or_default();
                    window.set_focus().unwrap_or_default();
                }
            }
            "copy_url" => {
                let state = app.state::<crate::sidecar::SidecarState>();
                let url = format!("http://localhost:{}", state.port());
                if let Some(window) = app.get_webview_window("main") {
                    window
                        .eval(&format!(
                            "navigator.clipboard.writeText('{url}')"
                        ))
                        .ok();
                }
            }
            "restart" => {
                let state = app.state::<crate::sidecar::SidecarState>();
                state.restart(app);
            }
            "quit" => {
                let state = app.state::<crate::sidecar::SidecarState>();
                state.kill();
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;

    Ok(())
}
