"""
Example Launcher

Lists available example scripts organized by category and runs
the selected one with proper workspace dependency resolution.

Usage:
    uv run --package osrm-api-gateway-examples examples/main.py
"""

import subprocess
import sys
from pathlib import Path

# Importing config loads examples/.env and exports it to os.environ, so the
# subprocesses launched below inherit the same gateway URL this menu prints.
#
# The workspace shares one .venv at the repository root, and a bare `uv run`
# from there syncs it to the root package -- whose dependencies are
# httpx/starlette/uvicorn only. That evicts this project's, so say which
# package you meant. Caught here because the raw ImportError names a missing
# module rather than the wrong command, which is the actual mistake.
try:
    from config import settings
except ImportError as exc:
    sys.exit(
        f"{exc}\n\n"
        "The examples run in their own workspace package. Use:\n"
        "    make examples\n"
        "or:\n"
        "    uv run --package osrm-api-gateway-examples examples/main.py"
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_SRC = PROJECT_ROOT / "examples" / "src"


def discover_examples():
    """Group every runnable script by the directory it sits in.

    The walk is recursive because the tree stopped being flat: `fleet` alone has
    six sub-directories, and listing only its top level hid 31 of the 51
    examples -- including every rich-VRP, allocation and dynamic-dispatch one.
    An example nobody can find from the menu is documentation, not a demo.
    """
    examples = {}
    for script in sorted(EXAMPLES_SRC.rglob("*.py")):
        if script.name.startswith("_") or "__pycache__" in script.parts:
            continue
        # Every example lives in a category directory. What sits loose at the
        # top of src/ is shared machinery -- config.py -- and running it does
        # nothing. The old one-level walk excluded it by accident; this does it
        # on purpose.
        if script.parent == EXAMPLES_SRC:
            continue
        category = script.parent.relative_to(EXAMPLES_SRC).as_posix()
        examples.setdefault(category, []).append(script)
    return examples


def show_menu(examples):
    print("\nAvailable examples:\n")
    index = 1
    mapping = {}
    for category in sorted(examples):
        print(f"  [{category}]")
        for script in examples[category]:
            name = script.stem.replace("_", " ").title()
            print(f"    {index:2d}. {name}")
            mapping[index] = script
            index += 1
    return mapping


def run_script(script_path):
    rel = script_path.relative_to(PROJECT_ROOT)
    # --package is required, not cosmetic: the workspace shares one .venv at the
    # repository root, and a bare `uv run` from there syncs it to the root
    # package, whose dependencies are httpx/starlette/uvicorn only. That evicts
    # folium, requests and pydantic-settings, so every script needing them dies
    # on ModuleNotFoundError.
    cmd = ["uv", "run", "--package", "osrm-api-gateway-examples", str(rel)]
    print(f"\n{'=' * 60}")
    print(f"Running: {rel}")
    print(f"  API: {settings.OSRM_API_URL}")
    print(f"{'=' * 60}\n")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)
    return result.returncode


def main():
    examples = discover_examples()
    if not examples:
        print("No example scripts found.")
        sys.exit(1)

    mapping = show_menu(examples)

    while True:
        try:
            choice = input("\nEnter number (or q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice.lower() in ("q", "quit", "exit", ""):
            break
        try:
            idx = int(choice)
        except ValueError:
            print(f"Invalid input: {choice}")
            continue
        if idx not in mapping:
            print(f"Invalid choice: {idx}")
            continue
        script = mapping[idx]
        rc = run_script(script)
        print(f"\nFinished with exit code {rc}")


if __name__ == "__main__":
    main()
