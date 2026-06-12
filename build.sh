#!/usr/bin/env bash
# Build Rank2 into a standalone app with PyInstaller, then sign and notarize.
# Run from the project root: bash build.sh
set -e

SIGN_IDENTITY="Developer ID Application: Ty Allen (6A8BNV3NWV)"
APPLE_ID="tyjallen44@mac.com"
TEAM_ID="6A8BNV3NWV"
KEYCHAIN_PROFILE="rank2-notary"
BUNDLE_ID="com.rank2.app"

echo "Installing PyInstaller..."
.venv/bin/pip install pyinstaller --quiet

echo "Checking Playwright browsers..."
.venv/bin/playwright install chromium --quiet 2>/dev/null || true

echo "Building Rank2 (~370MB, takes a minute)..."
.venv/bin/pyinstaller rank2.spec --clean --noconfirm

if [[ "$OSTYPE" == "darwin"* ]]; then
    # ── Launcher script ───────────────────────────────────────────────────────
    cat > "dist/Run Rank2.command" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/Rank2/Rank2"
EOF
    chmod +x "dist/Run Rank2.command"

    # ── Code signing ──────────────────────────────────────────────────────────
    # All binaries must be signed with --timestamp for notarization.
    # Sign leaf binaries first (depth-first), then the main executable.
    echo ""
    echo "Signing binaries..."

    CS=(codesign --force --sign "$SIGN_IDENTITY" --options runtime --timestamp)

    # .dylib and .so files
    find dist/Rank2/_internal -type f \( -name "*.dylib" -o -name "*.so" \) | while read -r f; do
        "${CS[@]}" "$f" 2>/dev/null || true
    done

    # Python3 framework binary then the bundle (order matters)
    PY3_BIN="dist/Rank2/_internal/Python3.framework/Versions/3.9/Python3"
    if [ -f "$PY3_BIN" ]; then
        echo "  Signing $PY3_BIN"
        "${CS[@]}" "$PY3_BIN"
    fi
    if [ -d "dist/Rank2/_internal/Python3.framework" ]; then
        echo "  Signing Python3.framework"
        "${CS[@]}" dist/Rank2/_internal/Python3.framework
    fi

    # Node.js driver and Chromium headless shell
    # node and chrome-headless-shell need allow-jit so V8 can allocate executable memory
    CSE=(codesign --force --sign "$SIGN_IDENTITY" --options runtime --timestamp --entitlements rank2.entitlements)
    find dist/Rank2/_internal -type f \( -name "node" -o -name "chrome-headless-shell" \) | while read -r f; do
        echo "  Signing $f"
        "${CSE[@]}" "$f"
    done

    # Main executable last (deep + entitlements)
    codesign --force --deep \
        --sign "$SIGN_IDENTITY" \
        --options runtime \
        --timestamp \
        --entitlements rank2.entitlements \
        dist/Rank2/Rank2

    # .command launcher
    "${CS[@]}" "dist/Run Rank2.command"

    echo "Verifying signature..."
    codesign --verify --deep --strict dist/Rank2/Rank2 && echo "  Signature OK"

    # ── Notarization ──────────────────────────────────────────────────────────
    echo ""
    echo "Creating zip for notarization..."
    # ditto preserves symlinks and HFS+ metadata that zip -r does not
    rm -rf /tmp/rank2-stage && mkdir /tmp/rank2-stage
    ditto dist/Rank2 /tmp/rank2-stage/Rank2
    cp "dist/Run Rank2.command" /tmp/rank2-stage/
    rm -f /tmp/rank2-submit.zip
    ditto -c -k /tmp/rank2-stage /tmp/rank2-submit.zip
    rm -rf /tmp/rank2-stage

    echo "Submitting to Apple for notarization (this takes 1-5 minutes)..."
    xcrun notarytool submit /tmp/rank2-submit.zip \
        --keychain-profile "$KEYCHAIN_PROFILE" \
        --wait
    rm -f /tmp/rank2-submit.zip

    echo "Notarization complete."

    # ── Distribution zip ──────────────────────────────────────────────────────
    echo ""
    echo "Creating distribution zip..."
    rm -f Rank2-mac.zip
    rm -rf /tmp/rank2-dist-stage && mkdir /tmp/rank2-dist-stage
    ditto dist/Rank2 /tmp/rank2-dist-stage/Rank2
    cp "dist/Run Rank2.command" /tmp/rank2-dist-stage/
    ditto -c -k /tmp/rank2-dist-stage Rank2-mac.zip
    rm -rf /tmp/rank2-dist-stage

    SIZE=$(du -sh dist/Rank2 2>/dev/null | cut -f1)
    ZIP_SIZE=$(du -sh Rank2-mac.zip 2>/dev/null | cut -f1)
    echo ""
    echo "Build complete."
    echo "  App folder : dist/Rank2/  ($SIZE)"
    echo "  Launcher   : dist/Run Rank2.command"
    echo "  Distrib zip: Rank2-mac.zip  ($ZIP_SIZE)  ← send this to users"
else
    echo ""
    echo "Build complete."
    echo "  Folder : dist/Rank2/"
    echo "  To run : dist/Rank2/Rank2.exe"
fi
