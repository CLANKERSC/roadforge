# Assumptions

Each entry: what we assumed, why, and how to check it.

## Project setup (M0)

- **Target machine is Windows 10/11 x64.** Confirmed by Honza. Development also works on macOS/Linux.
- **First test country: Czech Republic.** Not yet confirmed; drives which data adapters come first in M1.
- **GPU / RAM unknown.** Nothing is tuned for a specific GPU yet.
- **App name "RoadForge" is a placeholder.** Rename in `brand.json` (see README).
- **Base map for display:** OpenFreeMap "liberty" style (free, no key, OSM data). If it can't load,
  the app falls back to OSM's raster tiles, acceptable for light single-user use under the OSM tile
  usage policy. The base map is display-only and never used to generate game content.
- **Worker security:** binds to 127.0.0.1 only; every endpoint except `/health` needs the per-run
  random token; CORS allows only the Tauri webview origins and the dev server on port 1420.
- **Worker lifetime:** the worker exits when its stdin closes, so it dies with the desktop app even if
  the app crashes (PyInstaller one-file builds run as two processes, so killing only the parent
  would leave an orphan).
