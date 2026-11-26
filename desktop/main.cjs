const { app, BrowserWindow, globalShortcut, nativeImage, session } = require('electron')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')

const APP_TITLE = '鱼品质智能检测系统'
const APP_INTERNAL_NAME = 'fish-quality-desktop'
const APP_DESKTOP_FILE = 'fish-quality-desktop.desktop'
const DEVELOPMENT_URL = 'http://127.0.0.1:5173'
// In a packaged build the Electron process talks to the local backend through
// this origin. Keeping it configurable is useful when a deployment launcher
// moves the backend to another port or host.
const BACKEND_ORIGIN = process.env.FISH_BACKEND_ORIGIN || 'http://127.0.0.1:8000'
const ICON_PATH = app.isPackaged
  ? path.join(process.resourcesPath, 'app_icon.png')
  : path.join(__dirname, 'build', 'icons', '512x512.png')

const MIME_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.mp3': 'audio/mpeg',
  '.mp4': 'video/mp4',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.txt': 'text/plain; charset=utf-8',
  '.webm': 'video/webm',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
}

// Some deployment machines expose an X11 display without a usable GL/EGL
// context. Electron otherwise keeps retrying GPU initialization and the kiosk
// window may never become ready. The dashboard does not require WebGL for its
// startup path, so use Chromium's stable software compositor explicitly.
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-gpu-compositing')
// Keep the X11/Wayland application class ASCII-only.  Using the Chinese
// product title as WM_CLASS makes some Linux panels display its UTF-8 bytes as
// garbled text and prevents the window from matching its .desktop launcher.
app.commandLine.appendSwitch('class', APP_INTERNAL_NAME)
app.setName(APP_INTERNAL_NAME)
if (process.platform === 'linux') {
  app.setDesktopName(APP_DESKTOP_FILE)
}

let mainWindow = null
let rendererServer = null
let maintenanceMode = false
let maintenanceToggleBusy = false
const DESKTOP_LOG_PATH = process.env.FISH_DESKTOP_LOG || '/tmp/fish-quality-desktop.log'

function desktopLog(message, details = '') {
  const line = `[${new Date().toISOString()}] ${message}${details ? ` ${details}` : ''}`
  console.log(line)
  try { fs.appendFileSync(DESKTOP_LOG_PATH, `${line}\n`, 'utf8') } catch {}
}

function showLoadFailure(title, detail) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const safe = String(detail || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
  const html = `<!doctype html><meta charset="utf-8"><style>body{font-family:sans-serif;background:#10253d;color:#fff;padding:8vh 10vw}h1{color:#ffcc66}pre{white-space:pre-wrap;background:#071523;padding:20px;border-radius:8px}button{font-size:18px;padding:10px 24px}</style><h1>${title}</h1><p>程序界面没有正常加载。后端数据不会因此丢失。</p><pre>${safe}</pre><button onclick="location.reload()">重新加载</button>`
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
}

// Desktop launchers can be double-clicked more than once. Keep one window so
// fullscreen state and the maintenance shortcut cannot fight between apps.
const hasSingleInstanceLock = app.requestSingleInstanceLock()
if (!hasSingleInstanceLock) {
  app.quit()
}

function setMaintenanceMode(enabled) {
  maintenanceMode = enabled
  if (!mainWindow || mainWindow.isDestroyed()) return

  mainWindow.setKiosk(!enabled)
  mainWindow.setFullScreenable(true)
  mainWindow.setFullScreen(!enabled)
  // The `screen-saver` level can trigger oversized X11 requests on some
  // desktop/window-manager combinations. Normal always-on-top is sufficient
  // for the kiosk window and is much more portable.
  mainWindow.setAlwaysOnTop(!enabled)
  mainWindow.setMenuBarVisibility(enabled)
  if (typeof mainWindow.setClosable === 'function') {
    mainWindow.setClosable(enabled)
  }
  mainWindow.setResizable(enabled)
  mainWindow.webContents.send('fish-quality-maintenance-mode', enabled)
}

function toggleMaintenanceMode() {
  if (maintenanceToggleBusy) return
  maintenanceToggleBusy = true
  setMaintenanceMode(!maintenanceMode)
  setTimeout(() => {
    maintenanceToggleBusy = false
  }, 800)
}

/**
 * Return the read-only renderer directory for the current launch mode.
 *
 * electron-builder puts `extraResources` under process.resourcesPath. The
 * renderer is deliberately outside app.asar so it can be served by the
 * small local HTTP server below without relying on file:// URL semantics.
 */
function getRendererRoot() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'renderer')
  return path.join(__dirname, '..', 'frontend', 'docs')
}

function shouldProxy(pathname) {
  return pathname === '/health' || pathname.startsWith('/api/') || pathname.startsWith('/static/')
}

/** Proxy API/media requests to FastAPI while keeping the browser same-origin. */
function proxyToBackend(request, response) {
  let upstreamUrl
  try {
    upstreamUrl = new URL(request.url || '/', BACKEND_ORIGIN)
  } catch (error) {
    response.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({ detail: `后端地址无效：${error.message}` }))
    return
  }

  const upstream = http.request(
    upstreamUrl,
    {
      method: request.method,
      // Do not forward the local server's Host header to FastAPI. It can be
      // bound to a non-default origin in a deployment, so use the upstream
      // host instead.
      headers: { ...request.headers, host: upstreamUrl.host },
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers)
      upstreamResponse.pipe(response)
    },
  )
  upstream.on('error', (error) => {
    if (response.headersSent) {
      response.destroy(error)
      return
    }
    response.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({ detail: `后端服务不可用：${error.message}` }))
  })
  request.on('aborted', () => upstream.destroy())
  request.pipe(upstream)
}

