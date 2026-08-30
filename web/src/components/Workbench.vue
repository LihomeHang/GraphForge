<template>
  <div class="card">
    <div class="wb-head">
      <h2>构建工作台</h2>
      <span class="badge">{{ graph.name }}</span>
    </div>

    <!-- 步骤指示条 -->
    <div class="wb-steps">
      <div class="step" :class="{ done: staged.length > 0 }">
        <span class="dot">1</span><span>上传文档</span>
      </div>
      <span class="arrow"></span>
      <div class="step" :class="{ done: !!ontology }">
        <span class="dot">2</span><span>生成本体</span>
      </div>
      <span class="arrow"></span>
      <div class="step" :class="{ done: graph.status === 'ready' }">
        <span class="dot">3</span><span>构建图谱</span>
      </div>
    </div>

    <div class="wb-grid">
      <!-- 左：上传与动作 -->
      <div class="wb-col">
        <div
          class="dropzone"
          :class="{ dragover }"
          @click="pick"
          @dragover.prevent="dragover = true"
          @dragleave="dragover = false"
          @drop.prevent="onDrop"
        >
          <input ref="fileInput" type="file" accept=".pdf,.md,.txt,.markdown" multiple hidden @change="onFile" />
          <span class="file-icon">⬆️</span>
          <div class="file-meta">
            <strong>拖拽文件到此处，或点击选择（可多选）</strong>
            <span class="muted">支持 PDF / Markdown / 纯文本 · 多文件合并构建一个图谱</span>
          </div>
        </div>

        <!-- 待上传队列 -->
        <div v-if="pending.length" class="file-list">
          <div v-for="(f, i) in pending" :key="'p' + i" class="file-item pending">
            <span class="file-icon small">📄</span>
            <div class="file-meta">
              <strong>{{ f.name }}</strong>
              <span class="muted">{{ fmtSize(f.size) }}</span>
            </div>
            <button class="icon-btn" title="移除" @click="pending.splice(i, 1)">✕</button>
          </div>
          <button class="upload-now" :disabled="uploading" @click="uploadAll">
            {{ uploading ? '上传中…' : `⬆ 上传 ${pending.length} 个文件` }}
          </button>
        </div>

        <!-- 已暂存（服务端） -->
        <div v-if="staged.length" class="file-list">
          <div v-for="name in staged" :key="name" class="file-item">
            <span class="file-icon small">✅</span>
            <div class="file-meta">
              <strong>{{ name }}</strong>
              <span class="muted">已就绪，将参与构建</span>
            </div>
            <button class="icon-btn" title="从暂存区移除" @click="removeStaged(name)">✕</button>
          </div>
        </div>
        <input v-model="purpose" class="purpose" placeholder="分析目的（可选，帮助生成更贴合的本体）" />

        <div class="actions">
          <button :disabled="!hasDocs || busy || taskRunning" @click="genOntology">生成预览本体</button>
          <button class="primary" :disabled="!hasDocs || busy || taskRunning" @click="buildDirect">直接构建图谱</button>
        </div>
        <p class="muted hint">
          {{ taskRunning ? '构建任务进行中，构建操作已锁定；可继续上传/移除文档，供下次构建使用。' : '「直接构建」自动生成本体并开始抽取；多个文件将合并为一个语料参与构建。' }}
        </p>
      </div>

      <!-- 右：本体预览 -->
      <div class="wb-col">
        <div v-if="ontology" class="ontology-box">
          <p class="muted summary">{{ ontology.analysis_summary }}</p>
          <div class="tag-row">
            <span class="tag-label">实体类型</span>
            <div class="tags">
              <span v-for="et in ontology.entity_types" :key="et.name" class="badge">{{ et.name }}</span>
            </div>
          </div>
          <div class="tag-row">
            <span class="tag-label">关系类型</span>
            <div class="tags">
              <span v-for="et in ontology.edge_types" :key="et.name" class="badge green">{{ et.name }}</span>
            </div>
          </div>
          <button class="primary build-btn" :disabled="busy || taskRunning" @click="buildWithOntology">用此本体构建</button>
        </div>
        <div v-else class="ontology-empty">
          <p class="muted">尚未生成本体</p>
          <p class="muted">上传文档后点击「生成预览本体」查看抽取蓝图</p>
        </div>
      </div>
    </div>

    <!-- 任务进度 -->
    <div v-if="task" class="task-panel">
      <div class="task-line">
        <span class="task-title">
          任务 <code>{{ task.task_id.slice(0, 8) }}</code>
          <span class="badge" :class="{ ok: task.status === 'completed', err: task.status === 'failed' }">{{ task.status }}</span>
        </span>
        <span class="muted">{{ Math.round(task.progress * 100) }}% · {{ task.message }}</span>
      </div>
      <div class="progress">
        <div :style="{ width: Math.round(task.progress * 100) + '%' }"></div>
      </div>
      <div v-if="task.logs && task.logs.length" class="task-logs">
        <div v-for="(l, i) in task.logs.slice(-12)" :key="i" class="log-line">{{ l }}</div>
      </div>
      <p v-if="task.status === 'failed'" class="task-error">{{ task.error }}</p>
      <div v-if="task.status === 'failed'" class="retry-row">
        <button class="primary" :disabled="busy" @click="retryBuild">↻ 重试构建</button>
        <span class="muted">文件仍在暂存区；重试会复用已抽取的缓存块，不重复消耗 LLM 额度</span>
      </div>
      <a v-if="graph.status === 'ready'" class="export-btn" :href="api.exportUrl(graph.graph_id)">
        ⬇ 导出 MiroFish 兼容包 (zip)
      </a>
    </div>
    <p v-if="error" class="task-error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api'

