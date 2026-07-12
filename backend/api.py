import asyncio
import json
import logging
import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, select

from backend.agent import ChatAgent
from backend.agent_graph import TalkAgentGraph
from backend.animation_engine import AnimationEngine
from backend.config import get_settings
from backend.db import Conversation, Message, create_conversation, engine, init_db, list_messages, now
from backend.model import GemmaRuntime
from backend.observability import configure_observability
from backend.schemas import ChatRequest, RenameRequest
from backend.tools import extract_document
from backend.voice_engine import VoiceEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()
runtime = GemmaRuntime(settings)
agent = ChatAgent(runtime, settings)
talk_agent = TalkAgentGraph(runtime, settings)
voice_engine = VoiceEngine(settings)
animation_engine = AnimationEngine(settings)


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


@app.websocket("/api/talk/ws")
async def talk_socket(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    history: list = []
    preferences: dict[str, str] = {}

    async def send(event_type: str, **data):
        await websocket.send_json({"type": event_type, **data})

    async def respond(transcript: str):
        nonlocal history
        if not transcript.strip():
            await send("error", message="I could not hear any speech. Please try again.")
            await send("state", value="idle")
            return
        await send("transcript", content=transcript)
        await send("state", value="thinking")
        token_queue: asyncio.Queue[str] = asyncio.Queue()
        generation = asyncio.create_task(
            talk_agent.invoke(history, transcript, preferences, token_queue)
        )
        while not generation.done() or not token_queue.empty():
            try:
                token = await asyncio.wait_for(token_queue.get(), timeout=1)
                await send("token", content=token)
            except TimeoutError:
                await send("heartbeat")
        result = await generation
        response = result["response"]
        history = list(result["messages"])[-settings.model_context_messages:]
        await send("text_complete", content=response)
        await send("state", value="speaking")

        tts_task = asyncio.create_task(voice_engine.synthesize(response))
        animation_task = None
        if result["requires_animation"]:
            await send("animation_state", value="rendering")
            animation_task = asyncio.create_task(
                animation_engine.render(transcript[:60], response)
            )
        try:
            audio_url = await tts_task
            await send("audio_ready", url=audio_url)
        except Exception as exc:
            logger.warning("Talk TTS failed: %s", exc)
            await send("media_warning", message=str(exc))
        if animation_task:
            try:
                video_url = await animation_task
                await send("video_ready", url=video_url)
            except Exception as exc:
                logger.warning("Talk animation failed: %s", exc)
                await send("media_warning", message=str(exc))
        await send("state", value="idle")

    try:
        await send("state", value="idle")
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                audio_buffer.extend(message["bytes"])
                continue
            raw = message.get("text")
            if raw is None:
                continue
            command = json.loads(raw)
            if command.get("type") == "text":
                await respond(str(command.get("content", "")))
            elif command.get("type") == "commit":
                if not audio_buffer:
                    await send("error", message="No microphone audio was received")
                    continue
                suffix = ".webm" if "webm" in command.get("mime", "") else ".wav"
                path = settings.uploads_dir / f"voice-{uuid4()}{suffix}"
                path.write_bytes(audio_buffer)
                audio_buffer.clear()
                await send("state", value="thinking")
                await send("status", content="Transcribing locally with Whisper…")
                try:
                    transcript = await voice_engine.transcribe(path)
                    await respond(transcript)
                finally:
                    path.unlink(missing_ok=True)
            elif command.get("type") == "reset":
                history = []
                await send("reset_complete")
    except WebSocketDisconnect:
        logger.info("Talk client disconnected")
    except Exception as exc:
        logger.exception("Talk session failed")
        try:
            await send("error", message=str(exc))
            await send("state", value="error")
        except Exception:
            pass
