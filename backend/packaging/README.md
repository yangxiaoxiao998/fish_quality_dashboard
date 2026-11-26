# Linux 后端打包与部署

后端使用 `full` Conda 环境和 PyInstaller `onedir` 模式打包。目标电脑运行时不需要
Python、Conda或Node.js，但必须复制整个 `fish-quality-backend/` 目录。

```text
fish-quality-backend/
├── fish-quality-backend
├── _internal/
├── algorithms/                # 模型、vendor源码和算法配置
├── config/backend.env
├── data/                      # 本机可写业务数据
├── mediamtx/
├── start_backend.sh
└── stop_backend.sh
```

PyInstaller spec 会显式收集 XGBoost 的原生动态库和版本文件：
`_internal/xgboost/lib/libxgboost.so`、`_internal/xgboost/VERSION`。这是
XGBoost 3.x 在导入和创建 `XGBRegressor` 时的运行时要求，不能只依赖
`hiddenimports`。发布前必须执行：

```bash
CONDA_ENV_NAME=full INCLUDE_ALGORITHM_ASSETS=1 \
  backend/packaging/build_backend.sh
dist/backend/fish-quality-backend/fish-quality-backend doctor --deep
```

`doctor --deep` 会实际导入并初始化 GroundingDINO、SAM、XGBoost、LightGBM
和 CatBoost；任何一个原生库或资源缺失都会返回非零状态，禁止继续组装交付包。

默认数据在程序目录旁的 `data/`，模型在 `algorithms/`，配置在 `config/`。可通过
`FISH_DATA_ROOT`、`FISH_ALGO_ROOT`、`FISH_CONFIG_ROOT`迁移到其他磁盘。

目标电脑推荐 Ubuntu 22.04 x86_64，并需要 PostgreSQL、Redis、ffmpeg、ffprobe、curl、
iproute2；GPU推理需要兼容的 NVIDIA驱动。PostgreSQL业务数据和Redis缓存不包含在此目录，
历史数据库必须单独导出恢复。

## 基于打包产物生成更新包

统一交付目录生成后，可直接把 PyInstaller 后端和 Electron AppImage
打成更新包，不需要在目标机安装 Python、Conda、Node.js 或 npm：

```bash
bash backend/app/services/update/build_update_package.sh \
  --packaged \
  --release-root dist/fish-quality-linux-x86_64 \
  --output-dir update/dist
```

产物为 `update_artifacts_v<version>.tar.xz`。包内的 `backend/`、`frontend/`
分别是可直接替换的运行产物；目标机的 `backend/data/`、`backend/config/backend.env`
和历史备份会自动保留。升级管理器在 PyInstaller 冻结模式下会使用独立的
`upgrade-runner` 子进程完成停服、备份、替换、启动和失败回滚。
