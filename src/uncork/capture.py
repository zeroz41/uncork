"""
Prefix capture - normalize and export Wine prefixes to intermediate format.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Callable

from rich.progress import Progress, TaskID

from uncork.analysis import PrefixAnalyzer, PrefixAnalysis
from uncork.icons import extract_icon
from uncork.registry import RegistryProcessor
from uncork.spec import (
    AppMetadata,
    Executable,
    InstallConfig,
    PackageSpec,
    PrefixMetadata,
    WineConfig,
    WineMode,
)


class CaptureError(Exception):
    """Error during prefix capture."""
    pass


class PrefixCapture:
    """
    Captures and normalizes a Wine prefix for packaging.
    
    Usage:
        capture = PrefixCapture("/home/user/.wine-myapp")
        capture.add_executable("main", "My App", "drive_c/Program Files/MyApp/app.exe")
        capture.set_wine_mode("system", min_version="9.0")
        capture.normalize()
        capture.export("/tmp/myapp-intermediate")
    """
    
    # Default exclusion patterns
    DEFAULT_EXCLUSIONS = [
        "*.dxvk-cache",
        "*.log",
        "*.tmp",
        "mesa_shader_cache/**",
        "nvidiav1/**",
        "GLCache/**",
        "drive_c/users/*/Temp/**",
        "drive_c/users/*/Local Settings/Temp/**",
        "drive_c/windows/temp/**",
        "drive_c/windows/Temp/**",
        ".update-timestamp",
    ]
    
    # Placeholder for username in tokenized paths
    USER_TOKEN = "__WINE_USER__"
    HOME_TOKEN = "__USER_HOME__"

    def __init__(self, prefix_path: Path | str, update_prefix: bool = True):
        self.prefix_path = Path(prefix_path).expanduser().resolve()

        if not self.prefix_path.exists():
            raise CaptureError(f"Prefix path does not exist: {self.prefix_path}")

        # Run initial analysis
        self._analysis: PrefixAnalysis | None = None

        # Configuration
        self._executables: list[Executable] = []
        self._wine_config = WineConfig()
        self._app_metadata: AppMetadata | None = None
        self._install_config = InstallConfig()
        self._exclusions: list[str] = list(self.DEFAULT_EXCLUSIONS)
        self._update_prefix = update_prefix

        # State
        self._normalized = False
        self._export_path: Path | None = None

    def analyze(self) -> PrefixAnalysis:
        """Analyze the prefix and return results."""
        if self._analysis is None:
            self._analysis = PrefixAnalyzer(self.prefix_path).analyze()
        return self._analysis

    def add_executable(
        self,
        id: str,
        name: str,
        path: str,
        *,
        command: str | None = None,
        args: str = "",
        working_dir: str | None = None,
        icon_source: str | None = None,
        custom_icon_path: str | Path | None = None,
        description: str | None = None,
        desktop_entry: bool = True,
        categories: list[str] | None = None,
    ) -> None:
        """
        Add an executable entry point.

        Args:
            id: Unique identifier (used in filenames)
            name: Human-readable name for menus
            path: Path relative to prefix (e.g., "drive_c/Program Files/App/app.exe")
            command: Custom command name (defaults to auto-generated from position)
            args: Default command-line arguments
            working_dir: Working directory relative to prefix
            icon_source: Path to extract icon from within prefix (defaults to exe path)
            custom_icon_path: Absolute path to custom icon file (PNG/ICO) to use instead of extracting
            description: Description for .desktop file (falls back to app description if None)
            desktop_entry: Whether to create .desktop file
            categories: XDG categories for .desktop file
        """
        # Validate path exists
        full_path = self.prefix_path / path
        if not full_path.exists():
            raise CaptureError(f"Executable not found: {full_path}")

        # Validate custom icon if provided
        if custom_icon_path:
            custom_icon_path = Path(custom_icon_path)
            if not custom_icon_path.exists():
                raise CaptureError(f"Custom icon not found: {custom_icon_path}")

        exe = Executable(
            id=id,
            name=name,
            path=path,
            command=command,
            args=args,
            working_dir=working_dir or str(Path(path).parent),
            icon=None,  # Set during normalization
            description=description,
            create_desktop_entry=desktop_entry,
            categories=categories or ["Application"],
        )

        # Store icon source for later extraction
        exe._icon_source = icon_source or path  # type: ignore
        exe._custom_icon_path = custom_icon_path  # type: ignore

        self._executables.append(exe)

    def set_wine_mode(
        self,
        mode: str | WineMode,
        *,
        min_version: str | None = None,
        bundled_wine_path: str | None = None,
    ) -> None:
        """
        Set Wine runtime mode.
        
        Args:
            mode: "system" or "bundled"
            min_version: Minimum Wine version for system mode
            bundled_wine_path: Path to Wine installation for bundled mode
        """
        if isinstance(mode, str):
            mode = WineMode(mode)
        
        self._wine_config = WineConfig(
            mode=mode,
            system_min_version=min_version,
            bundled_path=bundled_wine_path,
        )
        
        if mode == WineMode.BUNDLED and not bundled_wine_path:
            raise CaptureError("Bundled mode requires bundled_wine_path")

    def set_app_metadata(
        self,
        name: str,
        display_name: str,
        *,
        version: str = "1.0.0",
        description: str = "A Windows application packaged for Linux",
        maintainer: str | None = None,
        homepage: str | None = None,
        license: str = "Proprietary",
    ) -> None:
        """Set application metadata."""
        self._app_metadata = AppMetadata(
            name=name,
            display_name=display_name,
            version=version,
            description=description,
            maintainer=maintainer,
            homepage=homepage,
            license=license,
        )

    def set_install_config(
        self,
        *,
        system_path: str = "/opt/{name}",
        user_data_path: str = "${XDG_DATA_HOME}/{name}",
        use_overlay: bool = False,
    ) -> None:
        """Configure installation paths."""
        self._install_config = InstallConfig(
            system_path=system_path,
            user_data_path=user_data_path,
            use_overlay=use_overlay,
        )

    def add_exclusion(self, pattern: str) -> None:
        """Add a glob pattern to exclude from capture."""
        if pattern not in self._exclusions:
            self._exclusions.append(pattern)

    def remove_exclusion(self, pattern: str) -> None:
        """Remove an exclusion pattern."""
        if pattern in self._exclusions:
            self._exclusions.remove(pattern)

    def normalize(self, progress_callback: Callable[[str, float], None] | None = None) -> None:
        """
        Normalize the prefix for portability.
        
        This is a validation/preparation step. Actual file operations
        happen during export().
        """
        analysis = self.analyze()
        
        if not analysis.is_valid_prefix:
            raise CaptureError("Invalid Wine prefix structure")
        
        if not self._executables:
            raise CaptureError("No executables configured. Call add_executable() first.")
        
        if not self._app_metadata:
            # Generate default metadata from first executable
            exe = self._executables[0]
            name = re.sub(r'[^a-z0-9]', '-', exe.name.lower()).strip('-')
            self._app_metadata = AppMetadata(
                name=name,
                display_name=exe.name,
            )
        
        self._normalized = True

    def _update_prefix_with_wineboot(self) -> None:
        """
        Run wineboot -u to update the prefix before capture.

        This prevents Wine from running wineboot on first user launch,
        which causes massive file copy-ups in overlay mode.
        """
        import subprocess
        import sys
        import shutil

        try:
            env = os.environ.copy()
            env['WINEPREFIX'] = str(self.prefix_path)
            env['WINEDEBUG'] = '-all'

            # Kill any running wineserver for this prefix
            try:
                subprocess.run(
                    ['wineserver', '-k'],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                pass

            # Completely disable all display connections
            # Remove all display-related environment variables
            for var in ['DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'WAYLAND_SOCKET']:
                env.pop(var, None)

            # Set display to invalid value as backup
            env['DISPLAY'] = ''
            env['WAYLAND_DISPLAY'] = ''

            result = subprocess.run(
                ['wineboot', '-u'],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )

            # Wait for wineboot to finish background tasks
            import time
            time.sleep(2)

        except subprocess.TimeoutExpired:
            # Wineboot took too long, continue anyway
            pass
        except FileNotFoundError:
            # Wine not installed on build system - warn but continue
            print("Warning: wineboot not found - prefix may trigger updates on first user run",
                  file=sys.stderr)
        except Exception as e:
            # Other error - warn but continue
            print(f"Warning: wineboot failed: {e}", file=sys.stderr)

    def export(
        self,
        output_path: Path | str,
        progress: Progress | None = None,
    ) -> PackageSpec:
        """
        Export normalized prefix to intermediate format.

        Args:
            output_path: Directory to create intermediate structure in
            progress: Optional rich Progress instance for UI feedback
            
        Returns:
            PackageSpec describing the exported package
        """
        if not self._normalized:
            self.normalize()

        # Update prefix with wineboot to prevent first-run updates
        if self._update_prefix:
            self._update_prefix_with_wineboot()

        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._export_path = output_path

        analysis = self.analyze()

        # Create directory structure
        prefix_template = output_path / "prefix-template"
        icons_dir = output_path / "icons"
        wine_dir = output_path / "wine"
        
        icons_dir.mkdir(exist_ok=True)
        
        task: TaskID | None = None
        if progress:
            task = progress.add_task("Exporting prefix...", total=100)
        
        def update_progress(pct: float) -> None:
            if progress and task is not None:
                progress.update(task, completed=pct)
        
        # 1. Copy prefix with exclusions and path normalization (0-70%)
        self._copy_prefix(prefix_template, update_progress)
        update_progress(70)
        
        # 2. Tokenize registry files (70-80%)
        self._tokenize_registry(prefix_template, analysis.detected_user)
        update_progress(80)
        
        # 3. Normalize user directory name (80-85%)
        self._normalize_user_dir(prefix_template, analysis.detected_user)
        update_progress(85)
        
        # 4. Extract icons (85-90%)
        self._extract_icons(icons_dir)
        update_progress(90)
        
        # 5. Bundle Wine if requested (90-95%)
        if self._wine_config.mode == WineMode.BUNDLED:
            self._bundle_wine(wine_dir)
        update_progress(95)
        
        # 6. Generate manifest (95-100%)
        spec = self._generate_spec(analysis)
        spec.save(output_path)
        update_progress(100)
        
        return spec

    def _copy_prefix(
        self,
        dest: Path,
        progress_callback: Callable[[float], None],
    ) -> None:
        """Copy prefix with exclusions."""
        
        def should_exclude(rel_path: str) -> bool:
            for pattern in self._exclusions:
                if fnmatch.fnmatch(rel_path, pattern):
                    return True
                if fnmatch.fnmatch(rel_path.replace("\\", "/"), pattern):
                    return True
            return False
        
        # Count total files first for progress
        total_files = sum(1 for _ in self.prefix_path.rglob("*") if _.is_file())
        copied = 0
        
        for src_path in self.prefix_path.rglob("*"):
            rel_path = src_path.relative_to(self.prefix_path)
            
            if should_exclude(str(rel_path)):
                continue
            
            dest_path = dest / rel_path

            # Check symlinks first (is_dir() follows symlinks)
            if src_path.is_symlink():
                # Handle symlinks specially - store as relative or skip
                self._handle_symlink(src_path, dest_path, rel_path)
            elif src_path.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
            elif src_path.is_file():
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_path)
                copied += 1
                progress_callback(70 * copied / max(total_files, 1))

    def _handle_symlink(self, src: Path, dest: Path, rel_path: Path) -> None:
        """Handle symlink during copy."""
        target = os.readlink(src)
        
        # dosdevices symlinks
        if "dosdevices" in str(rel_path):
            link_name = src.name
            if link_name == "c:":
                # c: -> ../drive_c (relative, portable)
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.symlink("../drive_c", dest)
            elif link_name == "z:":
                # z: -> / (recreated at install time, skip for now)
                pass
            else:
                # Other drive letters - keep if relative
                if not target.startswith("/"):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(target, dest)
        else:
            # User folder symlinks - will be recreated at install
            # Don't copy symlinks pointing to absolute paths
            if not target.startswith("/"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, dest)

    def _tokenize_registry(self, prefix_dir: Path, original_user: str | None) -> None:
        """Replace hardcoded paths in registry files with tokens."""
        if not original_user:
            return
        
        processor = RegistryProcessor(self.USER_TOKEN, self.HOME_TOKEN)
        
        for reg_file in ["system.reg", "user.reg", "userdef.reg"]:
            reg_path = prefix_dir / reg_file
            if reg_path.exists():
                processor.tokenize_file(reg_path, original_user)

    def _normalize_user_dir(self, prefix_dir: Path, original_user: str | None) -> None:
        """Rename user directory to token name."""
        if not original_user:
            return
        
        users_dir = prefix_dir / "drive_c" / "users"
        old_user_dir = users_dir / original_user
        new_user_dir = users_dir / self.USER_TOKEN
        
        if old_user_dir.exists() and not new_user_dir.exists():
            old_user_dir.rename(new_user_dir)

    def _extract_icons(self, icons_dir: Path) -> None:
        """Extract icons from executables or copy custom icons."""
        for exe in self._executables:
            icon_path = icons_dir / f"{exe.id}.png"

            # Check if custom icon path is provided
            custom_icon = getattr(exe, "_custom_icon_path", None)
            if custom_icon:
                try:
                    # Copy custom icon file
                    shutil.copy2(custom_icon, icon_path)
                    exe.icon = f"icons/{exe.id}.png"
                except Exception as e:
                    # Custom icon copy failed
                    exe.icon = None
            else:
                # Extract icon from executable
                icon_source = getattr(exe, "_icon_source", exe.path)
                source_path = self.prefix_path / icon_source

                if source_path.exists():
                    try:
                        extract_icon(source_path, icon_path)
                        exe.icon = f"icons/{exe.id}.png"
                    except Exception as e:
                        # Icon extraction failed, not critical
                        exe.icon = None

    def _bundle_wine(self, wine_dir: Path) -> None:
        """Copy bundled Wine installation."""
        if not self._wine_config.bundled_path:
            return
        
        src = Path(self._wine_config.bundled_path)
        if not src.exists():
            raise CaptureError(f"Bundled Wine path not found: {src}")
        
        # Copy Wine installation
        dest = wine_dir / src.name
        shutil.copytree(src, dest, symlinks=True)
        
        # Update config to point to relative path
        self._wine_config.bundled_path = f"wine/{src.name}"

    def _generate_spec(self, analysis: PrefixAnalysis) -> PackageSpec:
        """Generate package specification."""
        assert self._app_metadata is not None
        
        return PackageSpec(
            app=self._app_metadata,
            wine=self._wine_config,
            prefix=PrefixMetadata(
                original_user=analysis.detected_user or "user",
                original_path=str(self.prefix_path),
                normalized_user=self.USER_TOKEN,
                original_wine_version=analysis.wine_version,
                has_dxvk=analysis.has_dxvk,
                has_vkd3d=analysis.has_vkd3d,
                arch=analysis.arch,
            ),
            executables=self._executables,
            install=self._install_config,
            excluded_patterns=self._exclusions,
        )
