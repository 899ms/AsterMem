# AsterMem Desktop

Tauri v2 desktop shell — packages AsterMem as a native macOS / Windows app.

## Prerequisites

- [Rust](https://rustup.rs/) (stable)
- Node.js 18+
- Python 3.11+ (for PyInstaller sidecar build)
- Built `web-ui/dist/`

## Development

### 1. Build the Python sidecar

```bash
# From repo root
pip install pyinstaller
pyinstaller desktop/pyinstaller.spec --distpath desktop/src-tauri/binaries/

# Rename per Tauri sidecar convention (macOS arm64 example)
mv desktop/src-tauri/binaries/astermem-server \
   desktop/src-tauri/binaries/astermem-server-aarch64-apple-darwin
```

Naming convention:
| Platform | Filename |
|----------|----------|
| macOS arm64 | `astermem-server-aarch64-apple-darwin` |
| macOS x86_64 | `astermem-server-x86_64-apple-darwin` |
| Windows x64 | `astermem-server-x86_64-pc-windows-msvc.exe` |

### 2. Build the web UI

```bash
cd web-ui && npm install && npm run build && cd ..
```

### 3. Run in dev mode

```bash
cd desktop
npm install
npm run dev
```

In dev mode, Tauri starts the Vite dev server for the web UI (port 5173),
but the sidecar must already be built and placed in `src-tauri/binaries/`.

### 4. Build for release

```bash
cd desktop
npm run build
```

Output is in `desktop/src-tauri/target/release/bundle/`:
- macOS: `.dmg`
- Windows: `.msi` / `.exe` (NSIS)

### 5. CI build (recommended)

Go to **Actions → Build Desktop App → Run workflow**. The CI builds the
sidecar and Tauri app for macOS (arm64 + x86_64) and Windows (x64)
automatically. Download the installer from Artifacts when done.

## Architecture

```
desktop/
├── package.json                     # @tauri-apps/cli dev dependency
├── pyinstaller.spec                 # PyInstaller config for the sidecar
├── README.md
└── src-tauri/
    ├── Cargo.toml                   # Rust dependencies
    ├── build.rs
    ├── tauri.conf.json              # Window / tray / sidecar / bundle config
    ├── src/
    │   ├── main.rs                  # Entry: spawn sidecar → show window → hide-to-tray on close
    │   ├── tray.rs                  # System tray menu
    │   └── sidecar.rs               # Python process lifecycle management
    ├── binaries/                    # PyInstaller output (gitignored)
    └── icons/                       # App icons (all sizes)
```

## Window behavior

| Action | Behavior |
|--------|----------|
| Launch | Show main window + tray icon; start Python service in background |
| Close button (×) | Hide window to tray; service keeps running |
| Minimize | Minimize to Dock / taskbar (standard) |
| Left-click tray icon | Restore and focus window |
| Tray → Show Window | Restore and focus window |
| Tray → Copy API URL | Copy `http://localhost:<port>` to clipboard |
| Tray → Restart Service | Kill and respawn the Python sidecar |
| Tray → Quit | Stop the Python service and exit |

## Generating icons

Place a 1024×1024 (or 256×256 minimum) source PNG and run:

```bash
npx @tauri-apps/cli icon src-tauri/icons/source.png
```

This generates all platform-required sizes automatically.
