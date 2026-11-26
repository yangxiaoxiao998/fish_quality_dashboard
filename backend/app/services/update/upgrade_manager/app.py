from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.services.update.upgrade_manager import storage
from app.services.update.upgrade_manager.api import router as upgrade_router


def _allowed_origins() -> list[str]:
    raw = os.environ.get(
        "UPGRADE_MANAGER_ALLOWED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["http://127.0.0.1:5173"]


def create_app() -> FastAPI:
    app = FastAPI(title="Fish Quality Dashboard Upgrade Manager")
    storage.ensure_manager_dirs()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        # The packaged Electron renderer is served by a local Node server on
        # an ephemeral port, so it cannot be limited to the Vite port 5173.
        # Keep the exception loopback-only; the manager itself still binds to
        # 127.0.0.1 by default.
        allow_origin_regex=r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(upgrade_router)
    return app


app = create_app()


def main() -> int:
    import uvicorn

    host = os.environ.get("UPGRADE_MANAGER_HOST", "127.0.0.1")
    port = int(os.environ.get("UPGRADE_MANAGER_PORT", "8012"))
    uvicorn.run("app.services.update.upgrade_manager.app:app", host=host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
