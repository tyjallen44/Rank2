#!/usr/bin/env python
"""
Rank2 — market intelligence pipeline.

Usage (command-line):
    python run_analysis.py                      # file picker opens
    python run_analysis.py entities.csv         # use a specific file
    python run_analysis.py entities.csv --output-dir ~/Desktop/reports
"""
from __future__ import annotations

# When running from a PyInstaller bundle, point Playwright at the bundled
# Chromium headless shell and driver before anything else imports playwright.
import os as _os, sys as _sys, tempfile as _tempfile
if getattr(_sys, "frozen", False):
    _bundle = _sys._MEIPASS
    _os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _bundle)
    # Prevent Playwright's Node.js driver from trying to auto-update npm packages
    _os.environ.setdefault("NPM_CONFIG_PREFIX", _tempfile.mkdtemp(prefix="rank2-npm-"))
    _os.environ.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")

import argparse
import json
import os
import sys
from pathlib import Path


# ── Config file (stores API key between runs) ────────────────────────────────

def _config_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Rank2"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Rank2"
    else:
        base = Path.home() / ".config" / "rank2"
    return base / "config.json"


def _load_config() -> dict:
    p = _config_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_config(data: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


# ── Native dialog helpers ─────────────────────────────────────────────────────

def _choose_input_mode() -> str:
    """Ask the user whether to enter a location manually or choose a file.
    Returns 'manual' or 'file'."""
    if sys.platform == "darwin":
        return _choose_input_mode_macos()
    return _choose_input_mode_terminal()


def _choose_input_mode_macos() -> str:
    import subprocess
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        '  set r to choose from list '
        '{"Enter a Location", "Choose a File", "View History", "Quit"} '
        'with prompt "What would you like to do?" '
        'with title "Rank2" '
        'without multiple selections allowed and empty selection allowed\n'
        '  if r is false then return "Quit"\n'
        '  item 1 of r\n'
        'end tell'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return "quit"
    choice = r.stdout.strip()
    if "Enter a Location" in choice:
        return "manual"
    if "View History" in choice:
        return "history"
    if "Quit" in choice:
        return "quit"
    return "file"


def _choose_input_mode_terminal() -> str:
    print("How would you like to provide locations?")
    print("  1. Enter a location manually")
    print("  2. Choose a file")
    print("  3. View history")
    print("  4. Quit")
    choice = input("Enter 1, 2, 3, or 4 [2]: ").strip()
    if choice == "1":
        return "manual"
    if choice == "3":
        return "history"
    if choice == "4":
        return "quit"
    return "file"


# ── Single-instance lock ──────────────────────────────────────────────────────

def _lock_path() -> Path:
    return _config_path().parent / "rank2.pid"


def _is_analysis_running() -> bool:
    """Return True if another Rank2 process is currently running an analysis."""
    lock = _lock_path()
    if not lock.exists():
        return False
    try:
        pid = int(lock.read_text().strip())
        if pid == os.getpid():
            return False
        os.kill(pid, 0)  # signal 0: check existence without killing
        return True
    except ProcessLookupError:
        lock.unlink()
        return False
    except Exception:
        return False


def _acquire_lock() -> None:
    p = _lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()))


def _release_lock() -> None:
    try:
        _lock_path().unlink()
    except Exception:
        pass


def _show_busy_dialog() -> None:
    msg = (
        "An analysis is already in progress.\n\n"
        "Please wait for it to finish before starting a new one."
    )
    if sys.platform == "darwin":
        import subprocess
        subprocess.run([
            "osascript", "-e",
            f'tell application "System Events" to activate\n'
            f'display dialog "{msg}" with title "Rank2 — Busy" '
            f'buttons {{"OK"}} default button "OK"',
        ])
    else:
        print(f"\n{msg}\n", file=sys.stderr)


def _show_history(console: "Console") -> None:
    """Display past runs and, on macOS, let the user open one."""
    from perception.db import query_history
    from rich.table import Table
    from rich import box

    runs = query_history()
    if not runs:
        console.print("[yellow]No reports found in history.[/yellow]")
        return

    table = Table(box=box.ROUNDED, border_style="dark_sea_green4", show_header=True)
    table.add_column("#",          justify="right", style="dim", width=3)
    table.add_column("Date",       style="dim")
    table.add_column("Location",   style="bold white")
    table.add_column("Specialty",  style="dim")
    table.add_column("Providers",  justify="right")
    table.add_column("PDF",        justify="center")

    for i, run in enumerate(runs, 1):
        has_pdf = bool(run["pdf_path"] and Path(run["pdf_path"]).exists())
        table.add_row(
            str(i),
            str(run["generated_at"]),
            run["location"],
            run["specialty"] or "—",
            str(run["provider_count"]),
            "[green]✓[/green]" if has_pdf else "[dim]—[/dim]",
        )

    console.print()
    console.print(table)

    if sys.platform == "darwin":
        _open_history_macos(runs)
    else:
        _open_history_terminal(runs, console)


