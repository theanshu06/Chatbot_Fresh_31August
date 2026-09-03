const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8100";

async function request(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: isForm ? options.headers : { "Content-Type": "application/json", ...options.headers },
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

export function checkHealth() {
  return request("/api/health");
}

export function ingestPdf(file) {
  const form = new FormData();
  form.append("file", file);
  return request("/api/ingest/pdf", { method: "POST", body: form });
}

export function ingestWebsite(url, maxPages, maxDepth) {
  return request("/api/ingest/website", {
    method: "POST",
    body: JSON.stringify({ url, max_pages: maxPages, max_depth: maxDepth }),
  });
}

export function listDatabaseTables(params) {
  return request("/api/database/tables", { method: "POST", body: JSON.stringify(params) });
}

export function ingestDatabase(params) {
  return request("/api/ingest/database", { method: "POST", body: JSON.stringify(params) });
}

export function getIngested() {
  return request("/api/ingested");
}

export function clearIngested() {
  return request("/api/ingested", { method: "DELETE" });
}

export function deleteSource(sourceId) {
  return request(`/api/ingested/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
}

export function chat(question, topK, sourceType) {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify({ question, top_k: topK, source_type: sourceType || null }),
  });
}

export function search(query, topK, sourceType) {
  return request("/api/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK, source_type: sourceType || null }),
  });
}

export function getIngestedDbTables() {
  return request("/api/database/tables-ingested");
}

export function getRelationships() {
  return request("/api/relationships");
}

export function addRelationship(rel) {
  return request("/api/relationships", { method: "POST", body: JSON.stringify(rel) });
}

export function confirmRelationship(relId) {
  return request(`/api/relationships/${encodeURIComponent(relId)}/confirm`, { method: "POST" });
}

export function deleteRelationship(relId) {
  return request(`/api/relationships/${encodeURIComponent(relId)}`, { method: "DELETE" });
}
