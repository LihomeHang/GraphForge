import assert from 'node:assert/strict'
import test from 'node:test'

import { api } from './api.js'

test('graphData loads every node and edge page', async (t) => {
  const calls = []
  const totals = { nodes: 501, edges: 701 }

  t.mock.method(globalThis, 'fetch', async (rawUrl) => {
    const url = new URL(rawUrl, 'http://graphforge.test')
    const resource = url.pathname.endsWith('/nodes') ? 'nodes' : 'edges'
    const offset = Number(url.searchParams.get('offset'))
    const limit = Number(url.searchParams.get('limit'))
    const count = Math.max(0, Math.min(limit, totals[resource] - offset))
    calls.push(`${resource}:${offset}`)
    return {
      ok: true,
      json: async () => ({
        success: true,
        data: Array.from({ length: count }, (_, index) => ({ uuid: `${resource}-${offset + index}` })),
      }),
    }
  })

  const data = await api.graphData('graph-1')

  assert.equal(data.nodes.length, 501)
  assert.equal(data.edges.length, 701)
  assert.deepEqual(calls.sort(), ['edges:0', 'edges:500', 'nodes:0', 'nodes:500'])
})
