<template>
  <div class="card">
    <div class="row spread" style="margin-bottom: 12px">
      <h2>我的图谱</h2>
      <div class="row">
        <input v-model="newName" placeholder="新图谱名称" style="width: 220px" @keyup.enter="create" />
        <button class="primary" :disabled="!newName.trim() || creating" @click="create">
          {{ creating ? '创建中…' : '+ 创建图谱' }}
        </button>
      </div>
    </div>
    <p v-if="error" class="muted" style="color: var(--danger)">{{ error }}</p>
    <table v-if="graphs.length">
      <thead>
        <tr><th>名称</th><th>状态</th><th>节点</th><th>边</th><th>创建时间</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="g in graphs" :key="g.graph_id">
          <td><strong>{{ g.name }}</strong></td>
          <td>
            <span class="badge" :class="{ ok: g.status === 'ready', err: g.status === 'failed' }">
              {{ g.status }}
            </span>
          </td>
          <td>{{ g.node_count }}</td>
          <td>{{ g.edge_count }}</td>
          <td class="muted">{{ fmtTime(g.created_at) }}</td>
          <td class="row" style="justify-content: flex-end">
            <button class="primary" @click="$emit('enter', g.graph_id)">进入</button>
            <button class="danger" @click="remove(g)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted">暂无图谱，先创建一个。</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['enter'])
const graphs = ref([])
const newName = ref('')
const creating = ref(false)
const error = ref('')

async function load() {
  try {
    graphs.value = await api.listGraphs()
  } catch (e) {
    error.value = `加载失败: ${e.message}（请确认后端已启动）`
  }
}

async function create() {
  creating.value = true
  error.value = ''
  try {
    await api.createGraph(newName.value.trim())
    newName.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function remove(g) {
  if (!confirm(`确定删除图谱「${g.name}」？节点、边、向量将一并删除。`)) return
  await api.deleteGraph(g.graph_id)
  await load()
}

function fmtTime(t) {
  return t ? new Date(t).toLocaleString('zh-CN') : '-'
}

onMounted(load)
defineExpose({ load })
</script>
