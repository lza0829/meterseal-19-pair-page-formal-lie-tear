<script setup>
import { onMounted, ref, computed } from 'vue'
import { api } from '../api'
import { 锁口径, 标 } from '../labels'

const periodId = ref('2026-10')
const rows = ref([])
const policy = ref('lock_on_actual')
const corrections = ref([])
const form = ref({ meter_id: 'M-W-101', period_id: '2026-10', qty: 10, doc_no: '' })
const reviseForm = ref({ meter_id: '', period_id: '', qty: null, doc_no: '' })
const corrForm = ref({ meter_id: '', period_id: '', corrected_qty: null, reason: '', doc_no: '' })
const msg = ref('')
const err = ref('')

const isLockPolicy = computed(() => policy.value === 'lock_on_actual')

async function load() {
  err.value = ''; msg.value = ''
  const [s, list, corr] = await Promise.all([
    api.settings(),
    api.estimates(periodId.value),
    api.corrections(periodId.value),
  ])
  policy.value = s.estimate_lock_policy || 'lock_on_actual'
  rows.value = list
  corrections.value = corr
}

function expireHint(n) {
  if (n && n > 0) return `；已作废 ${n} 个旧开放对齐批，请重新跑对齐`
  return ''
}

async function submit() {
  err.value = ''; msg.value = ''
  try {
    const body = { ...form.value, doc_no: form.value.doc_no || ('估-' + Date.now()) }
    const r = await api.postEstimate(body)
    msg.value = '估抄已装载' + expireHint(r.expired_batches)
    await load()
  } catch (e) { err.value = e.message }
}

function pickRevise(r) {
  reviseForm.value = { meter_id: r.meter_id, period_id: r.period_id, qty: r.qty, doc_no: '' }
  err.value = ''; msg.value = ''
}

async function submitRevise() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.reviseEstimate({
      meter_id: reviseForm.value.meter_id,
      period_id: reviseForm.value.period_id,
      qty: reviseForm.value.qty,
      doc_no: reviseForm.value.doc_no || null,
    })
    msg.value = `估抄已修订为 ${r.qty}${expireHint(r.expired_batches)}`
    await load()
  } catch (e) { err.value = e.message }
}

function pickCorrect(r) {
  corrForm.value = {
    meter_id: r.meter_id,
    period_id: r.period_id,
    corrected_qty: r.latest_correction?.corrected_qty ?? r.qty,
    reason: '',
    doc_no: '',
  }
  err.value = ''; msg.value = ''
}

async function submitCorrect() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.postCorrection({
      meter_id: corrForm.value.meter_id,
      period_id: corrForm.value.period_id,
      corrected_qty: corrForm.value.corrected_qty,
      reason: corrForm.value.reason,
      doc_no: corrForm.value.doc_no || ('订正-' + Date.now()),
    })
    msg.value = `订正已登记（锁定估抄 ${r.locked_qty} 不改动，对齐仍以锁定值为准）`
    await load()
  } catch (e) { err.value = e.message }
}

onMounted(load)

const pairedHint = ref(0)
onMounted(async () => {
  try {
    const p = await api.get('/api/pairs')
    pairedHint.value = p.paired_count || 0
  } catch (_) {}
})
</script>
<template>
  <div>
    <h1>估抄</h1>
    <p class="sub">
      按账期装载估抄读数 · 当前口径：
      <b>{{ 标(锁口径, policy) }}</b>
      <template v-if="isLockPolicy">（首笔正式实抄到达即锁定该表该账期估抄，纠错走订正）</template>
      <template v-else>（账期封印前估抄始终可覆盖修订，改后开放对齐批自动作废）</template>
    </p>
    <div class="row">
      <label>账期筛选<input v-model="periodId" /></label>
      <button class="secondary" @click="load">刷新</button>
    </div>

    <div class="panel">
      <h3>装载估抄</h3>
      <div class="row">
        <label>表号<input v-model="form.meter_id" /></label>
        <label>账期<input v-model="form.period_id" /></label>
        <label>数量<input type="number" step="0.1" v-model.number="form.qty" /></label>
        <label>单号<input v-model="form.doc_no" placeholder="可空自动生成" /></label>
        <button @click="submit">装载估抄</button>
      </div>
    </div>

    <div class="panel">
      <h3>估抄清单与锁标记</h3>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
      <table>
        <thead>
          <tr><th>编号</th><th>表号</th><th>账期</th><th>数量</th><th>单号</th><th>锁标记</th><th>最近订正</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.estimate_id">
            <td>{{ r.estimate_id }}</td>
            <td>{{ r.meter_id }}</td>
            <td>{{ r.period_id }}</td>
            <td>{{ r.qty }}</td>
            <td>{{ r.doc_no }}</td>
            <td>
              <span v-if="r.estimate_locked" class="tag sealed">已锁定 · {{ r.locked_at }}</span>
              <span v-else class="tag open">未锁</span>
            </td>
            <td>
              <template v-if="r.latest_correction">
                {{ r.latest_correction.corrected_qty }}（{{ r.latest_correction.reason }}）
              </template>
              <template v-else>—</template>
            </td>
            <td>
              <button
                class="secondary"
                :disabled="isLockPolicy && r.estimate_locked"
                @click="pickRevise(r)"
              >修订</button>
              <button
                class="warn"
                v-if="isLockPolicy && r.estimate_locked"
                @click="pickCorrect(r)"
              >订正</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint" v-if="isLockPolicy">
        锁定行的「修订」按钮已禁用且后端写闸同样拒绝；确需纠错请点「订正」，订正不改对齐输入。
      </p>
    </div>

    <div class="panel" v-if="reviseForm.meter_id">
      <h3>修订估抄：{{ reviseForm.meter_id }} / {{ reviseForm.period_id }}</h3>
      <div class="row">
        <label>新数量<input type="number" step="0.1" v-model.number="reviseForm.qty" /></label>
        <label>新单号（可空）<input v-model="reviseForm.doc_no" placeholder="留空保留原单号" /></label>
        <button @click="submitRevise">提交修订</button>
      </div>
    </div>

    <div class="panel" v-if="corrForm.meter_id">
      <h3>估抄订正：{{ corrForm.meter_id }} / {{ corrForm.period_id }}</h3>
      <div class="row">
        <label>订正数量<input type="number" step="0.1" v-model.number="corrForm.corrected_qty" /></label>
        <label>订正理由<input v-model="corrForm.reason" placeholder="必填" /></label>
        <label>订正单号<input v-model="corrForm.doc_no" placeholder="可空自动生成" /></label>
        <button class="warn" @click="submitCorrect">登记订正</button>
      </div>
    </div>

    <div class="panel" v-if="corrections.length">
      <h3>本期订正流水</h3>
      <table>
        <thead><tr><th>订正号</th><th>表号</th><th>锁定估抄</th><th>订正数量</th><th>理由</th><th>时刻</th></tr></thead>
        <tbody>
          <tr v-for="c in corrections" :key="c.correction_id">
            <td>{{ c.correction_id }}</td>
            <td>{{ c.meter_id }}</td>
            <td>{{ c.locked_qty }}</td>
            <td>{{ c.corrected_qty }}</td>
            <td>{{ c.reason }}</td>
            <td>{{ c.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
