<template>
  <a-modal
    :visible="visible"
    title="录制时间设置"
    :width="640"
    :mask-closable="true"
    :footer="false"
    :unmount-on-close="false"
    @cancel="handleCancel"
    @update:visible="handleVisibleChange"
  >
    <div v-if="mode === 'schedule'" class="record-schedule-dialog">
      <div class="record-schedule-mode-tip">
        <i class="fa-solid fa-calendar-days"></i>
        <span>可安排今天或明天的录制任务，单次时长不能超过 24 小时，时间按中国标准时间计算。</span>
      </div>
      <div class="record-schedule-section pending-schedule-section">
        <div class="record-schedule-section-title">
          <span>尚未开始的计划录制</span>
          <a-button size="mini" :loading="pendingSchedulesLoading" @click="loadPendingSchedules">
            <template #icon><i class="fa-solid fa-rotate"></i></template>
            刷新
          </a-button>
        </div>
        <div v-if="pendingSchedulesLoading" class="pending-schedule-empty">正在读取已设置的计划...</div>
        <div v-else-if="pendingSchedules.length === 0" class="pending-schedule-empty">
          当前没有尚未开始的计划录制任务。
        </div>
        <div v-else class="pending-schedule-list">
          <div v-for="schedule in pendingSchedules" :key="schedule.schedule_id" class="pending-schedule-item">
            <div>
              <span class="pending-schedule-label">计划开始</span>
              <strong>{{ formatScheduleTime(schedule.planned_start_at) }}</strong>
            </div>
            <div>
              <span class="pending-schedule-label">计划结束</span>
              <strong>{{ formatScheduleTime(schedule.planned_end_at) }}</strong>
            </div>
            <a-tag color="arcoblue">待开始</a-tag>
          </div>
        </div>
      </div>
      <div class="record-schedule-subtitle">设置下一个计划录制</div>
      <div
        v-for="(task, index) in scheduledTasks"
        :key="task.id"
        class="record-schedule-section"
      >
        <div class="record-schedule-section-title">
          <span>录制任务 {{ index + 1 }}</span>
          <a-button
            v-if="scheduledTasks.length > 1"
            size="mini"
            status="danger"
            @click="removeScheduledTask(index)"
          >删除</a-button>
        </div>
        <div class="schedule-form-row">
          <div class="schedule-form-field">
            <label>开始时间</label>
            <a-date-picker
              v-model="task.startTime"
              show-time
              format="YYYY-MM-DD HH:mm:ss"
              value-format="YYYY-MM-DD HH:mm:ss"
              :disabled-date="disablePastDate"
              :disabled-time="disablePastTime"
               placeholder="选择今天或明天的未来时间"
              style="width: 100%"
            />
          </div>
          <div class="schedule-form-field schedule-duration-field">
            <label>结束时间</label>
            <a-date-picker
              v-model="task.endTimePicker"
              show-time
              format="YYYY-MM-DD HH:mm:ss"
              value-format="YYYY-MM-DD HH:mm:ss"
              :disabled-date="disablePastDate"
              :disabled-time="disablePastTime"
               placeholder="选择今天或明天的未来时间"
              style="width: 100%"
            />
          </div>
        </div>
        <div v-if="task.errorMessage" class="schedule-error">
          {{ task.errorMessage }}
        </div>
      </div>
      <a-button long @click="addScheduledTask">+ 新增任务</a-button>
      <div class="record-schedule-note">
        <i class="fa-solid fa-circle-info"></i>
        <span>相邻任务可以首尾相接，但录制时间不能重叠。</span>
      </div>
      <div class="record-schedule-footer">
        <a-button @click="handleCancel">取消</a-button>
        <a-button type="primary" :loading="scheduleSubmitting" @click="handleScheduleConfirm">
          确定并保存计划
        </a-button>
      </div>
    </div>
    <div v-else-if="currentTask" class="record-schedule-dialog">
      <div class="record-schedule-mode-tip">
        <i class="fa-solid fa-clock"></i>
        <span>
          当前为立即录制模式。点击“开始录制”后立即启动摄像头，
          并在达到设置时长后自动停止。
        </span>
      </div>

      <div class="record-schedule-section">
        <div class="record-schedule-section-title">
          <span>录制时长</span>
          <a-tag size="small" color="arcoblue">立即开始</a-tag>
        </div>
        <div class="record-schedule-section-desc">
          请填写整数小时和分钟，时长为 1～1440 分钟，最多录制 24 小时。
        </div>
        <div class="record-duration-control">
          <a-input-number
            v-model="durationHours"
            :min="0"
            :max="24"
            :step="1"
            placeholder="小时"
            @change="handleDurationPartsChange"
          />
          <span class="record-duration-unit">小时</span>
          <a-input-number
            v-model="durationMinutesPart"
            :min="0"
            :max="59"
            :step="1"
            placeholder="分钟"
            @change="handleDurationPartsChange"
          />
          <span class="record-duration-unit">分钟</span>
        </div>
      </div>

      <div class="record-schedule-section">
        <div class="record-schedule-section-title">预计录制时间</div>
        <div class="record-schedule-section-desc">
          以下时间使用中国标准时间（Asia/Shanghai）。
        </div>
        <div class="record-time-summary">
          <div class="record-time-item">
            <div class="record-time-icon">
              <i class="fa-solid fa-play"></i>
            </div>
            <div class="record-time-content">
              <span class="record-time-label">开始时间</span>
              <strong class="record-time-value">
                {{ currentTask.actualStartTime || '—' }}
              </strong>
            </div>
          </div>
          <div class="record-time-arrow">
            <i class="fa-solid fa-arrow-right"></i>
          </div>
          <div class="record-time-item">
            <div class="record-time-icon">
              <i class="fa-solid fa-stop"></i>
            </div>
            <div class="record-time-content">
              <span class="record-time-label">结束时间</span>
              <strong class="record-time-value">
                {{ currentTask.actualEndTime || '—' }}
              </strong>
            </div>
          </div>
        </div>
      </div>

      <div class="record-schedule-note">
        <i class="fa-solid fa-circle-info"></i>
        <span>
          指定未来时间和多个录制任务将在后端调度功能接入后开放。
        </span>
      </div>

      <div class="record-schedule-footer">
        <a-button @click="handleCancel">取消</a-button>
        <a-button type="primary" @click="handleConfirm">
          <template #icon>
            <i class="fa-solid fa-video"></i>
          </template>
          开始录制
        </a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { http } from '@/api/http'

