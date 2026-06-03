import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.cleanup import cleanup_loop
from app.config import get_settings
from app.routers import submit, videos


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(submit.router)
    app.include_router(videos.router)

    settings = get_settings()
    media_path = Path(settings.media_root)
    media_path.mkdir(parents=True, exist_ok=True)

    app.mount("/media", StaticFiles(directory=str(media_path)), name="media")
    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app


app = create_app()
