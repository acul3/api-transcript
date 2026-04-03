from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import logging

from app.db.database import get_db
from app.db import models
from app.schemas import schemas
from app.services.ai_service import generate_summary, get_queue_stats
from app.services.blob_service import (
    upload_transcript,
    download_transcript,
    list_transcripts,
    is_blob_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = 500_000  # ~500KB

@router.get("/queue", tags=["Queue"])
async def queue_status():
    """Get current AI processing queue status."""
    stats = get_queue_stats()
    stats["blob_enabled"] = is_blob_enabled()
    return stats

@router.get("/blobs", tags=["Blob Storage"])
async def list_blob_transcripts(prefix: str = ""):
    """List transcripts stored in Azure Blob Storage."""
    if not is_blob_enabled():
        raise HTTPException(status_code=404, detail="Blob storage is not configured.")
    return await list_transcripts(prefix)

async def _save_summary(
    text: str,
    ai_result: dict,
    db: AsyncSession,
    filename: str | None = None,
) -> models.Summary:
    db_summary = models.Summary(
        original_text=text,
        summary=ai_result.get("summary", ""),
        key_points=ai_result.get("key_points", []),
        action_items=ai_result.get("action_items", []),
    )
    db.add(db_summary)
    await db.commit()
    await db.refresh(db_summary)

    # Upload to blob storage (non-blocking, best-effort)
    blob_url = await upload_transcript(text, db_summary.id, filename)
    if blob_url:
        db_summary.blob_url = blob_url
        await db.commit()
        await db.refresh(db_summary)

    return db_summary

@router.get("/", response_model=list[schemas.SummaryResponse])
async def list_summaries(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all summaries with pagination."""
    result = await db.execute(
        select(models.Summary).order_by(models.Summary.id.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()

@router.post("/text", response_model=schemas.SummaryResponse)
async def create_summary_from_text(summary_request: schemas.SummaryCreate, db: AsyncSession = Depends(get_db)):
    """Generate a structured summary from a plain text transcript."""
    try:
        ai_result = await generate_summary(summary_request.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        logger.exception("AI service error")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("Failed to generate summary from text")
        raise HTTPException(status_code=500, detail="Failed to generate summary. Please try again later.")

    return await _save_summary(summary_request.text, ai_result, db)

@router.post("/file", response_model=schemas.SummaryResponse)
async def create_summary_from_file(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Generate a structured summary by uploading a .txt file transcript."""
    if not file.filename or not file.filename.endswith('.txt'):
        raise HTTPException(status_code=400, detail="Only .txt files are supported currently.")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File exceeds maximum size of {MAX_FILE_SIZE} bytes.")

    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Could not decode the file. Please ensure it is a utf-8 encoded text file.")

    try:
        ai_result = await generate_summary(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        logger.exception("AI service error")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("Failed to generate summary from file")
        raise HTTPException(status_code=500, detail="Failed to generate summary. Please try again later.")

    return await _save_summary(text, ai_result, db, filename=file.filename)

@router.get("/{summary_id}", response_model=schemas.SummaryResponse)
async def get_summary(summary_id: int, db: AsyncSession = Depends(get_db)):
    """Retrieve an existing summary by its ID."""
    result = await db.execute(select(models.Summary).where(models.Summary.id == summary_id))
    db_summary = result.scalars().first()

    if db_summary is None:
        raise HTTPException(status_code=404, detail="Summary not found")
    return db_summary

@router.get("/{summary_id}/transcript", tags=["Blob Storage"])
async def get_transcript_from_blob(summary_id: int, db: AsyncSession = Depends(get_db)):
    """Download the original transcript from Azure Blob Storage."""
    result = await db.execute(select(models.Summary).where(models.Summary.id == summary_id))
    db_summary = result.scalars().first()

    if db_summary is None:
        raise HTTPException(status_code=404, detail="Summary not found")
    if not db_summary.blob_url:
        raise HTTPException(status_code=404, detail="No blob storage URL for this summary.")

    text = await download_transcript(db_summary.blob_url)
    if text is None:
        raise HTTPException(status_code=502, detail="Failed to download transcript from blob storage.")

    return {"summary_id": summary_id, "blob_url": db_summary.blob_url, "transcript": text}

@router.patch("/{summary_id}", response_model=schemas.SummaryResponse)
async def update_summary(summary_id: int, summary_update: schemas.SummaryUpdate, db: AsyncSession = Depends(get_db)):
    """Partially update an existing summary."""
    result = await db.execute(select(models.Summary).where(models.Summary.id == summary_id))
    db_summary = result.scalars().first()

    if db_summary is None:
        raise HTTPException(status_code=404, detail="Summary not found")

    for field, value in summary_update.model_dump(exclude_unset=True).items():
        setattr(db_summary, field, value)

    await db.commit()
    await db.refresh(db_summary)
    return db_summary
