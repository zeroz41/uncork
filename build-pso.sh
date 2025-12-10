#!/bin/bash
# Quick build script for PSO package
set -e

# Configuration
PREFIX_PATH="/home/td/.local/share/ephinea-prefix"
OUTPUT_DIR="./myapp"
PACKAGES_DIR="./packages"

# Clean old builds
echo "Cleaning old builds..."
rm -rf "$OUTPUT_DIR" "$PACKAGES_DIR"

# Activate virtual environment
source venv/bin/activate

# Capture prefix with both executables and custom command names
echo "Capturing prefix..."
uncork capture "$PREFIX_PATH" \
    --output "$OUTPUT_DIR" \
    --exe "PSO Online:drive_c/EphineaPSO/online.exe:pso" \
    --exe "PSO Offline:drive_c/EphineaPSO/PsoBB.exe:psobb" \
    --app-name pso \
    --wine-mode system \
    --overlay \
    --min-wine-version 9.0

# Build package
echo "Building package..."
uncork build "$OUTPUT_DIR" \
    --output "$PACKAGES_DIR" \
    --format pacman

echo ""
echo "✓ Build complete!"
echo ""
echo "To install:"
echo "  sudo pacman -R pso --noconfirm 2>/dev/null || true"
echo "  rm -rf ~/.local/share/pso"
echo "  sudo pacman -U $PACKAGES_DIR/pso-*.pkg.tar.zst"
echo ""
echo "To run:"
echo "  pso      # PSO Online (online.exe)"
echo "  psobb    # PSO Offline (PsoBB.exe)"
echo ""
