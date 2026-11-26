# 鱼品质智能检测系统 Linux 统一交付包

```text
fish-quality-linux-x86_64/
├── frontend/                 # Electron AppImage
├── backend/                  # PyInstaller、模型、算法和 data
├── start.sh                  # 一键启动
├── stop.sh                   # 一键停止
└── README.md
```

推荐目标电脑为 Ubuntu 22.04 x86_64。目标电脑不需要安装 Python、Conda 或 Node.js，
但需要 PostgreSQL、Redis、ffmpeg、ffprobe、curl、iproute2 以及兼容的 NVIDIA 驱动。

启动：

```bash
chmod +x start.sh stop.sh frontend/*.AppImage
./start.sh
```

停止：

```bash
./stop.sh
```

模型在 `backend/algorithms/pth/`，业务数据在 `backend/data/`。PostgreSQL 业务数据和
Redis 缓存不包含在此目录中，需要单独迁移。项目算法 `core`、`tools` 不会以源码目录
形式复制到交付包，而是由 PyInstaller 收进后端内部资源。
