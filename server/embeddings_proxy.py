import os
import threading
from typing import Any, Dict, List

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
GEMINI_MODEL = os.getenv("GEMINI_EMBED_MODEL", "text-embedding-004")
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "768"))
TIMEOUT = int(os.getenv("EMBED_TIMEOUT", "60"))

app = FastAPI(title="Embeddings Proxy (OpenAI -> Gemini)")

_lock = threading.Lock()
_active = "openai" if OPENAI_API_KEY else ("gemini" if GEMINI_API_KEY else "")


class EmbedRequest(BaseModel):
    input: Any
    model: str = OPENAI_MODEL
    dimensions: int = EMBED_DIMS


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _openai_embed(texts: List[str], dims: int) -> List[List[float]]:
    resp = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": OPENAI_MODEL, "input": texts, "dimensions": dims},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def _gemini_embed(texts: List[str], dims: int = EMBED_DIMS) -> List[List[float]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:batchEmbedContents"
    body = {
        "requests": [
            {"model": f"models/{GEMINI_MODEL}", "content": {"parts": [{"text": t}]}, "outputDimensionality": dims} for t in texts
        ]
    }
    resp = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=TIMEOUT)
    resp.raise_for_status()
    return [e["values"] for e in resp.json()["embeddings"]]


def embed(texts: List[str], dims: int) -> List[List[float]]:
    global _active
    with _lock:
        active = _active

    order = [active] if active else []
    for p in ("openai", "gemini"):
        if p not in order:
            order.append(p)

    last_error: Exception | None = None
    for provider in order:
        try:
            if provider == "openai" and OPENAI_API_KEY:
                vectors = _openai_embed(texts, dims)
            elif provider == "gemini" and GEMINI_API_KEY:
                vectors = _gemini_embed(texts, dims)
            else:
                continue
            with _lock:
                if _active != provider:
                    _active = provider
            return vectors
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise HTTPException(status_code=502, detail=f"todos os providers falharam: {last_error}")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "active": _active,
        "openai": bool(OPENAI_API_KEY),
        "gemini": bool(GEMINI_API_KEY),
        "dims": EMBED_DIMS,
    }


@app.post("/v1/embeddings")
def embeddings(req: EmbedRequest) -> Dict[str, Any]:
    texts = _as_list(req.input)
    if not texts:
        raise HTTPException(status_code=400, detail="input vazio")
    dims = req.dimensions or EMBED_DIMS
    vectors = embed(texts, dims)
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": req.model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
