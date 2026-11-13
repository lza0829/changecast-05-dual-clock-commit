<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const rows = ref([])
const batches = ref([])
const busy = ref('')
const err = ref('')
const demo = ref(null)

async function refresh() {
  rows.value = await getJSON('/api/watermarks')
  batches.value = await getJSON('/api/batches?limit=30')
}

async function runDemo(streamId) {
  err.value = ''
  busy.value = 'demo-' + streamId
  try {
    demo.value = await postJSON(`/api/demo/${streamId}`)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})

function decisionTag(d) {
  return d === 'commit' ? 'ok' : d === 'catch_up' ? 'warn' : 'danger'
}
function decisionCn(d) {
  return d === 'commit' ? '双端同进' : d === 'catch_up' ? '追赶（仅 seq）' : '整趟拒绝'
}
</script>
<template>
  <div class="page">
    <h1>双水位看板</h1>
    <p class="muted">
      每条投影流同时维护 seq 水位与 event_time 水位。一趟成功必须双端同进；
      只能推进一端时默认整趟拒绝，追赶模式仅推进 seq 端并被批次标记。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>流</th><th>seq 水位</th><th>event_time 水位</th>
            <th>已应用事件</th><th>存活 / 已删</th><th>最近一批</th><th>演示</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.stream_id">
            <td>{{ r.title }}<div class="muted mono">{{ r.code }}</div></td>
            <td class="mono">{{ r.watermark_seq }}</td>
            <td class="mono">{{ r.watermark_event_time || '（空）' }}</td>
            <td class="mono">
              {{ r.evidence.applied_events }}
              <div class="muted mono">
                max seq {{ r.evidence.max_applied_seq }} ·
                max {{ r.evidence.max_applied_event_time || '（空）' }}
              </div>
            </td>
            <td class="mono">{{ r.projection_alive }} / {{ r.projection_deleted }}</td>
            <td class="mono" v-if="r.latest_batch">
              {{ r.latest_batch.batch_no }}
              <div><span class="tag" :class="decisionTag(r.latest_batch.decision)">{{ decisionCn(r.latest_batch.decision) }}</span></div>
            </td>
            <td v-else class="muted">尚无批次</td>
            <td>
              <button :disabled="!!busy" @click="runDemo(r.stream_id)">
                {{ busy === 'demo-' + r.stream_id ? '演示中…' : '跑全波次演示' }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length"><td colspan="7" class="muted">尚未初始化。</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="demo">
      <h3 style="margin-top:0">演示轨迹 · 流 {{ demo.stream_id }}</h3>
      <p class="muted">按夹具波次逐趟执行：先严格模式，若被拒再试追赶模式。</p>
      <table>
        <thead>
          <tr>
            <th>波次</th><th>模式</th><th>决策</th><th>seq 水位</th>
            <th>event_time 水位</th><th>原因</th><th>互证</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in demo.steps" :key="i">
            <td class="mono">W{{ s.wave }}</td>
            <td class="mono">{{ s.mode }}</td>
            <td><span class="tag" :class="decisionTag(s.decision)">{{ decisionCn(s.decision) }}</span></td>
            <td class="mono">{{ s.wm_seq_before }} → {{ s.wm_seq_after }}</td>
            <td class="mono">{{ s.wm_time_before || '（空）' }} → {{ s.wm_time_after || '（空）' }}</td>
            <td class="muted" style="max-width:22rem">{{ s.reason_text }}</td>
            <td><span class="tag" :class="s.reconcile_ok ? 'ok' : 'danger'">{{ s.reconcile_ok ? '通过' : '不一致' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">批次水位轨迹</h3>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>流</th><th>模式</th><th>决策</th>
            <th>seq 水位</th><th>event_time 水位</th><th>写 / 删 / 陈旧</th><th>原因码</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td class="mono">{{ b.mode }}</td>
            <td><span class="tag" :class="decisionTag(b.decision)">{{ decisionCn(b.decision) }}</span></td>
            <td class="mono">{{ b.wm_seq_before }} → {{ b.wm_seq_after }}</td>
            <td class="mono">{{ b.wm_time_before || '（空）' }} → {{ b.wm_time_after || '（空）' }}</td>
            <td class="mono">{{ b.applied_upsert }} / {{ b.applied_delete }} / {{ b.skipped_stale }}</td>
            <td class="mono">{{ b.reason_code }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="8" class="muted">尚无批次。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
