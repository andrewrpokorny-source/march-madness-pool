import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/march_madness")

# Railway uses DATABASE_URL with postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ESPN_POLL_INTERVAL_MINUTES = int(os.getenv("ESPN_POLL_INTERVAL_MINUTES", "5"))

# Prize payouts per round
ROUND_PRIZES = {
    "First Four": 1,
    "Round of 64": 1,
    "Round of 32": 2,
    "Sweet 16": 5,
    "Elite 8": 10,
    "Final Four": 20,
    "Championship": 50,
}

ROUND_ORDER = [
    "First Four",
    "Round of 64",
    "Round of 32",
    "Sweet 16",
    "Elite 8",
    "Final Four",
    "Championship",
]
