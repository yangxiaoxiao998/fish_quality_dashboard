const { app, BrowserWindow, globalShortcut, session } = require('electron')
const path = require('node:path')

const APP_TITLE = '鱼品质智能检测系统'
const START_URL = process.env.FISH_QUALITY_URL || 'http://127.0.0.1:5173'
const ICON_PATH = path.join(__dirname, '..', 'frontend', 'src', 'assets', 'images', 'software_logo.png')

// Some deployment machines expose an X11 display without a usable GL/EGL
// context. Electron otherwise keeps retrying GPU initialization and the kiosk
// window may never become ready. The dashboard does not require WebGL for its
// startup path, so use Chromium's stable software compositor explicitly.
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-gpu-compositing')
app.commandLine.appendSwitch('in-process-gpu')

let mainWindow = null
let maintenanceMode = false
let maintenanceToggleBusy = false

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

function createWindow() {
  mainWindow = new BrowserWindow({
    title: APP_TITLE,
    icon: ICON_PATH,
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
  mainWindow.loadURL(START_URL)
  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
    // GNOME may ignore fullscreen requested before the window is mapped.
    // Reapply kiosk/fullscreen after show so the desktop window is actually covered.
    setMaintenanceMode(false)
  })
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error(`[desktop] 页面加载失败：${errorCode} ${errorDescription} ${validatedURL}`)
  })
}

app.on('second-instance', () => {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

app.whenReady().then(() => {
  app.setName(APP_TITLE)
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  globalShortcut.register('CommandOrControl+Shift+P', toggleMaintenanceMode)
  createWindow()
})

app.on('will-quit', () => globalShortcut.unregisterAll())
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
