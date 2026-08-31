<template>
  <header class="app-header">
    <div class="logo"></div>
    <h1>GraphForge</h1>
    <span class="muted">文档 → 知识图谱 → 语义搜索</span>
    <div style="flex: 1"></div>
    <span v-if="llmBadge" class="badge" :title="'当前 LLM 配置'">{{ llmBadge }}</span>
    <button @click="settingsOpen = true">⚙ 设置</button>
    <button v-if="currentGraph" @click="backToList">← 返回图列表</button>
  </header>

  <SettingsModal v-model:open="settingsOpen" @saved="loadLlmBadge" />

  <div class="container">
    <!-- 区块 1：图列表 -->
    <GraphList v-if="!currentGraph" @enter="enterGraph" />

    <!-- 区块 2/3/4：图工作台 -->
    <template v-else>
      <Workbench :graph="currentGraph" @refresh="refreshGraph" @task-change="onTaskChange" />
      <div class="row" style="align-items: flex-start; gap: 16px">
        <div style="flex: 1.6; min-width: 0">
          <GraphView :graph-id="currentGraph.graph_id" :building="buildActive" :reload-key="reloadKey" />
        </div>
        <div style="flex: 1; min-width: 0">
          <SearchPanel :graph-id="currentGraph.graph_id" />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import GraphList from './components/GraphList.vue'
import Workbench from './components/Workbench.vue'
import GraphView from './components/GraphView.vue'
import SearchPanel from './components/SearchPanel.vue'
import SettingsModal from './components/SettingsModal.vue'
import { api } from './api'

const currentGraph = ref(null)
const reloadKey = ref(0)
const buildActive = ref(false)
const settingsOpen = ref(false)
const llmBadge = ref('')

async function loadLlmBadge() {
  try {
    const s = await api.getSettings()
    llmBadge.value = s.llm_provider === 'mock' ? 'Mock 模式' : `LLM: ${s.llm_model}`
  } catch {
    llmBadge.value = ''
  }
}
onMounted(loadLlmBadge)

async function enterGraph(id) {
  const g = await api.getGraph(id)
  currentGraph.value = g
  buildActive.value = g.status === 'building'
  reloadKey.value++
}

function onTaskChange(task) {
  buildActive.value = !!task && !['completed', 'failed'].includes(task.status)
}

async function refreshGraph() {
  if (currentGraph.value) {
    currentGraph.value = await api.getGraph(currentGraph.value.graph_id)
  }
}

function backToList() {
  currentGraph.value = null
  buildActive.value = false
}
</script>
