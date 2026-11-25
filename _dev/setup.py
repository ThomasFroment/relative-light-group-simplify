"""Create _dev/.venv with the test dependencies. Safe to run again.

Run with Python 3.14.2 or newer (Home Assistant's minimum), for example:
    python3.14 _dev/setup.py
"""

from pathlib import Path
import subprocess
import sys
import venv

# Latest Home Assistant release. pytest-homeassistant-custom-component lags
# behind and pins an older release, so this one is installed over it.
HA_VERSION = "2026.11.3"

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "_dev" / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main() -> None:
    """Build the venv and install the pinned packages."""
    if sys.version_info < (3, 14, 2):
        sys.exit(f"Python 3.14.2+ required, got {sys.version.split()[0]}")
    if not PYTHON.exists():
        venv.create(VENV, with_pip=True)
    pip = [str(PYTHON), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    subprocess.run([*pip, "-r", str(ROOT / "requirements_test.txt")], check=True)
    subprocess.run([*pip, f"homeassistant=={HA_VERSION}"], check=True)


if __name__ == "__main__":
    main()
