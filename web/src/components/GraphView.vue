<template>
  <div class="card">
    <div class="graph-head">
      <h2>图谱可视化</h2>
      <span class="graph-stats" :class="{ live: building }">
        <span v-if="building" class="live-dot"></span>{{ building ? '实时预览 · ' : '' }}{{ nodes.length }} 节点 / {{ edges.length }} 关系
      </span>
    </div>
    <p v-if="error" class="muted" style="color: var(--danger)">{{ error }}</p>
    <p v-if="!nodes.length" class="muted">
      {{ building ? '正在等待首批抽取结果…' : '暂无节点，先完成一次构建。' }}
    </p>
    <div ref="svgEl" class="graph-svg"></div>
    <!-- 详情浮层 -->
    <div v-if="detail" class="detail">
      <div class="row spread">
        <strong>{{ detail.name }}</strong>
        <button @click="detail = null">✕</button>
      </div>
      <span class="badge">{{ typeOf(detail) }}</span>
      <p class="muted" style="margin-top: 8px; white-space: pre-wrap">{{ detail.summary || '（无摘要）' }}</p>
      <div v-if="detail.facts && detail.facts.length" style="margin-top: 10px">
        <label class="muted">关联事实</label>
        <ul style="margin-top: 4px; padding-left: 18px">
          <li v-for="f in detail.facts" :key="f" style="font-size: 13px">{{ f }}</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as d3 from 'd3'
import { api } from '../api'
import { fitGraphTransform } from '../graphLayout'

const props = defineProps({ graphId: String, building: Boolean, reloadKey: Number })
const nodes = ref([])
const edges = ref([])
const detail = ref(null)
const error = ref('')
const svgEl = ref(null)
let simulation = null
let loadVersion = 0

const PALETTE = ['#4f6ef7', '#30a46c', '#e5484d', '#f7a417', '#9b6ef7', '#0ea5b7', '#e4559f', '#7d8a99']

const typeColors = {}
let colorIdx = 0
function typeOf(n) {
  return (n.labels || []).find((l) => l !== 'Entity') || 'Entity'
}
function colorOf(type) {
  if (!typeColors[type]) typeColors[type] = PALETTE[colorIdx++ % PALETTE.length]
  return typeColors[type]
}

async function load() {
  const version = ++loadVersion
  error.value = ''
  try {
    if (props.building) {
      const preview = await api.preview(props.graphId)
      // 页面切换或构建状态变化后，忽略较早发出的响应，避免空的正式数据覆盖预览结果。
      if (version !== loadVersion) return
      // 本体生成阶段预览尚为空；保留已有正式图谱，避免重构建时画布闪空。
      if (preview.nodes.length || !nodes.value.length) {
        nodes.value = preview.nodes
        edges.value = preview.edges
        render()
      }
      return
    }
    const data = await api.graphData(props.graphId)
    if (version !== loadVersion) return
    nodes.value = data.nodes
    edges.value = data.edges
    render()
  } catch (e) {
    if (version === loadVersion) error.value = e.message
  }
}

