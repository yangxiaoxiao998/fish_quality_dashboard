# 鱼品质智能检测系统桌面启动器

启动器使用项目根目录的 `start_dev.sh` 启动后端、Worker、MediaMTX 和 Vite，确认前端可访问后打开 Electron kiosk 窗口。

## 首次安装

在 `desktop` 目录安装 Electron 依赖：

```bash
PATH="/home/dsl/miniconda3/envs/full/bin:$PATH" \
  ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/" \
  npm_config_registry="https://registry.npmmirror.com" \
  /home/dsl/miniconda3/envs/full/bin/node \
  /home/dsl/miniconda3/envs/full/lib/node_modules/npm/bin/npm-cli.js install
```

必须同时设置 `PATH` 和显式 Node.js 20。Electron 的安装脚本通过
`/usr/bin/env node` 启动；如果 `PATH` 中先找到系统旧版 Node.js，会出现
`Cannot find module 'node:path'` 或 `SyntaxError: Unexpected token '?'`。

其中 `ELECTRON_MIRROR` 用于下载 Electron 二进制包，`npm_config_registry` 用于下载 npm 依赖。桌面启动器在首次安装 Electron 时会自动使用这两个镜像；如镜像不可用，可以临时覆盖：

```bash
ELECTRON_MIRROR="https://github.com/electron/electron/releases/download/" \
NPM_REGISTRY="https://registry.npmjs.org" \
  desktop/launch_fish_quality.sh
```

项目只向桌面放置一个启动入口，不把 `launch_fish_quality.sh`、Electron 配置或测试文件放到桌面：

```bash
chmod +x desktop/launch_fish_quality.sh desktop/鱼品质智能检测系统.desktop
rm -f "$HOME/Desktop"/*.desktop
cp desktop/鱼品质智能检测系统.desktop "$HOME/Desktop/鱼品质智能检测系统.desktop"
chmod +x "$HOME/Desktop/鱼品质智能检测系统.desktop"
```

桌面快捷方式中的项目路径由安装机器上的绝对路径明确指定，因此从桌面双击时不会依赖当前工作目录。项目源码中的启动器仍使用自身位置计算项目根目录。

如果桌面环境提示不信任该启动器，右键选择“允许启动”。

## 启动器测试

在项目根目录执行：

```bash
bash scripts/test_desktop_launcher.sh
```

测试覆盖：从任意工作目录执行、前端已启动时不重复启动、前端未启动时调用 `start_dev.sh`、Node/npm CLI 显式绑定、Electron 缺失时自动安装，以及服务启动超时返回失败。

## 运行行为

- 双击“鱼品质智能检测系统”后自动启动项目服务。
- 桌面启动器会自动启动本机 MinIO Server；MinIO 数据默认保存到 `$HOME/minio-data`，已有 9000 端口服务会直接复用。
- MinIO 自动启动需要提前设置 `MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD`；开发启动脚本默认不自动启动 MinIO，可设置 `FISH_MINIO_AUTOSTART=1` 开启。
- 也可以创建项目外配置文件 `$HOME/.config/fish-quality-dashboard/minio.env`，内容仅包含 `MINIO_ROOT_USER=...` 和 `MINIO_ROOT_PASSWORD=...`；不要把密码写入项目脚本。
- Electron 使用 Chromium 内核加载 `http://127.0.0.1:5173`。
- 默认全屏、kiosk、置顶，普通应用窗口不能覆盖其上。
- 用户仍可启动其他程序；开发维护快捷键为 `Ctrl + Shift + P`。
- 快捷键第一次使用会取消全屏和置顶并允许关闭窗口，再次使用可恢复 kiosk 状态。

系统级安全窗口、锁屏界面和以更高权限运行的远程控制层不属于普通应用窗口，操作系统仍可能将其置于最上层。
