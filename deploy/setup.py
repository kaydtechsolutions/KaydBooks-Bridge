"""Launch the repository installer before installing the application package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kaydbooks_bridge.installer import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(source=Path(__file__).resolve().parents[1]))
