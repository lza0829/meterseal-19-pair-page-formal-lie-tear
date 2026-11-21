<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { 配对口径, 配对来源, 标 } from '../labels'

const periodId = ref('2026-10')
const data = ref(null)
const msg = ref('')
const err = ref('')

async function load() {
  err.value = ''
  try {
    data.value = await api.pairs(periodId.value)
  } catch (e) { err.value = e.message }
}

async function repair() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.repairPairs(periodId.value)
    const 动作 = r.action === 'rollback_projection' ? '回滚半截配对投影' : '剔幽灵投影并补投影'
    msg.value = `已按口径「${标(配对口径, r.pair_policy)}」${动作}：补 ${r.added} 键、剔 ${r.removed} 键，当前已配 ${r.paired_count} 对`
    await load()
  } catch (e) { err.value = e.message }
}

function keyList(keys) {
  return keys && keys.length ? keys.map(k => k.meter_id).join('、') : '无'
}

onMounted(load)
watch(periodId, load)
</script>
<template>
  <div>
    <h1>配对结果</h1>
    <p class="sub" v-if="data">
      配对可见性口径：<b>{{ 标(配对口径, data.pair_policy) }}</b>
      <template v-if="data.pair_policy === 'formal_intersection'">
        —— 页面由正式估抄∩实抄键集合实时重放，正式缺一侧立即不绿
      </template>
      <template v-else>
        —— 页面读物化配对投影，装载/对齐/处置/封印同事务维护投影
      </template>
    </p>
    <div class="row">
      <label>账期<input v-model="periodId" /></label>
      <button class="secondary" @click="load">刷新</button>
    </div>
    <p class="msg err" v-if="err">{{ err }}</p>

    <template v-if="data">
      <div class="panel" v-if="data.diverged">
        <p class="msg err">
          配对投影与正式双侧键集合分叉：幽灵 {{ data.ghost_keys.length }} 键（{{ keyList(data.ghost_keys) }}），
          缺失 {{ data.missing_keys.length }} 键（{{ keyList(data.missing_keys) }}）。页面不得记绿。
        </p>
        <button class="warn" @click="repair">按口径修复收敛</button>
      </div>
      <div class="panel" v-else>
        <p class="msg ok">配对投影与正式双侧键集合一致，页面可记绿。</p>
      </div>
      <p class="msg ok" v-if="msg">{{ msg }}</p>

      <div class="panel">
        <p>
          已配 <b>{{ data.paired_count }}</b> 对（口径取数） ·
          正式交集 {{ data.formal_intersection_count }} 键 · 投影 {{ data.projection_count }} 键
        </p>
        <table>
          <thead>
            <tr><th>表号</th><th>账期</th><th>状态</th><th>来源</th><th>正式双侧</th><th>物化批</th><th>更新时刻</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in data.pairs" :key="p.period_id + '|' + p.meter_id">
              <td>{{ p.meter_id }}</td>
              <td>{{ p.period_id }}</td>
              <td>
                <span class="tag" :class="p.formal_present ? 'open' : 'sealed'">
                  {{ p.formal_present ? '已配对' : '幽灵投影' }}
                </span>
              </td>
              <td>{{ 标(配对来源, p.source) }}</td>
              <td>{{ p.formal_present ? '齐' : '缺一侧' }}</td>
              <td>{{ p.source_batch_id || '—' }}</td>
              <td>{{ p.updated_at || '—' }}</td>
            </tr>
            <tr v-if="!data.pairs.length">
              <td colspan="7">该账期当前无已配键（估抄与实抄双侧都存在的表号）</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
