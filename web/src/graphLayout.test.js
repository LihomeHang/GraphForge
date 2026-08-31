import assert from 'node:assert/strict'
import test from 'node:test'

import { fitGraphTransform } from './graphLayout.js'

test('fitGraphTransform scales a large layout into the viewport', () => {
  const transform = fitGraphTransform(
    [{ x: -1000, y: -500 }, { x: 1000, y: 500 }],
    600,
    420,
    30,
  )

  assert.deepEqual(transform, { x: 300, y: 210, k: 0.27 })
})

test('fitGraphTransform keeps a small layout at natural scale', () => {
  const transform = fitGraphTransform(
    [{ x: 100, y: 80 }, { x: 200, y: 160 }],
    600,
    420,
    30,
  )

  assert.deepEqual(transform, { x: 150, y: 90, k: 1 })
})

test('fitGraphTransform can fit a very large graph below two percent scale', () => {
  const transform = fitGraphTransform(
    [{ x: -12500, y: -12500 }, { x: 12500, y: 12500 }],
    705,
    420,
    24,
  )

  assert.ok(transform.k < 0.02)
  assert.ok(transform.k > 0.01)
})
