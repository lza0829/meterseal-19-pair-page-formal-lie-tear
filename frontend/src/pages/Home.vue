<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 锁口径, 标 } from '../labels'
const data = ref(null)
const err = ref('')
onMounted(async () => {
  try { data.value = await api.overview() } catch (e) { err.value = e.message }
})
</script>
<template>
  <div>
    <h1>总览</h1>
    <p class="sub">当前账期估抄、实抄、对齐与封印状态</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div class="grid" v-if="data">
      <div class="stat">当前账期<b>{{ data.current_period }}</b></div>
      <div class="stat">锁口径<b>{{ 标(锁口径, data.estimate_lock_policy) }}</b></div>
      <div class="stat">开放账期<b>{{ data.open_periods }}</b></div>
      <div class="stat">已封印账期<b>{{ data.sealed_periods }}</b></div>
      <div class="stat">表具数<b>{{ data.meter_count }}</b></div>
      <div class="stat">有效估抄锁<b>{{ data.locked_estimates }}</b></div>
      <div class="stat">估抄订正<b>{{ data.correction_count }}</b></div>
      <div class="stat">开放对齐批<b>{{ data.open_batch_count }}</b></div>
      <div class="stat">已作废对齐批<b>{{ data.expired_batch_count }}</b></div>
      <div class="stat">未决差额（开放批）<b>{{ data.pending_variances }}</b></div>
    </div>
    <div class="panel" v-if="data">
      <h3>最近对账批</h3>
      <table>
        <thead><tr><th>批号</th><th>账期</th><th>口径</th><th>锁标</th><th>匹配</th><th>差额</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in data.recent_batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ b.period_id }}</td>
            <td>{{ 标(锁口径, b.policy) }}</td>
            <td>{{ b.locked_count ?? 0 }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
