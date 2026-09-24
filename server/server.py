import datetime
import hashlib
import json
import os
import pathlib
import re
import unicodedata
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from mem0 import Memory

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("COLLECTION", "obsidian")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2:3b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = int(os.getenv("EMBED_DIMS", "768"))
DEFAULT_USER = os.getenv("DEFAULT_USER", "obsidian")
NOISE_PATTERNS = [p for p in os.getenv("NOISE_PATH_PATTERNS", "Analise de Perfil/,notas/video-").split(",") if p]
NOISE_PENALTY = float(os.getenv("NOISE_PENALTY", "0.5"))
VAULT_MEM0_DIR = os.getenv("VAULT_MEM0_DIR", "/vault-mem0")
VAULT_MEM0_REL = os.getenv("VAULT_MEM0_REL", "memoria/00-Sistema-Operacional/Mem0")
STATE_FILE = os.getenv("STATE_FILE", "/state/ingest-state.json")
FILE_UID = int(os.getenv("MEM0_FILE_UID", "1000"))
FILE_GID = int(os.getenv("MEM0_FILE_GID", "1000"))

CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": LLM_MODEL,
            "ollama_base_url": OLLAMA_URL,
            "temperature": 0.1,
            "max_tokens": 2000,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": os.getenv("EMBED_MODEL", "text-embedding-3-small"),
            "openai_base_url": os.getenv("EMBED_BASE_URL", "http://127.0.0.1:8091/v1"),
            "api_key": os.getenv("EMBED_API_KEY", "local-proxy"),
            "embedding_dims": EMBED_DIMS,
        },
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "collection_name": COLLECTION,
            "embedding_model_dims": EMBED_DIMS,
        },
    },
}

app = FastAPI(title="Mem0 Obsidian Service", version="1.0.0")

API_KEY = os.getenv("MEM0_API_KEY", "").strip()
PUBLIC_PATHS = {"/health"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if API_KEY and request.url.path not in PUBLIC_PATHS:
        supplied = request.headers.get("x-api-key", "")
        if not supplied:
            authz = request.headers.get("authorization", "")
            if authz.lower().startswith("bearer "):
                supplied = authz[7:].strip()
        if supplied != API_KEY:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)

_memory: Optional[Memory] = None


def memory() -> Memory:
    global _memory
    if _memory is None:
        try:
            ensure_collection()
        except Exception as exc:  # noqa: BLE001
            print(f"aviso: falha ao garantir collection: {exc}")
        _memory = Memory.from_config(CONFIG)
    return _memory


def ensure_collection() -> bool:
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    names = [c.name for c in client.get_collections().collections]
    if COLLECTION not in names:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIMS, distance=Distance.COSINE),
        )
        return True
    return False


def _slug(text: str, maxlen: int = 60) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:maxlen] or "memoria"


def _message_text(messages: Any) -> str:
    if isinstance(messages, str):
        return messages
    if isinstance(messages, dict):
        messages = [messages]
    parts: List[str] = []
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict):
                parts.append(str(m.get("content", "")))
            elif isinstance(m, str):
                parts.append(m)
    return "\n".join(p for p in parts if p).strip()


