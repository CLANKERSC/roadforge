"""Bundle the Python worker into one executable for the desktop app (PyInstaller).

Output: app/src-tauri/binaries/forge-worker-<rust target triple>[.exe], which is the name
Tauri's `externalBin` expects. Run on the OS you are building for (Windows for the .exe).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "src-tauri" / "binaries"


def target_triple() -> str:
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not read host triple from rustc -vV")


def main() -> None:
    sep = ";" if sys.platform == "win32" else ":"
    build = ROOT / "build" / "pyinstaller"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "forge-worker",
            "--distpath",
            str(build / "dist"),
            "--workpath",
            str(build / "work"),
            "--specpath",
            str(build),
            "--paths",
            str(ROOT / "worker"),
            "--collect-submodules",
            "forge_worker",
            # geo libraries ship data files and compiled plugins PyInstaller can't see by itself
            "--collect-all",
            "rasterio",
            "--collect-all",
            "pyproj",
            "--collect-data",
            "certifi",
            "--collect-data",
            "trimesh",
            "--add-data",
            f"{ROOT / 'brand.json'}{sep}.",
            str(ROOT / "worker" / "run_worker.py"),
        ],
        check=True,
    )
    exe = ".exe" if sys.platform == "win32" else ""
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"forge-worker-{target_triple()}{exe}"
    shutil.copy2(build / "dist" / f"forge-worker{exe}", dest)
    print(f"sidecar ready: {dest}")


if __name__ == "__main__":
    main()
