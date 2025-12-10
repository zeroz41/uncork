"""
Arch Linux pacman package builder.

Creates .pkg.tar.zst packages compatible with pacman.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from uncork.builder import BuildError, FormatBuilder
from uncork.spec import WineMode

if TYPE_CHECKING:
    from rich.progress import Progress


class PacmanBuilder(FormatBuilder):
    """Build Arch Linux .pkg.tar.zst packages."""

    def build(self, progress: Progress | None = None) -> Path:
        """Build pacman package."""
        
        # Generate .PKGINFO
        pkginfo = self._generate_pkginfo()
        pkginfo_path = self.staging_dir / ".PKGINFO"
        pkginfo_path.write_text(pkginfo)
        
        # Generate .MTREE (file metadata)
        # For simplicity, we'll skip this - pacman handles missing .MTREE
        
        # Generate install scriptlet if needed
        install_script = self._generate_install_script()
        if install_script:
            install_path = self.staging_dir / ".INSTALL"
            install_path.write_text(install_script)
        
        # Create the package
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Build tar.zst
        return self._build_package()

    def _generate_pkginfo(self) -> str:
        """Generate .PKGINFO file."""
        
        # Calculate installed size
        installed_size = sum(
            f.stat().st_size for f in self.staging_dir.rglob("*") if f.is_file()
        )
        
        lines = [
            f"pkgname = {self.package_name}",
            f"pkgver = {self.package_version}-1",
            f"pkgdesc = {self.package_description}",
            f"url = {self.spec.app.homepage or ''}",
            f"builddate = {int(os.path.getmtime(self.staging_dir))}",
            f"packager = {self.spec.app.maintainer or 'Unknown Packager <unknown@example.com>'}",
            f"size = {installed_size}",
            "arch = x86_64",
            f"license = {self.spec.app.license}",
        ]
        
        # Dependencies
        if self.spec.wine.mode == WineMode.SYSTEM:
            lines.append("depend = wine")
        
        if self.spec.install.use_overlay:
            lines.append("depend = fuse-overlayfs")
        
        # Optional dependencies
        lines.append("optdepend = winetricks: for additional Windows components")
        lines.append("optdepend = lib32-vulkan-icd-loader: for Vulkan/DXVK support")
        lines.append("optdepend = vulkan-icd-loader: for Vulkan/DXVK support")
        
        return "\n".join(lines) + "\n"

    def _generate_install_script(self) -> str:
        """Generate .INSTALL scriptlet."""
        use_overlay = self.spec.install.use_overlay

        cleanup_block = ""
        if use_overlay:
            # Use the universal unmounting script from base class
            cleanup_block = "\n" + self.generate_overlay_unmount_script()

        script = dedent(f'''\
            post_install() {{
                # Update desktop database
                if command -v update-desktop-database &>/dev/null; then
                    update-desktop-database -q /usr/share/applications
                fi

                # Update icon cache
                if command -v gtk-update-icon-cache &>/dev/null; then
                    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor
                fi
            }}

            post_upgrade() {{
                post_install
            }}
        ''')

        if use_overlay:
            script += dedent(f'''\

            pre_remove() {{{cleanup_block}
            }}

            post_remove() {{
                post_install
            }}
            ''')
        else:
            script += dedent('''\

            post_remove() {
                post_install
            }
            ''')

        return script

    def _build_package(self) -> Path:
        """Build the .pkg.tar.zst package."""
        
        # Determine output format based on file extension
        if str(self.output_path).endswith(".pkg.tar.zst"):
            compression = "zst"
        elif str(self.output_path).endswith(".pkg.tar.xz"):
            compression = "xz"
        elif str(self.output_path).endswith(".pkg.tar.gz"):
            compression = "gz"
        else:
            compression = "zst"
            self.output_path = Path(str(self.output_path) + ".pkg.tar.zst")
        
        # Check for compression tool availability
        if compression == "zst" and not self._has_command("zstd"):
            # Fall back to xz
            compression = "xz"
            self.output_path = Path(str(self.output_path).replace(".tar.zst", ".tar.xz"))
        
        # Create tar archive
        tar_path = self.output_path.with_suffix("")  # Remove compression extension
        if compression != "":
            tar_path = Path(str(self.output_path).rsplit(".", 1)[0])
        
        # Build with bsdtar if available (better compatibility), else use Python tarfile
        if self._has_command("bsdtar"):
            return self._build_with_bsdtar(compression)
        else:
            return self._build_with_python(compression)

    def _build_with_bsdtar(self, compression: str) -> Path:
        """Build using bsdtar (preferred method)."""
        
        # Compression flag mapping
        comp_flags = {
            "zst": ["--zstd"],
            "xz": ["-J"],
            "gz": ["-z"],
        }
        
        # Get all files, with .PKGINFO and .INSTALL first
        files = [".PKGINFO"]
        if (self.staging_dir / ".INSTALL").exists():
            files.append(".INSTALL")
        
        # Add all other files
        for item in self.staging_dir.iterdir():
            if item.name not in (".PKGINFO", ".INSTALL", ".MTREE"):
                files.append(item.name)
        
        try:
            cmd = [
                "bsdtar",
                "-cf", str(self.output_path),
                *comp_flags.get(compression, []),
                "-C", str(self.staging_dir),
                *files,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise BuildError(f"bsdtar failed: {e.stderr.decode()}")
        
        return self.output_path

    def _build_with_python(self, compression: str) -> Path:
        """Build using Python tarfile module."""
        
        # Python tarfile doesn't support zstd natively
        # We'll create uncompressed tar then compress
        
        tar_path = self.output_path.parent / f"{self.output_path.stem}.tar"
        
        with tarfile.open(tar_path, "w") as tar:
            # Add .PKGINFO first
            tar.add(self.staging_dir / ".PKGINFO", arcname=".PKGINFO")
            
            # Add .INSTALL if exists
            install_path = self.staging_dir / ".INSTALL"
            if install_path.exists():
                tar.add(install_path, arcname=".INSTALL")
            
            # Add all other files
            for item in self.staging_dir.iterdir():
                if item.name not in (".PKGINFO", ".INSTALL", ".MTREE"):
                    tar.add(item, arcname=item.name)
        
        # Compress
        if compression == "zst":
            subprocess.run(
                ["zstd", "-19", "--rm", str(tar_path), "-o", str(self.output_path)],
                check=True,
                capture_output=True,
            )
        elif compression == "xz":
            subprocess.run(
                ["xz", "-9", str(tar_path)],
                check=True,
                capture_output=True,
            )
            tar_path.with_suffix(".tar.xz").rename(self.output_path)
        elif compression == "gz":
            subprocess.run(
                ["gzip", "-9", str(tar_path)],
                check=True,
                capture_output=True,
            )
            tar_path.with_suffix(".tar.gz").rename(self.output_path)
        
        return self.output_path

    def _has_command(self, cmd: str) -> bool:
        """Check if a command is available."""
        try:
            subprocess.run(
                ["which", cmd],
                capture_output=True,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


def generate_pkgbuild(spec) -> str:
    """
    Generate a PKGBUILD file for building with makepkg.
    
    This is an alternative to direct package creation - useful for
    submitting to AUR or building in a clean chroot.
    """
    from uncork.spec import PackageSpec, WineMode
    
    if not isinstance(spec, PackageSpec):
        raise TypeError("Expected PackageSpec")
    
    depends = ["'wine'"] if spec.wine.mode == WineMode.SYSTEM else []
    if spec.install.use_overlay:
        depends.append("'fuse-overlayfs'")
    
    depends_str = " ".join(depends)
    
    optdepends = [
        "'winetricks: for additional Windows components'",
        "'lib32-vulkan-icd-loader: for DXVK support'",
    ]
    optdepends_str = "\n    ".join(optdepends)
    
    pkgbuild = dedent(f'''\
        # Maintainer: {spec.app.maintainer or "Your Name <your@email.com>"}
        
        pkgname={spec.app.name}
        pkgver={spec.app.version}
        pkgrel=1
        pkgdesc="{spec.app.description}"
        arch=('x86_64')
        url="{spec.app.homepage or ''}"
        license=('{spec.app.license}')
        depends=({depends_str})
        optdepends=(
            {optdepends_str}
        )
        source=("$pkgname-$pkgver.tar.gz")  # Update with actual source
        sha256sums=('SKIP')  # Update with actual checksum
        
        package() {{
            cd "$srcdir"
            
            # Install to /opt
            install -dm755 "$pkgdir/opt/$pkgname"
            cp -r prefix-template "$pkgdir/opt/$pkgname/"
            cp -r bin "$pkgdir/opt/$pkgname/"
            cp manifest.json "$pkgdir/opt/$pkgname/"
            
            # Install icons
            if [[ -d icons ]]; then
                for icon in icons/*.png; do
                    size=$(basename "$icon" .png | grep -oE '[0-9]+$')
                    if [[ -n "$size" ]]; then
                        install -Dm644 "$icon" "$pkgdir/usr/share/icons/hicolor/${{size}}x${{size}}/apps/$pkgname.png"
                    fi
                done
            fi
            
            # Install desktop files
            install -dm755 "$pkgdir/usr/share/applications"
            cp share/applications/*.desktop "$pkgdir/usr/share/applications/" 2>/dev/null || true
            
            # Create /usr/bin symlink
            install -dm755 "$pkgdir/usr/bin"
            ln -s "/opt/$pkgname/bin/main" "$pkgdir/usr/bin/$pkgname"
        }}
    ''')
    
    return pkgbuild