def _save_note(text: str, title: Optional[str], tags: Optional[List[str]], metadata: Optional[Dict[str, Any]]) -> str:
    now = datetime.datetime.now()
    date = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M%S")
    first = text.strip().splitlines()[0] if text.strip() else "memoria"
    base = (title or first)[:80]
    fname = f"{date}-{stamp}-{_slug(base)}.md"
    os.makedirs(VAULT_MEM0_DIR, exist_ok=True)
    abs_path = os.path.join(VAULT_MEM0_DIR, fname)
    tag_list = tags or ["ia/memoria", "mem0"]
    fm = ["---", f'title: "{base}"', f"date: {date}", "tags:"]
    fm.extend(f"  - {t}" for t in tag_list)
    if metadata:
        fm.append("metadata:")
        for k, v in metadata.items():
            fm.append(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
    fm.append("source: mem0-mcp")
    fm.append("---")
    body = "\n".join(fm) + "\n\n" + text.strip() + "\n"
    with open(abs_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        os.chmod(abs_path, 0o644)
        os.chown(abs_path, FILE_UID, FILE_GID)
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: chown/chmod falhou ({abs_path}): {exc}")
    return f"{VAULT_MEM0_REL}/{fname}"


def _extract_ids(result: Any) -> List[str]:
    ids: List[str] = []
    items = result
    if isinstance(result, dict):
        items = result.get("results", result.get("result", result))
    if isinstance(items, dict):
        items = [items]
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
    return ids


def _register_state(rel_path: str, ids: List[str]) -> None:
    try:
        abs_path = os.path.join(VAULT_MEM0_DIR, os.path.basename(rel_path))
        st = os.stat(abs_path)
        digest = hashlib.sha1(f"{st.st_mtime}:{st.st_size}".encode()).hexdigest()
        p = pathlib.Path(STATE_FILE)
        state = json.loads(p.read_text()) if p.exists() else {}
        state[rel_path] = {"digest": digest, "ids": ids, "chunks": 1}
        p.write_text(json.dumps(state, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: falha ao atualizar state ({rel_path}): {exc}")


def _tokens(text: str) -> set:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]{3,}", text))


def _prefix_hits(query_tokens: set, doc_tokens: set) -> float:
    if not query_tokens:
        return 0.0
    hits = 0
    for q in query_tokens:
        stem = q[:5]
        if any(d.startswith(stem) for d in doc_tokens):
            hits += 1
    return hits / len(query_tokens)


def _path_bonus(query_tokens: set, path: str) -> float:
    path_tokens = _tokens(path)
    if not query_tokens:
        return 0.0
    hits = sum(1 for q in query_tokens if any(t.startswith(q[:5]) for t in path_tokens))
    return hits / len(query_tokens)


class AddRequest(BaseModel):
    messages: Any
    user_id: str = DEFAULT_USER
    metadata: Optional[Dict[str, Any]] = None
    infer: bool = False
    save_to_vault: bool = False
    title: Optional[str] = None
    tags: Optional[List[str]] = None


class SearchRequest(BaseModel):
    query: str
    user_id: str = DEFAULT_USER
    limit: int = 10


class DeleteRequest(BaseModel):
    memory_id: str


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "collection": COLLECTION, "llm": LLM_MODEL, "embedder": EMBED_MODEL}


@app.post("/memories")
def add(req: AddRequest) -> Dict[str, Any]:
    try:
        text = _message_text(req.messages)
        rel_path = None
        if req.save_to_vault and text:
            rel_path = _save_note(text, req.title, req.tags, req.metadata)
        metadata = dict(req.metadata or {})
        if rel_path:
            metadata.update({"source": "mem0-note", "path": rel_path, "heading": req.title or ""})
        result = memory().add(req.messages, user_id=req.user_id, metadata=metadata or None, infer=req.infer)
        ids = _extract_ids(result)
        if rel_path:
            _register_state(rel_path, ids)
        return {"result": result, "vault_path": rel_path}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/search")
def search(req: SearchRequest) -> Dict[str, Any]:
    try:
        mem = memory()
        fetch = max(req.limit * 10, 100)
        raw = mem.search(req.query, filters={"user_id": req.user_id}, top_k=fetch, threshold=0.05)
        items = raw.get("results", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            items = []

        q = _tokens(req.query)
        best: Dict[str, Dict[str, Any]] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            meta = it.get("metadata", {}) or {}
            path = meta.get("path", "") or it.get("id", "")
            text = it.get("memory", "")
            vec = float(it.get("score", 0) or 0)
            lex = _prefix_hits(q, _tokens(text))
            bonus = _path_bonus(q, str(path))
            score = 0.55 * vec + 0.30 * lex + 0.15 * bonus
            for pat in NOISE_PATTERNS:
                if pat and pat in str(path):
                    score *= NOISE_PENALTY
                    break
            it["vector_score"] = round(vec, 4)
            it["combined_score"] = round(score, 4)
            key = str(path) or str(it.get("id"))
            if key not in best or it["combined_score"] > best[key]["combined_score"]:
                best[key] = it

        ranked = sorted(best.values(), key=lambda x: x["combined_score"], reverse=True)[: req.limit]
        return {"results": ranked}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/memories")
def get_all(user_id: str = DEFAULT_USER, limit: int = 100) -> Dict[str, Any]:
    try:
        results = memory().get_all(filters={"user_id": user_id}, limit=limit)
        return {"results": results}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/memories")
def delete(req: DeleteRequest) -> Dict[str, Any]:
    try:
        memory().delete(req.memory_id)
        return {"deleted": req.memory_id}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
