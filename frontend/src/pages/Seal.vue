<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 账期状态, 锁口径, 标 } from '../labels'
const periods = ref([])
const periodId = ref('2026-10')
const batchId = ref('')
const policy = ref('')
const hasOpen = ref(false)
const msg = ref('')
const err = ref('')
async function refresh() {
  const [ps, bs, s] = await Promise.all([
    api.periods(),
    api.batches(periodId.value),
    api.settings(),
  ])
  periods.value = ps
  policy.value = s.estimate_lock_policy || 'lock_on_actual'
  const open = bs.find(b => b.status === 'open')
  hasOpen.value = Boolean(open)
  batchId.value = open?.batch_id || bs[0]?.batch_id || ''
}
async function seal() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.seal({ period_id: periodId.value, batch_id: batchId.value || null })
    msg.value = `已封印 ${r.period_id}，绑定批 ${r.batch_id}`
    await refresh()
  } catch (e) { err.value = e.message }
}
onMounted(refresh)
</script>
<template>
  <div>
    <h1>账期封印</h1>
    <p class="sub">
      封印后该期估抄、实抄禁止再写（两种口径均拒改）· 当前锁口径：<b>{{ 标(锁口径, policy) }}</b>
    </p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="refresh" /></label>
      <label>对账批<input v-model="batchId" placeholder="可空取最新开放批" /></label>
      <button class="warn" @click="seal">封印账期</button>
      <button class="secondary" @click="refresh">刷新</button>
    </div>
    <p class="msg err" v-if="!hasOpen">该账期无开放对齐批：估抄/实抄写入后旧批已作废，请先到「对齐差额」重新跑对齐。</p>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>账期</th><th>状态</th><th>封印时刻</th><th>绑定批</th></tr></thead>
        <tbody>
          <tr v-for="p in periods" :key="p.period_id">
            <td>{{ p.period_id }}</td>
            <td><span class="tag" :class="p.status">{{ 标(账期状态, p.status) }}</span></td>
            <td>{{ p.sealed_at || '—' }}</td>
            <td>{{ p.batch_no || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
