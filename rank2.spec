# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Rank2.

Build with:
    bash build.sh

Output: dist/Rank2.app (macOS) or dist/Rank2/ (Windows/Linux)

Bundle size is ~370MB because it includes:
  - Playwright's Node.js driver (~129MB)
  - Chromium headless shell (~190MB)
  - Python + all other dependencies (~50MB)
"""
import sys
import pathlib
import playwright

block_cipher = None

# ── Locate Playwright internals ───────────────────────────────────────────────
_pw_pkg = pathlib.Path(playwright.__file__).parent

# ── Locate Chromium headless shell ────────────────────────────────────────────
if sys.platform == "darwin":
    _pw_browsers = pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
elif sys.platform == "win32":
    _pw_browsers = pathlib.Path.home() / "AppData" / "Local" / "ms-playwright"
else:
    _pw_browsers = pathlib.Path.home() / ".cache" / "ms-playwright"

_headless_shell = next(_pw_browsers.glob("chromium_headless_shell-*"), None)
if _headless_shell is None:
    raise SystemExit(
        "\nChromium headless shell not found.\n"
        "Run this first:  .venv/bin/playwright install chromium\n"
    )

print(f"Bundling Playwright driver: {_pw_pkg / 'driver'}")
print(f"Bundling Chromium headless shell: {_headless_shell}")

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["run_analysis.py"],
    pathex=["."],
    binaries=[
        # Playwright's Node.js driver must be an executable binary entry
        (str(_pw_pkg / "driver" / "node"), "playwright/driver"),
    ],
    datas=[
        # Playwright Node.js package (JS source used by the driver)
        (str(_pw_pkg / "driver" / "package"), "playwright/driver/package"),
        # Chromium headless shell (used for PDF rendering)
        (str(_headless_shell), _headless_shell.name),
        # Project assets (logo, etc.)
        ("perception/assets", "perception/assets"),
    ],
    hiddenimports=[
        "duckdb",
        "pydantic",
        "pydantic_settings",
        "playwright",
        "playwright.sync_api",
        "playwright._impl._browser",
        "playwright._impl._driver",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.simpledialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pytest_asyncio",
        "pytest_playwright",
        "weasyprint",
        "xhtml2pdf",
        "reportlab",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rank2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # never UPX-compress — corrupts Chromium and Node binaries
    console=True,       # keep terminal visible so users see analysis progress
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Rank2",
)

# macOS: wrap in a .app bundle so users can double-click it
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Rank2.app",
        icon=None,
        bundle_identifier="com.rank2.app",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleName": "Rank2",
            "CFBundleDisplayName": "Rank2",
            "NSHighResolutionCapable": True,
        },
    )
