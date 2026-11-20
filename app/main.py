from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.incidents import router as incidents_router
from app.api.ops import router as ops_router
from app.core.bootstrap import create_schema, seed_data
from app.core.config import get_settings, validate_runtime_configuration
from app.core.database import SessionLocal
from app.services.incident_tooling import get_incident_tool_worker


def create_app() -> FastAPI:
    app = FastAPI(
        title="NexusOps Incident Intelligence & Response Platform",
        version="1.0.0",
        description=(
            "Incident lifecycle, event-driven multi-Agent diagnosis, hybrid operational RAG, "
            "monitoring webhooks, human approvals, allowlisted tools and audit trails."
        ),
    )

    @app.middleware("http")
    async def no_cache_frontend_assets(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.on_event("startup")
    def startup() -> None:
        settings = get_settings()
        validate_runtime_configuration(settings)
        create_schema()
        db = SessionLocal()
        try:
            seed_data(db)
        finally:
            db.close()
        worker = get_incident_tool_worker(get_settings())
        worker.start()
        app.state.incident_tool_worker = worker

    @app.on_event("shutdown")
    def shutdown() -> None:
        worker = getattr(app.state, "incident_tool_worker", None)
        if worker is not None:
            worker.stop()

    app.include_router(incidents_router)
    app.include_router(ops_router)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
