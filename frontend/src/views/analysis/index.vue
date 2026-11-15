<template>
  <section class="page" data-module="analysis-entry">
    <header class="page-head">
      <div>
        <h2>跨模块消费侧分析入口</h2>
        <p class="page-desc">实例化只读取场景模板冻结版本里的实例化参数，不回查模板工作副本；只传模板编号时以首个冻结版本为准，并发实例化结果一致。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/scene">返回场景模板管理</RouterLink>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="instantiateByTemplate">
      <label class="filter-item">
        <span>场景模板编号</span>
        <input v-model="templateCode" placeholder="如 SCN-0002" />
      </label>
      <button class="btn primary" type="submit">按首个冻结版本实例化</button>
      <button class="btn" type="button" @click="instantiateConcurrent">同时发起两个实例化</button>
      <button class="btn ghost" type="button" @click="loadAll">刷新</button>
    </form>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="infoMessage" class="info-text">{{ infoMessage }}</span>
    </footer>

    <section v-if="latest" class="panel highlight">
      <h3>本次实例化结果（只读冻结参数）</h3>
      <dl class="param-grid">
        <div><dt>实例编号</dt><dd>{{ latest['实例编号'] }}</dd></div>
        <div><dt>模板编号</dt><dd>{{ latest['模板编号'] }}</dd></div>
        <div><dt>冻结版本</dt><dd>{{ latest['冻结版本'] }}</dd></div>
        <div><dt>适用场景</dt><dd>{{ latest['适用场景'] }}</dd></div>
        <div><dt>预设指标</dt><dd>{{ formatCell(latest['预设指标']) }}</dd></div>
        <div><dt>推荐图表</dt><dd>{{ formatCell(latest['推荐图表']) }}</dd></div>
        <div v-for="(value, key) in latestParams" :key="key">
          <dt>{{ key }}</dt>
          <dd>{{ value }}</dd>
        </div>
      </dl>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>实例化记录</h3>
        <span class="page-desc">模板后续修改（如默认区域改为华东）只产生新版本，这里的旧实例参数保持不变。</span>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>实例编号</th><th>模板编号</th><th>冻结版本</th><th>适用场景</th><th>预设指标</th><th>推荐图表</th><th>实例化参数</th><th>创建时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="inst in instances" :key="inst.id">
            <td>{{ inst['实例编号'] }}</td>
            <td>{{ inst['模板编号'] }}</td>
            <td>{{ inst['冻结版本'] }}</td>
            <td>{{ inst['适用场景'] }}</td>
            <td>{{ formatCell(inst['预设指标']) }}</td>
            <td>{{ formatCell(inst['推荐图表']) }}</td>
            <td>{{ formatCell(inst['实例化参数']) }}</td>
            <td>{{ formatTime(inst.created_at) }}</td>
          </tr>
          <tr v-if="!instances.length">
            <td colspan="8" class="empty-state">暂无实例，输入模板编号发起实例化</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type InstanceRow = Record<string, any> & { id: number; created_at: string }

const ENDPOINT = '/api/scene'
const templateCode = ref('SCEN-0002')
const instances = ref<InstanceRow[]>([])
const latest = ref<InstanceRow | null>(null)
const errorMessage = ref('')
const infoMessage = ref('')

const latestParams = computed<Record<string, any>>(() => {
  if (!latest.value) return {}
  return (latest.value['实例化参数'] ?? {}) as Record<string, any>
})

function formatCell(value: any): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatTime(value: string): string {
  if (!value) return '—'
  return value.replace('T', ' ').replace(/\.\d+.*$/, '').replace(/\+.*$/, '').replace('Z', ' UTC')
}

async function callInstantiate(code: string): Promise<InstanceRow> {
  const response = await request(`${ENDPOINT}/instantiations`, {
    method: 'POST',
    body: JSON.stringify({ values: { 模板编号: code } }),
  })
  const payload = (await response.json()) as { ok: boolean; message: string; entry?: InstanceRow }
  if (!response.ok || !payload.ok || !payload.entry) {
    throw new Error(payload.message || '实例化失败')
  }
  return payload.entry
}

async function instantiateByTemplate() {
  errorMessage.value = ''
  try {
    latest.value = await callInstantiate(templateCode.value.trim())
    infoMessage.value = `实例化完成，使用冻结版本 ${latest.value['冻结版本']}`
    await loadInstances()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '实例化失败'
  }
}

async function instantiateConcurrent() {
  errorMessage.value = ''
  const code = templateCode.value.trim()
  try {
    // 两个请求同时开始；服务端按模板加锁并固定首个冻结版本。
    const [first, second] = await Promise.all([callInstantiate(code), callInstantiate(code)])
    const sameVersion = first['冻结版本'] === second['冻结版本']
    const sameParams = formatCell(first['实例化参数']) === formatCell(second['实例化参数'])
    latest.value = first
    infoMessage.value = sameVersion && sameParams
      ? `两个并发实例化一致：均为 ${first['冻结版本']}，参数 ${formatCell(first['实例化参数'])}`
      : '并发实例化结果不一致，请检查冻结版本'
    await loadInstances()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '并发实例化失败'
  }
}

async function loadInstances() {
  const response = await request(`${ENDPOINT}/instantiations`)
  instances.value = ((await response.json()) as InstanceRow[]) ?? []
}

async function loadAll() {
  await loadInstances()
}

onMounted(loadAll)
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-top: 16px;
}
.panel.highlight {
  border-color: var(--brand);
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.panel h3 {
  margin: 0 0 8px;
  font-size: 15px;
}
.param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  margin: 0;
}
.param-grid dt {
  font-size: 12px;
  color: var(--muted);
}
.param-grid dd {
  margin: 2px 0 0;
  font-size: 14px;
}
.info-text {
  color: #067647;
}
</style>
