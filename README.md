# Mem0 Stack

🧠 Memória persistente para IAs via MCP - Sistema completo com API, vector store e integração OpenCode.

## Arquitetura

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   OpenCode/     │     │   Mem0 API      │     │    Qdrant       │
│   Claude/etc    │────▶│   (FastAPI)     │────▶│  (Vector DB)    │
│                 │     │   :8090         │     │  :6333          │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │    Ollama       │
                        │  (Embeddings)   │
                        │  :11434         │
                        └─────────────────┘
```

## Componentes

| Componente | Porta | Descrição |
|------------|-------|-----------|
| `mem0-api` | 8090 | API REST para operações de memória |
| `qdrant` | 6333 | Vector database para busca semântica |
| `ollama` | 11434 | Local LLM para embeddings |
| `mem0-mcp` | - | MCP server para OpenCode (stdio) |

## Instalação Rápida

### 1. Clone o repositório

```bash
git clone https://github.com/RicSchonfelder/mem0.git
cd mem0
```

### 2. Configure o ambiente

```bash
# Gerar chave de API
API_KEY=$(openssl rand -hex 32)
echo "Sua chave de API: $API_KEY"

# Criar arquivo .env
cat > .env << EOF
MEM0_API_KEY=$API_KEY
VAULT_PATH=/home/$USER/Obsidian
VAULT_MEM0_PATH=./vault-mem0
LLM_MODEL=llama3.2:3b
EMBED_DIMS=768
EOF
```

### 3. Inicie os serviços

```bash
# Build e start
docker compose up -d

# Aguardar serviços iniciarem
sleep 30

# Verificar saúde
curl http://localhost:8090/health
```

### 4. Configure o MCP no OpenCode

Adicione ao seu `~/.config/opencode/opencode.jsonc`:

```json
{
  "mcp": {
    "mem0": {
      "type": "local",
      "command": [
        "node",
        "/caminho/para/mem0/mcp/mem0_server.mjs"
      ],
      "enabled": true,
      "environment": {
        "MEM0_URL": "http://localhost:8090",
        "MEM0_API_KEY": "sua-chave-aqui"
      }
    }
  }
}
```

## Uso

### Via MCP (OpenCode)

```
# Salvar uma memória
memory_add(text="Decisão: usar Next.js no projeto X", tags=["decisao", "projeto-x"])

# Buscar memórias
memory_search(query="qual framework usar?")

# Listar memórias
memory_list(limit=10)
```

### Via API REST

```bash
# Salvar memória
curl -X POST http://localhost:8090/memories \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sua-chave" \
  -d '{
    "messages": [{"role": "user", "content": "Memória importante"}],
    "save_to_vault": true,
    "tags": ["importante"]
  }'

# Buscar
curl -X POST http://localhost:8090/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sua-chave" \
  -d '{"query": "termo de busca", "limit": 5}'
```

## Acesso em Rede

Para acessar de outras máquinas na rede:

1. **API**: A porta 8090 já está exposta em `0.0.0.0`
2. **MCP**: Use `MEM0_URL=http://IP-DO-SERVIDOR:8090`

### Exemplo: Outro computador na rede

```bash
# No computador remoto
MEM0_URL=http://192.168.100.100:8090 \
MEM0_API_KEY=sua-chave \
node /caminho/para/mem0_server.mjs
```

## Estrutura

```
mem0/
├── server/              # API Server (Python/FastAPI)
│   ├── server.py        # API principal
│   ├── ingest.py        # Ingestão de dados
│   ├── embeddings_proxy.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── start.sh
├── mcp/                 # MCP Server (Node.js)
│   └── mem0_server.mjs
├── docker-compose.yml
├── .env.example
└── README.md
```

## Troubleshooting

### Serviço não inicia

```bash
# Verificar logs
docker compose logs mem0-api

# Reiniciar
docker compose restart mem0-api
```

### Erro de conexão

```bash
# Verificar se os serviços estão rodando
docker compose ps

# Testar API
curl http://localhost:8090/health
```

### Modelo não encontrado

```bash
# Baixar modelo via Ollama
docker exec mem0-ollama ollama pull llama3.2:3b
```

## Licença

MIT
