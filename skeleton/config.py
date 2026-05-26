"""
TransitFlow Configuration
Reads from environment variables / .env file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Project paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "train-mock-data"

# ── LLM Provider ──────────────────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.0-flash-lite")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
GEMINI_EMBED_DIM = 3072

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:1b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
OLLAMA_EMBED_DIM = 768
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))

# ── PostgreSQL ────────────────────────────────────────────────────────────────
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_USER = os.getenv("PG_USER", "transitflow")
PG_PASSWORD = os.getenv("PG_PASSWORD", "transitflow")
PG_DB = os.getenv("PG_DB", "transitflow")

PG_DSN = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

# ── Neo4j ─────────────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7688")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "transitflow")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# ── Fare defaults (from metro_schedules.json / national_rail_schedules.json) ──
METRO_BASE_FARE_USD = float(os.getenv("METRO_BASE_FARE_USD", "0.80"))
METRO_PER_STOP_RATE_USD = float(os.getenv("METRO_PER_STOP_RATE_USD", "0.30"))

RAIL_STANDARD_BASE_FARE_USD = float(os.getenv("RAIL_STANDARD_BASE_FARE_USD", "2.50"))
RAIL_STANDARD_PER_STOP_RATE_USD = float(os.getenv("RAIL_STANDARD_PER_STOP_RATE_USD", "1.50"))
RAIL_FIRST_BASE_FARE_USD = float(os.getenv("RAIL_FIRST_BASE_FARE_USD", "4.00"))
RAIL_FIRST_PER_STOP_RATE_USD = float(os.getenv("RAIL_FIRST_PER_STOP_RATE_USD", "2.50"))

INTERCHANGE_WALKING_TIME_MIN = int(os.getenv("INTERCHANGE_WALKING_TIME_MIN", "5"))

# ── RAG settings ──────────────────────────────────────────────────────────────
VECTOR_TOP_K = int(os.getenv("VECTOR_TOP_K", "3"))
VECTOR_SIMILARITY_THRESHOLD = float(os.getenv("VECTOR_SIMILARITY_THRESHOLD", "0.5"))