function resolveRendererFile(rendererRoot, pathname) {
  let decodedPath = '/'
  try {
    decodedPath = decodeURIComponent(pathname)
  } catch {
    return null
  }

  const relativePath = decodedPath.replace(/^\/+/, '')
  const candidate = path.resolve(rendererRoot, relativePath || 'index.html')
  // Prevent `..` in a URL from escaping the bundled renderer directory.
  if (candidate !== rendererRoot && !candidate.startsWith(`${rendererRoot}${path.sep}`)) return null
  try {
    if (fs.statSync(candidate).isFile()) return candidate
  } catch {
    // Vue history routes are handled by index.html.
  }
  return path.join(rendererRoot, 'index.html')
}

function serveRenderer(request, response, rendererRoot, pathname) {
  const filePath = resolveRendererFile(rendererRoot, pathname)
  if (!filePath) {
    response.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' })
    response.end('Bad request')
    return
  }

  fs.stat(filePath, (statError, stat) => {
    if (statError || !stat.isFile()) {
      response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
      response.end('Renderer file not found')
      return
    }
    const contentType = MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream'
    response.writeHead(200, {
      'Content-Type': contentType,
      'Content-Length': stat.size,
      'Cache-Control': filePath.endsWith('index.html') ? 'no-cache' : 'public, max-age=31536000, immutable',
    })
    if (request.method === 'HEAD') {
      response.end()
      return
    }
    fs.createReadStream(filePath).pipe(response)
  })
}

/** Start a loopback HTTP server for the packaged Vue renderer. */
function startRendererServer() {
  const rendererRoot = getRendererRoot()
  const indexPath = path.join(rendererRoot, 'index.html')
  if (!fs.existsSync(indexPath)) throw new Error(`前端构建产物不存在：${indexPath}`)

  rendererServer = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || '/', 'http://127.0.0.1')
    if (shouldProxy(requestUrl.pathname)) {
      proxyToBackend(request, response)
      return
    }
    serveRenderer(request, response, rendererRoot, requestUrl.pathname)
  })

  // Port 0 avoids collisions with a separately started Vite dev server. A
  // fixed port can still be requested for diagnostics via FISH_DESKTOP_PORT.
  const configuredPort = Number(process.env.FISH_DESKTOP_PORT || 0)
  return new Promise((resolve, reject) => {
    rendererServer.once('error', reject)
    rendererServer.listen(configuredPort, '127.0.0.1', () => {
      const address = rendererServer.address()
      if (!address || typeof address === 'string') {
        reject(new Error('无法获取 Electron 本地服务器监听地址'))
        return
      }
      resolve(`http://127.0.0.1:${address.port}`)
      desktopLog('renderer server ready', `root=${rendererRoot} port=${address.port}`)
    })
  })
}

async function resolveStartUrl() {
  // Explicit URL is useful for development and remote-backend diagnostics.
  if (process.env.FISH_QUALITY_URL) return process.env.FISH_QUALITY_URL
  if (!app.isPackaged && process.env.FISH_DESKTOP_STATIC !== '1') return DEVELOPMENT_URL
  return startRendererServer()
}

function createWindow(startUrl) {
  desktopLog('creating main window', `url=${startUrl}`)
  const appIcon = nativeImage.createFromPath(ICON_PATH)
  if (appIcon.isEmpty()) {
    desktopLog('application icon unavailable', ICON_PATH)
  }
  mainWindow = new BrowserWindow({
    title: APP_TITLE,
    icon: appIcon.isEmpty() ? ICON_PATH : appIcon,
    show: false,
    fullscreen: true,
    fullscreenable: true,
    kiosk: true,
    autoHideMenuBar: true,
    frame: true,
    alwaysOnTop: true,
    resizable: false,
    closable: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      devTools: false,
    },
  })

  mainWindow.on('close', (event) => {
    if (!maintenanceMode) event.preventDefault()
  })

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.loadURL(startUrl)
  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    // GNOME may ignore fullscreen requested before the window is mapped.
    // Reapply kiosk/fullscreen after show so the desktop window is actually covered.
    setMaintenanceMode(false)
  })
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    const detail = `${errorCode} ${errorDescription} ${validatedURL}`
    desktopLog('renderer did-fail-load', detail)
    showLoadFailure('页面加载失败', detail)
  })
  mainWindow.webContents.on('did-finish-load', () => desktopLog('renderer did-finish-load', mainWindow.webContents.getURL()))
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    desktopLog('renderer process gone', JSON.stringify(details))
    showLoadFailure('界面进程异常退出', JSON.stringify(details, null, 2))
  })
  mainWindow.webContents.on('unresponsive', () => desktopLog('renderer unresponsive'))
  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    desktopLog('renderer console', `level=${level} ${sourceId}:${line} ${message}`)
  })
}

app.on('second-instance', () => {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  globalShortcut.register('CommandOrControl+Shift+P', toggleMaintenanceMode)
  try {
    createWindow(await resolveStartUrl())
  } catch (error) {
    const startupError = error && error.stack ? error.stack : String(error)
    desktopLog('desktop startup failed', startupError)
    createWindow('about:blank')
    showLoadFailure('桌面程序启动失败', startupError)
  }
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  if (rendererServer) rendererServer.close()
})
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
