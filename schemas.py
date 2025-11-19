"""
Database Schemas for Student Productivity App

Each Pydantic model corresponds to a MongoDB collection. The collection
name is the lowercase of the class name (e.g., Summary -> "summary").
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# Core entities

class StudentResource(BaseModel):
    """Represents an uploaded learning resource like PDF, audio, or image."""
    title: str = Field(..., description="Human friendly title")
    type: str = Field(..., description="pdf | audio | image | text")
    source_name: Optional[str] = Field(None, description="Original filename if any")
    content_text: Optional[str] = Field(None, description="Extracted/plain text content")
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Summary(BaseModel):
    title: str
    resource_id: Optional[str] = None
    content: str
    key_points: List[str] = Field(default_factory=list)
    reading_time_min: Optional[int] = None

class Note(BaseModel):
    title: str
    resource_id: Optional[str] = None
    bullets: List[str] = Field(default_factory=list)

class Flashcard(BaseModel):
    resource_id: Optional[str] = None
    question: str
    answer: str
    topic: Optional[str] = None

class StudyTask(BaseModel):
    title: str
    due_date: Optional[datetime] = None
    course: Optional[str] = None
    source: Optional[str] = None
    status: str = Field("todo", description="todo | in_progress | done")
    priority: str = Field("medium", description="low | medium | high | urgent")

class StudyPlan(BaseModel):
    title: str
    objectives: List[str] = Field(default_factory=list)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    timeframe_days: int = 7

class Doubt(BaseModel):
    question: str
    context: Optional[str] = None
    explanation_steps: List[str] = Field(default_factory=list)
    final_answer: Optional[str] = None
