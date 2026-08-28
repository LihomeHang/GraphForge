const BASE = '/api'

async function request(method, path, body) {
  const opts = { method, headers: {} }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const resp = await fetch(BASE + path, opts)
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok || data.success === false) {
    throw new Error(data.error || data.detail || `HTTP ${resp.status}`)
  }
  return data.data
}

export const api = {
  // 图谱
  listGraphs: () => request('GET', '/graphs'),
  createGraph: (name) => request('POST', '/graphs', { name }),
  getGraph: (id) => request('GET', `/graphs/${id}`),
  deleteGraph: (id) => request('DELETE', `/graphs/${id}`),
  // 文档
  uploadDocument: (id, fileList, purpose) => {
    const fd = new FormData()
    for (const f of fileList) fd.append('files', f)
    fd.append('purpose', purpose)
    return fetch(`${BASE}/graphs/${id}/documents`, { method: 'POST', body: fd })
      .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
      .then(({ ok, d }) => {
        if (!ok || d.success === false) throw new Error(d.detail || '上传失败')
        return d.data
      })
  },
  listDocuments: (id) => request('GET', `/graphs/${id}/documents`),
  removeDocument: (id, filename) => request('DELETE', `/graphs/${id}/documents/${encodeURIComponent(filename)}`),
  generateOntology: (id, purpose) =>
    request('POST', `/graphs/${id}/ontology/staged`, { purpose }),
  // 构建
  build: (id, payload) => request('POST', `/graphs/${id}/build`, payload),
  getTask: (taskId) => request('GET', `/tasks/${taskId}`),
  // 读取
  nodes: (id) => request('GET', `/graphs/${id}/nodes?limit=500`),
  edges: (id) => request('GET', `/graphs/${id}/edges?limit=500`),
  // 搜索
  search: (id, query, topK = 10) =>
    request('POST', `/graphs/${id}/search`, { query, top_k: topK }),
  // 导出
  exportUrl: (id) => `${BASE}/graphs/${id}/export/mirofish`,
}