export type RecordTaskMode = 'duration' | 'schedule'

export interface RecordTask {
  id: string
  mode: RecordTaskMode
  durationMinutes: number
  startTime: string | null
  endTime: string | null
  actualStartTime: string
  actualEndTime: string
}

interface InternalRecordTask extends RecordTask {
  startTimePicker: string | null
  endTimePicker: string | null
  errorMessage: string | null
}

interface PendingSchedule {
  schedule_id: string
  planned_start_at: string
  planned_end_at: string
  duration_minutes: number
}

const props = defineProps<{
  visible: boolean
  mode?: 'duration' | 'schedule'
}>()

const emit = defineEmits<{
  (event: 'update:visible', value: boolean): void
  (event: 'confirm', tasks: RecordTask[]): void
}>()

const tasks = ref<InternalRecordTask[]>([])
const scheduledTasks = ref<InternalRecordTask[]>([])
const scheduleSubmitting = ref(false)
const durationHours = ref<number>(0)
const durationMinutesPart = ref<number>(30)
const pendingSchedules = ref<PendingSchedule[]>([])
const pendingSchedulesLoading = ref(false)

const mode = computed(() => props.mode || 'duration')

function makeId(): string {
  return `task_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function getChinaNow(): Date {
  const now = new Date()
  const utc = now.getTime() + now.getTimezoneOffset() * 60_000
  return new Date(utc + 8 * 60 * 60 * 1000)
}

function pad(value: number): string {
  return value < 10 ? `0${value}` : `${value}`
}

function formatChinaDateTime(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

function createDefaultTask(): InternalRecordTask {
  const now = getChinaNow()
  return {
    id: makeId(),
    mode: 'duration',
    durationMinutes: 30,
    startTime: null,
    endTime: null,
    startTimePicker: null,
    endTimePicker: null,
    actualStartTime: formatChinaDateTime(now),
    actualEndTime: formatChinaDateTime(new Date(now.getTime() + 30 * 60_000)),
    errorMessage: null,
  }
}

function initTasks(): void {
  if (tasks.value.length === 0) {
    tasks.value = [createDefaultTask()]
  }
}

function reset(): void {
  tasks.value = []
  scheduledTasks.value = []
  durationHours.value = 0
  durationMinutesPart.value = 30
  pendingSchedules.value = []
}

function formatScheduleTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date).replace(/\//g, '-')
}

async function loadPendingSchedules(): Promise<void> {
  pendingSchedulesLoading.value = true
  try {
    const response = await http.get<{ ok: boolean; items: PendingSchedule[] }>(
      '/api/camera_yzt/schedules?status=pending&include_terminal=false&limit=100',
    )
    pendingSchedules.value = (response.data?.items || []).filter((item) => {
      const start = new Date(item.planned_start_at).getTime()
      return Number.isFinite(start) && start > Date.now()
    })
  } catch {
    pendingSchedules.value = []
    Message.error('读取已设置的计划录制失败')
  } finally {
    pendingSchedulesLoading.value = false
  }
}

const currentTask = computed<InternalRecordTask | null>(() => {
  if (tasks.value.length === 0) {
    tasks.value = [createDefaultTask()]
  }
  return tasks.value[0] ?? null
})

function handleDurationChange(task: InternalRecordTask): void {
  task.errorMessage = null
  const minutes = Math.max(1, Math.min(1440, Number(task.durationMinutes) || 1))
  task.durationMinutes = minutes
  const now = getChinaNow()
  task.actualStartTime = formatChinaDateTime(now)
  task.actualEndTime = formatChinaDateTime(
    new Date(now.getTime() + minutes * 60_000),
  )
}

function handleDurationPartsChange(): void {
  const hours = Number(durationHours.value) || 0
  const minutes = Number(durationMinutesPart.value) || 0
  if (currentTask.value) {
    currentTask.value.durationMinutes = hours * 60 + minutes
    handleDurationChange(currentTask.value)
  }
}

function handleConfirm(): void {
  if (!currentTask.value) {
    Message.error('录制任务初始化失败')
    return
  }
  const task = currentTask.value
  task.mode = 'duration'
  handleDurationPartsChange()
  if (
    !Number.isFinite(task.durationMinutes)
    || !Number.isInteger(task.durationMinutes)
    || task.durationMinutes <= 0
  ) {
    task.errorMessage = '小时和分钟必须填写整数，且录制时长必须大于 0 分钟'
    Message.error(task.errorMessage)
    return
  }
  if (task.durationMinutes > 1440) {
    task.errorMessage = '录制时长不能超过 24 小时'
    Message.error(task.errorMessage)
    return
  }
  const now = getChinaNow()
  task.actualStartTime = formatChinaDateTime(now)
  task.actualEndTime = formatChinaDateTime(
    new Date(now.getTime() + task.durationMinutes * 60_000),
  )
  task.startTime = null
  task.endTime = null
  task.startTimePicker = null
  task.endTimePicker = null
  task.errorMessage = null
  const cleaned: RecordTask = {
    id: task.id,
    mode: 'duration',
    durationMinutes: task.durationMinutes,
    startTime: null,
    endTime: null,
    actualStartTime: task.actualStartTime,
    actualEndTime: task.actualEndTime,
  }
  emit('confirm', [cleaned])
  emit('update:visible', false)
}

function handleCancel(): void {
  emit('update:visible', false)
}

function createScheduledTask(): InternalRecordTask {
  return {
    ...createDefaultTask(),
    startTime: null,
    endTime: null,
    actualStartTime: '',
    actualEndTime: '',
  }
}

function initScheduledTasks(): void {
  if (scheduledTasks.value.length === 0) {
    scheduledTasks.value = [createScheduledTask()]
  }
}

function addScheduledTask(): void {
  scheduledTasks.value.push(createScheduledTask())
}

function removeScheduledTask(index: number): void {
  scheduledTasks.value.splice(index, 1)
}

function disablePastDate(date: Date): boolean {
  const now = getChinaNow()
  const tomorrow = new Date(now)
  tomorrow.setDate(tomorrow.getDate() + 1)
  return date.toDateString() !== now.toDateString()
    && date.toDateString() !== tomorrow.toDateString()
}

function disablePastTime(date: Date) {
  const now = getChinaNow()
  if (date.toDateString() !== now.toDateString()) return {}
  return {
    disabledHours: () => Array.from({ length: now.getHours() }, (_, i) => i),
    disabledMinutes: (hour: number) =>
      hour === now.getHours() ? Array.from({ length: now.getMinutes() + 1 }, (_, i) => i) : [],
    disabledSeconds: (hour: number, minute: number) =>
      hour === now.getHours() && minute === now.getMinutes()
        ? Array.from({ length: now.getSeconds() + 1 }, (_, i) => i)
        : [],
  }
}

function parseChinaTime(value: string): Date | null {
  if (!value) return null
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/)
  if (!match) return null
  return new Date(Date.UTC(
    Number(match[1]), Number(match[2]) - 1, Number(match[3]),
    Number(match[4]) - 8, Number(match[5]), Number(match[6]),
  ))
}

async function handleScheduleConfirm(): Promise<void> {
  const now = Date.now()
  const ranges: Array<{ start: number; end: number; index: number; duration: number }> = []
  for (const [index, task] of scheduledTasks.value.entries()) {
    task.errorMessage = null
    const start = parseChinaTime(task.startTime || '')
    const end = parseChinaTime(task.endTimePicker || '')
    if (!start) {
      task.errorMessage = '请选择今天或明天的未来开始时间。'
      return
    }
    if (start.getTime() <= now) {
      task.errorMessage = '开始时间不能是当前时间或过去时间，请选择未来时间。'
      return
    }
    if (!end) {
      task.errorMessage = '请选择今天或明天的未来结束时间。'
      return
    }
    if (end.getTime() <= start.getTime()) {
      task.errorMessage = '结束时间必须晚于开始时间。'
      return
    }
    const duration = Math.floor((end.getTime() - start.getTime()) / 60_000)
    if (duration < 1 || duration > 1440) {
      task.errorMessage = '结束时间与开始时间相隔 1～1440 分钟（最多 24 小时）。'
      return
    }
    ranges.push({ start: start.getTime(), end: end.getTime(), index, duration })
  }
  ranges.sort((a, b) => a.start - b.start)
  for (let i = 1; i < ranges.length; i += 1) {
    const previous = ranges[i - 1]
    const current = ranges[i]
    if (!previous || !current) continue
    if (current.start < previous.end) {
      const task = scheduledTasks.value[current.index]
      if (task) task.errorMessage = '该任务与已设置的任务时间重叠，请调整开始或结束时间。'
      return
    }
  }

  scheduleSubmitting.value = true
  try {
    await http.post('/api/camera_yzt/schedules', {
      tasks: ranges.map((range) => ({
        start_time: scheduledTasks.value[range.index]?.startTime,
        end_time: scheduledTasks.value[range.index]?.endTimePicker,
        duration_minutes: range.duration,
      })),
    })
    Message.success(`已保存 ${ranges.length} 条录制计划`)
    await loadPendingSchedules()
    emit('update:visible', false)
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    Message.error(
      typeof detail === 'object' && detail?.message
        ? detail.message
        : '保存录制计划失败，请检查是否与已有计划重叠。',
    )
  } finally {
    scheduleSubmitting.value = false
  }
}

function handleVisibleChange(value: boolean): void {
  emit('update:visible', value)
}

watch(
  () => props.visible,
  (value) => {
    if (value) {
      if (mode.value === 'schedule') initScheduledTasks()
      else initTasks()
    } else {
      reset()
    }
  },
  { immediate: true },
)

watch(
  () => [props.visible, mode.value] as const,
  ([visible, currentMode]) => {
    if (visible && currentMode === 'schedule') void loadPendingSchedules()
  },
  { immediate: true },
)
</script>

<style scoped>
.record-schedule-dialog {
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: #1d2129;
}
.record-schedule-mode-tip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgb(245, 245, 245);
  color: #4e5969;
  font-size: 12px;
  line-height: 1.7;
}
.record-schedule-mode-tip i {
  flex: 0 0 auto;
  margin-top: 3px;
  color: #165dff;
}
.record-schedule-section {
  padding: 14px;
  border: 1px solid rgb(232, 232, 232);
  border-radius: 6px;
  background: #fafbfc;
}
.record-schedule-subtitle {
  margin-top: 2px;
  color: #1d2129;
  font-size: 14px;
  font-weight: 600;
}
.pending-schedule-section {
  background: #f7f9ff;
  border-color: rgb(190 211 255);
}
.pending-schedule-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pending-schedule-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 9px 10px;
  border: 1px solid rgb(232 232 232);
  border-radius: 6px;
  background: #fff;
}
.pending-schedule-item strong {
  display: block;
  margin-top: 2px;
  color: #1d2129;
  font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  font-weight: 500;
}
.pending-schedule-label {
  color: #86909c;
  font-size: 11px;
}
.pending-schedule-empty {
  padding: 8px 0 2px;
  color: #86909c;
  font-size: 12px;
}
.record-schedule-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
  color: #1d2129;
  font-size: 14px;
  font-weight: 600;
}
.record-schedule-section-desc {
  margin-bottom: 10px;
  color: #86909c;
  font-size: 12px;
  line-height: 1.6;
}
.record-duration-control {
  display: flex;
  align-items: center;
  gap: 10px;
}
.record-duration-control :deep(.arco-input-number) {
  width: 220px;
}
.record-duration-unit {
  color: #4e5969;
  font-size: 13px;
}
.schedule-form-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 14px;
}
.schedule-form-field {
  min-width: 0;
}
.schedule-form-field label {
  display: block;
  margin-bottom: 6px;
  color: #4e5969;
  font-size: 12px;
}
.schedule-duration-field .record-duration-control :deep(.arco-input-number) {
  width: 170px;
}
.schedule-error {
  margin-top: 8px;
  color: #f53f3f;
  font-size: 12px;
}
.record-time-summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: stretch;
  gap: 12px;
}
.record-time-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border: 1px solid rgb(232, 232, 232);
  border-radius: 6px;
  background: #fff;
}
.record-time-icon {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: rgb(22 93 255 / 10%);
  color: #165dff;
}
.record-time-content {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 3px;
}
.record-time-label {
  color: #86909c;
  font-size: 12px;
}
.record-time-value {
  overflow: hidden;
  color: #1d2129;
  font-family:
    SarasaMonoSC,
    Menlo,
    Monaco,
    Consolas,
    monospace;
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.record-time-arrow {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #86909c;
}
.record-schedule-note {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  color: #86909c;
  font-size: 12px;
  line-height: 1.6;
}
.record-schedule-note i {
  flex: 0 0 auto;
  margin-top: 3px;
}
.record-schedule-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid rgb(232, 232, 232);
}

@media (width <= 768px) {
  .schedule-form-row {
    grid-template-columns: 1fr;
  }
  .record-time-summary {
    grid-template-columns: 1fr;
  }
  .pending-schedule-item {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .record-time-arrow {
    transform: rotate(90deg);
  }
  .record-duration-control {
    align-items: stretch;
    flex-direction: column;
  }
  .record-duration-control :deep(.arco-input-number) {
    width: 100%;
  }
}
</style>
