"""
Debian .deb package builder.

Creates Debian packages using dpkg-deb or ar+tar if dpkg not available.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from uncork.builder import BuildError, FormatBuilder
from uncork.spec import WineMode

if TYPE_CHECKING:
    from rich.progress import Progress


class DebBuilder(FormatBuilder):
    """Build Debian .deb packages."""

    def build(self, progress: Progress | None = None) -> Path:
        """Build .deb package."""
        
        # Create DEBIAN control directory
        debian_dir = self.staging_dir / "DEBIAN"
        debian_dir.mkdir(exist_ok=True)
        
        # Write control file
        control_content = self._generate_control()
        (debian_dir / "control").write_text(control_content)
        
        # Write postinst script
        postinst = self._generate_postinst()
        postinst_path = debian_dir / "postinst"
        postinst_path.write_text(postinst)
        postinst_path.chmod(0o755)
        
        # Write postrm script
        postrm = self._generate_postrm()
        postrm_path = debian_dir / "postrm"
        postrm_path.write_text(postrm)
        postrm_path.chmod(0o755)
        
        # Build the package
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self._has_dpkg_deb():
            return self._build_with_dpkg()
        else:
            return self._build_manual()

    def _generate_control(self) -> str:
        """Generate Debian control file."""
        
        # Determine dependencies
        depends = []
        recommends = []
        
        if self.spec.wine.mode == WineMode.SYSTEM:
            depends.append("wine | wine-stable | wine-staging | winehq-stable | winehq-staging")
        
        if self.spec.install.use_overlay:
            depends.append("fuse-overlayfs")
        
        # Graphics recommendations
        recommends.extend([
            "libvulkan1",
            "mesa-vulkan-drivers | nvidia-vulkan-icd",
        ])
        
        depends_str = ", ".join(depends) if depends else ""
        recommends_str = ", ".join(recommends) if recommends else ""
        
        # Calculate installed size (in KB)
        installed_size = sum(
            f.stat().st_size for f in self.staging_dir.rglob("*") if f.is_file()
        ) // 1024
        
        control = dedent(f'''\
            Package: {self.package_name}
            Version: {self.package_version}
            Section: games
            Priority: optional
            Architecture: amd64
            Installed-Size: {installed_size}
            Maintainer: {self.spec.app.maintainer or "Unknown <unknown@example.com>"}
            Description: {self.spec.app.display_name}
             {self.package_description}
             .
             This is a Windows application packaged to run via Wine.
        ''')
        
        if depends_str:
            control += f"Depends: {depends_str}\n"
        if recommends_str:
            control += f"Recommends: {recommends_str}\n"
        if self.spec.app.homepage:
            control += f"Homepage: {self.spec.app.homepage}\n"
        
        return control

    def _generate_postinst(self) -> str:
        """Generate post-installation script."""
        return dedent('''\
            #!/bin/bash
            set -e
            
            # Update desktop database
            if command -v update-desktop-database &>/dev/null; then
                update-desktop-database -q /usr/share/applications || true
            fi
            
            # Update icon cache
            if command -v gtk-update-icon-cache &>/dev/null; then
                gtk-update-icon-cache -q /usr/share/icons/hicolor || true
            fi
            
            exit 0
        ''')

    def _generate_postrm(self) -> str:
        """Generate post-removal script."""
        return dedent('''\
            #!/bin/bash
            set -e
            
            # Update desktop database
            if command -v update-desktop-database &>/dev/null; then
                update-desktop-database -q /usr/share/applications || true
            fi
            
            # Update icon cache
            if command -v gtk-update-icon-cache &>/dev/null; then
                gtk-update-icon-cache -q /usr/share/icons/hicolor || true
            fi
            
            exit 0
        ''')

    def _has_dpkg_deb(self) -> bool:
        """Check if dpkg-deb is available."""
        try:
            subprocess.run(
                ["dpkg-deb", "--version"],
                capture_output=True,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _build_with_dpkg(self) -> Path:
        """Build using dpkg-deb."""
        try:
            subprocess.run(
                ["dpkg-deb", "--build", "--root-owner-group", str(self.staging_dir), str(self.output_path)],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise BuildError(f"dpkg-deb failed: {e.stderr.decode()}")
        
        return self.output_path

    def _build_manual(self) -> Path:
        """
        Build .deb manually using ar and tar.
        
        A .deb is an ar archive containing:
        - debian-binary (text: "2.0\n")
        - control.tar.gz (the DEBIAN directory)
        - data.tar.gz (the actual files)
        """
        import struct
        
        # Create control.tar.gz
        control_tar = BytesIO()
        with tarfile.open(fileobj=control_tar, mode="w:gz") as tar:
            debian_dir = self.staging_dir / "DEBIAN"
            for item in debian_dir.iterdir():
                tar.add(item, arcname=f"./{item.name}")
        control_tar_data = control_tar.getvalue()
        
        # Create data.tar.gz (everything except DEBIAN)
        data_tar = BytesIO()
        with tarfile.open(fileobj=data_tar, mode="w:gz") as tar:
            for item in self.staging_dir.iterdir():
                if item.name != "DEBIAN":
                    tar.add(item, arcname=f"./{item.name}")
        data_tar_data = data_tar.getvalue()
        
        # Create ar archive
        with open(self.output_path, "wb") as f:
            # AR magic
            f.write(b"!<arch>\n")
            
            # debian-binary
            self._write_ar_entry(f, "debian-binary", b"2.0\n")
            
            # control.tar.gz
            self._write_ar_entry(f, "control.tar.gz", control_tar_data)
            
            # data.tar.gz
            self._write_ar_entry(f, "data.tar.gz", data_tar_data)
        
        return self.output_path

    def _write_ar_entry(self, f, name: str, data: bytes) -> None:
        """Write an entry to an ar archive."""
        import time
        
        # AR header format: name(16) mtime(12) uid(6) gid(6) mode(8) size(10) magic(2)
        name_bytes = name.encode().ljust(16)[:16]
        mtime = str(int(time.time())).encode().ljust(12)
        uid = b"0".ljust(6)
        gid = b"0".ljust(6)
        mode = b"100644".ljust(8)
        size = str(len(data)).encode().ljust(10)
        magic = b"`\n"
        
        f.write(name_bytes + mtime + uid + gid + mode + size + magic)
        f.write(data)
        
        # Pad to even byte boundary
        if len(data) % 2:
            f.write(b"\n")
