"""
RPM package builder.

Creates .rpm packages using rpmbuild or fpm if available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from uncork.builder import BuildError, FormatBuilder
from uncork.spec import WineMode

if TYPE_CHECKING:
    from rich.progress import Progress


class RpmBuilder(FormatBuilder):
    """Build RPM packages."""

    def build(self, progress: Progress | None = None) -> Path:
        """Build .rpm package."""
        
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Try fpm first (easier), fall back to rpmbuild
        if self._has_fpm():
            return self._build_with_fpm()
        elif self._has_rpmbuild():
            return self._build_with_rpmbuild()
        else:
            raise BuildError(
                "Neither fpm nor rpmbuild found. Install one of:\n"
                "  gem install fpm\n"
                "  dnf install rpm-build\n"
                "  apt install rpm"
            )

    def _build_with_fpm(self) -> Path:
        """Build using fpm (Effing Package Management)."""
        
        # Build dependency list
        deps = []
        if self.spec.wine.mode == WineMode.SYSTEM:
            deps.extend(["-d", "wine"])
        if self.spec.install.use_overlay:
            deps.extend(["-d", "fuse-overlayfs"])
        
        cmd = [
            "fpm",
            "-s", "dir",
            "-t", "rpm",
            "-n", self.package_name,
            "-v", self.package_version,
            "--description", self.package_description,
            "--architecture", "x86_64",
            "--license", self.spec.app.license,
            "-p", str(self.output_path),
            *deps,
            # Post-install script
            "--after-install", self._create_post_script("install"),
            "--after-remove", self._create_post_script("remove"),
            # Input directory
            "-C", str(self.staging_dir),
            ".",
        ]
        
        if self.spec.app.homepage:
            cmd.extend(["--url", self.spec.app.homepage])
        if self.spec.app.maintainer:
            cmd.extend(["--maintainer", self.spec.app.maintainer])
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise BuildError(f"fpm failed: {e.stderr.decode()}")
        
        return self.output_path

    def _build_with_rpmbuild(self) -> Path:
        """Build using rpmbuild."""
        
        # Create rpmbuild directory structure
        with tempfile.TemporaryDirectory(prefix="uncork-rpm-") as tmpdir:
            rpmbuild_dir = Path(tmpdir)
            
            for subdir in ["BUILD", "RPMS", "SOURCES", "SPECS", "SRPMS"]:
                (rpmbuild_dir / subdir).mkdir()
            
            # Create tarball of staged files
            source_name = f"{self.package_name}-{self.package_version}"
            source_tar = rpmbuild_dir / "SOURCES" / f"{source_name}.tar.gz"
            
            # Create source tarball
            import tarfile
            with tarfile.open(source_tar, "w:gz") as tar:
                tar.add(self.staging_dir, arcname=source_name)
            
            # Generate spec file
            spec_content = self._generate_spec_file(source_name)
            spec_path = rpmbuild_dir / "SPECS" / f"{self.package_name}.spec"
            spec_path.write_text(spec_content)
            
            # Run rpmbuild
            try:
                subprocess.run(
                    [
                        "rpmbuild",
                        "-bb",
                        f"--define=_topdir {rpmbuild_dir}",
                        str(spec_path),
                    ],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                raise BuildError(f"rpmbuild failed: {e.stderr.decode()}")
            
            # Find and move output RPM
            rpm_dir = rpmbuild_dir / "RPMS" / "x86_64"
            for rpm in rpm_dir.glob("*.rpm"):
                shutil.move(rpm, self.output_path)
                return self.output_path
            
            raise BuildError("rpmbuild did not produce an RPM file")

    def _generate_spec_file(self, source_name: str) -> str:
        """Generate RPM spec file."""
        
        requires = []
        if self.spec.wine.mode == WineMode.SYSTEM:
            requires.append("wine")
        if self.spec.install.use_overlay:
            requires.append("fuse-overlayfs")
        
        requires_str = "\n".join(f"Requires: {r}" for r in requires) if requires else ""
        
        return dedent(f'''\
            Name:           {self.package_name}
            Version:        {self.package_version}
            Release:        1%{{?dist}}
            Summary:        {self.spec.app.display_name}
            
            License:        {self.spec.app.license}
            URL:            {self.spec.app.homepage or ""}
            Source0:        {source_name}.tar.gz
            
            BuildArch:      x86_64
            {requires_str}
            
            %description
            {self.package_description}
            
            This is a Windows application packaged to run via Wine.
            
            %prep
            %setup -q
            
            %install
            mkdir -p %{{buildroot}}
            cp -r * %{{buildroot}}/
            
            %post
            /usr/bin/update-desktop-database /usr/share/applications &>/dev/null || :
            /usr/bin/gtk-update-icon-cache /usr/share/icons/hicolor &>/dev/null || :
            
            %postun
            /usr/bin/update-desktop-database /usr/share/applications &>/dev/null || :
            /usr/bin/gtk-update-icon-cache /usr/share/icons/hicolor &>/dev/null || :
            
            %files
            /opt/{self.package_name}
            /usr/bin/{self.package_name}
            /usr/share/applications/{self.package_name}.desktop
            /usr/share/icons/hicolor/*/apps/{self.package_name}.png
            
            %changelog
            * $(date "+%a %b %d %Y") {self.spec.app.maintainer or "Package Builder"} - {self.package_version}-1
            - Initial package
        ''')

    def _create_post_script(self, action: str) -> str:
        """Create a temporary post-install/remove script and return path."""
        import tempfile
        
        script = dedent('''\
            #!/bin/bash
            update-desktop-database /usr/share/applications &>/dev/null || true
            gtk-update-icon-cache /usr/share/icons/hicolor &>/dev/null || true
        ''')
        
        fd, path = tempfile.mkstemp(suffix=f"-{action}.sh")
        os.write(fd, script.encode())
        os.close(fd)
        os.chmod(path, 0o755)
        
        return path

    def _has_fpm(self) -> bool:
        """Check if fpm is available."""
        try:
            subprocess.run(["fpm", "--version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _has_rpmbuild(self) -> bool:
        """Check if rpmbuild is available."""
        try:
            subprocess.run(["rpmbuild", "--version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
