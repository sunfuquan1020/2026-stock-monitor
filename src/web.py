"""FastAPI read-only workbench for stock-monitor artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.dashboard import ArtifactNotFound, DashboardRepository, InvalidArtifactRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_STATIC_DIR = PROJECT_ROOT / "web" / "dist"


def create_app(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    static_dir: str | Path = DEFAULT_STATIC_DIR,
) -> FastAPI:
    """Create the read-only API and optional single-page frontend."""
    repository = DashboardRepository(output_dir)
    frontend = Path(static_dir)
    index_file = frontend / "index.html"

    app = FastAPI(
        title="Stock Monitor Workbench",
        description="Read-only adapter for /stock reports, judgments and focus state.",
        version="1.0.0",
    )

    @app.middleware("http")
    async def disable_api_caching(request: Any, call_next: Any):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "read_only": True}

    @app.get("/api/v1/dashboard", tags=["dashboard"])
    def dashboard() -> dict[str, Any]:
        return repository.snapshot()

    @app.get("/api/v1/reports", tags=["reports"])
    def reports() -> list[dict[str, Any]]:
        return repository.list_reports()

    @app.get("/api/v1/reports/{artifact_date}/{kind}", tags=["reports"])
    def report_document(artifact_date: str, kind: str) -> dict[str, str]:
        try:
            return repository.get_document(artifact_date, kind)
        except InvalidArtifactRequest as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArtifactNotFound as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc

    assets_dir = frontend / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    def spa_response():
        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "frontend is not built",
                "hint": "run npm install && npm run build in web/",
            },
        )

    @app.get("/", include_in_schema=False)
    def frontend_root():
        return spa_response()

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_fallback(full_path: str):
        candidate = frontend / full_path
        if candidate.is_file() and frontend in candidate.resolve().parents:
            return FileResponse(candidate)
        return spa_response()

    return app


app = create_app()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Stock Monitor read-only Web workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_app(args.output_dir, args.static_dir),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    cli()
