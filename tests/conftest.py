import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The helpers import one another by bare name when the shell runs them; scripts/ holds the installer.
for folder in (ROOT, ROOT / "helpers", ROOT / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
