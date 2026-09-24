#!/bin/sh
uvicorn embeddings_proxy:app --host 127.0.0.1 --port 8091 --log-level warning &
exec uvicorn server:app --host 0.0.0.0 --port 8090