const props = defineProps({ graph: Object })
const emit = defineEmits(['refresh'])

const pending = ref([]) // 待上传文件（File 对象）
const staged = ref([]) // 服务端已暂存文件名
const uploading = ref(false)
const purpose = ref('')
const ontology = ref(null)
const task = ref(null)
const busy = ref(false)
const error = ref('')
const dragover = ref(false)
const fileInput = ref(null)
const hasDocs = computed(() => staged.value.length > 0)
const taskRunning = computed(() => !!task.value && !['completed', 'failed'].includes(task.value.status))
let pollTimer = null

function addFiles(fileList) {
  for (const f of fileList) {
    if (!pending.value.some((p) => p.name === f.name && p.size === f.size)) {
      pending.value.push(f)
    }
  }
  ontology.value = null
}

function onFile(e) {
  addFiles(e.target.files || [])
  e.target.value = ''
}

function onDrop(e) {
  dragover.value = false
  const files = e.dataTransfer && e.dataTransfer.files
  if (files && files.length) addFiles(files)
}

function pick() {
  fileInput.value?.click()
}

async function refreshStaged() {
  try {
    staged.value = (await api.listDocuments(props.graph.graph_id)).filenames || []
  } catch {
    staged.value = []
  }
}

async function uploadAll() {
  if (!pending.value.length) return
  uploading.value = true
  error.value = ''
  try {
    await api.uploadDocument(props.graph.graph_id, pending.value, purpose.value)
    pending.value = []
    await refreshStaged()
  } catch (e) {
    error.value = e.message
  } finally {
    uploading.value = false
  }
}

async function removeStaged(name) {
  error.value = ''
  try {
    await api.removeDocument(props.graph.graph_id, name)
    await refreshStaged()
  } catch (e) {
    error.value = e.message
  }
}

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

