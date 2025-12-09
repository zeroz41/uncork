"""
Package specification models.

Defines the intermediate format structure that sits between
prefix capture and package building.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class WineMode(str, Enum):
    """Wine runtime mode for the package."""
    SYSTEM = "system"      # Depend on system-installed Wine
    BUNDLED = "bundled"    # Bundle Wine runtime in package


class Executable(BaseModel):
    """An executable entry point in the package."""
    id: str = Field(description="Unique identifier for this executable")
    name: str = Field(description="Human-readable name for menus/launchers")
    path: str = Field(description="Path relative to prefix, e.g. 'drive_c/Program Files/App/app.exe'")
    command: Optional[str] = Field(default=None, description="Command name (defaults to id)")
    args: str = Field(default="", description="Default command-line arguments")
    working_dir: Optional[str] = Field(default=None, description="Working directory relative to prefix")
    icon: Optional[str] = Field(default=None, description="Path to icon file in intermediate structure")
    description: Optional[str] = Field(default=None, description="Description for .desktop file (falls back to app description)")
    wm_class: Optional[str] = Field(default=None, description="Override StartupWMClass (defaults to exe filename)")
    create_desktop_entry: bool = Field(default=True, description="Whether to create .desktop file")
    categories: list[str] = Field(default_factory=lambda: ["Application"])


class WineConfig(BaseModel):
    """Wine runtime configuration."""
    mode: WineMode = Field(default=WineMode.SYSTEM)
    system_min_version: Optional[str] = Field(default=None, description="Minimum Wine version for system mode")
    bundled_path: Optional[str] = Field(default=None, description="Path to bundled Wine in intermediate structure")


class PrefixMetadata(BaseModel):
    """Metadata about the original captured prefix."""
    original_user: str = Field(description="Username from original prefix")
    original_path: str = Field(description="Original prefix path")
    normalized_user: str = Field(default="__WINE_USER__", description="Placeholder used in tokenized paths")
    original_wine_version: Optional[str] = Field(default=None, description="Wine version that created prefix")
    has_dxvk: bool = Field(default=False)
    has_vkd3d: bool = Field(default=False)
    arch: str = Field(default="win64", description="win32 or win64")


class InstallConfig(BaseModel):
    """Installation path configuration."""
    system_path: str = Field(default="/opt/{name}", description="Where package installs (template)")
    user_data_path: str = Field(default="${XDG_DATA_HOME}/{name}", description="Where user data lives")
    use_overlay: bool = Field(default=False, description="Use fuse-overlayfs instead of copy")


class AppMetadata(BaseModel):
    """Application metadata for the package."""
    name: str = Field(description="Package name (lowercase, no spaces)")
    display_name: str = Field(description="Human-readable application name")
    version: str = Field(default="1.0.0")
    description: str = Field(default="A Windows application packaged for Linux")
    maintainer: Optional[str] = Field(default=None)
    homepage: Optional[str] = Field(default=None)
    license: str = Field(default="Proprietary")


class PackageSpec(BaseModel):
    """
    Complete package specification.
    
    This is the intermediate format that sits between prefix capture
    and package building. It's serialized to manifest.json in the
    intermediate directory structure.
    """
    schema_version: str = Field(default="1")
    app: AppMetadata
    wine: WineConfig = Field(default_factory=WineConfig)
    prefix: PrefixMetadata
    executables: list[Executable] = Field(default_factory=list)
    install: InstallConfig = Field(default_factory=InstallConfig)
    excluded_patterns: list[str] = Field(default_factory=lambda: [
        "*.dxvk-cache",
        "*.log",
        "mesa_shader_cache/**",
        "nvidiav1/**",
        "GLCache/**",
        "drive_c/users/*/Temp/**",
    ])

    def save(self, path: Path | str) -> None:
        """Save specification to manifest.json."""
        path = Path(path)
        manifest_path = path / "manifest.json" if path.is_dir() else path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path | str) -> PackageSpec:
        """Load specification from manifest.json or directory containing it."""
        path = Path(path)
        manifest_path = path / "manifest.json" if path.is_dir() else path
        data = json.loads(manifest_path.read_text())
        return cls.model_validate(data)

    def get_system_path(self) -> str:
        """Get resolved system installation path."""
        return self.install.system_path.replace("{name}", self.app.name)

    def get_user_data_path(self) -> str:
        """Get user data path template (still contains env vars)."""
        return self.install.user_data_path.replace("{name}", self.app.name)