def _open_history_macos(runs: list) -> None:
    import subprocess

    labels = []
    for run in runs:
        spec = run["specialty"] or "hospitals"
        has_pdf = bool(run["pdf_path"] and Path(run["pdf_path"]).exists())
        marker = "  [no PDF]" if not has_pdf else ""
        labels.append(f"{run['generated_at']}  —  {run['location']}  —  {spec}{marker}")

    items = "{" + ", ".join(f'"{l}"' for l in labels) + "}"
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        f'  set r to choose from list {items} '
        'with prompt "Select a report to open:" with title "Rank2 — History"\n'
        '  if r is false then return ""\n'
        '  item 1 of r\n'
        'end tell'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    selected = result.stdout.strip()
    if not selected or selected == "false":
        return

    idx = next((i for i, l in enumerate(labels) if l == selected), None)
    if idx is None:
        return

    pdf = runs[idx].get("pdf_path")
    if pdf and Path(pdf).exists():
        subprocess.Popen(["open", pdf])
    else:
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to activate\n'
            'display dialog "PDF not found. It may have been moved or deleted." '
            'with title "Rank2" buttons {"OK"} default button "OK"',
        ])


def _open_history_terminal(runs: list, console: "Console") -> None:
    choice = input("\nEnter report # to open (or press Enter to skip): ").strip()
    if not choice:
        return
    try:
        idx = int(choice) - 1
        pdf = runs[idx].get("pdf_path")
    except (ValueError, IndexError):
        console.print("[red]Invalid selection.[/red]")
        return
    if pdf and Path(pdf).exists():
        _open_folder(Path(pdf).parent)
    else:
        console.print("[yellow]PDF not found — it may have been moved or deleted.[/yellow]")


def _prompt_location_macos() -> "tuple[str, str, str | None] | None":
    """Collect city, state, analysis type, specialty via AppleScript dialogs.
    Returns (city, state, specialty) or None if the user cancels."""
    import subprocess

    def _ask(prompt: str, default: str = "", last: bool = False) -> "str | None":
        btn = "Done" if last else "Next"
        script = (
            'tell application "System Events"\n'
            '  activate\n'
            f'  set r to display dialog "{prompt}" default answer "{default}" '
            f'with title "Rank2" buttons {{"Cancel", "{btn}"}} default button "{btn}"\n'
            '  if button returned of r is "Cancel" then error number -128\n'
            '  text returned of r\n'
            'end tell'
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _choose(prompt: str, options: "list[str]") -> "str | None":
        items = "{" + ", ".join(f'"{o}"' for o in options) + "}"
        script = (
            'tell application "System Events"\n'
            '  activate\n'
            f'  set r to choose from list {items} with prompt "{prompt}" with title "Rank2"\n'
            '  if r is false then error number -128\n'
            '  item 1 of r\n'
            'end tell'
        )
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or result.stdout.strip() == "false":
            return None
        return result.stdout.strip()

    city = _ask("City:")
    if not city:
        return None

    state = _ask("State (2-letter code, e.g. CA, TX, NY):")
    if not state:
        return None
    state = state.strip().upper()[:2]

    analysis_type = _choose("Analysis type:", ["Hospital market", "Specialty practice"])
    if not analysis_type:
        return None

    specialty = None
    if analysis_type == "Specialty practice":
        specialty = _ask("Specialty (e.g. Orthopedics, Cardiology, Dermatology):", last=True)
        if not specialty:
            return None

    return city, state, specialty


def _prompt_location_terminal() -> "tuple[str, str, str | None] | None":
    """Terminal fallback for manual location entry."""
    city = input("City: ").strip()
    if not city:
        return None
    state = input("State (2-letter code): ").strip().upper()[:2]
    if not state:
        return None
    kind = input("Analysis type — [H]ospital market or [S]pecialty practice? [H]: ").strip().upper()
    specialty = None
    if kind == "S":
        specialty = input("Specialty (e.g. Orthopedics): ").strip() or None
    return city, state, specialty


def _pick_file() -> str:
    """Show a native OS file-picker dialog and return the chosen path."""
    if sys.platform == "darwin":
        return _pick_file_macos()
    return _pick_file_tkinter()


def _pick_file_macos() -> str:
    """Use AppleScript to show a native macOS file picker."""
    import subprocess
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        '  set f to choose file with prompt "Select your entities spreadsheet:" '
        'of type {"csv", "xlsx", "xls"}\n'
        '  POSIX path of f\n'
        'end tell'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return input("Path to entities spreadsheet: ").strip()


def _pick_file_tkinter() -> str:
    """Fallback file picker using tkinter (Windows / Linux)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Select your entities spreadsheet",
            filetypes=[
                ("Spreadsheets", "*.csv *.xlsx *.xls"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        return path or ""
    except Exception:
        return input("Path to entities spreadsheet: ").strip()


def _prompt_api_key() -> str:
    """Ask the user to enter their Anthropic API key."""
    if sys.platform == "darwin":
        return _prompt_api_key_macos()
    return _prompt_api_key_tkinter()


def _prompt_api_key_macos() -> str:
    """Use AppleScript to prompt for the API key on macOS."""
    import subprocess
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        '  text returned of (display dialog '
        '"Enter your Anthropic API key (starts with sk-ant-):'
        '\\n\\nYou only need to do this once — it will be saved for future runs." '
        'default answer "" with title "Rank2 — API Key" buttons {"Cancel", "OK"} '
        'default button "OK")\n'
        'end tell'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return input("Enter your Anthropic API key (sk-ant-...): ").strip()


def _prompt_api_key_tkinter() -> str:
    """Fallback API key prompt using tkinter (Windows / Linux)."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        key = simpledialog.askstring(
            "Anthropic API Key",
            "Enter your Anthropic API key (starts with sk-ant-):\n\n"
            "You only need to do this once — it will be saved for future runs.",
            parent=root,
        )
        root.destroy()
        return (key or "").strip()
    except Exception:
        return input("Enter your Anthropic API key (sk-ant-...): ").strip()


