#!/usr/bin/env python3
"""Launch the Streamlit workspace in the project .venv; --light uses stdlib UI."""
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit("Create .venv and install requirements-streamlit.txt first. See README.md.")
    args = sys.argv[1:]
    if "--light" in args:
        args.remove("--light")
        command = [str(python), str(root / "app.py"), *args]
    else:
        command = [str(python), "-m", "streamlit", "run", str(root / "streamlit_app.py"),
                   "--server.address=127.0.0.1", "--browser.gatherUsageStats=false", *args]
    try:
        raise SystemExit(subprocess.call(command, cwd=root))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
