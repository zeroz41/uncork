"""
Icon extraction from Windows executables.

Uses icoextract library to pull icons from PE files,
then Pillow to convert to PNG at various sizes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

# Standard icon sizes for Linux desktop integration
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def extract_icon(
    exe_path: Path | str,
    output_path: Path | str,
    size: int = 256,
) -> Path:
    """
    Extract icon from a Windows executable.
    
    Args:
        exe_path: Path to .exe file
        output_path: Path for output PNG file
        size: Desired icon size (will use closest available)
        
    Returns:
        Path to extracted icon
        
    Raises:
        IconExtractionError: If extraction fails
    """
    exe_path = Path(exe_path)
    output_path = Path(output_path)
    
    try:
        from icoextract import IconExtractor
        from PIL import Image
    except ImportError as e:
        raise IconExtractionError(
            f"Missing dependency: {e}. Install with: pip install icoextract Pillow"
        )
    
    try:
        extractor = IconExtractor(str(exe_path))
    except Exception as e:
        raise IconExtractionError(f"Failed to open executable: {e}")
    
    # Extract to temporary ICO
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ico", delete=False) as tmp:
        tmp_ico = Path(tmp.name)
    
    try:
        extractor.export_icon(str(tmp_ico), num=0)
        
        # Open ICO and find best size
        with Image.open(tmp_ico) as ico:
            # ICO files can contain multiple sizes
            # Find the largest one up to our target
            best_size = None
            best_frame = 0
            
            for i in range(getattr(ico, 'n_frames', 1)):
                ico.seek(i)
                w, h = ico.size
                if best_size is None or (w <= size and w > best_size):
                    best_size = w
                    best_frame = i
            
            ico.seek(best_frame)
            
            # Convert to RGBA and resize if needed
            img = ico.convert("RGBA")
            if img.size[0] != size:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            
            # Save as PNG
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "PNG")
            
    finally:
        tmp_ico.unlink(missing_ok=True)
    
    return output_path


def extract_icon_sizes(
    exe_path: Path | str,
    output_dir: Path | str,
    name: str,
    sizes: Sequence[int] = ICON_SIZES,
) -> dict[int, Path]:
    """
    Extract icon at multiple sizes for desktop integration.
    
    Args:
        exe_path: Path to .exe file
        output_dir: Directory for output PNGs
        name: Base name for icon files (e.g., "myapp")
        sizes: List of sizes to generate
        
    Returns:
        Dict mapping size to output path
    """
    exe_path = Path(exe_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results = {}
    
    try:
        from icoextract import IconExtractor
        from PIL import Image
    except ImportError as e:
        raise IconExtractionError(
            f"Missing dependency: {e}. Install with: pip install icoextract Pillow"
        )
    
    try:
        extractor = IconExtractor(str(exe_path))
    except Exception as e:
        raise IconExtractionError(f"Failed to open executable: {e}")
    
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ico", delete=False) as tmp:
        tmp_ico = Path(tmp.name)
    
    try:
        extractor.export_icon(str(tmp_ico), num=0)
        
        with Image.open(tmp_ico) as ico:
            # Find the largest available size
            largest = None
            largest_frame = 0
            
            for i in range(getattr(ico, 'n_frames', 1)):
                ico.seek(i)
                w, h = ico.size
                if largest is None or w > largest:
                    largest = w
                    largest_frame = i
            
            ico.seek(largest_frame)
            base_img = ico.convert("RGBA")
            
            # Generate each requested size
            for size in sizes:
                output_path = output_dir / f"{name}-{size}.png"
                
                if size == base_img.size[0]:
                    img = base_img
                else:
                    img = base_img.resize((size, size), Image.Resampling.LANCZOS)
                
                img.save(output_path, "PNG")
                results[size] = output_path
                
    finally:
        tmp_ico.unlink(missing_ok=True)
    
    return results


class IconExtractionError(Exception):
    """Error extracting icon from executable."""
    pass
