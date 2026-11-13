<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const stats = ref(null)
const err = ref('')
onMounted(async () => {
  try { stats.value = await getJSON('/api/dashboard') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>总览</h1>
    <p class="muted">
      变更日志投影：源 CDC 事件按波次落到当前投影，seq 水位与 event_time 水位同成同败，
      批次计数与快照互证。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="stats">
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ stats.stream_count }}</div><div class="stat-label">投影流</div></div>
        <div class="stat"><div class="stat-num">{{ stats.committed_batches }}</div><div class="stat-label">成功批次</div></div>
        <div class="stat"><div class="stat-num">{{ stats.catch_up_batches }}</div><div class="stat-label">追赶批次</div></div>
        <div class="stat"><div class="stat-num">{{ stats.rejected_batches }}</div><div class="stat-label">拒绝批次</div></div>
        <div class="stat"><div class="stat-num">{{ stats.projection_alive }}</div><div class="stat-label">存活投影</div></div>
        <div class="stat"><div class="stat-num">{{ stats.projection_deleted }}</div><div class="stat-label">已删投影</div></div>
      </div>
      <p style="margin-top:1rem">
        默认提交模式 <span class="tag">{{ stats.commit_mode_default }}</span>
        · 双水位规则 <span class="tag">{{ stats.dual_watermark }}</span>
      </p>
      <table style="margin-top:0.75rem">
        <thead><tr><th>流</th><th>seq 水位</th><th>event_time 水位</th></tr></thead>
        <tbody>
          <tr v-for="s in stats.dual_watermark_streams" :key="s.stream_id">
            <td>{{ s.title }}</td>
            <td class="mono">{{ s.watermark_seq }}</td>
            <td class="mono">{{ s.watermark_event_time || '（空）' }}</td>
          </tr>
        </tbody>
      </table>
      <p><router-link to="/run">去投影台跑一趟 →</router-link> · <router-link to="/watermarks">双水位看板 →</router-link></p>
    </div>
  </div>
</template>