async function genOntology() {
  busy.value = true
  error.value = ''
  try {
    ontology.value = await api.generateOntology(props.graph.graph_id, purpose.value)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function buildDirect() {
  await startBuild({ purpose: purpose.value })
}

async function buildWithOntology() {
  await startBuild({ ontology: ontology.value, purpose: purpose.value })
}

async function retryBuild() {
  error.value = ''
  // 优先复用本次会话生成的本体；否则从图谱元数据取回上次本体，避免重试时重新生成本体。
  // 若连本体都没有（失败在生成阶段），则带 purpose 让管道重新生成。
  let onto = ontology.value
  if (!onto) {
    try {
      onto = (await api.getGraph(props.graph.graph_id)).ontology || null
    } catch {
      onto = null
    }
  }
  await startBuild(onto ? { ontology: onto, purpose: purpose.value } : { purpose: purpose.value })
}

async function startBuild(payload) {
  busy.value = true
  error.value = ''
  try {
    const data = await api.build(props.graph.graph_id, payload)
    task.value = { task_id: data.task_id, status: 'pending', progress: 0, message: '' }
    poll()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function poll() {
  clearTimeout(pollTimer)
  try {
    task.value = await api.getTask(task.value.task_id)
    if (!['completed', 'failed'].includes(task.value.status)) {
      pollTimer = setTimeout(poll, 1000)
    } else {
      emit('refresh')
      // 构建成功后服务端会清空暂存区，同步前端列表
      if (task.value.status === 'completed') await refreshStaged()
    }
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  await refreshStaged()
  // 恢复最近一次构建任务：刷新页面/切换图谱后进度与日志不丢
  try {
    const t = await api.latestTask(props.graph.graph_id)
    if (t) {
      task.value = t
      if (!['completed', 'failed'].includes(t.status)) poll()
    }
  } catch {
    /* 无任务或查询失败时静默，不影响工作台使用 */
  }
})
onUnmounted(() => clearTimeout(pollTimer))
</script>

<style scoped>
.wb-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

/* 步骤指示条 */
.wb-steps {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: #fafbff;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.step {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-dim);
}
.step .dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--border);
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background .2s;
}
.step.done { color: var(--text); font-weight: 500; }
.step.done .dot { background: var(--ok); }
.arrow {
  width: 24px;
  height: 1px;
  background: var(--border);
  position: relative;
}
.arrow::after {
  content: '';
  position: absolute;
  right: 0;
  top: -2.5px;
  border-left: 5px solid var(--border);
  border-top: 3px solid transparent;
  border-bottom: 3px solid transparent;
}

/* 两栏排版 */
.wb-grid {
  display: grid;
  grid-template-columns: 1fr 1.1fr;
  gap: 18px;
}
@media (max-width: 820px) {
  .wb-grid { grid-template-columns: 1fr; }
}
.wb-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 上传拖拽区 */
.dropzone {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1.5px dashed var(--border);
  border-radius: 10px;
  padding: 16px;
  cursor: pointer;
  background: #fafbff;
  transition: border-color .15s, background .15s;
}
.dropzone:hover, .dropzone.dragover {
  border-color: var(--primary);
  background: #f0f3ff;
}
.dropzone.filled {
  border-style: solid;
  background: #fff;
}
.file-icon { font-size: 22px; }
.file-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.file-meta strong {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.icon-btn {
  border: none;
  background: transparent;
  color: var(--text-dim);
  font-size: 14px;
  padding: 4px 8px;
}
.icon-btn:hover { color: var(--danger); }

.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.hint { font-size: 12px; line-height: 1.5; }

/* 文件列表（待上传 / 已暂存） */
.file-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  background: #fff;
}
.file-item.pending { background: #fafbff; }
.file-icon.small { font-size: 15px; }
.upload-now {
  align-self: flex-start;
  border-style: dashed;
}

/* 本体预览 */
.ontology-box {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  background: #fafbff;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.summary { font-size: 12px; line-height: 1.6; }
.tag-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.tag-label {
  font-size: 12px;
  color: var(--text-dim);
  white-space: nowrap;
  padding-top: 3px;
  width: 56px;
  flex-shrink: 0;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.badge.green { background: #e6f6ef; color: var(--ok); }
.build-btn { align-self: flex-start; }
.ontology-empty {
  border: 1.5px dashed var(--border);
  border-radius: 10px;
  padding: 24px 14px;
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  justify-content: center;
}

/* 任务进度 */
.task-panel {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.task-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.task-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.task-error {
  color: var(--danger);
  font-size: 12px;
  margin-top: 4px;
}
.task-logs {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 160px;
  overflow-y: auto;
  background: #f7f8fc;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
}
.log-line {
  font-family: ui-monospace, 'Cascadia Mono', monospace;
  font-size: 11px;
  color: var(--text-dim);
  word-break: break-all;
}
.retry-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.retry-row .muted { font-size: 12px; }
.export-btn {
  font-size: 13px;
  color: var(--primary);
  text-decoration: none;
  align-self: flex-start;
}
.export-btn:hover { text-decoration: underline; }
</style>