def _show_complete_dialog(count: int, output_dir: "Path | str") -> str:
    """Show a 'ready for next task' popup after reports finish.
    Returns 'new', 'history', or 'quit'."""
    if sys.platform == "darwin":
        return _show_complete_dialog_macos(count, str(output_dir))
    return _show_complete_dialog_terminal(count)


def _show_complete_dialog_macos(count: int, output_dir: str) -> str:
    import subprocess
    noun = "report" if count == 1 else "reports"
    msg = (
        f"{count} {noun} generated successfully.\\n\\n"
        f"Saved to: {output_dir}\\n\\n"
        "Rank2 is ready for your next task."
    )
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        f'  set btn to button returned of (display dialog "{msg}" '
        'buttons {"Quit", "View History", "New Analysis"} '
        'default button "New Analysis" '
        'with title "Rank2 — Ready")\n'
        '  btn\n'
        'end tell'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return "new"
    btn = r.stdout.strip()
    if "Quit" in btn:
        return "quit"
    if "History" in btn:
        return "history"
    return "new"


def _show_complete_dialog_terminal(count: int) -> str:
    noun = "report" if count == 1 else "reports"
    print(f"\n✓  {count} {noun} complete. Rank2 is ready for your next task.")
    print("  1. New analysis")
    print("  2. View history")
    print("  3. Quit")
    choice = input("Enter 1, 2, or 3 [1]: ").strip()
    if choice == "3":
        return "quit"
    if choice == "2":
        return "history"
    return "new"


def _open_folder(path: str | Path) -> None:
    """Open the output folder in Finder/Explorer after the run."""
    import subprocess
    path = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ── API key setup ────────────────────────────────────────────────────────────

