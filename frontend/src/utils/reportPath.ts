/**
 * frontend/src/utils/reportPath.ts
 *
 * 视频 / 静态路径规范化。
 *
 * 后端可能返回：
 *   - data/results/<video_id>/input/xxx.mp4
 *   - /data/results/<video_id>/input/xxx.mp4
 *   - /static/results/<video_id>/input/xxx.mp4
 *   - /home/dsl/.../data/results/.../xxx.mp4
 *   - /home/yangzt/.../...xxx.mp4
 *   - http(s)://... 完整 URL
 *
 * 浏览器 <video>/<img> 标签需要的格式：
 *   - 相对路径 /static/...  ← 让 Vite proxy (1124) / 后端 StaticFiles (8000) 处理
 *   - 完整 URL
 */

export function normalizeReportUrl(p?: string | null): string {
  if (!p) return ''
  let url = String(p).trim()
  if (!url) return ''

  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url
  }

  if (url.startsWith('/static/')) {
    return url
  }

  // data/... → /static/...
  if (url.startsWith('data/')) {
    return '/static/' + url.slice('data/'.length)
  }
  if (url.startsWith('/data/')) {
    return '/static/' + url.slice('/data/'.length)
  }

  // results/... → /static/results/...
  if (url.startsWith('results/')) {
    return '/static/' + url
  }
  if (url.startsWith('/results/')) {
    return '/static' + url
  }

  // /home/... 这种服务器绝对路径：尝试提取 /data/... 或 /results/... 子串
  if (url.startsWith('/home/') || url.startsWith('/')) {
    const dataIdx = url.indexOf('/data/')
    if (dataIdx >= 0) {
      return '/static/' + url.slice(dataIdx + '/data/'.length)
    }
    const resultsIdx = url.indexOf('/results/')
    if (resultsIdx >= 0) {
      return '/static' + url.slice(resultsIdx)
    }
    // 其它绝对路径（如 /home/.../something.mp4 没有任何 /data/ 或 /results/），不能给浏览器
    return ''
  }

  // 其它 / 开头的相对路径
  if (url.startsWith('/')) {
    return url
  }

  // 兜底当作 /static/ 处理
  return '/static/' + url
}

export function normalizeImageUrl(p?: string | null): string {
  return normalizeReportUrl(p)
}

export function isBrowserPlayableUrl(p?: string | null): boolean {
  const u = normalizeReportUrl(p)
  return u.startsWith('http://') || u.startsWith('https://') || u.startsWith('/')
}
