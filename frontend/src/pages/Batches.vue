<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const err = ref('')
onMounted(async () => {
  try { rows.value = await getJSON('/api/batches?limit=100') } catch (e) { err.value = String(e.message || e) }
})
function decisionTag(d) {
  return d === 'commit' ? 'ok' : d === 'catch_up' ? 'warn' : 'danger'
}
function decisionCn(d) {
  return d === 'commit' ? '双端同进' : d === 'catch_up' ? '追赶' : '拒绝'
}
</script>
<template>
  <div class="page">
    <h1>应用批次</h1>
    <p class="muted">
      每批次记录双水位旧新值、模式与决策、写入/删除/陈旧数，与投影快照计数互证。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>投影流</th><th>W</th><th>模式</th><th>决策</th>
            <th>源事件</th><th>写 / 删 / 陈旧 / 跳过</th><th>存活 / 已删</th>
            <th>seq 水位</th><th>event_time 水位</th><th>原因码</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td class="mono">W{{ b.wave }}</td>
            <td class="mono">{{ b.mode }}</td>
            <td><span class="tag" :class="decisionTag(b.decision)">{{ decisionCn(b.decision) }}</span></td>
            <td>{{ b.source_events }}</td>
            <td class="mono">{{ b.applied_upsert }} / {{ b.applied_delete }} / {{ b.skipped_stale }} / {{ b.skipped_duplicate }}</td>
            <td class="mono">{{ b.projected_alive }} / {{ b.projected_deleted }}</td>
            <td class="mono">{{ b.wm_seq_before }} → {{ b.wm_seq_after }}</td>
            <td class="mono">{{ b.wm_time_before || '（空）' }} → {{ b.wm_time_after || '（空）' }}</td>
            <td class="mono">{{ b.reason_code }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="12" class="muted">尚无批次。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
