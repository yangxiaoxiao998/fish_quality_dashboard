/**
 * useCameraJpegFramePolling - 统一的实时 JPEG 帧轮询 composable
 *
 * 设计目标：
 * 1. 同一时间最多一个 GET /api/camera_yzt/frame 请求，串行执行。
 * 2. 短暂失败不清空最后成功帧；显示"画面重连中"。
 * 3. 持续失败超过 staleFrameTimeoutMs（默认 3 秒）后清空旧帧并标记 stale。
 * 4. JPEG 预解码成功后才替换可见 frameUrl；候选 URL 失败不暴露。
 * 5. Object URL 必须释放，防止内存泄漏。
 * 6. 组件卸载 / 主动 stop 时 AbortController 取消请求。
 * 7. 失败日志节流：最多每 5 秒打印一次。
 *
 * 同时只能有一个摄像头主推流。
 * Dashboard 与 DataInput 共享同一份 page-level composable 实例，
 * 主画面与光圈预览共用 frameUrl。
 */
import {
  computed,
  onBeforeUnmount,
  ref,
  shallowRef,
  type Ref,
} from 'vue'

export type CameraFrameState =
  | 'idle'
  | 'waiting'
  | 'live'
  | 'reconnecting'
  | 'stale'
  | 'stopped'

export interface CameraJpegFramePollingOptions {
  endpoint?: string
  intervalMs?: number
  emptyFrameRetryMs?: number
  errorRetryMs?: number
  maxErrorRetryMs?: number
  requestTimeoutMs?: number
  staleFrameTimeoutMs?: number
}

export interface UseCameraJpegFramePollingReturn {
  frameUrl: Ref<string>
  frameState: Ref<CameraFrameState>
  isRunning: Ref<boolean>
  isRequesting: Ref<boolean>
  lastSuccessAt: Ref<number | null>
  lastError: Ref<string | null>
  consecutiveErrors: Ref<number>
  start: () => void
  stop: (opts?: { clearFrame?: boolean }) => void
  requestNextNow: () => void
  clearFrame: () => void
  onVisibleImgError: () => void
}

const DEFAULT_ENDPOINT = '/api/camera_yzt/frame'
const DEFAULT_INTERVAL_MS = 200
const DEFAULT_EMPTY_RETRY_MS = 300
const DEFAULT_ERROR_RETRY_MS = 300
const DEFAULT_MAX_ERROR_RETRY_MS = 2000
const DEFAULT_REQUEST_TIMEOUT_MS = 3000
const DEFAULT_STALE_TIMEOUT_MS = 3000
const LOG_THROTTLE_MS = 5000

function resolveApiBase(): string {
  const rawBase =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ||
    (import.meta.env.VITE_API_DOMAIN as string | undefined) ||
    ''
  return String(rawBase).replace(/\/+$/, '')
}

