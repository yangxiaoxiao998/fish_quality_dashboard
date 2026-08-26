"""向后兼容入口：``filter_complete_pairs.py`` -> ``core.filter_complete_fish``。"""

import sys
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_ALGORITHMS_ROOT = _THIS_FILE.parents[2]  # algorithms/
if str(_ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ALGORITHMS_ROOT))

from algorithms.core.filter_complete_fish import main  # noqa: E402  pylint: disable=wrong-import-position


if __name__ == "__main__":
    raise SystemExit(main())
