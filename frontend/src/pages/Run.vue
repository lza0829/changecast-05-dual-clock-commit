<script setup>
import { onMounted, ref, watch } from 'vue'
import { getJSON, postJSON } from '../api'

const streams = ref([])
const batches = ref([])
const streamId = ref(null)
const wave = ref(1)
const mode = ref('strict')
const note = ref('')
const last = ref(null)
const busy = ref(false)
const err = ref('')

const selected = ref(null)

async function refresh() {
  streams.value = await getJSON('/api/streams')
  batches.value = await getJSON('/api/batches?limit=20')
  if (streamId.value == null && streams.value.length) {
    streamId.value = streams.value[0].id
    wave.value = streams.value[0].waves[0]
  }
  selected.value = streams.value.find((s) => s.id === streamId.value) || null
}

async function run() {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/project', {
      stream_id: Number(streamId.value),
      mode: mode.value,
      wave: Number(wave.value),
      note: note.value || null,
    })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

watch(streamId, () => {
  selected.value = streams.value.find((s) => s.id === streamId.value) || null
  if (selected.value && selected.value.waves.length) wave.value = selected.value.waves[0]
})

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
    <h1>投影台</h1>
    <p class="muted">
      选一条投影流与一个到达波次，按双水位提交规则应用。默认整趟拒绝：只能推进一端时不落投影、不动水位；
      追赶模式仅推进 seq 端并写入批次标记。软删除留墓碑行。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3 style="margin-top:0">发起投影</h3>
      <div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
        <label>
          投影流
          <select v-model.number="streamId" style="display:block;min-width:16rem;margin-top:0.25rem">
            <option v-for="s in streams" :key="s.id" :value="s.id">{{ s.title }}</option>
          </select>
        </label>
        <label>
          波次
          <select v-model.number="wave" style="display:block;min-width:7rem;margin-top:0.25rem">
            <option v-for="w in (selected ? selected.waves : [1])" :key="w" :value="w">W{{ w }}</option>
          </select>
        </label>
        <label>
          提交模式
          <select v-model="mode" style="display:block;min-width:11rem;margin-top:0.25rem">
            <option value="strict">strict（默认整趟拒绝）</option>
            <option value="catch_up">catch_up（追赶仅 seq）</option>
          </select>
        </label>
        <label>
          备注（可选）
          <input v-model="note" style="display:block;width:14rem;margin-top:0.25rem" placeholder="本趟说明" />
        </label>
        <button :disabled="busy || !streamId" @click="run">{{ busy ? '投影中…' : '应用本波' }}</button>
      </div>
      <p v-if="selected" class="muted" style="margin-bottom:0">
        当前水位：seq <span class="mono">{{ selected.watermark_seq }}</span> ·
        event_time <span class="mono">{{ selected.watermark_event_time || '（空）' }}</span>
      </p>
    </div>

    <div class="panel" v-if="last">
      <h3 style="margin-top:0">最近一趟 · {{ last.batch_no }}</h3>
      <p>
        {{ last.stream_title }} · 波次 <span class="mono">W{{ last.wave }}</span> ·
        模式 <span class="mono">{{ last.mode }}</span> ·
        <span class="tag" :class="decisionTag(last.decision)">{{ decisionCn(last.decision) }}</span>
      </p>
      <p class="muted">{{ last.reason_text }}</p>

      <table>
        <thead><tr><th>水位端</th><th>上次</th><th>本趟候选</th><th>本趟落定</th></tr></thead>
        <tbody>
          <tr>
            <td>seq</td>
            <td class="mono">{{ last.wm_seq_before }}</td>
            <td class="mono">{{ last.max_seq }}</td>
            <td class="mono">{{ last.wm_seq_after }}</td>
          </tr>
          <tr>
            <td>event_time</td>
            <td class="mono">{{ last.wm_time_before || '（空）' }}</td>
            <td class="mono">{{ last.max_event_time || '（空）' }}</td>
            <td class="mono">{{ last.wm_time_after || '（空）' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="stat-grid" style="margin-top:0.75rem">
        <div class="stat"><div class="stat-num">{{ last.source_events }}</div><div class="stat-label">源事件</div></div>
        <div class="stat"><div class="stat-num">{{ last.applied_upsert }}</div><div class="stat-label">写入</div></div>
        <div class="stat"><div class="stat-num">{{ last.applied_delete }}</div><div class="stat-label">删除</div></div>
        <div class="stat"><div class="stat-num">{{ last.skipped_duplicate }}</div><div class="stat-label">跳过重复</div></div>
        <div class="stat"><div class="stat-num">{{ last.skipped_stale }}</div><div class="stat-label">陈旧跳过</div></div>
        <div class="stat"><div class="stat-num">{{ last.projected_alive }}</div><div class="stat-label">存活</div></div>
        <div class="stat"><div class="stat-num">{{ last.projected_deleted }}</div><div class="stat-label">已删</div></div>
      </div>

      <h3 style="margin-top:1rem">互证</h3>
      <p>
        投影指纹 <span class="mono">{{ last.projection_fingerprint.slice(0, 16) }}…</span>
        <span class="tag" :class="last.reconcile.all_ok ? 'ok' : 'danger'" style="margin-left:0.5rem">
          {{ last.reconcile.all_ok ? '四项通过' : '存在不一致' }}
        </span>
      </p>
      <table>
        <thead><tr><th>校验</th><th>结果</th><th>证据</th></tr></thead>
        <tbody>
          <tr v-for="c in last.reconcile.checks" :key="c.name">
            <td>{{ c.name }}</td>
            <td><span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '不一致' }}</span></td>
            <td class="muted">{{ c.detail }}</td>
          </tr>
        </tbody>
      </table>

      <p style="margin-top:0.75rem">
        <router-link :to="{ path: '/projections', query: { stream_id: last.stream_id } }">查看投影快照 →</router-link>
        ·
        <router-link :to="{ path: '/events', query: { batch_id: last.id } }">事件留痕 →</router-link>
        ·
        <router-link to="/watermarks">双水位看板 →</router-link>
      </p>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">历史批次</h3>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>投影流</th><th>W</th><th>模式</th><th>决策</th>
            <th>seq 水位</th><th>event_time 水位</th><th>写入/删除</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td class="mono">W{{ b.wave }}</td>
            <td class="mono">{{ b.mode }}</td>
            <td><span class="tag" :class="decisionTag(b.decision)">{{ decisionCn(b.decision) }}</span></td>
            <td class="mono">{{ b.wm_seq_before }} → {{ b.wm_seq_after }}</td>
            <td class="mono">{{ b.wm_time_before || '（空）' }} → {{ b.wm_time_after || '（空）' }}</td>
            <td class="mono">{{ b.applied_upsert }} / {{ b.applied_delete }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="9" class="muted">还没有批次，先跑一趟。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
