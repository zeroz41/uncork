"""
Uncork - Uncork your Wine apps into portable Linux system packages.

Example usage:
    from uncork import PrefixCapture, PackageSpec, PackageBuilder

    # Capture and normalize a prefix
    capture = PrefixCapture("/home/user/.wine-myapp")
    capture.add_executable("main", "My App", "drive_c/Program Files/MyApp/app.exe")
    capture.set_wine_mode("system", min_version="9.0")
    capture.normalize()
    capture.export("/tmp/myapp-intermediate")

    # Build packages
    spec = PackageSpec.load("/tmp/myapp-intermediate")
    builder = PackageBuilder(spec)
    builder.build_deb("/out/myapp.deb")
    builder.build_pacman("/out/myapp.pkg.tar.zst")
"""

from uncork.capture import PrefixCapture
from uncork.analysis import PrefixAnalysis
from uncork.spec import PackageSpec, WineMode, Executable
from uncork.builder import PackageBuilder

__version__ = "0.1.0"
__all__ = [
    "PrefixCapture",
    "PrefixAnalysis", 
    "PackageSpec",
    "PackageBuilder",
    "WineMode",
    "Executable",
]
