"""Entry point for hosted Streamlit (Community Cloud): makes ``src/`` importable, then runs the UI."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from neo.ui.app import main  # noqa: E402

main()
