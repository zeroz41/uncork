"""
Prefix analysis - scan and detect contents of Wine prefixes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DetectedExecutable:
    """An executable found in the prefix."""
    path: str                    # Relative to prefix
    name: str                    # Filename without extension
    size: int                    # File size in bytes
    probable_app: bool = False   # Likely a main application (not installer/uninstaller)


@dataclass
class PrefixAnalysis:
    """Results of analyzing a Wine prefix."""
    prefix_path: Path
    exists: bool = False
    is_valid_prefix: bool = False
    
    # Structure
    arch: str = "win64"                    # win32 or win64
    has_system_reg: bool = False
    has_user_reg: bool = False
    
    # Size
    total_size: int = 0                    # Total size in bytes
    drive_c_size: int = 0
    
    # Wine version detection
    wine_version: str | None = None
    
    # Graphics stack
    has_dxvk: bool = False
    dxvk_version: str | None = None
    has_vkd3d: bool = False
    vkd3d_version: str | None = None
    
    # User info
    detected_user: str | None = None
    
    # Detected executables
    executables: list[DetectedExecutable] = field(default_factory=list)
    
    # Potential issues
    warnings: list[str] = field(default_factory=list)


class PrefixAnalyzer:
    """Analyzes Wine prefix contents."""
    
    # Patterns to skip when scanning for executables
    SKIP_EXE_PATTERNS = [
        r"unins\d*\.exe$",
        r"uninst.*\.exe$",
        r"setup\.exe$",
        r"install.*\.exe$",
        r"update.*\.exe$",
        r"crash.*\.exe$",
        r"report.*\.exe$",
        r"helper.*\.exe$",
        r"launcher\.exe$",  # Often not the main app
    ]
    
    # System directories to skip
    SKIP_DIRS = [
        "windows",
        "Program Files/Common Files",
        "Program Files (x86)/Common Files", 
        "Program Files/Windows",
        "ProgramData",
    ]

    def __init__(self, prefix_path: Path | str):
        self.prefix_path = Path(prefix_path).expanduser().resolve()

    def analyze(self) -> PrefixAnalysis:
        """Perform full analysis of the prefix."""
        result = PrefixAnalysis(prefix_path=self.prefix_path)
        
        if not self.prefix_path.exists():
            result.warnings.append(f"Prefix path does not exist: {self.prefix_path}")
            return result
        
        result.exists = True
        
        # Check for basic prefix structure
        drive_c = self.prefix_path / "drive_c"
        system_reg = self.prefix_path / "system.reg"
        user_reg = self.prefix_path / "user.reg"
        
        result.has_system_reg = system_reg.exists()
        result.has_user_reg = user_reg.exists()
        result.is_valid_prefix = drive_c.exists() and result.has_system_reg
        
        if not result.is_valid_prefix:
            result.warnings.append("Missing drive_c or system.reg - may not be a valid Wine prefix")
            return result
        
        # Detect architecture
        result.arch = self._detect_arch()
        
        # Detect user
        result.detected_user = self._detect_user()
        
        # Calculate sizes
        result.total_size = self._get_dir_size(self.prefix_path)
        result.drive_c_size = self._get_dir_size(drive_c)
        
        # Detect Wine version from registry
        result.wine_version = self._detect_wine_version()
        
        # Detect DXVK
        dxvk_info = self._detect_dxvk()
        result.has_dxvk = dxvk_info[0]
        result.dxvk_version = dxvk_info[1]
        
        # Detect VKD3D
        vkd3d_info = self._detect_vkd3d()
        result.has_vkd3d = vkd3d_info[0]
        result.vkd3d_version = vkd3d_info[1]
        
        # Find executables
        result.executables = self._find_executables()
        
        # Check for potential issues
        self._check_issues(result)
        
        return result

    def _detect_arch(self) -> str:
        """Detect if prefix is 32-bit or 64-bit."""
        # win64 prefixes have system32 as 64-bit and syswow64 as 32-bit
        syswow64 = self.prefix_path / "drive_c" / "windows" / "syswow64"
        return "win64" if syswow64.exists() else "win32"

    def _detect_user(self) -> str | None:
        """Detect the username in the prefix."""
        users_dir = self.prefix_path / "drive_c" / "users"
        if not users_dir.exists():
            return None
        
        for entry in users_dir.iterdir():
            if entry.is_dir() and entry.name.lower() not in ("public", "default"):
                return entry.name
        return None

    def _detect_wine_version(self) -> str | None:
        """Try to detect Wine version from registry."""
        system_reg = self.prefix_path / "system.reg"
        if not system_reg.exists():
            return None
        
        try:
            content = system_reg.read_text(errors="ignore")
            # Look for Wine version in registry
            match = re.search(r'#arch=(\w+)', content)
            # Also look for actual version string
            version_match = re.search(r'"ProductName"="Wine (\d+\.\d+[^"]*)"', content)
            if version_match:
                return version_match.group(1)
        except Exception:
            pass
        return None

    def _detect_dxvk(self) -> tuple[bool, str | None]:
        """Detect DXVK installation and version."""
        system32 = self.prefix_path / "drive_c" / "windows" / "system32"
        
        # Check for DXVK DLLs
        dxvk_dlls = ["d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll"]
        has_dxvk = False
        
        for dll in dxvk_dlls:
            dll_path = system32 / dll
            if dll_path.exists():
                # Check if it's actually DXVK (not Windows native)
                try:
                    # DXVK DLLs are typically larger than stubs
                    if dll_path.stat().st_size > 100000:  # > 100KB
                        has_dxvk = True
                        break
                except Exception:
                    pass
        
        # Try to get version from dxvk.conf or DLL
        version = None
        # Could parse version from DLL but that's complex
        
        return has_dxvk, version

    def _detect_vkd3d(self) -> tuple[bool, str | None]:
        """Detect VKD3D-Proton installation."""
        system32 = self.prefix_path / "drive_c" / "windows" / "system32"
        d3d12_dll = system32 / "d3d12.dll"
        
        has_vkd3d = False
        if d3d12_dll.exists():
            try:
                if d3d12_dll.stat().st_size > 100000:
                    has_vkd3d = True
            except Exception:
                pass
        
        return has_vkd3d, None

    def _find_executables(self) -> list[DetectedExecutable]:
        """Find executable files in the prefix."""
        executables = []
        drive_c = self.prefix_path / "drive_c"
        
        skip_patterns = [re.compile(p, re.IGNORECASE) for p in self.SKIP_EXE_PATTERNS]
        
        for root, dirs, files in os.walk(drive_c):
            rel_root = Path(root).relative_to(self.prefix_path)
            
            # Skip system directories
            skip = False
            for skip_dir in self.SKIP_DIRS:
                if skip_dir.lower() in str(rel_root).lower():
                    skip = True
                    break
            if skip:
                continue
            
            for file in files:
                if not file.lower().endswith(".exe"):
                    continue
                
                # Check skip patterns
                should_skip = any(p.search(file) for p in skip_patterns)
                if should_skip:
                    continue
                
                file_path = Path(root) / file
                rel_path = file_path.relative_to(self.prefix_path)
                
                try:
                    size = file_path.stat().st_size
                except Exception:
                    size = 0
                
                # Heuristic: larger executables are more likely to be main apps
                probable_app = size > 1_000_000  # > 1MB
                
                executables.append(DetectedExecutable(
                    path=str(rel_path),
                    name=file_path.stem,
                    size=size,
                    probable_app=probable_app,
                ))
        
        # Sort by size descending (larger = more likely main app)
        executables.sort(key=lambda x: x.size, reverse=True)
        return executables

    def _get_dir_size(self, path: Path) -> int:
        """Get total size of directory in bytes."""
        total = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except Exception:
                        pass
        except Exception:
            pass
        return total

    def _check_issues(self, result: PrefixAnalysis) -> None:
        """Check for potential portability issues."""
        # Check for absolute paths in user directory symlinks
        if result.detected_user:
            user_dir = self.prefix_path / "drive_c" / "users" / result.detected_user
            for item in ["Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos"]:
                link = user_dir / item
                if link.is_symlink():
                    target = os.readlink(link)
                    if target.startswith("/"):
                        result.warnings.append(
                            f"Shell folder '{item}' links to absolute path: {target}"
                        )
        
        # Check for z: drive
        z_drive = self.prefix_path / "dosdevices" / "z:"
        if z_drive.is_symlink():
            target = os.readlink(z_drive)
            if target == "/":
                result.warnings.append(
                    "Z: drive exposes full filesystem - consider sandboxing"
                )


def analyze_prefix(prefix_path: Path | str) -> PrefixAnalysis:
    """Convenience function to analyze a prefix."""
    return PrefixAnalyzer(prefix_path).analyze()
