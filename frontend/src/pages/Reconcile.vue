<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 命运, 锁口径, 标 } from '../labels'
const periodId = ref('2026-10')
const batch = ref(null)
const allBatches = ref([])
const vars = ref([])
const msg = ref('')
const err = ref('')

async function loadBatches() {
  allBatches.value = await api.batches(periodId.value)
  const open = allBatches.value.find(b => b.status === 'open')
  const target = open || allBatches.value[0]
  batch.value = target || null
  vars.value = target
    ? await api.variances({ batch_id: target.batch_id })
    : []
}

async function align() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.align(periodId.value)
    msg.value = `对齐完成：匹配 ${r.matched_count}，差额 ${r.variance_count}，口径「${标(锁口径, r.policy)}」，锁标 ${r.locked_count} 个`
    await loadBatches()
  } catch (e) { err.value = e.message }
}
async function fate(id, f) {
  err.value = ''
  try {
    await api.setFate(id, f)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
  } catch (e) { err.value = e.message }
}
onMounted(loadBatches)
</script>
<template>
  <div>
    <h1>对齐差额</h1>
    <p class="sub">按表号与账期对齐估抄、实抄；任一侧写入后旧开放批自动作废，必须重新对齐</p>
    <div class="row">
      <label>账期<input v-model="periodId" /></label>
      <button @click="align">跑对齐</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>

    <div class="panel" v-if="allBatches.length && !allBatches.some(b => b.status === 'open')">
      <p class="msg err">当前账期无开放对齐批（最新批已作废或已封印），请重新跑对齐后再封印。</p>
    </div>

    <div class="panel" v-if="batch">
      <p>
        批号 {{ batch.batch_id }} · 指纹 {{ batch.fingerprint }} ·
        口径 <b>{{ 标(锁口径, batch.policy) }}</b> ·
        锁标 {{ batch.locked_count ?? 0 }} 个 ·
        <span class="tag" :class="batch.status">{{ 标(批状态, batch.status) }}</span>
        <span v-if="batch.status === 'expired'" class="hint">（该批已作废，数字为历史快照，不可用于封印）</span>
      </p>
      <table>
        <thead>
          <tr><th>差额编号</th><th>表号</th><th>估抄</th><th>实抄</th><th>差值</th><th>锁快照</th><th>命运</th><th>处置</th></tr>
        </thead>
        <tbody>
          <tr v-for="v in vars" :key="v.variance_id">
            <td>{{ v.variance_id }}</td>
            <td>{{ v.meter_id }}</td>
            <td>{{ v.estimate_qty }}</td>
            <td>{{ v.actual_qty }}</td>
            <td>{{ v.delta }}</td>
            <td>
              <span v-if="v.estimate_locked" class="tag sealed">已锁</span>
              <span v-else class="tag open">未锁</span>
            </td>
            <td>{{ 标(命运, v.fate) }}</td>
            <td>
              <button class="secondary" @click="fate(v.variance_id, 'accept_short')">认短</button>
              <button class="secondary" @click="fate(v.variance_id, 'accept_over')">认溢</button>
              <button class="warn" @click="fate(v.variance_id, 'quarantine')">隔离</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="allBatches.length">
      <h3>本账期对齐批沿革</h3>
      <table>
        <thead><tr><th>批号</th><th>状态</th><th>口径</th><th>锁标数</th><th>匹配</th><th>差额</th><th>生成时刻</th></tr></thead>
        <tbody>
          <tr v-for="b in allBatches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
            <td>{{ 标(锁口径, b.policy) }}</td>
            <td>{{ b.locked_count ?? 0 }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td>{{ b.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
