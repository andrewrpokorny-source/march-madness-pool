import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import engine, Base, SessionLocal
from app.config import ESPN_POLL_INTERVAL_MINUTES
from app.services.espn import fetch_tournament_scores
from app.routers import leaderboard, bracket, admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_sync():
    """Background job to sync ESPN scores."""
    db = SessionLocal()
    try:
        stats = await fetch_tournament_scores(db)
        logger.info(f"Auto-sync: {stats}")
    except Exception as e:
        logger.error(f"Auto-sync error: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables and start scheduler
    Base.metadata.create_all(bind=engine)
    scheduler.add_job(scheduled_sync, "interval", minutes=ESPN_POLL_INTERVAL_MINUTES)
    scheduler.start()
    logger.info(f"ESPN score sync scheduled every {ESPN_POLL_INTERVAL_MINUTES} minutes")
    yield
    # Shutdown
    scheduler.shutdown()


app = FastAPI(title="March Madness Pool 2026", lifespan=lifespan)

app.include_router(leaderboard.router)
app.include_router(bracket.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
