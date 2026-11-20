<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 锁口径 } from '../labels'
const current = ref('')
const policy = ref('lock_on_actual')
const msg = ref('')
const err = ref('')
const options = ['lock_on_actual', 'editable_until_sealed']

async function load() {
  const s = await api.settings()
  current.value = s.current_period || ''
  policy.value = s.estimate_lock_policy || 'lock_on_actual'
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
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
    </div>
  </div>
</template>
