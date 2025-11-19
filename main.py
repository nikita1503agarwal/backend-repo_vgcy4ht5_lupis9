import os
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from PyPDF2 import PdfReader
from dateutil import parser as dateparser
from datetime import datetime, timedelta

from database import create_document, get_documents, db
from schemas import StudentResource, Summary, Note, Flashcard, StudyTask, StudyPlan, Doubt

app = FastAPI(title="Student Productivity API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------- Utility functions (simple heuristics) -----------------------

def _extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        import io
        reader = PdfReader(io.BytesIO(file_bytes))
        text_parts = []
        for page in reader.pages:
            try:
                text_parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(text_parts).strip()
    except Exception as e:
        return ""


def _simple_summarize(text: str, max_sentences: int = 5) -> Dict[str, Any]:
    import re
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    key_points = []
    for s in sentences:
        if len(key_points) >= max_sentences:
            break
        # prefer sentences with key academic keywords
        if any(k in s.lower() for k in ["important", "key", "therefore", "defines", "theorem", "proof", "exam", "result", "conclusion"]):
            key_points.append(s)
    if len(key_points) < max_sentences:
        for s in sentences:
            if s not in key_points:
                key_points.append(s)
            if len(key_points) >= max_sentences:
                break
    summary_text = " ".join(key_points[:max_sentences])
    return {
        "content": summary_text,
        "key_points": [p if len(p) <= 200 else p[:197] + "..." for p in key_points[:max_sentences]],
        "reading_time_min": max(1, int(len(text.split()) / 180)),
    }


def _make_exam_notes(text: str, max_points: int = 10) -> List[str]:
    import re
    lines = re.split(r"\n+", text)
    bullets = []
    for line in lines:
        line = line.strip(" -•\t")
        if not line:
            continue
        if any(x in line.lower() for x in ["definition", "formula", "step", "theorem", "law", "property", "example:"]):
            bullets.append(line)
        elif len(line.split()) <= 12:
            bullets.append(line)
        if len(bullets) >= max_points:
            break
    # fallback: first sentences
    if not bullets:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        bullets = sentences[:max_points]
    return [b[:180] + ("..." if len(b) > 180 else "") for b in bullets]


def _generate_flashcards(text: str, n: int = 8) -> List[Dict[str, str]]:
    import re
    sentences = re.split(r"(?<=[.!?])\s+", text)
    cards = []
    for s in sentences:
        words = s.split()
        if len(words) >= 6 and ("is" in words or "are" in words):
            try:
                if "is" in words:
                    idx = words.index("is")
                else:
                    idx = words.index("are")
                subject = " ".join(words[:idx]).strip(", .")
                description = " ".join(words[idx+1:]).strip()
                if subject and description:
                    q = f"What is {subject}?"
                    a = description
                    cards.append({"question": q[:180], "answer": a[:300]})
            except Exception:
                continue
        if len(cards) >= n:
            break
    # fallback generic
    if len(cards) < n:
        for i in range(n - len(cards)):
            cards.append({
                "question": f"Key point {i+1}?",
                "answer": "Review your notes for this topic.",
            })
    return cards[:n]


def _extract_tasks_and_deadlines(text: str) -> List[Dict[str, Any]]:
    # simple heuristic: look for lines with verbs and dates
    tasks: List[Dict[str, Any]] = []
    for line in text.splitlines():
        l = line.strip()
        if not l:
            continue
        lower = l.lower()
        if any(v in lower for v in ["submit", "finish", "complete", "read", "solve", "revise", "review", "write", "prepare"]):
            due = None
            try:
                due = dateparser.parse(l, fuzzy=True, default=datetime.now())
                # Only keep if parsed date is in the future-ish
                if due < datetime.now() - timedelta(days=1):
                    due = None
            except Exception:
                due = None
            tasks.append({
                "title": l[:120],
                "due_date": due.isoformat() if due else None,
                "status": "todo",
                "priority": "high" if any(p in lower for p in ["exam", "midterm", "final"]) else "medium",
            })
    return tasks


# ----------------------- Models for requests -----------------------

class TextIn(BaseModel):
    title: str
    text: str

class GenerateIn(BaseModel):
    resource_id: Optional[str] = None
    text: Optional[str] = None
    count: Optional[int] = 8

class PlanIn(BaseModel):
    title: str
    objectives: List[str] = []
    days: int = 7
    daily_hours: float = 2.0

class DoubtIn(BaseModel):
    question: str
    context: Optional[str] = None


# ----------------------- Basic routes -----------------------

@app.get("/")
def root():
    return {"message": "Student Productivity API running"}

@app.get("/test")
def test_database():
    try:
        collections = db.list_collection_names() if db else []
        return {"backend": "ok", "database": bool(db), "collections": collections[:10]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ----------------------- Resource ingestion -----------------------

@app.post("/api/resources/upload")
async def upload_resource(file: UploadFile = File(...), title: Optional[str] = Form(None)):
    data = await file.read()
    content_text = ""
    rtype = "unknown"
    if file.filename.lower().endswith(".pdf"):
        rtype = "pdf"
        content_text = _extract_text_from_pdf(data)
    elif any(file.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg"]):
        rtype = "image"
        # OCR not included in this demo environment
        content_text = ""
    else:
        rtype = "binary"

    doc = StudentResource(
        title=title or file.filename,
        type=rtype,
        source_name=file.filename,
        content_text=content_text,
        metadata={"size": len(data)}
    )
    rid = create_document("studentresource", doc)
    return {"resource_id": rid, "detected_type": rtype, "chars": len(content_text)}


@app.post("/api/resources/text")
async def create_text_resource(payload: TextIn):
    doc = StudentResource(
        title=payload.title,
        type="text",
        source_name=None,
        content_text=payload.text,
        metadata={}
    )
    rid = create_document("studentresource", doc)
    return {"resource_id": rid, "chars": len(payload.text)}


# ----------------------- Generators -----------------------

@app.post("/api/summarize")
async def summarize(payload: GenerateIn):
    text = payload.text
    if not text and payload.resource_id:
        items = get_documents("studentresource", {"_id": {"$eq": db.studentresource._ObjectId(payload.resource_id) if False else None}})
        # Fallback since we can't easily ObjectId with helper; fetch by scanning
        if not items:
            items = get_documents("studentresource")
            for it in items:
                if str(it.get("_id")) == str(payload.resource_id):
                    text = it.get("content_text", "")
                    break
        else:
            text = items[0].get("content_text", "")
    text = text or ""
    result = _simple_summarize(text)
    summ = Summary(title="Summary", resource_id=payload.resource_id, content=result["content"], key_points=result["key_points"], reading_time_min=result["reading_time_min"]) 
    sid = create_document("summary", summ)
    return {"summary_id": sid, **result}


@app.post("/api/notes")
async def notes(payload: GenerateIn):
    text = payload.text or ""
    bullets = _make_exam_notes(text)
    note = Note(title="Exam-focused Notes", resource_id=payload.resource_id, bullets=bullets)
    nid = create_document("note", note)
    return {"note_id": nid, "bullets": bullets}


@app.post("/api/flashcards")
async def flashcards(payload: GenerateIn):
    text = payload.text or ""
    cards = _generate_flashcards(text, n=payload.count or 8)
    created_ids: List[str] = []
    for c in cards:
        fc = Flashcard(resource_id=payload.resource_id, question=c["question"], answer=c["answer"], topic=None)
        cid = create_document("flashcard", fc)
        created_ids.append(cid)
    return {"count": len(cards), "cards": cards, "ids": created_ids}


@app.post("/api/tasks/extract")
async def extract_tasks(payload: GenerateIn):
    text = payload.text or ""
    tasks = _extract_tasks_and_deadlines(text)
    created: List[Dict[str, Any]] = []
    for t in tasks:
        task = StudyTask(title=t["title"], due_date=dateparser.parse(t["due_date"]) if t.get("due_date") else None, course=None, source="extracted", status="todo", priority=t.get("priority", "medium"))
        tid = create_document("studytask", task)
        t["id"] = tid
        created.append(t)
    return {"tasks": created}


@app.post("/api/plan")
async def plan(payload: PlanIn):
    # naive schedule: split objectives across days and allocate sessions
    tasks: List[Dict[str, Any]] = []
    if not payload.objectives:
        payload.objectives = ["Review notes", "Practice problems", "Revise key formulas"]
    per_day = max(1, int(len(payload.objectives) / max(1, payload.days)) )
    day_pointer = 0
    start = datetime.now().date()
    for i, obj in enumerate(payload.objectives):
        due = start + timedelta(days=min(day_pointer, payload.days - 1))
        tasks.append({
            "title": f"{obj}",
            "due_date": due.isoformat(),
            "status": "todo",
            "priority": "medium",
        })
        if (i + 1) % per_day == 0:
            day_pointer += 1
    plan_doc = StudyPlan(title=payload.title, objectives=payload.objectives, tasks=tasks, timeframe_days=payload.days)
    pid = create_document("studyplan", plan_doc)
    return {"plan_id": pid, "tasks": tasks, "days": payload.days}


@app.post("/api/doubts")
async def doubts(payload: DoubtIn):
    steps: List[str] = []
    q = payload.question.strip()
    if payload.context:
        steps.append("Understand the context: " + payload.context[:200])
    steps.append("Restate the question: " + q)
    steps.append("Identify knowns and unknowns")
    steps.append("Break into sub-problems and solve step-by-step")
    steps.append("Verify the result with a quick check or example")
    final = "This is a heuristic explanation. For precise solutions, consult course materials."
    doc = Doubt(question=q, context=payload.context, explanation_steps=steps, final_answer=final)
    did = create_document("doubt", doc)
    return {"doubt_id": did, "steps": steps, "final_answer": final}


# ----------------------- List endpoints -----------------------

@app.get("/api/flashcards")
async def list_flashcards(limit: int = 20):
    items = get_documents("flashcard", {}, limit)
    # Serialize ObjectIds
    for it in items:
        if "_id" in it:
            it["_id"] = str(it["_id"])
    return {"items": items}


@app.get("/api/tasks")
async def list_tasks(limit: int = 50):
    items = get_documents("studytask", {}, limit)
    for it in items:
        if "_id" in it:
            it["_id"] = str(it["_id"])
    return {"items": items}


@app.get("/api/summaries")
async def list_summaries(limit: int = 20):
    items = get_documents("summary", {}, limit)
    for it in items:
        if "_id" in it:
            it["_id"] = str(it["_id"])
    return {"items": items}


@app.get("/api/notes")
async def list_notes(limit: int = 20):
    items = get_documents("note", {}, limit)
    for it in items:
        if "_id" in it:
            it["_id"] = str(it["_id"])
    return {"items": items}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
