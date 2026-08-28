<template>
  <div class="card">
    <h2>构建工作台 <span class="badge">{{ graph.name }}</span></h2>

    <div class="row" style="align-items: flex-start; flex-wrap: wrap">
      <!-- 步骤 1：上传 -->
      <div style="flex: 1; min-width: 260px">
        <label class="muted">① 上传文档（pdf / md / txt）</label>
        <div class="row" style="margin-top: 6px">
          <input type="file" accept=".pdf,.md,.txt,.markdown" @change="onFile" />
        </div>
        <input v-model="purpose" placeholder="分析目的（可选）" style="margin-top: 8px" />
        <div class="row" style="margin-top: 8px">
          <button :disabled="!file || busy" @click="genOntology">② 生成本体</button>
          <button class="primary" :disabled="!file || busy" @click="buildDirect">
            直接构建（自动本体）
          </button>
        </div>
      </div>

      <!-- 步骤 2：本体预览 -->
      <div style="flex: 1.4; min-width: 300px">
        <label class="muted">② 本体预览（只读）</label>
        <div v-if="ontology" class="ontology-box">
          <p class="muted" style="margin-bottom: 6px">{{ ontology.analysis_summary }}</p>
          <div class="row" style="flex-wrap: wrap; gap: 4px">
            <span v-for="et in ontology.entity_types" :key="et.name" class="badge">{{ et.name }}</span>
          </div>
          <div class="row" style="flex-wrap: wrap; gap: 4px; margin-top: 6px">
            <span v-for="et in ontology.edge_types" :key="et.name" class="badge" style="background: #f0fdf4; color: var(--ok)">{{ et.name }}</span>
          </div>
          <button class="primary" style="margin-top: 10px" :disabled="busy" @click="buildWithOntology">
            ③ 用此本体构建
          </button>
        </div>
        <p v-else class="muted" style="margin-top: 6px">尚未生成本体</p>
      </div>
    </div>

    <!-- 任务进度 -->
    <div v-if="task" style="margin-top: 14px">
      <div class="row spread">
        <span>
          任务 <code>{{ task.task_id.slice(0, 8) }}</code>
          <span class="badge" :class="{ ok: task.status === 'completed', err: task.status === 'failed' }">{{ task.status }}</span>
        </span>
        <span class="muted">{{ task.message }}</span>
      </div>
      <div class="progress" style="margin-top: 6px">
        <div :style="{ width: Math.round(task.progress * 100) + '%' }"></div>
      </div>
      <p v-if="task.status === 'failed'" class="muted" style="color: var(--danger)">{{ task.error }}</p>
      <a v-if="graph.status === 'ready'" :href="api.exportUrl(graph.graph_id)" style="font-size: 13px">
        ⬇ 导出 MiroFish 兼容包 (zip)
      </a>
    </div>
    <p v-if="error" class="muted" style="color: var(--danger); margin-top: 8px">{{ error }}</p>
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
let pollTimer = null

function onFile(e) {
  file.value = e.target.files[0] || null
  ontology.value = null
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
.ontology-box {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  margin-top: 6px;
  background: #fafbff;
}
input[type='file'] { border: none; padding: 0; }
</style>
