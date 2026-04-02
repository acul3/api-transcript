from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SummaryBase(BaseModel):
    summary: str
    key_points: List[str]
    action_items: List[str]

class SummaryCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)

class SummaryUpdate(BaseModel):
    summary: Optional[str] = None
    key_points: Optional[List[str]] = None
    action_items: Optional[List[str]] = None

class SummaryResponse(SummaryBase):
    id: int
    original_text: str
    created_at: datetime

    class Config:
        from_attributes = True