export function useCameraJpegFramePolling(
  options: CameraJpegFramePollingOptions = {}
): UseCameraJpegFramePollingReturn {
  const endpoint = options.endpoint ?? DEFAULT_ENDPOINT
  const intervalMs = options.intervalMs ?? DEFAULT_INTERVAL_MS
  const emptyFrameRetryMs =
    options.emptyFrameRetryMs ?? DEFAULT_EMPTY_RETRY_MS
  const errorRetryMs = options.errorRetryMs ?? DEFAULT_ERROR_RETRY_MS
  const maxErrorRetryMs =
    options.maxErrorRetryMs ?? DEFAULT_MAX_ERROR_RETRY_MS
  const requestTimeoutMs =
    options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  const staleFrameTimeoutMs =
    options.staleFrameTimeoutMs ?? DEFAULT_STALE_TIMEOUT_MS

  const frameUrl = shallowRef<string>('')
  const frameState = ref<CameraFrameState>('idle')
  const isRunning = ref<boolean>(false)
  const isRequesting = ref<boolean>(false)
  const lastSuccessAt = ref<number | null>(null)
  const lastError = ref<string | null>(null)
  const consecutiveErrors = ref<number>(0)

  let nextTimer: ReturnType<typeof setTimeout> | null = null
  let staleTimer: ReturnType<typeof setTimeout> | null = null
  let activeController: AbortController | null = null
  let currentObjectUrl: string | null = null
  let generation = 0
  let lastWarnAt = 0
  let isRunningFlag = false

  function clearTimers(): void {
    if (nextTimer !== null) {
      clearTimeout(nextTimer)
      nextTimer = null
    }
    if (staleTimer !== null) {
      clearTimeout(staleTimer)
      staleTimer = null
    }
  }

  function abortActiveRequest(): void {
    if (activeController !== null) {
      try {
        activeController.abort()
      } catch {
        /* ignore */
      }
      activeController = null
    }
  }

  function clearFrame(): void {
    if (currentObjectUrl) {
      try {
        URL.revokeObjectURL(currentObjectUrl)
      } catch {
        /* ignore */
      }
      currentObjectUrl = null
    }
    frameUrl.value = ''
  }

  function scheduleStaleCheck(): void {
    if (staleTimer !== null) {
      clearTimeout(staleTimer)
    }
    staleTimer = setTimeout(() => {
      staleTimer = null
      if (!isRunningFlag) return
      if (lastSuccessAt.value === null) {
        frameState.value = 'waiting'
        return
      }
      if (Date.now() - lastSuccessAt.value > staleFrameTimeoutMs) {
        clearFrame()
        frameState.value = 'stale'
      }
    }, staleFrameTimeoutMs + 50)
  }

  function logWarn(message: string, force = false): void {
    const now = Date.now()
    if (!force && now - lastWarnAt < LOG_THROTTLE_MS) return
    lastWarnAt = now
    // eslint-disable-next-line no-console
    console.warn(message)
  }

  function logRecover(message: string): void {
    lastWarnAt = Date.now()
    // eslint-disable-next-line no-console
    console.log(message)
  }

  function scheduleNext(delayMs: number): void {
    if (!isRunningFlag) return
    if (nextTimer !== null) {
      clearTimeout(nextTimer)
    }
    nextTimer = setTimeout(() => {
      nextTimer = null
      void requestOnce()
    }, Math.max(0, delayMs))
  }

  async function requestOnce(): Promise<void> {
    if (!isRunningFlag) return
    if (isRequesting.value) return
    if (typeof AbortController === 'undefined') {
      // 浏览器不支持 AbortController：放弃轮询以避免悬挂请求
      isRunningFlag = false
      frameState.value = 'idle'
      return
    }
    isRequesting.value = true
    const myGen = generation
    const controller = new AbortController()
    activeController = controller
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    if (requestTimeoutMs > 0) {
      timeoutId = setTimeout(() => {
        try {
          controller.abort()
        } catch {
          /* ignore */
        }
      }, requestTimeoutMs)
    }

    const base = resolveApiBase()
    const url = `${base}${endpoint}?t=${Date.now()}`

    let response: Response | null = null
    try {
      response = await fetch(url, {
        method: 'GET',
        cache: 'no-store',
        signal: controller.signal,
        headers: { Accept: 'image/jpeg' },
      })
    } catch (err: any) {
      if (timeoutId !== null) clearTimeout(timeoutId)
      if (myGen !== generation) {
        isRequesting.value = false
        return
      }
      handleError(
        err?.name === 'AbortError'
          ? 'aborted'
          : (err?.message || 'fetch error')
      )
      isRequesting.value = false
      scheduleNext(errorRetryMs)
      return
    }

    if (timeoutId !== null) clearTimeout(timeoutId)
    if (myGen !== generation) {
      try {
        await response.body?.cancel()
      } catch {
        /* ignore */
      }
      isRequesting.value = false
      return
    }

    // 204 No Content: 后端明确表示当前没有帧
    if (response.status === 204) {
      handleEmpty()
      isRequesting.value = false
      scheduleNext(emptyFrameRetryMs)
      return
    }

    if (!response.ok) {
      handleError(`HTTP ${response.status}`)
      try {
        await response.body?.cancel()
      } catch {
        /* ignore */
      }
      isRequesting.value = false
      scheduleNext(errorRetryMs)
      return
    }

    const contentType = response.headers.get('content-type') || ''
    if (!contentType.toLowerCase().startsWith('image/jpeg')) {
      handleError(`unexpected content-type: ${contentType || '(empty)'}`)
      try {
        await response.body?.cancel()
      } catch {
        /* ignore */
      }
      isRequesting.value = false
      scheduleNext(errorRetryMs)
      return
    }

    let blob: Blob
    try {
      blob = await response.blob()
    } catch (err: any) {
      handleError(err?.message || 'blob read failed')
      isRequesting.value = false
      scheduleNext(errorRetryMs)
      return
    }

    if (blob.size <= 0) {
      handleEmpty()
      isRequesting.value = false
      scheduleNext(emptyFrameRetryMs)
      return
    }

    const candidateUrl = URL.createObjectURL(blob)
    let decoded = false
    try {
      const probe = new Image()
      probe.decoding = 'async'
      decoded = await decodeImage(probe, candidateUrl)
    } catch {
      decoded = false
    }

    if (!decoded) {
      try {
        URL.revokeObjectURL(candidateUrl)
      } catch {
        /* ignore */
      }
      handleError('jpeg decode failed')
      isRequesting.value = false
      scheduleNext(errorRetryMs)
      return
    }

    if (myGen !== generation) {
      try {
        URL.revokeObjectURL(candidateUrl)
      } catch {
        /* ignore */
      }
      isRequesting.value = false
      return
    }

    const oldUrl = currentObjectUrl
    currentObjectUrl = candidateUrl
    frameUrl.value = candidateUrl
    lastSuccessAt.value = Date.now()
    lastError.value = null
    consecutiveErrors.value = 0
    const wasStale = frameState.value === 'reconnecting' || frameState.value === 'stale'
    frameState.value = 'live'
    isRequesting.value = false
    if (oldUrl && oldUrl !== candidateUrl) {
      try {
        URL.revokeObjectURL(oldUrl)
      } catch {
        /* ignore */
      }
    }
    if (wasStale) {
      logRecover('[camera-frame] 实时画面已恢复')
    }
    scheduleStaleCheck()
    if (intervalMs > 0) {
      scheduleNext(intervalMs)
    }
  }

  function handleError(message: string): void {
    if (message === 'aborted') {
      // AbortError 通常来自主动 stop 或组件卸载，不输出告警
      return
    }
    consecutiveErrors.value += 1
    lastError.value = message
    if (lastSuccessAt.value !== null) {
      frameState.value = 'reconnecting'
    } else {
      frameState.value = 'waiting'
    }
    const ce = consecutiveErrors.value
    if (ce === 1 || ce % 10 === 0 || Date.now() - lastWarnAt > LOG_THROTTLE_MS) {
      logWarn(
        `[camera-frame] 画面暂时中断，连续失败=${ce}（${message}）`
      )
    }
    if (
      lastSuccessAt.value !== null &&
      Date.now() - lastSuccessAt.value > staleFrameTimeoutMs
    ) {
      clearFrame()
      frameState.value = 'stale'
    }
  }

  function handleEmpty(): void {
    consecutiveErrors.value += 1
    lastError.value = 'empty'
    if (lastSuccessAt.value !== null) {
      frameState.value = 'reconnecting'
    } else {
      frameState.value = 'waiting'
    }
    if (
      lastSuccessAt.value !== null &&
      Date.now() - lastSuccessAt.value > staleFrameTimeoutMs
    ) {
      clearFrame()
      frameState.value = 'stale'
    }
  }

  function start(): void {
    if (isRunningFlag) return
    isRunningFlag = true
    isRunning.value = true
    if (frameState.value === 'idle' || frameState.value === 'stopped') {
      frameState.value = lastSuccessAt.value === null ? 'waiting' : 'reconnecting'
    }
    scheduleNext(0)
  }

  function stop(opts: { clearFrame?: boolean } = {}): void {
    if (!isRunningFlag && activeController === null) {
      isRunning.value = false
      frameState.value = 'stopped'
      if (opts.clearFrame !== false) {
        clearFrame()
      }
      return
    }
    isRunningFlag = false
    isRunning.value = false
    generation += 1
    clearTimers()
    abortActiveRequest()
    isRequesting.value = false
    frameState.value = 'stopped'
    if (opts.clearFrame !== false) {
      clearFrame()
    }
  }

  function requestNextNow(): void {
    if (!isRunningFlag) return
    scheduleNext(0)
  }

  // 可见 <img> 自身解码失败时调用：立即清空错误帧
  function onVisibleImgError(): void {
    if (!isRunningFlag) return
    clearFrame()
    if (lastSuccessAt.value === null) {
      frameState.value = 'waiting'
    } else {
      frameState.value = 'reconnecting'
    }
    scheduleNext(errorRetryMs)
  }

  onBeforeUnmount(() => {
    stop({ clearFrame: true })
  })

  return {
    frameUrl,
    frameState,
    isRunning,
    isRequesting,
    lastSuccessAt,
    lastError,
    consecutiveErrors,
    start,
    stop,
    requestNextNow,
    clearFrame,
    onVisibleImgError,
  }
}

/**
 * decodeImage - 兼容 Image.decode() 不可用时的兜底
 * 优先用 image.decode()；不支持时降级到 onload / onerror
 */
function decodeImage(image: HTMLImageElement, url: string): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const cleanup = () => {
      image.onload = null
      image.onerror = null
    }
    const ok = () => {
      if (settled) return
      settled = true
      cleanup()
      resolve(true)
    }
    const fail = () => {
      if (settled) return
      settled = true
      cleanup()
      resolve(false)
    }
    image.onload = ok
    image.onerror = fail
    image.src = url
    if (typeof (image as any).decode === 'function') {
      ;(image as any)
        .decode()
        .then(ok)
        .catch(fail)
    }
  })
}
