import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from app.db.database import engine, Base
from app.api.routes import router as summary_router

BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title="Transcript Summarizer API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(summary_router, prefix="/summaries", tags=["Summaries"])

# ── Sample transcripts API ─────────────────────────────────────────────────
@app.get("/samples", tags=["Samples"])
async def list_samples():
    """List available sample transcript files."""
    if not TRANSCRIPTS_DIR.exists():
        return []
    files = sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    samples = []
    for f in files:
        content = f.read_text(encoding="utf-8")
        lines = content.split("\n")
        meta = {}
        for line in lines[:5]:
            if line.startswith("Case:"):
                meta["case"] = line.split(":", 1)[1].strip()
            elif line.startswith("Specialty:"):
                meta["specialty"] = line.split(":", 1)[1].strip()
            elif line.startswith("Word Count:"):
                meta["word_count"] = int(line.split(":", 1)[1].strip())
        # Extract just the transcript (after the === line)
        transcript_start = content.find("=" * 20)
        transcript = content[transcript_start:].split("\n", 2)[-1].strip() if transcript_start != -1 else content
        samples.append({
            "filename": f.name,
            "case": meta.get("case", f.stem),
            "specialty": meta.get("specialty", "General"),
            "word_count": meta.get("word_count", len(content.split())),
            "transcript": transcript,
        })
    return samples

# ── Serve frontend ──────────────────────────────────────────────────────────
STATIC_DIR = BASE_DIR / "static"

@app.get("/", include_in_schema=False)
async def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html")
