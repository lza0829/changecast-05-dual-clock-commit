<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const err = ref('')
onMounted(async () => {
  try { rows.value = await getJSON('/api/streams') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>投影流</h1>
    <p class="muted">
      每条流绑定 CDC 夹具、实体键名与载荷字段，并维护 seq 与 event_time 两条水位。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>编码</th><th>标题</th><th>业务域</th>
            <th>实体键</th><th>载荷字段</th><th>夹具 / 波次</th>
            <th>seq 水位</th><th>event_time 水位</th><th>启用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td>{{ s.id }}</td>
            <td class="mono">{{ s.code }}</td>
            <td>{{ s.title }}</td>
            <td>{{ s.domain }}</td>
            <td class="mono">{{ s.entity_key_name }}</td>
            <td class="mono">{{ s.payload_fields.join(', ') }}</td>
            <td class="mono">
              {{ s.fixture }}
              <div class="muted">W{{ (s.waves || []).join(' / W') }}</div>
            </td>
            <td class="mono">{{ s.watermark_seq }}</td>
            <td class="mono">{{ s.watermark_event_time || '（空）' }}</td>
            <td><span class="tag" :class="s.enabled ? 'ok' : 'danger'">{{ s.enabled ? '是' : '否' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3 style="margin-top:0">双端同进示例</h3>
      <p class="muted" style="margin:0">
        同一波事件里 seq 与 event_time 均高于上次成功水位时，一趟提交同时推进两端；
        任一端未前进或回拨则按所选策略整趟拒绝或走追赶。
        <router-link to="/run">去投影台试一趟 →</router-link>
      </p>
    </div>
  </div>
</template>
