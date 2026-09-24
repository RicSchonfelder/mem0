#!/usr/bin/env node
// MCP (stdio) server exposing the Mem0 Obsidian memory service.
// Tools: memory_search, memory_add, memory_list, memory_health.
// Env: MEM0_URL, MEM0_API_KEY.
import readline from "node:readline";

const URL = (process.env.MEM0_URL || "https://mem.iag.app.br").replace(/\/$/, "");
const KEY = process.env.MEM0_API_KEY || "";
const PROTOCOL_VERSION = "2024-11-05";
const SERVER_INFO = { name: "mem0-obsidian", version: "1.0.0" };

const TOOLS = [
  {
    name: "memory_search",
    description:
      "Busca semantica (hibrida) no vault do Obsidian indexado no Mem0. Retorna os trechos mais relevantes com caminho do arquivo e score.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Pergunta ou termos de busca." },
        limit: { type: "integer", description: "Numero de resultados (padrao 5).", default: 5 },
      },
      required: ["query"],
    },
  },
  {
    name: "memory_add",
    description:
      "Grava uma memoria no Mem0. Por padrao tambem salva uma nota .md no vault do Obsidian e a indexa.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "Conteudo a memorizar." },
        title: { type: "string", description: "Titulo da nota (opcional)." },
        tags: { type: "array", items: { type: "string" }, description: "Tags da nota." },
        metadata: { type: "object", description: "Metadados opcionais." },
        save_to_vault: { type: "boolean", description: "Se true, cria nota .md no vault.", default: true },
      },
      required: ["text"],
    },
  },
  {
    name: "memory_list",
    description: "Lista memorias recentes do indice.",
    inputSchema: {
      type: "object",
      properties: { limit: { type: "integer", description: "Quantidade (padrao 20).", default: 20 } },
    },
  },
  {
    name: "memory_health",
    description: "Verifica o status do servico de memoria.",
    inputSchema: { type: "object", properties: {} },
  },
];

async function http(method, path, payload) {
  const headers = { "Content-Type": "application/json", "User-Agent": "curl/8.7.1" };
  if (KEY) headers["X-API-Key"] = KEY;
  try {
    const res = await fetch(URL + path, {
      method,
      headers,
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const text = await res.text();
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      body = { raw: text.slice(0, 500) };
    }
    return { status: res.status, body };
  } catch (err) {
    return { status: 0, body: { error: String(err) } };
  }
}

function fmtResults(results) {
  let list = results;
  if (!Array.isArray(list)) list = (list && list.results) || [];
  if (!list.length) return "Nenhum resultado.";
  return list
    .map((r) => {
      const meta = r.metadata || {};
      const path = meta.path || "?";
      const score = r.combined_score ?? r.score ?? 0;
      let text = (r.memory || "").replace(/\n/g, " ").trim();
      if (text.length > 300) text = text.slice(0, 300) + "...";
      return `- (${Number(score).toFixed(3)}) ${path}\n  ${text}`;
    })
    .join("\n");
}

async function callTool(name, args) {
  args = args || {};
  if (name === "memory_search") {
    if (!args.query) return { text: "Erro: 'query' obrigatorio.", isError: true };
    const limit = parseInt(args.limit ?? 5, 10) || 5;
    const { status, body } = await http("POST", "/search", { query: args.query, limit });
    if (status !== 200) return { text: `Erro ${status}: ${JSON.stringify(body)}`, isError: true };
    return { text: fmtResults(body.results), isError: false };
  }
  if (name === "memory_add") {
    if (!args.text) return { text: "Erro: 'text' obrigatorio.", isError: true };
    const payload = {
      messages: [{ role: "user", content: args.text }],
      infer: false,
      save_to_vault: args.save_to_vault !== false,
      title: args.title,
      tags: args.tags,
      metadata: args.metadata || { source: "opencode-mcp" },
    };
    const { status, body } = await http("POST", "/memories", payload);
    if (status !== 200) return { text: `Erro ${status}: ${JSON.stringify(body)}`, isError: true };
    const path = body.vault_path ? ` nota: ${body.vault_path}` : " (sem nota)";
    return { text: `Memoria gravada.${path}`, isError: false };
  }
  if (name === "memory_list") {
    const limit = parseInt(args.limit ?? 20, 10) || 20;
    const { status, body } = await http("GET", `/memories?limit=${limit}`);
    if (status !== 200) return { text: `Erro ${status}: ${JSON.stringify(body)}`, isError: true };
    return { text: fmtResults(body.results), isError: false };
  }
  if (name === "memory_health") {
    const { status, body } = await http("GET", "/health");
    return { text: `${status} ${JSON.stringify(body)}`, isError: status !== 200 };
  }
  return { text: `Tool desconhecida: ${name}`, isError: true };
}

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  line = line.trim();
  if (!line) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return;
  }
  const method = msg.method;
  const id = msg.id;
  if (method === "initialize") {
    const version = (msg.params && msg.params.protocolVersion) || PROTOCOL_VERSION;
    send({ jsonrpc: "2.0", id, result: { protocolVersion: version, capabilities: { tools: {} }, serverInfo: SERVER_INFO } });
  } else if (method === "notifications/initialized" || method === "notifications/cancelled") {
    // ignore
  } else if (method === "ping") {
    send({ jsonrpc: "2.0", id, result: {} });
  } else if (method === "tools/list") {
    send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
  } else if (method === "tools/call") {
    const { text, isError } = await callTool(msg.params && msg.params.name, (msg.params && msg.params.arguments) || {});
    send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text }], isError } });
  } else if (id !== undefined) {
    send({ jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found: " + method } });
  }
});
