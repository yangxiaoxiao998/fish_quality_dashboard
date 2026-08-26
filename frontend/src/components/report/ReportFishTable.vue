<template>
  <a-table
    :columns="columns"
    :data="rows"
    :pagination="false"
    :loading="loading"
    row-key="fish_id"
    stripe
    hoverable
    :scroll="{ x: 1100 }"
  >
    <template #bodyCell="{ column, record }">
      <template v-if="column.dataIndex === 'crop_image'">
        <div class="fish-thumb-cell">
          <img
            v-if="fishCropUrl(record)"
            :src="fishCropUrl(record)"
            :alt="'fish-' + (record.track_id ?? record.fish_uid ?? record.fish_id)"
            @error="onImgError"
          />
          <span v-else class="dim">--</span>
        </div>
      </template>
      <template v-else-if="column.dataIndex === 'status'">
        <a-tag :color="statusTagColor(record.status)">{{ statusTagText(record.status) }}</a-tag>
      </template>
      <template v-else-if="column.dataIndex === 'surface_damage_ratio'">
        {{ formatPercentRatio(record.surface_damage_ratio) }}
      </template>
      <template v-else-if="column.dataIndex === 'hex_color'">
        <span
          v-if="isHexColor(record.hex_color)"
          class="color-swatch"
          :style="{ backgroundColor: record.hex_color }"
        />
        <span v-else class="dim">--</span>
      </template>
      <template v-else-if="column.dataIndex === 'hex_color'">
        <span
          v-if="isHexColor(record.hex_color)"
          class="color-swatch"
          :style="{ backgroundColor: record.hex_color }"
        />
        <span v-else class="dim">--</span>
      </template>
      <template v-else-if="column.dataIndex === 'error_message'">
        <span class="dim">{{ record.error_message || '--' }}</span>
      </template>
      <template v-else-if="column.dataIndex === 'track_id'">
        <a-tag color="arcoblue">{{ record.track_id ?? '--' }}</a-tag>
      </template>
      <template v-else>
        <span :class="{ dim: isEmptyValue(record[column.dataIndex]) }">
          {{ formatCell(record[column.dataIndex], column.digits) }}
        </span>
      </template>
    </template>
  </a-table>
</template>

<script setup lang="ts">
import { getFishImageUrl } from '@/utils/fishFields'

const props = defineProps<{
  rows: any[]
  loading?: boolean
  showVideoColumn?: boolean
}>()

function onImgError(e: Event) {
  const img = e.target as HTMLImageElement
  img.style.opacity = '0.3'
}

function statusTagColor(s: string): string {
  const v = String(s || '').toLowerCase()
  if (v === 'done' || v === 'complete') return 'green'
  if (v === 'incomplete') return 'orange'
  if (v === 'failed') return 'red'
  return 'gray'
}

function statusTagText(s: string): string {
  const v = String(s || '').toLowerCase()
  if (v === 'done' || v === 'complete') return '完整'
  if (v === 'incomplete') return '不完整'
  if (v === 'failed') return '失败'
  return s || '--'
}

function isHexColor(value: unknown): boolean {
  return /^#[0-9a-f]{6}$/i.test(String(value || '').trim())
}

function formatPercentRatio(v: any): string {
  if (v === null || v === undefined) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return `${(n * 100).toFixed(2)}%`
}

function isEmptyValue(v: any): boolean {
  return v === null || v === undefined || v === ''
}

function formatCell(v: any, digits: any = 2): string {
  if (v === null || v === undefined || v === '') return '--'
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return '--'
    return Number(v).toFixed(typeof digits === 'number' ? digits : 2)
  }
  return String(v)
}

function fishCropUrl(fish: any): string {
  // Arco table 的 record 可能是原始 fish，也可能包在 record.raw 中。
  const raw = (fish && (fish as any).raw) || fish
  const url = getFishImageUrl(raw)
  if (typeof window !== 'undefined') {
    // dev 阶段打印，方便排查
    if ((window as any).__fishLog === undefined) {
      // 单点打印一次即可
      console.log('[ReportFishTable] sample image url:', {
        track_id: (raw && (raw as any).track_id),
        crop_image_url: (raw && (raw as any).crop_image_url),
        crop_image_path: (raw && (raw as any).crop_image_path),
        resolved: url,
      })
      ;(window as any).__fishLog = true
    }
  }
  return url || ''
}

const columns = [
  { title: '序号', dataIndex: 'track_id', width: 90, fixed: 'left' as const },
  { title: '鱼图', dataIndex: 'crop_image', width: 110 },
  { title: 'fish_uid', dataIndex: 'fish_uid', width: 200 },
  { title: 'frame_index', dataIndex: 'frame_index', width: 110 },
  { title: '状态', dataIndex: 'status', width: 100 },
  { title: '体长 (cm)', dataIndex: 'body_length_cm', width: 100, digits: 2 },
  { title: '无尾体长 (cm)', dataIndex: 'body_length_no_tail_cm', width: 120, digits: 2 },
  { title: '体高 (cm)', dataIndex: 'body_height_cm', width: 100, digits: 2 },
  { title: '体重 (g)', dataIndex: 'pred_weight_g', width: 100, digits: 2 },
  { title: '肥满度', dataIndex: 'condition_factor', width: 90, digits: 3 },
  { title: '破损率', dataIndex: 'surface_damage_ratio', width: 90 },
  { title: '腹部颜色', dataIndex: 'hex_color', width: 110 },
  { title: 'error_message', dataIndex: 'error_message', width: 200 },
]
</script>

<style scoped>
.fish-thumb-cell {
  width: 88px;
  height: 56px;
  background: #000;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.fish-thumb-cell img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.dim {
  color: #c9cdd4;
}
.color-swatch {
  display: inline-block;
  width: 58px;
  height: 22px;
  border: 1px solid rgb(0 0 0 / 18%);
  border-radius: 4px;
  vertical-align: middle;
}
</style>
