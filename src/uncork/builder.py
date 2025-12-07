"""
Package builder - converts intermediate format to distro packages.
"""

from __future__ import annotations

import shutil
import stat
from abc import ABC, abstractmethod
from pathlib import Path
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
        
        for exe in self.spec.executables:
            # Symlink /usr/bin/appname -> /opt/appname/bin/main
            link_name = self.spec.app.name if exe.id == "main" else f"{self.spec.app.name}-{exe.id}"
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
        """Install icons to XDG icon directories."""
        icons_src = self.intermediate_path / "icons" if self.intermediate_path else None
        if not icons_src or not icons_src.exists():
            return
        
        icons_base = staging_root / "usr" / "share" / "icons" / "hicolor"
        
        for icon_file in icons_src.glob("*.png"):
            # Try to determine size from filename (e.g., "main-256.png")
            name = icon_file.stem
            
            # Check if filename contains size
            for size in [256, 128, 64, 48, 32, 24, 16]:
                if f"-{size}" in name:
                    size_dir = icons_base / f"{size}x{size}" / "apps"
                    size_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Use app name as icon name
                    clean_name = name.replace(f"-{size}", "")
                    if clean_name == "main":
                        clean_name = self.spec.app.name
                    
                    shutil.copy2(icon_file, size_dir / f"{clean_name}.png")
                    break
            else:
                # No size in filename, assume 256
                size_dir = icons_base / "256x256" / "apps"
                size_dir.mkdir(parents=True, exist_ok=True)
                
                clean_name = name if name != "main" else self.spec.app.name
                shutil.copy2(icon_file, size_dir / f"{clean_name}.png")


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
