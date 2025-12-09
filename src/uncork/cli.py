"""
Command-line interface for uncork.

Usage:
    uncork analyze /path/to/prefix
    uncork capture /path/to/prefix -o ./intermediate --exe "My App:drive_c/Program Files/App/app.exe"
    uncork build ./intermediate -o ./output --format deb --format pacman
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from uncork import __version__


console = Console()


@click.group()
@click.version_option(__version__)
def cli():
    """Uncork - Uncork your Wine apps into Linux packages."""
    pass


@cli.command()
@click.argument("prefix_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def analyze(prefix_path: Path):
    """Analyze a Wine prefix and show its contents."""
    from uncork.analysis import PrefixAnalyzer
    
    with console.status("Analyzing prefix..."):
        analyzer = PrefixAnalyzer(prefix_path)
        result = analyzer.analyze()
    
    # Display results
    console.print()
    console.print(f"[bold]Prefix:[/bold] {result.prefix_path}")
    console.print()
    
    if not result.is_valid_prefix:
        console.print("[red]Not a valid Wine prefix![/red]")
        for warning in result.warnings:
            console.print(f"  [yellow]⚠[/yellow] {warning}")
        return
    
    # Basic info table
    info_table = Table(show_header=False, box=None)
    info_table.add_column("Property", style="bold")
    info_table.add_column("Value")
    
    info_table.add_row("Architecture", result.arch)
    info_table.add_row("Wine Version", result.wine_version or "Unknown")
    info_table.add_row("User", result.detected_user or "Unknown")
    info_table.add_row("Total Size", _format_size(result.total_size))
    info_table.add_row("DXVK", "✓ Installed" if result.has_dxvk else "✗ Not installed")
    info_table.add_row("VKD3D", "✓ Installed" if result.has_vkd3d else "✗ Not installed")
    
    console.print(info_table)
    console.print()
    
    # Executables
    if result.executables:
        console.print("[bold]Detected Executables:[/bold]")
        exe_table = Table()
        exe_table.add_column("Name")
        exe_table.add_column("Path")
        exe_table.add_column("Size", justify="right")
        exe_table.add_column("Main App?")
        
        for exe in result.executables[:15]:  # Limit to top 15
            exe_table.add_row(
                exe.name,
                str(Path(exe.path).parent),
                _format_size(exe.size),
                "✓" if exe.probable_app else "",
            )
        
        console.print(exe_table)
        
        if len(result.executables) > 15:
            console.print(f"  ... and {len(result.executables) - 15} more")
    
    # Warnings
    if result.warnings:
        console.print()
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for warning in result.warnings:
            console.print(f"  [yellow]⚠[/yellow] {warning}")


@cli.command()
@click.argument("prefix_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path),
              help="Output directory for intermediate format")
@click.option("--exe", "-e", multiple=True,
              help="Executable: 'Name:path[:command]' - command is optional (can specify multiple)")
@click.option("--icon", "-i", multiple=True,
              help="Custom icon: 'command:path/to/icon.png' (can specify multiple)")
@click.option("--exe-desc", multiple=True,
              help="Per-executable description: 'command:description' (can specify multiple)")
@click.option("--exe-args", multiple=True,
              help="Per-executable arguments: 'command:args' (can specify multiple)")
@click.option("--exe-wmclass", multiple=True,
              help="Override WM_CLASS/StartupWMClass: 'command:wmclass' (can specify multiple)")
@click.option("--app-name", help="Application/package name (default: derived from first executable)")
@click.option("--name", help="Deprecated: use --app-name instead")
@click.option("--version", "pkg_version", default="1.0.0", help="Package version")
@click.option("--wine-mode", type=click.Choice(["system", "bundled"]), default="system",
              help="Wine runtime mode")
@click.option("--wine-path", type=click.Path(exists=True, path_type=Path),
              help="Path to Wine installation (for bundled mode)")
@click.option("--min-wine-version", default="9.0",
              help="Minimum Wine version (for system mode)")
@click.option("--overlay/--no-overlay", default=False,
              help="Use fuse-overlayfs instead of copying prefix")
@click.option("--no-wineboot-update", is_flag=True, default=False,
              help="Skip running wineboot -u before capture (not recommended for overlay mode)")
def capture(
    prefix_path: Path,
    output: Path,
    exe: tuple[str, ...],
    icon: tuple[str, ...],
    exe_desc: tuple[str, ...],
    exe_args: tuple[str, ...],
    exe_wmclass: tuple[str, ...],
    app_name: str | None,
    name: str | None,
    pkg_version: str,
    wine_mode: str,
    wine_path: Path | None,
    min_wine_version: str,
    overlay: bool,
    no_wineboot_update: bool,
):
    """Capture and normalize a Wine prefix."""
    from uncork.capture import PrefixCapture, CaptureError

    if not exe:
        console.print("[red]Error:[/red] At least one executable required. Use --exe 'Name:path[:command]'")
        console.print()
        console.print("Examples:")
        console.print("  uncork capture ~/.wine -o ./output --exe 'My Game:drive_c/Games/game.exe'")
        console.print()
        console.print("  uncork capture ~/.wine -o ./output \\")
        console.print("    --exe 'Game:drive_c/game.exe:mygame' \\")
        console.print("    --exe 'Settings:drive_c/settings.exe:mygame-settings' \\")
        console.print("    --exe-desc 'mygame:Launch the game in fullscreen mode' \\")
        console.print("    --exe-desc 'mygame-settings:Configure game settings' \\")
        console.print("    --exe-args 'mygame:--fullscreen'")
        sys.exit(1)

    if wine_mode == "bundled" and not wine_path:
        console.print("[red]Error:[/red] --wine-path required for bundled mode")
        sys.exit(1)

    # Handle deprecated --name option
    if name and not app_name:
        console.print("[yellow]Warning:[/yellow] --name is deprecated, use --app-name instead")
        app_name = name

    try:
        capture_obj = PrefixCapture(prefix_path, update_prefix=not no_wineboot_update)

        # Parse custom icons
        custom_icons = {}
        for icon_spec in icon:
            if ":" not in icon_spec:
                console.print(f"[red]Error:[/red] Invalid icon format: {icon_spec}")
                console.print("Expected format: 'command:path/to/icon.png'")
                sys.exit(1)

            icon_id, icon_path = icon_spec.split(":", 1)
            custom_icons[icon_id.strip()] = Path(icon_path.strip())

        # Parse per-executable descriptions
        exe_descriptions = {}
        for desc_spec in exe_desc:
            if ":" not in desc_spec:
                console.print(f"[red]Error:[/red] Invalid exe-desc format: {desc_spec}")
                console.print("Expected format: 'command:description'")
                sys.exit(1)

            cmd, description = desc_spec.split(":", 1)
            exe_descriptions[cmd.strip()] = description.strip()

        # Parse per-executable arguments
        exe_arguments = {}
        for args_spec in exe_args:
            if ":" not in args_spec:
                console.print(f"[red]Error:[/red] Invalid exe-args format: {args_spec}")
                console.print("Expected format: 'command:args'")
                sys.exit(1)

            cmd, arguments = args_spec.split(":", 1)
            exe_arguments[cmd.strip()] = arguments.strip()

        # Parse per-executable WM_CLASS overrides
        exe_wmclasses = {}
        for wm_spec in exe_wmclass:
            if ":" not in wm_spec:
                console.print(f"[red]Error:[/red] Invalid exe-wmclass format: {wm_spec}")
                console.print("Expected format: 'command:wmclass'")
                sys.exit(1)

            cmd, wmclass_val = wm_spec.split(":", 1)
            exe_wmclasses[cmd.strip()] = wmclass_val.strip()

        # Track exe IDs to handle duplicates
        exe_id_counts: dict[str, int] = {}

        # Parse and add executables
        for i, exe_spec in enumerate(exe):
            parts = exe_spec.split(":")
            if len(parts) < 2:
                console.print(f"[red]Error:[/red] Invalid executable format: {exe_spec}")
                console.print("Expected format: 'Display Name:path/to/file.exe[:command]'")
                sys.exit(1)

            exe_name = parts[0].strip()
            exe_path = parts[1].strip()
            exe_command = parts[2].strip() if len(parts) >= 3 else None

            # Generate unique ID from name
            base_id = re.sub(r'[^a-z0-9]', '-', exe_name.lower()).strip('-')

            # Handle duplicate IDs by appending number
            if base_id in exe_id_counts:
                exe_id_counts[base_id] += 1
                exe_id = f"{base_id}-{exe_id_counts[base_id]}"
            else:
                exe_id_counts[base_id] = 0
                exe_id = base_id

            # Determine the final command name (same logic as builder.py)
            if exe_command:
                # Explicit command name provided
                final_command = exe_command
            elif i == 0:
                # First executable gets app name (will be set later if app_name is provided)
                final_command = app_name if app_name else base_id
            else:
                # Additional executables get app-name-exe-id format
                final_command = f"{app_name if app_name else base_id}-{exe_id}" if i > 0 else (app_name if app_name else base_id)

            # Look up description, args, and icon by command name
            exe_description = exe_descriptions.get(final_command)
            exe_args_str = exe_arguments.get(final_command, "")
            exe_custom_icon = custom_icons.get(final_command)
            exe_wmclass_override = exe_wmclasses.get(final_command)

            capture_obj.add_executable(
                id=exe_id,
                name=exe_name,
                path=exe_path,
                command=exe_command,
                args=exe_args_str,
                description=exe_description,
                custom_icon_path=exe_custom_icon,
                wm_class=exe_wmclass_override,
            )
        
        # Set Wine mode
        if wine_mode == "bundled":
            capture_obj.set_wine_mode("bundled", bundled_wine_path=str(wine_path))
        else:
            capture_obj.set_wine_mode("system", min_version=min_wine_version)

        # Set metadata
        if app_name:
            capture_obj.set_app_metadata(
                name=app_name,
                display_name=app_name,
                version=pkg_version,
            )
        
        # Set install config
        capture_obj.set_install_config(use_overlay=overlay)
        
        # Run capture
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            spec = capture_obj.export(output, progress=progress)
        
        console.print()
        console.print(f"[green]✓[/green] Prefix captured to: {output}")
        console.print()
        console.print("Next step: build packages with:")
        console.print(f"  [dim]uncork build {output} -o ./packages --format deb --format pacman[/dim]")
        
    except CaptureError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("intermediate_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path),
              help="Output directory for packages")
@click.option("--format", "-f", "formats", multiple=True,
              type=click.Choice(["deb", "pacman", "rpm", "directory"]),
              help="Package formats to build (can specify multiple)")
def build(intermediate_path: Path, output: Path, formats: tuple[str, ...]):
    """Build packages from intermediate format."""
    from uncork.builder import PackageBuilder, BuildError
    from uncork.spec import PackageSpec
    
    if not formats:
        console.print("[red]Error:[/red] At least one format required. Use --format")
        sys.exit(1)
    
    output.mkdir(parents=True, exist_ok=True)
    
    try:
        spec = PackageSpec.load(intermediate_path)
        builder = PackageBuilder(spec, intermediate_path=intermediate_path)
        
        console.print(f"Building [bold]{spec.app.display_name}[/bold] v{spec.app.version}")
        console.print()
        
        for fmt in formats:
            with console.status(f"Building {fmt} package..."):
                if fmt == "deb":
                    out_file = output / f"{spec.app.name}_{spec.app.version}_amd64.deb"
                    builder.build_deb(out_file)
                elif fmt == "pacman":
                    out_file = output / f"{spec.app.name}-{spec.app.version}-1-x86_64.pkg.tar.zst"
                    builder.build_pacman(out_file)
                elif fmt == "rpm":
                    out_file = output / f"{spec.app.name}-{spec.app.version}-1.x86_64.rpm"
                    builder.build_rpm(out_file)
                elif fmt == "directory":
                    out_file = output / f"{spec.app.name}-{spec.app.version}"
                    builder.build_directory(out_file)
            
            console.print(f"  [green]✓[/green] {fmt}: {out_file}")
        
        console.print()
        console.print("[green]Build complete![/green]")
        
    except BuildError as e:
        console.print(f"[red]Build error:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("intermediate_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def info(intermediate_path: Path):
    """Show information about an intermediate package."""
    from uncork.spec import PackageSpec
    
    spec = PackageSpec.load(intermediate_path)
    
    console.print()
    console.print(f"[bold]{spec.app.display_name}[/bold]")
    console.print(f"  Version: {spec.app.version}")
    console.print(f"  Package name: {spec.app.name}")
    console.print(f"  Description: {spec.app.description}")
    console.print()
    console.print("[bold]Wine Configuration:[/bold]")
    console.print(f"  Mode: {spec.wine.mode.value}")
    if spec.wine.mode.value == "system":
        console.print(f"  Min version: {spec.wine.system_min_version}")
    else:
        console.print(f"  Bundled path: {spec.wine.bundled_path}")
    console.print()
    console.print("[bold]Executables:[/bold]")
    for exe in spec.executables:
        console.print(f"  • {exe.name} ({exe.id})")
        console.print(f"    Path: {exe.path}")
    console.print()
    console.print("[bold]Installation:[/bold]")
    console.print(f"  System path: {spec.get_system_path()}")
    console.print(f"  User data: {spec.get_user_data_path()}")
    console.print(f"  Overlay mode: {'Yes' if spec.install.use_overlay else 'No'}")


def _format_size(size: int) -> str:
    """Format byte size for display."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main():
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
