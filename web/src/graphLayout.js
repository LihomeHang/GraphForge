export function fitGraphTransform(nodes, width, height, padding = 24) {
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const node of nodes) {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) continue
    minX = Math.min(minX, node.x)
    maxX = Math.max(maxX, node.x)
    minY = Math.min(minY, node.y)
    maxY = Math.max(maxY, node.y)
  }
  if (!Number.isFinite(minX)) return { x: 0, y: 0, k: 1 }

  const graphWidth = Math.max(maxX - minX, 1)
  const graphHeight = Math.max(maxY - minY, 1)
  const scale = Math.max(
    0.005,
    Math.min(1, (width - padding * 2) / graphWidth, (height - padding * 2) / graphHeight),
  )
  const centerX = (minX + maxX) / 2
  const centerY = (minY + maxY) / 2

  return {
    x: width / 2 - centerX * scale,
    y: height / 2 - centerY * scale,
    k: scale,
  }
}
