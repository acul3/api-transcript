from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import logging

from app.db.database import get_db
from app.db import models
from app.schemas import schemas
from app.services.ai_service import generate_summary, get_queue_stats

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = 500_000  # ~500KB

@router.get("/queue", tags=["Queue"])
async def queue_status():
    """Get current AI processing queue status."""
    return get_queue_stats()

async def _save_summary(text: str, ai_result: dict, db: AsyncSession) -> models.Summary:
    db_summary = models.Summary(
        original_text=text,
        summary=ai_result.get("summary", ""),
        key_points=ai_result.get("key_points", []),
        action_items=ai_result.get("action_items", [])
    )
    db.add(db_summary)
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

    return await _save_summary(text, ai_result, db)

@router.get("/{summary_id}", response_model=schemas.SummaryResponse)
async def get_summary(summary_id: int, db: AsyncSession = Depends(get_db)):
    """Retrieve an existing summary by its ID."""
    result = await db.execute(select(models.Summary).where(models.Summary.id == summary_id))
    db_summary = result.scalars().first()

    if db_summary is None:
        raise HTTPException(status_code=404, detail="Summary not found")
    return db_summary

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
