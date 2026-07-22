from __future__ import annotations

import sys
from pathlib import Path

# 确保 backend/ 在 sys.path 中（避免从 tools/ 运行时找不到 app 包）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories.db import engine, Base  # noqa: E402
from app.repositories import models  # noqa: F401, E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("tables created")


if __name__ == "__main__":
    main()