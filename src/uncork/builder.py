"""
Package builder - converts intermediate format to distro packages.
"""

from __future__ import annotations

import shutil
import stat
from abc import ABC, abstractmethod
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from uncork.launcher import generate_all_launchers
from uncork.spec import PackageSpec

if TYPE_CHECKING:
    from rich.progress import Progress


class BuildError(Exception):
    """Error during package build."""
    pass


class PackageBuilder:
    """
    Build distribution packages from intermediate format.
    
    Usage:
        spec = PackageSpec.load("/path/to/intermediate")
        builder = PackageBuilder(spec, intermediate_path="/path/to/intermediate")
        builder.build_deb("/output/app.deb")
        builder.build_pacman("/output/app.pkg.tar.zst")
    """

    def __init__(self, spec: PackageSpec, intermediate_path: Path | str | None = None):
        self.spec = spec
        self.intermediate_path = Path(intermediate_path) if intermediate_path else None

    @classmethod
    def from_directory(cls, path: Path | str) -> PackageBuilder:
        """Load spec and create builder from intermediate directory."""
        path = Path(path)
        spec = PackageSpec.load(path)
        return cls(spec, intermediate_path=path)

    def build_deb(self, output_path: Path | str, progress: Progress | None = None) -> Path:
        """Build Debian .deb package."""
        from uncork.builders.deb import DebBuilder
        return self._build_with(DebBuilder, output_path, progress)

    def build_pacman(self, output_path: Path | str, progress: Progress | None = None) -> Path:
        """Build Arch Linux .pkg.tar.zst package."""
        from uncork.builders.pacman import PacmanBuilder
        return self._build_with(PacmanBuilder, output_path, progress)

    def build_rpm(self, output_path: Path | str, progress: Progress | None = None) -> Path:
        """Build RPM package."""
        from uncork.builders.rpm import RpmBuilder
        return self._build_with(RpmBuilder, output_path, progress)

    def build_directory(self, output_path: Path | str, progress: Progress | None = None) -> Path:
        """
        Build to a directory (for inspection/testing).
        
        Creates the same structure that would go into a package,
        without actually packaging it.
        """
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Stage all files
        self._stage_files(output_path)
        
        return output_path

    def _build_with(
        self,
        builder_class: type[FormatBuilder],
        output_path: Path | str,
        progress: Progress | None,
    ) -> Path:
        """Build using a specific format builder."""
        output_path = Path(output_path)
        
        # Create temp staging directory
        import tempfile
        with tempfile.TemporaryDirectory(prefix="uncork-") as tmpdir:
            staging = Path(tmpdir) / "staging"
            staging.mkdir()
            
            # Stage files
            self._stage_files(staging)
            
            # Build package
            builder = builder_class(self.spec, staging, output_path)
            return builder.build(progress)

    def _stage_files(self, staging_root: Path) -> None:
        """
        Stage all package files to a directory.
        
        Creates the filesystem layout as it will appear when installed.
        """
        system_path = self.spec.get_system_path()
        
        # Remove leading / for joining
        rel_system = system_path.lstrip("/")
        install_dir = staging_root / rel_system
        install_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Copy prefix-template
        if self.intermediate_path:
            src_prefix = self.intermediate_path / "prefix-template"
            if src_prefix.exists():
                shutil.copytree(src_prefix, install_dir / "prefix-template", symlinks=True)
        
        # 2. Copy bundled Wine if present
        if self.intermediate_path and self.spec.wine.bundled_path:
            src_wine = self.intermediate_path / "wine"
            if src_wine.exists():
                shutil.copytree(src_wine, install_dir / "wine", symlinks=True)
        
        # 3. Copy icons
        if self.intermediate_path:
            src_icons = self.intermediate_path / "icons"
            if src_icons.exists():
                shutil.copytree(src_icons, install_dir / "icons")
        
        # 4. Generate and write launchers
        launchers = generate_all_launchers(self.spec)
        
        for rel_path, content in launchers.items():
            if rel_path.startswith("bin/"):
                # Launcher scripts go in package install dir
                dest = install_dir / rel_path
            elif rel_path.startswith("share/"):
                # Desktop files go in /usr/share
                dest = staging_root / "usr" / rel_path
            else:
                dest = install_dir / rel_path
            
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            
            # Make scripts executable
            if rel_path.startswith("bin/"):
                dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        
        # 5. Create /usr/bin symlinks for easy CLI access
        usr_bin = staging_root / "usr" / "bin"
        usr_bin.mkdir(parents=True, exist_ok=True)

        for i, exe in enumerate(self.spec.executables):
            # Determine command name
            if exe.command:
                # Use explicit command name if provided
                link_name = exe.command
            elif i == 0:
                # First executable gets the app name (no suffix)
                link_name = self.spec.app.name
            else:
                # Additional executables get app-name-exe-id format
                link_name = f"{self.spec.app.name}-{exe.id}"

            link_path = usr_bin / link_name
            target = f"{system_path}/bin/{exe.id}"

            link_path.symlink_to(target)
        
        # 6. Install icons to standard locations
        if self.intermediate_path:
            self._install_icons(staging_root)
        
        # 7. Copy manifest for reference
        if self.intermediate_path:
            manifest_src = self.intermediate_path / "manifest.json"
            if manifest_src.exists():
                shutil.copy2(manifest_src, install_dir / "manifest.json")

    def _install_icons(self, staging_root: Path) -> None:
        """Install icons to XDG icon directories with names matching command names."""
        icons_src = self.intermediate_path / "icons" if self.intermediate_path else None
        if not icons_src or not icons_src.exists():
            return

        icons_base = staging_root / "usr" / "share" / "icons" / "hicolor"

        # Iterate over executables to map exe.id -> command name
        for i, exe in enumerate(self.spec.executables):
            if not exe.icon:
                continue

            # Find the icon file (stored as icons/{exe.id}.png in intermediate)
            icon_file = self.intermediate_path / exe.icon
            if not icon_file.exists():
                continue

            # Determine the command name (must match launcher.py and builder.py symlink logic)
            if exe.command:
                icon_name = exe.command
            elif i == 0:
                icon_name = self.spec.app.name
            else:
                icon_name = f"{self.spec.app.name}-{exe.id}"

            # Install icon with the command name
            size_dir = icons_base / "256x256" / "apps"
            size_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon_file, size_dir / f"{icon_name}.png")


