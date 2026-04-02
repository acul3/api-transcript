from datetime import datetime, timezone
from sqlalchemy import Column, Integer, DateTime, Text, JSON
from app.db.database import Base

class Summary(Base):
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True, index=True)
    original_text = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    key_points = Column(JSON, nullable=False)
    action_items = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
