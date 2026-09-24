import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

API = os.getenv("MEM0_API_URL", "http://127.0.0.1:8090")
VAULT_DIR = Path(os.getenv("VAULT_DIR", "/vault"))
STATE_FILE = Path(os.getenv("STATE_FILE", "/state/ingest-state.json"))
USER_ID = os.getenv("DEFAULT_USER", "obsidian")
MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "1500"))
OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "120"))
API_KEY = os.getenv("MEM0_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def split_text(text: str, max_chars: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            window = text[start:end]
            brk = max(window.rfind("\n\n"), window.rfind("\n#"), window.rfind(". "))
            if brk > max_chars // 2:
                end = start + brk + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def heading_for(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:120]
    return ""


def extract_ids(result: Any) -> List[str]:
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


def delete_ids(ids: List[str]) -> None:
    for mid in ids:
        try:
            requests.delete(f"{API}/memories", json={"memory_id": mid}, headers=HEADERS, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! falha ao deletar {mid}: {exc}", file=sys.stderr)


def add_chunks(rel: str, chunks: List[str]) -> List[str]:
    ids: List[str] = []
    for i, chunk in enumerate(chunks):
        payload = {
            "messages": [{"role": "user", "content": chunk}],
            "user_id": USER_ID,
            "infer": False,
            "save_to_vault": False,
            "metadata": {
                "source": "obsidian",
                "path": rel,
                "heading": heading_for(chunk),
                "chunk": i,
            },
        }
        resp = requests.post(f"{API}/memories", json=payload, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        ids.extend(extract_ids(resp.json().get("result", {})))
    return ids


def iter_markdown(root: Path):
    for path in root.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexa um vault Obsidian no Mem0.")
    parser.add_argument("--full", action="store_true", help="Reindexa tudo ignorando o estado.")
    parser.add_argument("--limit", type=int, default=0, help="Limita o numero de arquivos (0 = todos).")
    args = parser.parse_args()

    if not VAULT_DIR.exists():
        print(f"vault nao encontrado: {VAULT_DIR}", file=sys.stderr)
        return 1

    state = {} if args.full else load_state()
    files = sorted(iter_markdown(VAULT_DIR))
    if args.limit:
        files = files[: args.limit]

    print(f"vault={VAULT_DIR} arquivos={len(files)} modo={'full' if args.full else 'incremental'}")

    processed = 0
    skipped = 0
    total_chunks = 0
    started = time.time()

    for idx, path in enumerate(files, 1):
        rel = str(path.relative_to(VAULT_DIR))
        print(f"[{idx}/{len(files)}] {rel}", flush=True)
        try:
            mtime = path.stat().st_mtime
            digest = hashlib.sha1(f"{mtime}:{path.stat().st_size}".encode()).hexdigest()
        except OSError:
            continue

        prev = state.get(rel)
        if prev and prev.get("digest") == digest:
            skipped += 1
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            print(f"  ! leitura falhou {rel}: {exc}", file=sys.stderr)
            continue

        if prev and prev.get("ids"):
            delete_ids(prev["ids"])

        chunks = split_text(text, MAX_CHARS, OVERLAP)
        if not chunks:
            state[rel] = {"digest": digest, "ids": []}
            continue

        try:
            ids = add_chunks(rel, chunks)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! indexacao falhou {rel}: {exc}", file=sys.stderr)
            continue

        state[rel] = {"digest": digest, "ids": ids, "chunks": len(chunks)}
        processed += 1
        total_chunks += len(chunks)

        if idx % 25 == 0 or idx == len(files):
            save_state(state)
            elapsed = time.time() - started
            print(f"[{idx}/{len(files)}] indexados={processed} pulados={skipped} chunks={total_chunks} ({elapsed:.0f}s)")

    save_state(state)
    print(f"concluido: indexados={processed} pulados={skipped} chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
