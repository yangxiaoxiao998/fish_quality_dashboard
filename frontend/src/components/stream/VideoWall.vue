<template>
  <div class="video-wall">
    <div class="stage" @click="onVideoClick">
      <video
        ref="videoRef"
        v-if="videoSrc"
        class="video"
        :src="videoSrc"
        controls
        autoplay
        playsinline
        preload="metadata"
        :loop="false"
        @ended="handleVideoEnded"
        @error="handleVideoError"
      />
      <div v-else class="placeholder">
        <div class="placeholder-title">等待摄像头输入</div>
        <div class="placeholder-sub">
          请点击“摄像头输入”按钮
        </div>
      </div>
    </div>

    <div class="statusbar">
      <div v-if="videoId" class="status">
        <span class="k">video_id：</span>
        <span class="v" :title="videoId">{{ shortVideoId }}</span>
      </div>
      <div v-if="videoSrc" class="status">
        <span class="k">播放源：</span>
        <span class="v">已加载</span>
      </div>
      <div v-if="algorithmRunning" class="status algorithm-running">
        <span class="k">算法：</span>
        <span class="v">算法处理中，请稍候...</span>
      </div>
      <div v-else-if="algorithmStatus === 'done'" class="status algorithm-done">
        <span class="k">算法：</span>
        <span class="v">已完成 (video_id: {{ algorithmVideoId }})</span>
      </div>
      <div v-else-if="algorithmStatus && algorithmStatus !== 'done'" class="status algorithm-failed">
        <span class="k">算法：</span>
        <span class="v">{{ algorithmStatus }} - {{ algorithmError }}</span>
      </div>
      <div v-if="errorMsg" class="error">
        {{ errorMsg }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { http, toErrorMessage } from '@/api/http'
import type { FishDetail } from '@/types/fish'
import { getVideoState, setVideoState, clearVideoState } from '@/store/video'
import { runAlgorithmVideo, getAlgorithmVideoResult } from '@/api/algorithm'

const emit = defineEmits<{
  (e: 'upload-success', data: { video_id: string, videoSrc: string }): void
  (e: 'upload-error', error: string): void
  (e: 'fish-selected', fish: FishDetail): void
}>()

type UploadResp = {
  status: string
  video_id: string
  job_id: string
  playback_url: string
  // 后端 /api/videos 现在还会返回 source_path / playback_url 等；
  // 部分前端流程依赖 videoSrc；这里保留旧字段，但真正调用算法走 input_video 字段。
  source_path?: string
  video_path?: string
}

type AlgorithmStatus = 'done' | 'failed' | 'interrupted' | string

const algorithmRunning = ref(false)
const algorithmVideoId = ref<string>('')
const algorithmStatus = ref<AlgorithmStatus>('')
const algorithmError = ref<string>('')

const fileInputRef = ref<HTMLInputElement | null>(null)
const videoRef = ref<HTMLVideoElement | null>(null)

const pickedFile = ref<File | null>(null)
const uploading = ref(false)
const errorMsg = ref('')
const videoId = ref('')
const videoSrc = ref('')
const fishes = ref<FishDetail[]>([])
let pollTimer: number | null = null

onMounted(() => {
  const savedState = getVideoState()
  if (savedState.videoSrc) {
    videoId.value = savedState.videoId
    videoSrc.value = savedState.videoSrc
    // 等 video 节点渲染完再尝试 play()
    nextTick(() => {
      if (savedState.isPlaying && videoRef.value) {
        const p = videoRef.value.play()
        if (p && typeof p.then === 'function') {
          p.catch(() => {
            // 自动播放被浏览器策略阻止时，记录 isPlaying=false，等用户点播放
            setVideoState(
              videoId.value,
              videoSrc.value,
              false,
              { playOnce: savedState.playOnce, source: savedState.source }
            )
          })
        }
      }
    })
    if (savedState.videoId) {
      startPollingFishes()
    }
  }
})

onUnmounted(() => {
  // 不清理 store/video：用户切到别的页面或 admin 后再回来，
  // 视频状态（id / src / isPlaying / playOnce）必须保留。
  // 只清轮询。
  stopPollingFishes()
})

function triggerFileInput() {
  fileInputRef.value?.click()
}

function onPickFile(e: Event) {
  errorMsg.value = ''
  const input = e.target as HTMLInputElement
  const f = input.files?.[0] || null
  pickedFile.value = f
  if (f) {
    onUpload()
  }
}

function shortVideoId(value: string): string {
  if (!value) return '-'
  if (value.length <= 24) return value
  return `${value.slice(0, 10)}…${value.slice(-8)}`
}

function buildAbsoluteUrl(playbackUrl: string) {
  const base = import.meta.env.VITE_API_DOMAIN || 'http://localhost:8000'
  if (!playbackUrl) return ''
  if (playbackUrl.startsWith('http://') || playbackUrl.startsWith('https://')) return playbackUrl
  return `${base.replace(/\/+$/, '')}/${playbackUrl.replace(/^\/+/, '')}`
}

async function onUpload() {
  if (!pickedFile.value) return

  uploading.value = true
  errorMsg.value = ''
  algorithmError.value = ''
  algorithmStatus.value = ''

  try {
    const form = new FormData()
    form.append('file', pickedFile.value)

    const resp = await http.post<UploadResp>('/api/videos', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    videoId.value = resp.data.video_id
    videoSrc.value = buildAbsoluteUrl(resp.data.playback_url)

    if (fileInputRef.value) fileInputRef.value.value = ''
    pickedFile.value = null

    emit('upload-success', { video_id: videoId.value, videoSrc: videoSrc.value })

    // 第一阶段：上传完成后调用真实算法 CLI。
    // 后端第一阶段同步阻塞接口，因此放到 finally 前。
    // 注意：uploadVideo 在新版后端实际会返回 input_video / source_path，
    // 这里使用 playback_url 作为兜底，避免后端字段不一致时调用失败。
    const inputVideo = resp.data.video_path || resp.data.source_path || resp.data.playback_url || ''
    if (inputVideo) {
      try {
        algorithmRunning.value = true
        const algoResp = await runAlgorithmVideo({
          input_video: inputVideo,
          camera_id: 'default',
          device: 'cuda',
          video_fps: 25,
          overwrite: true,
          import_to_db: true,
        })
        if (algoResp.data?.ok && algoResp.data.status === 'done' && algoResp.data.video_id) {
          algorithmVideoId.value = algoResp.data.video_id
          algorithmStatus.value = 'done'
          // 立即拉一次结果保存到 store（不阻塞 UI）
          getAlgorithmVideoResult(algoResp.data.video_id)
            .then((r) => {
              if (r.data?.data?.fish) {
                fishes.value = r.data.data.fish.map((f) => ({
                  fish_id: String(f.track_id ?? f.fish_uid ?? ''),
                  track_id: f.track_id,
                  crop_image_url: f.crop_image_url,
                  crop_image_path: f.crop_image_path,
                  body_length_cm: f.body_length_cm,
                  body_length_no_tail_cm: f.body_length_no_tail_cm,
                  body_height_cm: f.body_height_cm,
                  pred_weight_g: f.pred_weight_g,
                  condition_factor: f.condition_factor,
                  surface_damage_ratio: f.surface_damage_ratio,
                  status: f.status as any,
                  error_message: f.error_message,
                } as FishDetail))
              }
            })
            .catch(() => {})
        } else {
          algorithmStatus.value = algoResp.data?.status ?? 'failed'
          algorithmError.value =
            algoResp.data?.error_conclusion || algoResp.data?.error_message || '算法处理失败'
        }
      } catch (e: any) {
        algorithmError.value = e?.message || '调用算法 CLI 失败'
      } finally {
        algorithmRunning.value = false
      }
    }

    startPollingFishes()
  } catch (err) {
    errorMsg.value = toErrorMessage(err)
    emit('upload-error', errorMsg.value)
  } finally {
    uploading.value = false
  }
}

async function pollFishes() {
  // 第一阶段：算法执行走 runAlgorithmVideo，upload 后立即调用；
  // 这里只轮询 upload 阶段 video_task 状态，避免不必要的旧接口调用。
  if (!videoId.value) return
  try {
    const resp = await http.get(`/api/videos/${videoId.value}`)
    const videoData = resp.data
    if (videoData.status === 'done' || videoData.status === 'failed') {
      stopPollingFishes()
    }
  } catch (err) {
    console.error('Failed to poll video status:', err)
  }
}

function startPollingFishes() {
  stopPollingFishes()
  pollTimer = window.setInterval(pollFishes, 2000)
}

function stopPollingFishes() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/**
 * 把鱼 bbox 归一化到当前 video 显示坐标（0~1）。
 *
 * 后端 bbox 可能是像素（[0,0,186,209]）也可能是归一化（[0,0,0.5,0.5]）。
 * 简单判断：如果所有值都 <= 1，就当归一化；否则按像素除以 video 尺寸。
 */
function normalizeBbox(
  bbox: number[] | null | undefined,
  video: HTMLVideoElement
): [number, number, number, number] | null {
  if (!Array.isArray(bbox) || bbox.length !== 4) return null
  const x1 = Number(bbox[0])
  const y1 = Number(bbox[1])
  const x2Orig = Number(bbox[2])
  const y2Orig = Number(bbox[3])
  if ([x1, y1, x2Orig, y2Orig].some((v) => !Number.isFinite(v))) return null

  const allNormalized = [x1, y1, x2Orig, y2Orig].every((v) => v >= 0 && v <= 1)
  let x2 = x2Orig
  let y2 = y2Orig
  if (!allNormalized) {
    const w = video.videoWidth || 0
    const h = video.videoHeight || 0
    if (w <= 0 || h <= 0) return null
    x2 = x2Orig / w
    y2 = y2Orig / h
  }
  return [
    Math.min(x1, x2),
    Math.min(y1, y2),
    Math.max(x1, x2),
    Math.max(y1, y2),
  ]
}

/**
 * 视频自然结束。
 * - playOnce=true：保持 store 视频信息（id/src/source），但 isPlaying=false、清掉 playOnce。
 *   不会自动重启，下次进入也不会再自动播。
 * - playOnce=false：保留当前行为（保留 isPlaying=true，让 controls 决定）。
 */
function handleVideoEnded() {
  const saved = getVideoState()
  if (saved.playOnce) {
    // 单次播放：保留视频信息，但不再自动续播
    setVideoState(saved.videoId, saved.videoSrc, false, {
      playOnce: false,
      source: saved.source,
    })
    stopPollingFishes()
  } else {
    // 历史浏览模式已结束也保留视频；不强制清掉
  }
}

function handleVideoError() {
  errorMsg.value = '视频加载失败，请检查视频文件是否存在或后端是否启动'
}

/**
 * 点击视频本体：
 *   1) 先尝试命中鱼 bbox → 选中鱼，emit fish-selected，**不**触发暂停/播放。
 *   2) 没命中鱼 → 切换 播放/暂停 状态。
 */
async function onVideoClick(event: MouseEvent) {
  // 优先尝试选鱼
  const hit = await trySelectFishByClick(event)
  if (hit) return

  // 没选中鱼 → 切换播放/暂停
  toggleVideoPlay()
}

async function trySelectFishByClick(event: MouseEvent): Promise<boolean> {
  if (fishes.value.length === 0) return false
  const video = videoRef.value
  if (!video) return false

  const rect = video.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) return false
  const clickX = (event.clientX - rect.left) / rect.width
  const clickY = (event.clientY - rect.top) / rect.height

  for (const fish of fishes.value) {
    if (!fish.bbox) continue
    const norm = normalizeBbox(fish.bbox, video)
    if (!norm) continue
    const [x1, y1, x2, y2] = norm
    if (clickX >= x1 && clickX <= x2 && clickY >= y1 && clickY <= y2) {
      emit('fish-selected', fish)
      return true
    }
  }
  return false
}

function toggleVideoPlay() {
  const video = videoRef.value
  if (!video) return
  const saved = getVideoState()
  const persistOpts = {
    playOnce: saved.playOnce,
    source: saved.source,
  }
  if (video.paused || video.ended) {
    video.play().catch(() => {})
    setVideoState(videoId.value, videoSrc.value, true, persistOpts)
  } else {
    video.pause()
    setVideoState(videoId.value, videoSrc.value, false, persistOpts)
  }
}

function playVideo() {
  if (videoRef.value) {
    videoRef.value.play().catch(() => {})
  }
}

function pauseVideo() {
  if (videoRef.value) {
    videoRef.value.pause()
  }
}

function onClearVideo() {
  if (videoRef.value) {
    videoRef.value.pause()
    videoRef.value.src = ''
  }
  pickedFile.value = null
  videoId.value = ''
  videoSrc.value = ''
  errorMsg.value = ''
  if (fileInputRef.value) fileInputRef.value.value = ''
  // 用户主动清空：从 localStorage 也清掉，下次进入不恢复
  clearVideoState()
}

function getVideoId() {
  return videoId.value
}

function getVideoSrc() {
  return videoSrc.value
}

defineExpose({
  triggerFileInput,
  playVideo,
  pauseVideo,
  clearVideo: onClearVideo,
  upload: onUpload,
  getVideoId,
  getVideoSrc,
  getAlgorithmVideoId: () => algorithmVideoId.value,
  getAlgorithmStatus: () => algorithmStatus.value,
  getAlgorithmError: () => algorithmError.value,
})
</script>

<style scoped>
.video-wall {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}
.stage {
  position: relative;
  flex: 1 1 0;
  width: 100%;
  min-width: 0;
  min-height: 0;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid rgb(255 255 255 / 10%);
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.video {
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
  background: #000;
}
.placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  color: rgb(255 255 255 / 78%);
}
.placeholder-title {
  font-weight: 800;
  opacity: 0.85;
}
.placeholder-sub {
  font-size: 12px;
  opacity: 0.65;
  text-align: center;
  padding-left: 12px;
  padding-right: 12px;
}
.statusbar {
  flex: 0 0 auto;
  max-height: 76px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  opacity: 0.85;
}
.status {
  display: flex;
  gap: 6px;
  overflow: hidden;
  min-width: 0;
}
.k {
  opacity: 0.7;
  flex: 0 0 auto;
}
.v {
  opacity: 0.95;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.error {
  flex: 0 0 auto;
  max-height: 36px;
  overflow: auto;
  color: #ffb4b4;
  opacity: 0.95;
  white-space: pre-wrap;
}
</style>
