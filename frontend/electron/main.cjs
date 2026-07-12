const { app, BrowserWindow, session, shell } = require('electron')
const path = require('path')

const isDev = !app.isPackaged

function createWindow() {
  const window = new BrowserWindow({
    width: 1440, height: 920, minWidth: 900, minHeight: 620,
    backgroundColor: '#0b0d0e', titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, sandbox: true, nodeIntegration: false,
    },
  })
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  if (isDev) window.loadURL('http://localhost:5173')
  else window.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback) => {
    callback(permission === 'media')
  })
  createWindow()
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow() })
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
