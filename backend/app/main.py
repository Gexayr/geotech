from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import blocks, layers, metrics, route, site

app = FastAPI(
    title="Vineyard AI Field Challenge — API",
    description="Serves annotation layers, measurements and the optimized "
    "inspection route for a vineyard block.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only — tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(blocks.router)
app.include_router(layers.router)
app.include_router(metrics.router)
app.include_router(route.router)
app.include_router(site.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# In the single-container Docker build, the frontend's production build is
# copied to app/static and served from the same origin/port as the API —
# no separate frontend service, no CORS needed between them. Mounted last
# so it never shadows the /api/* routes above it. Absent in local dev
# (`uvicorn --reload` outside Docker), where the Vite dev server on its own
# port serves the frontend instead.
_STATIC_DIR = Path(__file__).parent.parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
