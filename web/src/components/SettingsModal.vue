<template>
  <div v-if="open" class="modal-mask" @click.self="close">
    <div class="modal">
      <div class="row spread" style="margin-bottom: 8px">
        <h2 style="font-size: 16px">系统设置</h2>
        <button class="icon-btn" @click="close">✕</button>
      </div>
      <p class="muted" style="margin-bottom: 14px">
        LLM / Embedding 配置保存后立即生效（无需重启容器）。API Key 留空表示保持现有值不变。
      </p>

      <div class="field">
        <label>Provider</label>
        <select v-model="form.llm_provider">
          <option value="openai">openai（OpenAI 兼容接口）</option>
          <option value="mock">mock（离线演示，无需 API Key）</option>
        </select>
      </div>

      <template v-if="form.llm_provider === 'openai'">
        <div class="field">
          <label>Base URL</label>
          <input v-model="form.llm_base_url" placeholder="https://api.openai.com/v1" />
        </div>
        <div class="field">
          <label>
            API Key
            <span v-if="keySet" class="muted">（已设置 {{ keyMasked }}，留空保持不变）</span>
          </label>
          <input
            v-model="form.llm_api_key"
            type="password"
            placeholder="sk-..."
            autocomplete="new-password"
          />
        </div>
        <div class="field">
          <label>模型</label>
          <input v-model="form.llm_model" placeholder="gpt-4o-mini" />
        </div>
        <div class="field">
          <label>Temperature</label>
          <input
            v-model.number="form.llm_temperature"
            type="number"
            min="0"
            max="2"
            step="0.1"
            style="width: 120px"
          />
        </div>

        <details style="margin: 8px 0 4px">
          <summary class="muted" style="cursor: pointer; font-size: 13px">
            Embedding 高级选项（缺省复用 LLM 配置）
          </summary>
          <div class="field" style="margin-top: 10px">
            <label>Embedding Base URL</label>
            <input
              v-model="form.embedding_base_url"
              :placeholder="form.llm_base_url || 'https://api.openai.com/v1'"
            />
          </div>
          <div class="field">
            <label>
              Embedding API Key
              <span v-if="embKeySet" class="muted">（已设置，留空保持不变）</span>
            </label>
            <input
              v-model="form.embedding_api_key"
              type="password"
              placeholder="留空复用 LLM API Key"
              autocomplete="new-password"
            />
          </div>
          <div class="field">
            <label>Embedding 模型</label>
            <input v-model="form.embedding_model" placeholder="text-embedding-3-small" />
          </div>
          <div class="field">
            <label>向量维度（留空自动探测）</label>
            <input
              v-model="form.embedding_dim"
              type="number"
              min="1"
              placeholder="自动探测"
              style="width: 160px"
            />
          </div>
        </details>
      </template>
      <p v-else class="muted" style="margin: 8px 0">
        mock 模式使用确定性假客户端，可离线跑通全流程（抽取结果为词频启发式，仅供演示）。
      </p>

      <p v-if="message" :class="messageOk ? 'msg-ok' : 'msg-err'">{{ message }}</p>

      <div class="row" style="justify-content: flex-end; margin-top: 14px; gap: 8px">
        <button :disabled="busy" @click="runTest">测试连接</button>
        <button class="primary" :disabled="busy" @click="save">
          {{ busy ? '处理中…' : '保存并生效' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['update:open', 'saved'])

const form = reactive({
  llm_provider: 'mock',
  llm_base_url: '',
  llm_api_key: '',
  llm_model: '',
  llm_temperature: 0,
  embedding_base_url: '',
  embedding_api_key: '',
  embedding_model: '',
  embedding_dim: '',
})
const keySet = ref(false)
const keyMasked = ref('')
const embKeySet = ref(false)
const message = ref('')
const messageOk = ref(false)
const busy = ref(false)

function notify(text, ok) {
  message.value = text
  messageOk.value = ok
}

async function load() {
  message.value = ''
  try {
    const s = await api.getSettings()
    form.llm_provider = s.llm_provider
    form.llm_base_url = s.llm_base_url || ''
    form.llm_api_key = ''
    form.llm_model = s.llm_model || ''
    form.llm_temperature = s.llm_temperature ?? 0
    form.embedding_base_url = s.embedding_base_url || ''
    form.embedding_api_key = ''
    form.embedding_model = s.embedding_model || ''
    form.embedding_dim = s.embedding_dim ?? ''
    keySet.value = s.llm_api_key_set
    keyMasked.value = s.llm_api_key_masked
    embKeySet.value = s.embedding_api_key_set
  } catch (e) {
    notify(`加载设置失败: ${e.message}`, false)
  }
}

watch(
  () => props.open,
  (v) => {
    if (v) load()
  }
)

// 请求体：只携带用户可见字段；api_key 仅在输入了新值时携带（留空 = 不修改）
function payload() {
  const p = { llm_provider: form.llm_provider }
  if (form.llm_provider === 'openai') {
    p.llm_base_url = form.llm_base_url.trim()
    if (form.llm_api_key.trim()) p.llm_api_key = form.llm_api_key.trim()
    p.llm_model = form.llm_model.trim()
    p.llm_temperature = form.llm_temperature
    p.embedding_base_url = form.embedding_base_url.trim()
    if (form.embedding_api_key.trim()) p.embedding_api_key = form.embedding_api_key.trim()
    p.embedding_model = form.embedding_model.trim()
    p.embedding_dim = form.embedding_dim === '' || form.embedding_dim == null ? null : Number(form.embedding_dim)
  }
  return p
}

async function save() {
  busy.value = true
  try {
    await api.updateSettings(payload())
    notify('已保存，配置立即生效。', true)
    emit('saved')
    await load()
  } catch (e) {
    notify(e.message, false)
  } finally {
    busy.value = false
  }
}

async function runTest() {
  busy.value = true
  notify('测试中…', true)
  try {
    const r = await api.testSettings(payload())
    const parts = []
    let allOk = true
    for (const key of ['llm', 'embedding']) {
      const item = r[key]
      if (!item) continue
      if (!item.ok) allOk = false
      parts.push(`${key === 'llm' ? 'LLM' : 'Embedding'}: ${item.message}`)
    }
    notify(parts.join('\n'), allOk && parts.length > 0)
  } catch (e) {
    notify(e.message, false)
  } finally {
    busy.value = false
  }
}

function close() {
  emit('update:open', false)
}
</script>
