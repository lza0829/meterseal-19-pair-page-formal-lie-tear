<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 锁口径, 配对口径 } from '../labels'
const current = ref('')
const policy = ref('lock_on_actual')
const pairPolicy = ref('formal_intersection')
const msg = ref('')
const err = ref('')
const options = ['lock_on_actual', 'editable_until_sealed']
const pairOptions = ['formal_intersection', 'pair_projection']

async function load() {
  const s = await api.settings()
  current.value = s.current_period || ''
  policy.value = s.estimate_lock_policy || 'lock_on_actual'
  pairPolicy.value = s.pair_visibility_policy || 'formal_intersection'
}
async function savePeriod() {
  err.value = ''; msg.value = ''
  try {
    await api.putSetting('current_period', current.value)
    msg.value = '已保存当前账期'
    await load()
  } catch (e) { err.value = e.message }
}
async function savePolicy() {
  err.value = ''; msg.value = ''
  try {
    await api.putSetting('estimate_lock_policy', policy.value)
    msg.value = `已切换锁口径：${锁口径[policy.value]}。写闸、锁标记展示、对齐快照即刻按新口径执行`
    await load()
  } catch (e) { err.value = e.message }
}
async function savePairPolicy() {
  err.value = ''; msg.value = ''
  try {
    await api.putSetting('pair_visibility_policy', pairPolicy.value)
    msg.value = `已切换配对可见性口径：${配对口径[pairPolicy.value]}。配对页、总览已配计数、封印预检即刻按新口径取数`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <h1>设置</h1>
    <p class="sub">运行参数</p>
    <div class="panel">
      <div class="row">
        <label>当前账期<input v-model="current" /></label>
        <button @click="savePeriod">保存</button>
      </div>
    </div>
    <div class="panel">
      <h3>估抄锁口径（全局唯一，同一时刻只贯彻一种）</h3>
      <div class="row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
        <label v-for="opt in options" :key="opt" class="row" style="gap: 6px;">
          <input type="radio" name="policy" :value="opt" v-model="policy" />
          <b>{{ 锁口径[opt] }}</b>
          <span class="hint" v-if="opt === 'lock_on_actual'">
            —— 该表该账期一旦存在正式实抄，估抄拒绝再改；纠错走订正链路；快照 estimate_locked=true；未到实抄前估抄可修订
          </span>
          <span class="hint" v-else>
            —— 只要账期未封印，估抄可覆盖修订；改后开放对齐批自动作废；已有实抄不锁估抄
          </span>
        </label>
        <button @click="savePolicy">保存口径</button>
      </div>
    </div>
    <div class="panel">
      <h3>配对可见性口径（全局唯一，同一时刻只贯彻一种）</h3>
      <div class="row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
        <label v-for="opt in pairOptions" :key="opt" class="row" style="gap: 6px;">
          <input type="radio" name="pairPolicy" :value="opt" v-model="pairPolicy" />
          <b>{{ 配对口径[opt] }}</b>
          <span class="hint" v-if="opt === 'formal_intersection'">
            —— 配对页与总览由正式估抄∩实抄键集合实时重放；删一侧正式页面立即不绿；修复即回滚半截配对投影
          </span>
          <span class="hint" v-else>
            —— 配对页与总览读物化投影表；装载/对齐/处置/封印同事务维护投影；撕裂显式报分叉，修复即剔幽灵、补投影
          </span>
        </label>
        <button @click="savePairPolicy">保存口径</button>
      </div>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
    </div>
  </div>
</template>
