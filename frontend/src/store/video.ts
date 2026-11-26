// frontend/src/store/video.ts
//
// 视频状态 store：
//   - reactive 状态（由 Dashboard 和历史报告页面读取）
//   - 写 localStorage，刷新或切路由不丢
//   - 支持：上传 / 历史跳转 / 摄像头 等不同 source 标记
//
// localStorage key: 'fish_dashboard_video_state'
//
// 注意：
//   - 这是 SPA 单 tab 状态；如果客户机未来要做多 tab 同步，迁到 sessionStorage / cross-tab event。
//   - 任何"读 store"操作必须用 getVideoState() 返回 reactive 对象（不能返回 snapshot）。

import { reactive } from 'vue'

const STORAGE_KEY = 'fish_dashboard_video_state'

export type VideoSource = 'history' | 'upload' | 'camera' | ''

export interface VideoState {
  videoId: string
  videoSrc: string
  isPlaying: boolean
  /**
   * 是否只播放一次。
   * - true：视频自然结束时自动暂停（不 loop）
   * - false：正常播放（前端用 default autoplay 行为，由 video 标签控制）
   */
  playOnce: boolean
  source: VideoSource
  updatedAt: number
}

function loadState(): VideoState {
  if (typeof localStorage === 'undefined') {
    return defaultState()
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaultState()
    const data = JSON.parse(raw)
    return {
      videoId: typeof data.videoId === 'string' ? data.videoId : '',
      videoSrc: typeof data.videoSrc === 'string' ? data.videoSrc : '',
      isPlaying: Boolean(data.isPlaying),
      playOnce: Boolean(data.playOnce),
      source: (data.source as VideoSource) || '',
      updatedAt: Number(data.updatedAt) || 0,
    }
  } catch {
    return defaultState()
  }
}

function defaultState(): VideoState {
  return {
    videoId: '',
    videoSrc: '',
    isPlaying: false,
    playOnce: false,
    source: '',
    updatedAt: 0,
  }
}

const state = reactive<VideoState>(loadState())

function persist() {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        videoId: state.videoId,
        videoSrc: state.videoSrc,
        isPlaying: state.isPlaying,
        playOnce: state.playOnce,
        source: state.source,
        updatedAt: state.updatedAt,
      })
    )
  } catch {
    // 静默失败（localStorage 满 / 隐私模式）
  }
}

export function setVideoState(
  videoId: string,
  videoSrc: string,
  isPlaying: boolean = false,
  options: Partial<Pick<VideoState, 'playOnce' | 'source'>> = {}
) {
  state.videoId = videoId || ''
  state.videoSrc = videoSrc || ''
  state.isPlaying = Boolean(isPlaying)
  state.playOnce = Boolean(options.playOnce)
  state.source = (options.source as VideoSource) || ''
  state.updatedAt = Date.now()
  persist()
}

/**
 * 历史视频跳转专用：写入 store 并标记 playOnce = true。
 * 配合视频播放页面的 ended 处理器，自然结束时不再自动续播。
 */
export function setHistoryVideoState(videoId: string, videoSrc: string) {
  setVideoState(videoId, videoSrc, true, {
    playOnce: true,
    source: 'history',
  })
}

export function getVideoState() {
  return state
}

export function clearVideoState() {
  state.videoId = ''
  state.videoSrc = ''
  state.isPlaying = false
  state.playOnce = false
  state.source = ''
  state.updatedAt = Date.now()
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 静默
    }
  }
}

export default state
