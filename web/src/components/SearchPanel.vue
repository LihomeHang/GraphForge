<template>
  <div class="card">
    <h2>语义搜索</h2>
    <div class="row">
      <input v-model="query" placeholder="输入查询，如：谁负责该项目？" @keyup.enter="doSearch" />
      <button class="primary" :disabled="!query.trim() || loading" @click="doSearch">
        {{ loading ? '搜索中…' : '搜索' }}
      </button>
    </div>
    <p v-if="error" class="muted" style="color: var(--danger); margin-top: 8px">{{ error }}</p>

    <template v-if="hits.length">
      <label class="muted" style="display: block; margin-top: 12px">节点</label>
      <div v-for="h in hits.filter((x) => x.type === 'node')" :key="h.uuid" class="hit">
        <div class="row spread">
          <strong>{{ h.name }}</strong>
          <span class="muted">{{ h.score.toFixed(3) }}</span>
        </div>
        <p class="muted">{{ h.summary }}</p>
      </div>
      <label class="muted" style="display: block; margin-top: 12px">事实</label>
      <div v-for="h in hits.filter((x) => x.type === 'edge')" :key="h.uuid" class="hit">
        <div class="row spread">
          <span>{{ factText(h) }}</span>
          <span class="muted">{{ h.score.toFixed(3) }}</span>
        </div>
      </div>
    </template>
    <p v-else-if="searched" class="muted" style="margin-top: 12px">无结果</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'

const props = defineProps({ graphId: String })
const query = ref('')
const hits = ref([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')

async function doSearch() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.search(props.graphId, query.value.trim())
    hits.value = data.hits
    searched.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function factText(h) {
  const s = h.source_name || h.source_node_uuid.slice(0, 8)
  const t = h.target_name || h.target_node_uuid.slice(0, 8)
  return `${s} → ${t}：${h.fact}`
}
</script>

<style scoped>
.hit { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; margin-top: 6px; }
</style>