def _ensure_api_key() -> None:
    """Make sure ANTHROPIC_API_KEY is set, prompting the user if needed."""
    # 1. Already in environment (e.g. from .env)
    if os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-"):
        return

    # 2. Check perception settings (loaded from .env)
    try:
        from perception.config import settings
        if settings.anthropic_api_key.startswith("sk-"):
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
            return
    except Exception:
        pass

    # 3. Check saved config file
    config = _load_config()
    key = config.get("anthropic_api_key", "")
    if key.startswith("sk-"):
        os.environ["ANTHROPIC_API_KEY"] = key
        return

    # 4. Prompt the user
    print("No Anthropic API key found.")
    key = _prompt_api_key()
    if not key.startswith("sk-"):
        print("Error: invalid or missing API key.", file=sys.stderr)
        sys.exit(1)

    config["anthropic_api_key"] = key
    _save_config(config)
    os.environ["ANTHROPIC_API_KEY"] = key
    print(f"API key saved to {_config_path()}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank2 — generate branded market intelligence reports from a spreadsheet"
    )
    parser.add_argument(
        "spreadsheet",
        nargs="?",
        help="Path to CSV or Excel file with entities (a file picker opens if omitted)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "Documents" / "Rank2 Reports"),
        help="Directory to write reports (default: ~/Documents/Rank2 Reports/)",
    )
    args = parser.parse_args()

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.rule import Rule
    from rich import box

    console = Console(force_terminal=True, stderr=True)

    console.print(Panel(
        "[bold white]Rank2[/bold white]  [dim]Market Intelligence[/dim]",
        border_style="dark_sea_green4",
        padding=(0, 2),
    ))

    _ensure_api_key()

    from perception.analyzer import analyze_entities
    from perception.db import init_db
    from perception.loader import load

    with console.status("[bold dark_sea_green4]Initializing…[/bold dark_sea_green4]"):
        init_db()

    output_dir = Path(args.output_dir).resolve()

    # If a spreadsheet was supplied on the command line, process it on the first
    # iteration and then fall into the persistent loop.
    pending_spreadsheet: "str | None" = args.spreadsheet

    while True:
        entities = None
        spreadsheet = None

        if pending_spreadsheet:
            spreadsheet = pending_spreadsheet
            pending_spreadsheet = None
        else:
            mode = _choose_input_mode()

            if mode == "quit":
                console.print("[dim]Goodbye.[/dim]")
                break

            if mode == "history":
                _show_history(console)
                continue

            if _is_analysis_running():
                _show_busy_dialog()
                continue

            if mode == "manual":
                console.print("[dim]Enter location details…[/dim]")
                location = (
                    _prompt_location_macos() if sys.platform == "darwin"
                    else _prompt_location_terminal()
                )
                if not location:
                    console.print("[yellow]Cancelled.[/yellow]")
                    continue
                city, state, specialty = location

                import re as _re
                from perception.models import Entity, EntityType
                entity_type = EntityType.hospital if not specialty else EntityType.practice
                slug = _re.sub(r"[^a-z0-9]+", "-",
                               f"{entity_type.value}-{city}-{state}".lower()).strip("-")
                entities = [Entity(
                    id=slug,
                    entity_type=entity_type,
                    name=f"{city}, {state}",
                    city=city,
                    state=state,
                    specialty=specialty,
                )]
                console.print(
                    f"[green]✓[/green] Location: [bold]{city}, {state}[/bold]"
                    + (f"  •  [dim]{specialty}[/dim]" if specialty else "")
                )
            else:
                console.print("[dim]Opening file picker…[/dim]")
                spreadsheet = _pick_file()
                if not spreadsheet:
                    console.print("[yellow]No file selected.[/yellow]")
                    continue

        # Load entities from file when not provided via manual entry
        if entities is None:
            try:
                entities = load(spreadsheet)
            except (FileNotFoundError, ValueError) as exc:
                console.print(f"[red]Error:[/red] {exc}")
                continue

            if not entities:
                console.print("[yellow]No entities found in spreadsheet.[/yellow]")
                continue

            fname = Path(spreadsheet).name
            console.print(f"[green]✓[/green] Loaded [bold]{len(entities)}[/bold] "
                          f"{'entry' if len(entities) == 1 else 'entries'} from [dim]{fname}[/dim]")

        _acquire_lock()
        try:
            results = analyze_entities(entities, output_dir=str(output_dir))
        except Exception as exc:
            console.print(f"\n[red]Analysis failed:[/red] {exc}")
            if sys.platform == "darwin":
                import subprocess
                safe_msg = str(exc)[:200].replace('"', "'")
                subprocess.run(["osascript", "-e",
                    'tell application "System Events" to display dialog '
                    f'"Analysis failed:\\n\\n{safe_msg}" '
                    'with title "Rank2 — Error" buttons {"OK"} default button "OK"'])
            continue
        finally:
            _release_lock()

        # ── Summary table ─────────────────────────────────────────────────────
        console.print()
        console.print(Rule("[dim]Complete[/dim]", style="dark_sea_green4"))
        console.print()

        table = Table(box=box.ROUNDED, border_style="dark_sea_green4", show_header=True)
        table.add_column("Location",   style="bold white")
        table.add_column("Specialty",  style="dim")
        table.add_column("Providers",  justify="right")
        table.add_column("Status",     justify="center")

        for r in results:
            table.add_row(
                r.location,
                r.specialty or "—",
                str(len(r.rankings)),
                "[green]✓ Done[/green]",
            )

        console.print(table)
        console.print(f"\n[dim]Reports saved to:[/dim] {output_dir}\n")
        _open_folder(output_dir)

        # ── Ready for next task ───────────────────────────────────────────────
        next_action = _show_complete_dialog(len(results), output_dir)

        if next_action == "quit":
            console.print("[dim]Goodbye.[/dim]")
            break
        if next_action == "history":
            _show_history(console)
        # "new" → loop back to mode-selection dialog


if __name__ == "__main__":
    main()
