<template>
  <div class="card">
    <div class="wb-head">
      <h2>构建工作台</h2>
      <span class="badge">{{ graph.name }}</span>
    </div>

    <!-- 步骤指示条 -->
    <div class="wb-steps">
      <div class="step" :class="{ done: !!file }">
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
          :class="{ filled: file, dragover }"
          @click="pick"
          @dragover.prevent="dragover = true"
          @dragleave="dragover = false"
          @drop.prevent="onDrop"
        >
          <input ref="fileInput" type="file" accept=".pdf,.md,.txt,.markdown" hidden @change="onFile" />
          <template v-if="file">
            <span class="file-icon">📄</span>
            <div class="file-meta">
              <strong>{{ file.name }}</strong>
              <span class="muted">{{ fmtSize(file.size) }}</span>
            </div>
            <button class="icon-btn" title="移除文件" @click.stop="clearFile">✕</button>
          </template>
          <template v-else>
            <span class="file-icon">⬆️</span>
            <div class="file-meta">
              <strong>拖拽文件到此处，或点击选择</strong>
              <span class="muted">支持 PDF / Markdown / 纯文本</span>
            </div>
          </template>
        </div>

        <input v-model="purpose" class="purpose" placeholder="分析目的（可选，帮助生成更贴合的本体）" />

        <div class="actions">
          <button :disabled="!file || busy" @click="genOntology">生成预览本体</button>
          <button class="primary" :disabled="!file || busy" @click="buildDirect">直接构建图谱</button>
        </div>
        <p class="muted hint">「直接构建」自动生成本体并开始抽取；想先审阅本体请先生成预览。</p>
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
          <button class="primary build-btn" :disabled="busy" @click="buildWithOntology">用此本体构建</button>
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
      <p v-if="task.status === 'failed'" class="task-error">{{ task.error }}</p>
      <a v-if="graph.status === 'ready'" class="export-btn" :href="api.exportUrl(graph.graph_id)">
        ⬇ 导出 MiroFish 兼容包 (zip)
      </a>
    </div>
    <p v-if="error" class="task-error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import { api } from '../api'

const props = defineProps({ graph: Object })
const emit = defineEmits(['refresh'])

const file = ref(null)
const purpose = ref('')
const ontology = ref(null)
const task = ref(null)
const busy = ref(false)
const error = ref('')
const dragover = ref(false)
const fileInput = ref(null)
let pollTimer = null

function onFile(e) {
  file.value = e.target.files[0] || null
  e.target.value = ''
  ontology.value = null
}

function onDrop(e) {
  dragover.value = false
  const f = e.dataTransfer.files && e.dataTransfer.files[0]
  if (!f) return
  file.value = f
  ontology.value = null
}

function pick() {
  fileInput.value?.click()
}

function clearFile() {
  file.value = null
  ontology.value = null
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
    ontology.value = await api.generateOntology(props.graph.graph_id, file.value, purpose.value)
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
    }
  } catch (e) {
    error.value = e.message
  }
}

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
.export-btn {
  font-size: 13px;
  color: var(--primary);
  text-decoration: none;
  align-self: flex-start;
}
.export-btn:hover { text-decoration: underline; }
</style>
