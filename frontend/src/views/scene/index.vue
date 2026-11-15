<template>
  <section class="page" data-module="scene">
    <header class="page-head">
      <div>
        <h2>场景化配置模板管理</h2>
        <p class="page-desc">发布时把预设指标、推荐图表、默认参数与适用场景冻结成指标图表配置快照；导出包随包携带快照，分析入口只读取冻结的实例化参数。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn primary" to="/analysis-entry">跨模块分析入口</RouterLink>
        <button class="btn" type="button" @click="exportRows">导出场景化配置模板清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row[column]) }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="runAction('发布模板', row)">发布模板</button>
            <button class="link" type="button" @click="openEditor(row)">修改默认参数</button>
            <button class="link" type="button" @click="viewReleases(row)">冻结版本</button>
            <button class="link" type="button" @click="exportLatest(row)">导出包</button>
            <button class="link" type="button" @click="instantiate(row)">实例化</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无场景化配置模板数据，可先登记场景模板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条场景化配置模板记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="infoMessage" class="info-text">{{ infoMessage }}</span>
    </footer>

    <div v-if="editing" class="panel">
      <h3>修改模板默认参数（{{ editing['模板编号'] }}）</h3>
      <p class="page-desc">已有冻结版本时，修改只作用于工作副本，重新发布后产生新版本；旧实例继续使用旧快照参数。</p>
      <form class="filter-bar" @submit.prevent="saveEditor">
        <label v-for="key in paramKeys" :key="key" class="filter-item">
          <span>{{ key }}</span>
          <input v-model="editParams[key]" :placeholder="key" />
        </label>
        <button class="btn primary" type="submit">保存并回到草稿</button>
        <button class="btn ghost" type="button" @click="editing = null">取消</button>
      </form>
    </div>

    <section class="panel">
      <div class="panel-head">
        <h3>冻结版本与指标图表配置快照</h3>
        <button class="btn" type="button" @click="loadReleases()">刷新</button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>冻结版本</th><th>模板编号</th><th>适用场景</th><th>预设指标</th><th>推荐图表</th><th>默认参数</th><th>冻结时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="rel in releases" :key="rel.id">
            <td>{{ rel.version }}</td>
            <td>{{ rel['模板编号'] }}</td>
            <td>{{ rel.snapshot['适用场景'] }}</td>
            <td>{{ formatCell(rel.snapshot['预设指标']) }}</td>
            <td>{{ formatCell(rel.snapshot['推荐图表']) }}</td>
            <td>{{ formatCell(rel.snapshot['默认参数']) }}</td>
            <td>{{ formatTime(rel.frozen_at) }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="exportRelease(rel.id)">生成导出包</button>
            </td>
          </tr>
          <tr v-if="!releases.length">
            <td colspan="8" class="empty-state">暂无冻结版本，发布模板后自动生成</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>场景模板导出包（含快照与默认参数，可下载后再次实例化）</h3>
        <button class="btn" type="button" @click="loadPackages">刷新</button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>包编号</th><th>模板编号</th><th>冻结版本</th><th>文件名</th><th>默认参数</th><th>生成时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="pkg in packages" :key="pkg.id">
            <td>{{ pkg.id }}</td>
            <td>{{ pkg['模板编号'] }}</td>
            <td>{{ pkg.version }}</td>
            <td>{{ pkg['文件名'] }}</td>
            <td>{{ formatCell(pkg.manifest['默认参数']) }}</td>
            <td>{{ formatTime(pkg.created_at) }}</td>
            <td class="row-actions">
              <a class="link" :href="`${ENDPOINT}/packages/${pkg.id}/download`" target="_blank" rel="noopener">下载 zip</a>
            </td>
          </tr>
          <tr v-if="!packages.length">
            <td colspan="7" class="empty-state">暂无导出包，在冻结版本上生成</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>分析入口实例记录（实例化参数仅来自冻结快照）</h3>
        <button class="btn" type="button" @click="loadInstances">刷新</button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>实例编号</th><th>模板编号</th><th>冻结版本</th><th>适用场景</th><th>实例化参数</th><th>创建时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="inst in instances" :key="inst.id">
            <td>{{ inst['实例编号'] }}</td>
            <td>{{ inst['模板编号'] }}</td>
            <td>{{ inst['冻结版本'] }}</td>
            <td>{{ inst['适用场景'] }}</td>
            <td>{{ formatCell(inst['实例化参数']) }}</td>
            <td>{{ formatTime(inst.created_at) }}</td>
          </tr>
          <tr v-if="!instances.length">
            <td colspan="6" class="empty-state">暂无实例，可在表格行内点击「实例化」</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type Release = Row & { id: number; version: string; snapshot: Row; frozen_at: string }
type PackageRow = Row & { id: number; manifest: Row; created_at: string }
type InstanceRow = Row & { id: number; created_at: string }

const ENDPOINT = '/api/scene'
const columns = ['模板编号', '模板名称', '适用场景', '预设指标', '推荐图表', '默认参数', '模板描述', '模板状态', '当前冻结版本', '冻结版本数']
const stats = [{ label: '场景模板总数', value: 0 }, { label: '冻结版本总数', value: 0 }, { label: '实例总数', value: 0 }]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const releases = ref<Release[]>([])
const packages = ref<PackageRow[]>([])
const instances = ref<InstanceRow[]>([])
const editing = ref<Row | null>(null)
const editParams = ref<Record<string, string>>({})
const paramKeys = ref<string[]>([])

function formatCell(value: any): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatTime(value: string): string {
  if (!value) return '—'
  return value.replace('T', ' ').replace(/\.\d+.*$/, '').replace(/\+.*$/, '').replace('Z', ' UTC')
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function post(path: string, body: Record<string, unknown>): Promise<Row> {
  const response = await request(`${ENDPOINT}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = (await response.json()) as { ok: boolean; message: string; entry?: Row }
  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || '操作未生效，请稍后重试')
  }
  infoMessage.value = payload.message
  return payload.entry ?? {}
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    await post(`/${row.id}/actions`, { values: { action } })
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景化配置模板操作失败'
  }
}

function openEditor(row: Row) {
  const params = (row['默认参数'] ?? {}) as Record<string, any>
  editParams.value = Object.fromEntries(
    Object.entries(params).map(([key, value]) => [key, value === null ? '' : String(value)]),
  )
  paramKeys.value = Object.keys(editParams.value)
  editing.value = row
}

async function saveEditor() {
  if (!editing.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${editing.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ values: { 默认参数: { ...editParams.value } } }),
    })
    const payload = (await response.json()) as { ok: boolean; message: string }
    if (!response.ok || !payload.ok) throw new Error(payload.message)
    infoMessage.value = payload.message
    editing.value = null
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '默认参数保存失败'
  }
}

async function viewReleases(row: Row) {
  await loadReleases(String(row['模板编号'] ?? ''))
}

async function loadReleases(templateCode = '') {
  const query = templateCode ? `?template_code=${encodeURIComponent(templateCode)}` : ''
  const response = await request(`${ENDPOINT}/releases${query}`)
  releases.value = ((await response.json()) as Release[]) ?? []
}

async function exportRelease(releaseId: number) {
  errorMessage.value = ''
  try {
    await post(`/releases/${releaseId}/export`, {})
    await loadPackages()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出包生成失败'
  }
}

async function exportLatest(row: Row) {
  errorMessage.value = ''
  try {
    await loadReleases(String(row['模板编号'] ?? ''))
    const target = releases.value.find((item) => String(item['模板编号']) === String(row['模板编号']))
    if (!target) {
      throw new Error('该模板还没有冻结版本，请先发布模板')
    }
    await exportRelease(target.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出包生成失败'
  }
}

async function instantiate(row: Row) {
  errorMessage.value = ''
  try {
    await post('/instantiations', { values: { 模板编号: row['模板编号'] } })
    await loadInstances()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '实例化失败'
  }
}

async function loadPackages() {
  const response = await request(`${ENDPOINT}/packages`)
  packages.value = ((await response.json()) as PackageRow[]) ?? []
}

async function loadInstances() {
  const response = await request(`${ENDPOINT}/instantiations`)
  instances.value = ((await response.json()) as InstanceRow[]) ?? []
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) throw new Error('场景模板列表读取失败')
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景化配置模板列表读取失败'
  }
}

async function reloadAll() {
  await Promise.all([reload(), loadReleases(), loadPackages(), loadInstances()])
  stats[0].value = total.value
  stats[1].value = releases.value.length
  stats[2].value = instances.value.length
}

onMounted(reloadAll)
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-top: 16px;
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
.info-text {
  color: #067647;
}
</style>