class FormatBuilder(ABC):
    """Base class for format-specific package builders."""

    def __init__(self, spec: PackageSpec, staging_dir: Path, output_path: Path):
        self.spec = spec
        self.staging_dir = staging_dir
        self.output_path = output_path

    @abstractmethod
    def build(self, progress: Progress | None = None) -> Path:
        """Build the package and return path to output."""
        pass

    @property
    def package_name(self) -> str:
        return self.spec.app.name

    @property
    def package_version(self) -> str:
        return self.spec.app.version

    @property
    def package_description(self) -> str:
        return self.spec.app.description

    def generate_overlay_unmount_script(self) -> str:
        """
        Generate universal overlay filesystem unmounting script.

        This script works reliably across all package formats (deb, pacman, rpm).
        It uses an aggressive retry strategy to ensure overlays are unmounted during
        package removal, even if Wine processes are running.

        Returns:
            Bash script code (no shebang) that unmounts all user overlays
        """
        app_name = self.spec.app.name

        return dedent(f'''\
            # Unmount and clean up overlay mounts for all users
            for user_home in /home/*; do
                [[ -d "$user_home" ]] || continue
                username=$(basename "$user_home")
                user_data="${{user_home}}/.local/share/{app_name}"
                merged_dir="${{user_data}}/prefix"

                # Check if mounted using mount command (more reliable than mountpoint)
                if mount | grep -q "$merged_dir"; then
                    # Try multiple times with increasing aggression
                    for attempt in {{1..10}}; do
                        # Kill all wine processes first
                        pkill -9 -u "$username" wine 2>/dev/null || true
                        pkill -9 -u "$username" wineserver 2>/dev/null || true

                        # Kill anything using the mount
                        fuser -km "$merged_dir" 2>/dev/null || true
                        sleep 0.2

                        # Try lazy unmount as user (FUSE mounts are user-owned)
                        su "$username" -c "fusermount -uz '$merged_dir' 2>/dev/null" && break

                        # If that failed, try as root with force
                        umount -l "$merged_dir" 2>/dev/null && break

                        sleep 0.5
                    done
                fi

                # Remove user data directory
                if [[ -d "$user_data" ]]; then
                    rm -rf "$user_data" 2>/dev/null || true
                fi
            done
        ''')
