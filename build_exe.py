#!/usr/bin/env python3
"""
在 GitHub 倉庫目錄執行打包。

注意：倉庫若在 OneDrive 內，PyInstaller 的 build/dist 會放到
本機暫存目錄，避免 OneDrive 鎖定檔案導致「存取被拒」。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "HKO_Rainfall_Downloader.py")
DIST_NAME = "HKO_Rainfall_Downloader"
OUTPUT_EXE = os.path.join(ROOT, f"{DIST_NAME}.exe")

# 打包暫存目錄（避開 OneDrive 同步鎖定）
BUILD_ROOT = os.path.join(os.path.expanduser("~"), ".hko_pyinstaller")
WORKPATH = os.path.join(BUILD_ROOT, "build")
DISTPATH = os.path.join(BUILD_ROOT, "dist")
SPECPATH = os.path.join(BUILD_ROOT, "spec")


def main() -> int:
    os.makedirs(WORKPATH, exist_ok=True)
    os.makedirs(DISTPATH, exist_ok=True)
    os.makedirs(SPECPATH, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--windowed",
        "--name",
        DIST_NAME,
        "--workpath",
        WORKPATH,
        "--distpath",
        DISTPATH,
        "--specpath",
        SPECPATH,
        SCRIPT,
    ]
    print("Running:", " ".join(cmd))
    print(f"Build cache: {BUILD_ROOT}")
    subprocess.run(cmd, cwd=ROOT, check=True)

    built = os.path.join(DISTPATH, f"{DIST_NAME}.exe")
    if not os.path.exists(built):
        print("Build failed: exe not found.")
        return 1

    shutil.copy2(built, OUTPUT_EXE)
    print(f"Build complete:\n  {built}\n  {OUTPUT_EXE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())