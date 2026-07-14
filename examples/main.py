"""
Example Launcher

Lists available example scripts organized by category and runs
the selected one with proper workspace dependency resolution.

Usage:
    uv run examples/main.py
"""

import subprocess
import sys
from pathlib import Path

# Load .env into os.environ before any subprocess runs
_src = Path(__file__).parent / "src"
sys.path.insert(0, str(_src))
from config import settings  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_SRC = PROJECT_ROOT / "examples" / "src"


def discover_examples():
    examples = {}
    for category_dir in sorted(EXAMPLES_SRC.iterdir()):
        if not category_dir.is_dir():
            continue
        scripts = sorted(
            f for f in category_dir.iterdir()
            if f.suffix == ".py" and not f.name.startswith("_")
        )
        if scripts:
            examples[category_dir.name] = scripts
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
    cmd = ["uv", "run", str(rel)]
    print(f"\n{'=' * 60}")
    print(f"Running: {rel}")
    print(f"  API: {settings.OSRM_API_URL}")
    print(f"{'=' * 60}\n")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
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
