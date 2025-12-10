#!/bin/bash
# Test script: Install package, run app, then uninstall to verify overlay cleanup
# Run with: sudo ./test-overlay-cleanup.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PKG_NAME="pso"

echo -e "${BLUE}=======================================${NC}"
echo -e "${BLUE}Overlay Cleanup Test${NC}"
echo -e "${BLUE}=======================================${NC}"
echo ""

# Step 1: Clean removal of old package
echo -e "${YELLOW}Step 1: Removing old package and data...${NC}"
pacman -R "$PKG_NAME" --noconfirm 2>/dev/null || true
rm -rf ~/.local/share/"$PKG_NAME"
echo -e "${GREEN}✓ Cleaned up old installation${NC}"
echo ""

# Step 2: Install new package
echo -e "${YELLOW}Step 2: Installing new package...${NC}"
PKG_FILE=$(ls ./packages/"$PKG_NAME"-*.pkg.tar.zst | head -1)
if [ -z "$PKG_FILE" ]; then
    echo -e "${RED}ERROR: No package found in ./packages/${NC}"
    exit 1
fi
echo -e "${BLUE}Installing: $PKG_FILE${NC}"
pacman -U "$PKG_FILE" --noconfirm
echo -e "${GREEN}✓ Package installed${NC}"
echo ""

# Step 3: Choose launcher
echo -e "${YELLOW}Step 3: Choose which launcher to test:${NC}"
echo -e "  ${GREEN}1)${NC} pso   - PSO Online (online.exe)"
echo -e "  ${GREEN}2)${NC} psobb - PSO Offline (PsoBB.exe)"
echo ""
read -p "Choice [1/2]: " -n 1 -r
echo ""
echo ""

if [[ $REPLY == "2" ]]; then
    LAUNCHER="psobb"
else
    LAUNCHER="pso"
fi

# Step 4: Launch the app
echo -e "${YELLOW}Step 4: Launching $LAUNCHER...${NC}"
echo -e "${BLUE}(This will create the overlay mount)${NC}"
echo ""

# Get the real user who ran sudo
REAL_USER="${SUDO_USER:-$USER}"
REAL_UID=$(id -u "$REAL_USER")
REAL_HOME=$(eval echo ~"$REAL_USER")

# Preserve the environment that was active before sudo
# The sudo command preserves some env vars with SUDO_ prefix
PRESERVED_DISPLAY="${SUDO_DISPLAY:-$DISPLAY}"
PRESERVED_WAYLAND="${SUDO_WAYLAND_DISPLAY:-$WAYLAND_DISPLAY}"
PRESERVED_XAUTH="${SUDO_XAUTHORITY:-$XAUTHORITY}"

echo -e "${BLUE}Running as user: $REAL_USER${NC}"
echo -e "${YELLOW}Close the app when you're done testing...${NC}"
echo ""

# Run the launcher as the real user with preserved environment
sudo -u "$REAL_USER" \
    env \
    HOME="$REAL_HOME" \
    USER="$REAL_USER" \
    LOGNAME="$REAL_USER" \
    DISPLAY="$PRESERVED_DISPLAY" \
    WAYLAND_DISPLAY="$PRESERVED_WAYLAND" \
    XAUTHORITY="$PRESERVED_XAUTH" \
    XDG_RUNTIME_DIR="/run/user/$REAL_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$REAL_UID/bus" \
    "$LAUNCHER"

echo ""
echo -e "${GREEN}✓ Application closed${NC}"
echo ""

# Show mount status after app closes
echo -e "${BLUE}Mounts AFTER closing app:${NC}"
MOUNTS_AFTER_CLOSE=$(mount | grep "$PKG_NAME/prefix" || true)
if [ -n "$MOUNTS_AFTER_CLOSE" ]; then
    echo "$MOUNTS_AFTER_CLOSE"
    echo -e "${YELLOW}(Overlay still mounted - this is normal, launcher cleanup may have run)${NC}"
else
    echo "  (no mounts)"
fi
echo ""

# Step 5: Uninstall and verify cleanup
echo -e "${YELLOW}Step 5: Uninstalling to test overlay cleanup...${NC}"
echo -e "${BLUE}This will trigger the pre_remove hook with your new unmounting logic${NC}"
echo ""

# Show mounts before uninstall
echo -e "${BLUE}Mounts BEFORE uninstall:${NC}"
MOUNTS_BEFORE=$(mount | grep "$PKG_NAME/prefix" || true)
if [ -n "$MOUNTS_BEFORE" ]; then
    echo "$MOUNTS_BEFORE"
else
    echo "  (no mounts)"
fi
echo ""

# Perform uninstall
pacman -R "$PKG_NAME" --noconfirm

echo ""
echo -e "${GREEN}✓ Package uninstalled${NC}"
echo ""

# Critical test: Verify overlay was unmounted
echo -e "${YELLOW}Step 6: Verifying overlay cleanup...${NC}"
sleep 1

MOUNTS_AFTER=$(mount | grep "$PKG_NAME/prefix" || true)
if [ -n "$MOUNTS_AFTER" ]; then
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}✗ FAILED: Overlay still mounted!${NC}"
    echo -e "${RED}========================================${NC}"
    echo "$MOUNTS_AFTER"
    echo ""
    echo -e "${RED}The unmounting logic did not work!${NC}"
    exit 1
else
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✓ SUCCESS: Overlay unmounted!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${GREEN}The universal unmounting script works correctly!${NC}"
fi
echo ""

# Check user data cleanup
if [ -d "/home/$REAL_USER/.local/share/$PKG_NAME" ]; then
    echo -e "${YELLOW}User data still exists at ~/.local/share/$PKG_NAME${NC}"
else
    echo -e "${GREEN}✓ User data was also cleaned up${NC}"
fi
echo ""

echo -e "${BLUE}=======================================${NC}"
echo -e "${BLUE}Test Complete!${NC}"
echo -e "${BLUE}=======================================${NC}"
