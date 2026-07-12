import asyncio
import json
import logging
import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, select

from backend.agent import ChatAgent
from backend.config import get_settings
from backend.db import Conversation, Message, create_conversation, engine, init_db, list_messages, now
from backend.model import GemmaRuntime
from backend.observability import configure_observability
from backend.schemas import ChatRequest, RenameRequest
from backend.tools import extract_document

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()
runtime = GemmaRuntime(settings)
agent = ChatAgent(runtime, settings)


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    configure_observability(settings, application)
    yield


app = FastAPI(title="Gemma Studio API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/generated", StaticFiles(directory=settings.generated_dir), name="generated")


def message_dict(item: Message) -> dict:
    return {
        "id": str(item.id), "role": item.role, "content": item.content,
        "created_at": item.created_at.isoformat(), "attachments": item.attachments,
        "metadata": item.message_metadata,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "model": settings.model_id, "model_loaded": runtime.loaded, "model_error": runtime.load_error}


@app.get("/api/conversations")
def conversations():
    with Session(engine) as session:
        items = session.exec(select(Conversation).order_by(Conversation.updated_at.desc())).all()
        return items


@app.post("/api/conversations")
def new_conversation():
    with Session(engine) as session:
        return create_conversation(session)


@app.get("/api/conversations/{conversation_id}/messages")
def messages(conversation_id: UUID):
    with Session(engine) as session:
        if not session.get(Conversation, conversation_id):
            raise HTTPException(404, "Conversation not found")
        return [message_dict(item) for item in list_messages(session, conversation_id)]


@app.patch("/api/conversations/{conversation_id}")
def rename(conversation_id: UUID, payload: RenameRequest):
    with Session(engine) as session:
        item = session.get(Conversation, conversation_id)
        if not item:
            raise HTTPException(404, "Conversation not found")
        item.title = payload.title.strip()
        item.updated_at = now()
        session.add(item); session.commit(); session.refresh(item)
        return item


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID):
    with Session(engine) as session:
        for item in list_messages(session, conversation_id): session.delete(item)
        conversation = session.get(Conversation, conversation_id)
        if conversation: session.delete(conversation)
        session.commit()


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Filename is required")
    extension = Path(file.filename).suffix.lower()
    if extension not in {".pdf", ".docx", ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv"}:
        raise HTTPException(415, "Unsupported file type")
    upload_id = str(uuid4())
    destination = settings.uploads_dir / f"{upload_id}{extension}"
    with destination.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    if destination.stat().st_size > 25 * 1024 * 1024:
        destination.unlink(missing_ok=True)
        raise HTTPException(413, "File exceeds 25 MB")
    return {"id": upload_id, "name": file.filename, "content_type": file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream", "size": destination.stat().st_size}


def attachment_data(ids: list[str]) -> tuple[list[dict], str]:
    attachments, contexts = [], []
    for upload_id in ids:
        matches = list(settings.uploads_dir.glob(f"{upload_id}.*"))
        if not matches: continue
        path = matches[0]
        attachments.append({"id": upload_id, "name": path.name, "content_type": mimetypes.guess_type(path)[0] or "application/octet-stream", "size": path.stat().st_size})
        contexts.append(
            f"DOCUMENT {path.name}:\n"
            f"{extract_document(path, max_chars=settings.document_max_chars)}"
        )
    return attachments, "\n\n".join(contexts)


@app.post("/api/chat/stream")
async def chat(payload: ChatRequest):
    conversation_id = payload.conversation_id
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id) if conversation_id else None
        if not conversation:
            conversation = create_conversation(session, payload.message[:60])
            conversation_id = conversation.id
        attachments, context = attachment_data(payload.attachment_ids)
        user_message = Message(conversation_id=conversation_id, role="user", content=payload.message, attachments=attachments)
        session.add(user_message); conversation.updated_at = now(); session.add(conversation); session.commit()
        prior = list_messages(session, conversation_id)[:-1]
    history = [AIMessage(content=x.content) if x.role == "assistant" else HumanMessage(content=x.content) for x in prior[-20:]]

    async def events():
        yield f"data: {json.dumps({'type': 'start', 'conversation_id': str(conversation_id), 'message_id': str(user_message.id)})}\n\n"
        try:
            token_queue: asyncio.Queue[str] = asyncio.Queue()
            generation = asyncio.create_task(
                agent.invoke(history, payload.message, payload.mode, context, token_queue)
            )
            elapsed = 0
            streamed = False
            while not generation.done() or not token_queue.empty():
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=2)
                    streamed = True
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                except TimeoutError:
                    elapsed += 2
                    detail = (
                        "Preparing the local model…"
                        if elapsed < 10
                        else f"Generating on CPU… {elapsed}s"
                    )
                    yield f"data: {json.dumps({'type': 'status', 'content': detail})}\n\n"
            result = await generation
            answer = str(result["messages"][-1].content)
            # Tool-only responses (for example image errors) do not pass through the LLM stream.
            if not streamed:
                yield f"data: {json.dumps({'type': 'token', 'content': answer})}\n\n"
            with Session(engine) as session:
                saved = Message(conversation_id=conversation_id, role="assistant", content=answer, message_metadata={"mode": result.get("mode"), "artifact_url": result.get("artifact_url")})
                session.add(saved); session.commit(); session.refresh(saved)
            yield f"data: {json.dumps({'type': 'done', 'message': message_dict(saved)})}\n\n"
        except Exception as exc:
            logging.exception("Chat failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