function render() {
  simulation?.stop()
  simulation = null
  const el = d3.select(svgEl.value)
  el.selectAll('*').remove()
  if (!nodes.value.length) return
  const width = el.node().clientWidth || 600
  const height = 420

  // d3.forceLink 要求边带 source/target 字段；后端字段是 *_node_uuid，先映射，
  // 并过滤端点不在节点集中的悬空边（分页截断或数据不一致时防御），
  // 同时复制对象避免 forceLink 把 source/target 替换成节点引用后污染原始数据
  const uuidSet = new Set(nodes.value.map((n) => n.uuid))
  const links = edges.value
    .filter((e) => uuidSet.has(e.source_node_uuid) && uuidSet.has(e.target_node_uuid))
    .map((e) => ({ ...e, source: e.source_node_uuid, target: e.target_node_uuid }))

  const factsByUuid = {}
  for (const e of links) {
    for (const u of [e.source, e.target]) {
      ;(factsByUuid[u] ||= []).push(e.fact)
    }
  }

  const svg = el.append('svg').attr('width', width).attr('height', height)
  const g = svg.append('g')
  let userMovedViewport = false

  const zoom = d3.zoom()
    .scaleExtent([0.005, 8])
    .on('zoom', (ev) => g.attr('transform', ev.transform))
  svg.call(zoom)
  svg.on('wheel.fit mousedown.fit touchstart.fit', () => { userMovedViewport = true })

  function fitToViewport() {
    if (userMovedViewport) return
    const transform = fitGraphTransform(nodes.value, width, height)
    svg.call(
      zoom.transform,
      d3.zoomIdentity.translate(transform.x, transform.y).scale(transform.k),
    )
  }

  const sim = d3
    .forceSimulation(nodes.value)
    .force('link', d3.forceLink(links).id((d) => d.uuid).distance(110))
    .force('charge', d3.forceManyBody().strength(-260))
    .force('center', d3.forceCenter(width / 2, height / 2))
  simulation = sim

  const link = g
    .append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', '#aeb7c7')
    .attr('stroke-opacity', 0.55)
    .attr('stroke-width', 0.7)
    .attr('vector-effect', 'non-scaling-stroke')

  const linkLabel = g
    .append('g')
    .selectAll('text')
    .data(links)
    .join('text')
    .text((d) => d.name)
    .attr('font-size', 9)
    .attr('fill', '#8b93a3')
    .attr('text-anchor', 'middle')

  const node = g
    .append('g')
    .selectAll('g')
    .data(nodes.value)
    .join('g')
    .style('cursor', 'pointer')
    .on('click', (ev, d) => {
      detail.value = { ...d, facts: factsByUuid[d.uuid] || [] }
    })

  node
    .append('circle')
    .attr('r', 14)
    .attr('fill', (d) => colorOf(typeOf(d)))
    .attr('stroke', '#fff')
    .attr('stroke-width', 2)

  node
    .append('text')
    .text((d) => d.name)
    .attr('text-anchor', 'middle')
    .attr('dy', 30)
    .attr('font-size', 12)
    .attr('fill', '#1f2430')

  node.call(
    d3
      .drag()
      .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y })
      .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null })
  )

  sim.on('tick', () => {
    link
      .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y)
    linkLabel
      .attr('x', (d) => (d.source.x + d.target.x) / 2)
      .attr('y', (d) => (d.source.y + d.target.y) / 2 - 4)
    node.attr('transform', (d) => `translate(${d.x},${d.y})`)
    fitToViewport()
  })
  sim.on('end', fitToViewport)
}

let previewTimer = null
let pollingVersion = 0
// 构建期间持续刷新预览（每块完成即可见），结束后拉取正式数据并停止轮询。
function syncPreviewPolling() {
  pollingVersion++
  clearTimeout(previewTimer)
  previewTimer = null
  if (props.building) {
    // 请求完成后再安排下一轮，避免慢请求重叠并形成过期响应覆盖。
    const version = pollingVersion
    const poll = async () => {
      await load()
      if (version === pollingVersion && props.building) previewTimer = setTimeout(poll, 1500)
    }
    previewTimer = setTimeout(poll, 1500)
  }
}

onMounted(() => {
  load()
  syncPreviewPolling()
})
watch(
  () => props.building,
  () => {
    load()
    syncPreviewPolling()
  }
)
watch(() => props.reloadKey, load)
onUnmounted(() => {
  pollingVersion++
  clearTimeout(previewTimer)
  loadVersion++
  simulation?.stop()
})
</script>

<style scoped>
.graph-svg { border: 1px solid var(--border); border-radius: 8px; background: #fcfdff; }
.graph-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.graph-stats {
  display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; font-weight: 600;
}
.graph-stats.live { color: var(--ok); }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .35; } }
.detail {
  margin-top: 10px; border: 1px solid var(--border); border-radius: 8px; padding: 10px; background: #fafbff;
}
</style>
