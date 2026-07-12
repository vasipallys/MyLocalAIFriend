import asyncio
import json
import re
from pathlib import Path
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from backend.config import Settings


def extract_document(path: Path, max_chars: int = 80_000) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        from docx import Document

        text = "\n".join(p.text for p in Document(path).paragraphs)
    elif suffix in {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported document type: {suffix}")
    return text[:max_chars]


async def web_search(query: str, settings: Settings, limit: int = 5) -> list[dict]:
    if settings.tavily_api_key:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": settings.tavily_api_key, "query": query, "max_results": limit},
            )
            response.raise_for_status()
            return response.json().get("results", [])
    from ddgs import DDGS

    return await asyncio.to_thread(lambda: list(DDGS().text(query, max_results=limit)))


async def fetch_page(url: str) -> str:
    if not re.match(r"^https?://", url):
        raise ValueError("Only http(s) URLs are allowed")
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": "GemmaStudio/0.1"}
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup(["script", "style", "nav", "footer"]):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:25_000]


async def generate_image(prompt: str, settings: Settings) -> str:
    if not settings.image_model_id:
        raise RuntimeError("Image generation is disabled. Set IMAGE_MODEL_ID in .env.")

    def run() -> str:
        import torch
        from diffusers import AutoPipelineForText2Image

        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        pipe = AutoPipelineForText2Image.from_pretrained(
            settings.image_model_id, torch_dtype=dtype
        )
        pipe = pipe.to("cuda" if torch.cuda.is_available() else "cpu")
        image = pipe(prompt=prompt, num_inference_steps=28).images[0]
        name = f"{uuid4()}.png"
        image.save(settings.generated_dir / name)
        return f"/generated/{name}"

    return await asyncio.to_thread(run)


def research_context(results: list[dict]) -> str:
    return "\n".join(
        f"[{index}] {item.get('title', 'Result')}\nURL: {item.get('url') or item.get('href')}\n"
        f"Summary: {item.get('content') or item.get('body', '')}"
        for index, item in enumerate(results, 1)
    )

