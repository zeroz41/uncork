# Uncork

## WORK IN PROGRESS ##

> Uncork your Wine apps

Convert Wine prefixes into portable Linux system packages (.deb, .pkg.tar.zst, .rpm).

**Status**: Early development

## Overview

Uncork captures existing, working Wine prefixes and packages them for distribution as native Linux packages. Unlike recipe-based tools (Lutris, Bottles), Uncork preserves your exact working configuration.

### Features

- **Prefix capture**: Snapshot a working Wine prefix with all configurations
- **Path normalization**: Automatically rewrites hardcoded paths for portability
- **Multiple package formats**: Debian (.deb), Arch (.pkg.tar.zst), RPM
- **Two Wine modes**:
  - **System**: Package depends on system-installed Wine (smaller packages)
  - **Bundled**: Include specific Wine/GE/Proton version (guaranteed compatibility)
- **First-run initialization**: Prefix is copied to user's home on first launch
- **Optional overlay mode**: Use fuse-overlayfs for instant startup and minimal disk usage
- **Python API**: Integrate into your build pipelines

## Installation

```bash
pip install uncork
```

Or from source:

```bash
git clone https://github.com/yourname/uncork
cd uncork
pip install -e .
```

## Quick Start

### CLI Usage

```bash
# 1. Analyze a prefix to see what's in it
uncork analyze ~/.wine-myapp

# 2. Capture the prefix to intermediate format
uncork capture ~/.wine-myapp \
    --output ./myapp-intermediate \
    --exe "My Application:drive_c/Program Files/MyApp/app.exe" \
    --wine-mode system \
    --min-wine-version 9.0

# 3. Build packages
uncork build ./myapp-intermediate \
    --output ./packages \
    --format deb \
    --format pacman
```

### Python API

```python
from uncork import PrefixCapture, PackageSpec, PackageBuilder

# Capture a prefix
capture = PrefixCapture("/home/user/.wine-myapp")
capture.add_executable(
    id="main",
    name="My Application",
    path="drive_c/Program Files/MyApp/app.exe",
)
capture.set_wine_mode("system", min_version="9.0")
capture.normalize()
capture.export("/tmp/myapp-intermediate")

# Build packages
spec = PackageSpec.load("/tmp/myapp-intermediate")
builder = PackageBuilder(spec, intermediate_path="/tmp/myapp-intermediate")
builder.build_deb("/out/myapp.deb")
builder.build_pacman("/out/myapp.pkg.tar.zst")
```

## How It Works

### Intermediate Format

Uncork first exports to an intermediate format:

```
myapp-intermediate/
├── manifest.json           # Package metadata and configuration
├── prefix-template/        # Normalized Wine prefix
│   ├── drive_c/
│   ├── system.reg          # Tokenized registry
│   ├── user.reg
│   └── dosdevices/
├── wine/                   # Optional: bundled Wine runtime
├── icons/                  # Extracted application icons
└── launchers/              # Generated launcher scripts
```

### Path Tokenization

Hardcoded paths are replaced with tokens:
- `C:\users\originaluser\` → `C:\users\__WINE_USER__\`
- `/home/originaluser/` → `/home/__WINE_USER__/`

These are resolved at first-run time.

### Installation Structure

Packages install to:
```
/opt/myapp/                     # Read-only, root-owned
├── prefix-template/            # The captured prefix
├── bin/main                    # Launcher script
└── manifest.json

~/.local/share/myapp/           # Created on first run, user-owned
└── prefix/                     # User's working prefix copy
```

## Wine Mode Comparison

| Aspect | System Wine | Bundled Wine |
|--------|-------------|--------------|
| Package size | Small (~100MB+ depending on app) | Large (+400-700MB for Wine) |
| Dependencies | Requires `wine` package | Self-contained |
| Updates | Gets system Wine updates | Frozen Wine version |
| Compatibility | May break with Wine updates | Guaranteed compatible |
| Best for | Simple apps, distro repos | Games, complex apps |

## Overlay Mode

For large prefixes, enable overlay mode to avoid copying GBs of data:

```bash
uncork capture ~/.wine-game --overlay ...
```

This uses `fuse-overlayfs` to mount the read-only template with a writable user layer. Only changes are stored in the user's home directory.

**Requirements**: `fuse-overlayfs` package

## Building from CI/CD

Example GitHub Actions workflow:

```yaml
- name: Package Windows app for Linux
  run: |
    pip install uncork
    uncork capture ./wine-prefix \
      --output ./intermediate \
      --exe "MyApp:drive_c/app.exe"
    uncork build ./intermediate \
      --output ./dist \
      --format deb \
      --format pacman
    
- uses: actions/upload-artifact@v3
  with:
    name: linux-packages
    path: ./dist/*
```

## Limitations

- **x86_64 only**: 32-bit pure prefixes not supported
- **No macOS**: Linux packaging only. For now
- **License compliance**: You're responsible for ensuring you can legally distribute the packaged application
- **Wine compatibility**: Prefixes may still fail on systems with very different configurations

## Dependencies

### Required
- Python 3.10+
- click
- rich
- pydantic
- icoextract
- Pillow

### For package building
- **Debian**: `dpkg-deb` (or builds manually)
- **Arch**: `bsdtar`, `zstd`
- **RPM**: `fpm` (recommended) or `rpmbuild`

## Contributing

Issues and PRs welcome.

## License

GPLv3
