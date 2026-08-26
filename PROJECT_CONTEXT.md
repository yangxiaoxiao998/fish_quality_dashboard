# Fish Quality Dashboard 项目上下文

本文件是后续开发任务的项目背景。它根据当前工作区代码、运行配置和已检查的调用链整理，不代表 Git 历史中的某个稳定版本。当前工作区可能包含其他开发者的未提交修改、算法迁移文件和运行时产物；开始任何开发前必须重新检查 `git status` 和目标文件的实际内容。

## 1. 项目目标

Fish Quality Dashboard 是鱼品质智能检测系统，面向养殖和生产现场，接入树莓派摄像头实时视频或用户上传的视频，对鱼进行检测、跟踪、完整性分类和品质指标计算。

系统最终保存并展示：

- 鱼目标跟踪 ID、鱼 UID、关键帧、裁剪图和 mask。
- 鱼体完整/不完整状态。
- 体长、去尾体长、体高、预测重量、肥满度。
- 体色、体表破损率、拱背等品质指标。
- 视频任务、算法阶段状态、日报、视频报告和运行日志。

当前正式业务链路是异步整视频 pipeline，不是历史的逐帧 FastAPI 检测链路。

## 2. 整体架构图

```text
树莓派摄像头 / 上传视频
        |
        | 树莓派 HTTP 控制、RTMP 推流或 multipart 上传
        v
MediaMTX
  RTMP 1935 / RTSP 8554 / HLS 8888 / WebRTC 8889
        |
        | OpenCV 读取 RTMP；上传流程直接保存视频
        v
FastAPI backend/app/main.py
        |
        +--> VideoTask / CameraRecordSchedule 写入 PostgreSQL
        +--> Redis queue `fish` / RQ Worker
        +--> camera_yzt_service 录制、预览、停止、转码
        |
        v
algorithms.tools.run_video_pipeline
        |
        +--> video_to_frames: 视频抽帧
        +--> adjust: 透视校正
        +--> RF-DETR TensorRT + segmentation + ByteTrack
        +--> first_match: 跟踪目标关键帧/crop/mask
        +--> filter_complete_fish: 完整鱼筛选
        +--> fish_condition: 尺寸、重量、肥满度、颜色、破损
        +--> arched-back: 拱背指标
        |
        v
pipeline_result.json / CSV / error_conclusion.txt
        |
        +--> algorithm_result_service 导入 PostgreSQL
        +--> 视频 DOCX/PDF 报告
        +--> 日报 DOCX/PDF 更新
        |
        v
Vue 3 前端
  Dashboard / DataInput / VideoReport / DataReport / SystemOps / Settings
```

## 3. 技术栈

### 前端

- Vue 3.5、TypeScript、Vite 7、Vue Router 4。
- Axios；默认 API 客户端超时 8 秒，长任务使用 `httpLong` 60 秒或接口级超时。
- Arco Design Vue、ECharts、Sass Embedded、Stylelint。
- Vite 开发端口 `5173`，开发代理把 `/api` 和 `/static` 转发到 `127.0.0.1:8000`。
- 前端构建命令是 `cd frontend && npm run build`，输出 `frontend/docs/`。

### 后端

- Python >= 3.10、FastAPI、Uvicorn、Pydantic Settings。
- SQLAlchemy 2、Psycopg、PostgreSQL。
- Redis、RQ；正式队列名为 `fish`。
- OpenCV、FFmpeg/ffprobe、ReportLab、python-docx、LibreOffice。
- 路由主要只做参数校验和响应，业务逻辑位于 `backend/app/services/`，数据库访问位于 `backend/app/repositories/`。

### 算法

- PyTorch/CUDA、TensorRT、RF-DETR segmentation、ByteTrack。
- 鱼品质流程还依赖 mask、尾部模型、Mask R-CNN、CatBoost、颜色和破损模型。
- 权重通常位于 `algorithms/pth/`，第三方实现位于 `algorithms/vendor/`。
- 正式入口：`python -m algorithms.tools.run_video_pipeline`。

### 基础设施

- MediaMTX：`mediamtx/mediamtx` 和 `mediamtx/mediamtx.yml`。
- PostgreSQL 默认：`postgresql://fish:fish@127.0.0.1:5432/fishdb`。
- Redis 默认：`redis://127.0.0.1:6379/0`。
- FastAPI：`8000`；Vite：`5173`；Upgrade Manager 默认配置端口 `8012`，服务代码注释中另有 `8011` 历史值，部署时以环境变量和启动日志为准。

## 4. 目录结构

```text
fish_quality_dashboard/
├── PROJECT_CONTEXT.md              # 本项目背景文档
├── AI_RULESv2.MD                   # AI 开发约束
├── start_dev.sh / stop_dev.sh      # 开发环境启动/停止
├── version.json                    # 软件版本
├── requirements.txt
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app、lifespan、路由注册
│   │   ├── settings.py             # 环境变量和运行参数
│   │   ├── paths.py                # PROJECT_ROOT、DATA_DIR
│   │   ├── routers/                # HTTP API
│   │   ├── schemas/                # Pydantic schema
│   │   ├── services/               # 业务服务
│   │   ├── repositories/           # ORM、DB、查询、启动迁移
│   │   ├── workers/                # RQ queue、worker、任务 handler
│   │   ├── infra/                  # Redis、算法运行基础设施
│   │   └── config/                 # app.yaml、thresholds.yaml，当前内容很少
│   ├── migrations/                 # Alembic 目录
│   ├── start_backend.sh
│   └── alembic.ini / pyproject.toml / uv.lock
├── algorithms/
│   ├── tools/                      # run_video_pipeline、video_to_frames
│   ├── core/                       # 校正、检测、跟踪、完整性、指标
│   ├── config/                     # paths、naming、state、cleanup
│   ├── pth/                        # 模型权重和 TensorRT engine
│   ├── vendor/                     # 第三方源码
│   ├── core_old/ tools_old/        # 历史实现，不是默认生产入口
│   └── algorithms/                 # 更早的历史模型目录
├── frontend/
│   ├── src/main.ts                 # Vue 入口
│   ├── src/App.vue
│   ├── src/router/index.ts
│   ├── src/views/
│   ├── src/components/
│   ├── src/api/
│   ├── src/composables/
│   ├── src/store/
│   ├── vite.config.ts
│   └── docs/                       # 构建产物
├── mediamtx/
├── scripts/
├── .runtime/                       # PID 文件，运行时目录
└── data/                           # 视频、jobs、results、reports、logs 等运行数据
```

测试辅助文件、截图、临时 JSON 和测试日志不得写入项目目录；本次真实测试使用的目录是项目外的 `~/Code/yangzt/test/baobiaotest1`。

## 5. 后端入口和生命周期

后端入口：

```text
backend/app/main.py
FastAPI 对象：app.main:app
开发启动：python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`create_app()` 注册静态目录和 API 路由。`lifespan()` 启动顺序大致如下：

1. `app.paths.ensure_directories()` 创建运行目录。
2. 检查 PostgreSQL，初始化 ORM 表。
3. 执行 `startup_migrations.py` 中的幂等迁移。
4. 恢复 Redis/RQ 中丢失的算法任务。
5. 执行相对路径迁移。
6. 恢复摄像头录制状态。
7. 启动定时录制调度器。
8. 启动备份调度器。

关闭时停止定时录制调度器和备份调度器。后端启动恢复不能把未完成任务无条件永久标记失败；需要根据源视频、RQ job 和计划时间决定继续执行、重新入队或明确失败。

## 6. 数据库设计

连接和 Session：

- `backend/app/repositories/db.py`：`Base`、`engine`、`SessionLocal`、`get_db()`。
- 数据库结构目前同时受 `Base.metadata.create_all`、`startup_migrations.py` 和 Alembic 影响，迁移责任不是单一机制。

### 主要表

- `video_task`
  - 主键：`video_id`。
  - 来源：upload、rtsp/摄像头、algorithm 等。
  - 状态：queued、recording、processing、done、failed、stopped、interrupted 等。
  - 保存源视频、playable 视频、结果目录、报告路径、算法阶段、鱼数量、处理时间、录制时长和调度字段。
- `fish`
  - 主键：`fish_id`，关联 `video_task`。
  - 保存 `track_id`、`fish_uid`、关键帧、crop、完整性、体长、去尾体长、体高、重量、肥满度、体色和破损率。
- `video_frame`、`frame_fish_detection`
  - 历史逐帧链路表，当前正式整视频 pipeline 不再写入。
- `stats_5min`
  - 五分钟时间桶统计。
- `detection_report`
  - 日报、视频报告、自定义合并报告；通过 `report_type` 区分。
- `operation_log`
  - HTTP 操作日志和用户动作日志。
- `camera_record_schedule`
  - 未来录制计划；保存计划时间、duration、状态、claim、video_id、实际起止时间和失败原因。

新增字段必须同步 ORM、启动迁移/Alembic、结果导入、API schema 和前端展示。测试不能清空数据库或无审计删除业务记录。

## 7. API 设计

主要路由统一由 `backend/app/main.py` 以 `/api` 前缀挂载；静态资源由 `/static` 暴露 `data/`。

### 健康和队列

- `GET /api/health`
- `GET /api/ready`
- `GET /api/health/database`
- `GET /api/worker/status`

### 视频

- `POST /api/stream/upload`
- `GET /api/videos`
- `GET /api/videos/{video_id}`、`/detail`、`/play`、`/playback`、`/fishes`
- `POST /api/videos/{video_id}/stop`
- `POST /api/videos/{video_id}/requeue`
- `POST /api/videos/batch-delete`

### 摄像头和计划

- `POST /api/camera_yzt/start`
- `POST /api/camera_yzt/stop`
- `POST /api/camera_yzt/force-restart`
- `POST /api/camera_yzt/preview/start`、`/preview/stop`
- `GET /api/camera_yzt/status`、`/frame`、`/history`
- `GET/PUT /api/camera_yzt/settings`
- `GET/PUT /api/camera_yzt/focus/settings`
- `POST /api/camera_yzt/focus/photo`、`/focus/confirm`、`/focus`
- `GET/POST /api/camera_yzt/schedules`
- `GET /api/camera_yzt/schedules/{schedule_id}`
- `POST /api/camera_yzt/schedules/{schedule_id}/cancel`

### 算法和运维

- `POST /api/algorithm/videos`
- `GET /api/algorithm/videos/{video_id}/result`
- `GET /api/algorithm/videos/{video_id}/error-conclusion`
- `POST /api/algorithm/videos/{video_id}/import-result`
- `GET /api/ops/algorithm-tasks`
- `GET /api/ops/algorithm-tasks/{video_id}`
- `POST /api/ops/algorithm-tasks/{video_id}/mark-failed`
- `POST /api/ops/algorithm-tasks/{video_id}/retry`

### 报表

- 鱼品质汇总、图表、鱼体和视频列表 API。
- `POST /api/reports/fish-quality/daily-pdf/generate`
- `GET /api/reports/history`
- `GET /api/reports/history/{report_id}/preview`
- `GET /api/reports/history/{report_id}/download`
- `POST /api/reports/history/{report_id}/regenerate`
- `DELETE /api/reports/history/{report_id}`
- `POST /api/reports/history/batch-download`
- `POST /api/reports/custom-pdf/generate`

API 约束：不能返回数据库密码、模型真实路径或机器绝对路径；浏览器使用相对路径和 `/static/...` URL；长任务必须异步执行；不要制造 `/api/api/...`。

## 8. 前端入口和页面功能

入口：

- `frontend/src/main.ts`：创建 Vue app、注册 Arco 和路由。
- `frontend/src/App.vue`：应用根组件。
- `frontend/src/router/index.ts`：页面路由和技术调试密码守卫。

主要页面：

- `/`：`ScreenHome.vue`，系统入口。
- `/dashboard`：`Dashboard.vue`，视频/摄像头、鱼体指标、统计和大屏操作。
- `/data-input`：`DataInput.vue`，摄像头输入、录制、计划录制、预览和光圈设置。
- `/video-report/:videoId?`：`VideoDetectionReport.vue`，单视频报告。
- `/admin/data-report`：`DataReport.vue`，报表工作区、鱼体明细、视频和日报。
- `/admin/report-overview`：`ReportOverview.vue`，视频报表、日报、自定义报表、批量下载和批量删除。
- `/admin/system-ops`：`SystemOpsHome.vue`，运维首页和 `AlgorithmTaskMonitor.vue`。
- `/admin/system-settings`：`SystemSettings.vue`，视觉标定、摄像头、备份、更新。
- `CameraSettingsPanel.vue`：摄像头基础参数、实时预览、焦距、四角 AF window、关闭摄像头和 LED。
- `useCameraJpegFramePolling.ts`：统一 `/api/camera_yzt/frame` JPEG 轮询，负责 abort、Object URL 和 stale 状态。

系统设置和系统运维目前主要由前端技术调试密码保护；这不是后端安全鉴权。

## 9. 摄像头、RTSP 和录制流程

地址约定：

- `FISH_PI_BASE_URL`：树莓派控制服务，常用 `http://10.42.0.2:9000` 或 `http://raspberrypi.local:9000`。
- `FISH_CAMERA_PUSH_URL`：树莓派可以访问的主机地址，例如 `rtmp://10.42.0.1:1935/fish_camera`。
- `FISH_CAMERA_READ_URL`：后端本机读取地址，通常是 `rtmp://127.0.0.1:1935/fish_camera`。

正式录制：

```text
DataInput / Dashboard
  -> POST /api/camera_yzt/start
  -> camera_yzt_service.start_stream
  -> 创建 VideoTask(status=recording)
  -> POST 树莓派 /camera/start
  -> MediaMTX 接收 RTMP
  -> OpenCV VideoCapture 读取
  -> capture_loop 保存最新 JPEG、写本地 mp4
  -> 首帧后武装 duration watchdog
  -> 1 分钟或用户 stop
  -> camera_yzt_service.stop_stream
  -> 释放 writer/capture，通知树莓派 /camera/stop
  -> FFmpeg 生成 playable H.264/yuv420p 副本
  -> RQ 入队 algorithm_video_pipeline
```

预览模式使用 `calibration_only=True`、`write_video=False`、`create_video_task=False`，不会写正式视频、不会建 `video_task`、不会入算法队列。焦距流程的照片保存在 `data/tmp/focalLength`，焦距配置在 `data/config/focus_settings.json`。

计划录制由 `camera_record_schedule_service.py` 的 daemon 每秒扫描 `pending` 计划；到点后调用同一 `start_stream`，首帧后启动 watchdog。系统重启恢复必须区分：计划尚未结束则恢复 pending，已结束则完成/补入队，不能无条件标记 failed。

## 10. 视频和算法处理流程

入口：

```text
backend/app/workers/queue.py
  -> backend/app/workers/worker_main.py
  -> backend/app/workers/handlers/algorithm_video_pipeline.py
  -> backend/app/services/algorithm_cli_service.py
  -> subprocess: python -m algorithms.tools.run_video_pipeline
```

CLI 阶段：

1. 检查 `data/calibration/current_calibration.json`。
2. `algorithms.tools.video_to_frames` 把视频写入 `data/jobs/<video_id>/frames/raw/`。
3. `algorithms.core.adjust` 做透视校正，写 `frames/adjusted/`。
4. `algorithms.core.grounded_sam_mmdet_batch` 使用 RF-DETR TensorRT、GPU mask 和 ByteTrack，输出跟踪视频、标注帧、first_match crop/mask。
5. `algorithms.core.filter_complete_fish` 把样本分到 `complete_match/` 或 `incomplete_match/`。
6. `algorithms.core.fish_condition` 计算尺寸、尾部、重量、肥满度、腹部颜色、体表破损。
7. `algorithms/core/arched-back.py` 计算拱背指标。
8. 生成 CSV、`pipeline_result.json`、`error_conclusion.txt` 和 `pipeline_state.json`。
9. 从 job 目录发布到 `data/results/<video_id>/`。
10. Worker 导入 PostgreSQL、生成视频报告、更新日报。

算法任务使用 Redis generation token 和 cancel key 防止旧任务覆盖重试结果。CLI 失败时保留现场并写 `last_error`；只有结果完整导入后才进入 done。算法结果路径应使用相对 `data/` 路径。

## 11. 数据库、报告和管理员后台关系

管理员后台入口由 `SystemSettings.vue`、`SystemOpsHome.vue`、`DataReport.vue` 和 `ReportOverview.vue` 组成：

```text
技术调试密码守卫
        |
        +--> 摄像头设置
        |      +--> 基础 stream_url/width/height/fps
        |      +--> 焦距和 AF window
        |      +--> 预览、拍照、重启、停止、LED
        |
        +--> 视觉标定
        |      +--> 独立抓帧
        |      +--> current_calibration.json
        |
        +--> 系统运维
        |      +--> RQ/Worker 状态
        |      +--> 算法任务详情、日志、重试、失败标记
        |
        +--> 数据报表
               +--> 日报自动生成和刷新
               +--> 视频报告、Word/PDF
               +--> 批量下载/删除
```

日报生成主要发生在算法任务成功导入后，由 `algorithm_video_pipeline._generate_daily_report_after_task()` 重新计算当天统计并覆盖当日日报记录。删除视频后也必须按受影响日期重新生成已有日报，避免数据库统计和 Word/PDF 内容滞后。

报告文件主要位于：

- `data/reports/daily/YYYY/MM/DD/`：日报 DOCX/PDF。
- `data/reports/custom/`：自定义合并报告。
- `data/results/<video_id>/reports/`：视频算法结果和视频报告。

## 12. 配置、启动、部署和打包

### 配置

- `backend/app/settings.py`：数据库、Redis、算法抽帧、超时等环境变量。
- `backend/app/paths.py`：项目根和 `data/` 目录。
- `algorithms/config/paths.py`：算法输入、jobs、results、权重和模型路径。
- `data/calibration/current_calibration.json`：运行时标定配置。
- `data/config/camera_settings.json`：摄像头基础设置。
- `data/config/focus_settings.json`：焦距设置。
- `mediamtx/mediamtx.yml`：流媒体协议和端口。
- `start_dev.sh`：`CONDA_ENV_NAME`、`DATABASE_URL`、`REDIS_URL`、树莓派和端口环境变量。

### 开发启动

`start_dev.sh` 根据脚本位置计算项目根目录，不应重新引入机器绝对路径。它检查 Conda Python/Node/npm、PostgreSQL、Redis、前端依赖，然后启动：

```text
MediaMTX: mediamtx/mediamtx mediamtx/mediamtx.yml
RQ:       python -m app.workers.worker_main
Backend:  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Frontend: npm run dev -- --host 0.0.0.0 --port 5173 --strictPort
```

每个进程 PID 写入 `.runtime/`，开发日志写入 `data/logs/dev/`。`stop_dev.sh` 按 PID 精确停止，禁止无条件杀掉同名系统进程。

### 打包和更新

- 前端：`cd frontend && npm run build`，生成 `frontend/docs/`。
- 更新包：`backend/app/services/update/build_update_package.sh`、`build_runtime_update_bundle.py`。
- 更新执行：`updater.py`，包含预检、备份、应用、校验、回滚和 manifest。
- 独立 Upgrade Manager：`scripts/start_upgrade_manager.sh`，模块入口为 `app.services.update.upgrade_manager`。
- 当前没有完整 Docker/生产一键打包流程；source tar.xz 和更新 ZIP 需要真实部署机端到端验收。

## 13. 已完成功能

- Vue3 开屏、检测大屏、数据录入、视频报告、日报和后台 workspace。
- 上传视频、摄像头录制、预览、停止、播放和 H.264 playable 副本。
- 树莓派推流、RTMP/MediaMTX/OpenCV 拉流、状态、事件历史、LED 和摄像头设置。
- 摄像头基础设置、焦距模式、手动镜头位置、焦距照片、四角 AF window、停止预览。
- 视觉标定和独立标定抓帧。
- RQ 异步整视频算法 pipeline、取消、重试、运维监控、结果导入。
- 系统重启后 queued/processing/interrupted 算法任务恢复入队；未结束的录制计划恢复调度。
- 鱼检测、跟踪、完整性、尺寸、重量、肥满度、体色、破损和拱背分析。
- 视频 DOCX/PDF、日报 DOCX/PDF、历史报告、自定义合并报告。
- 日报在算法完成后自动更新；删除视频后按日期重新计算已有日报。
- 报表总览批量下载：单个报表按名称下载，多个报表按名称打包 ZIP。
- 操作日志、平台使用统计、数据备份、软件更新和独立升级服务。

## 14. 当前可能存在的问题和未完成任务

- 录制视频时长必须严格满足用户设置的时长。目前系统已从明显压缩的 20~30 秒改进到接近 60 秒，但真实硬件抓帧、OpenCV、writer、FFmpeg 和停止边界仍可能造成几十毫秒到数秒误差，必须继续用 `ffprobe` 做严格 `>=60s` 验收。
- 后端进程在高频前端轮询、GPU 算法和长时间摄像头录制并发时曾出现退出；需要检查进程退出码、内存、systemd/supervisor 管理和日志轮转，不能只依赖 nohup。
- RQ worker 和后端如果同时重启，算法任务 token、Redis job、DB 状态、pipeline_state 和源视频需要一致性测试；恢复不能重复写结果或覆盖新任务。
- 录制计划恢复已增加自动恢复逻辑，但“重启时正在 recording 的任务”需要真实硬件模拟重启验收，确认不会重复创建 VideoTask、不会产生双重推流。
- 个别历史任务的算法现场可能已经损坏，恢复后仍会在 `video_to_frames` 等阶段失败；需要保留失败证据并区分“恢复失败”和“源文件损坏”。
- `algorithm_router.py` 曾存在历史重复结果转换代码和状态接口兼容问题，后续应清理并补测试。
- 数据库仍同时使用 create_all、启动迁移和 Alembic，schema 责任边界不清晰。
- `algorithms/` 中 old/history/legacy 目录较多，容易发生错误导入；正式代码不得把历史目录加入 `sys.path`。
- 真实模型依赖 CUDA、TensorRT、RF-DETR engine、vendor 扩展、尾部模型、伤口模型和 CatBoost，缺一项都可能导致 pipeline 失败。
- 技术调试密码主要在前端，后端接口没有对应强鉴权，不能视为生产级权限控制。
- `backend/app/config/app.yaml`、`thresholds.yaml` 基本为空，算法阈值和运行配置仍分散在 Python、环境变量和 JSON 中。
- data/jobs、data/results、上传视频、报告和日志缺少统一配额、保留、清理和并发策略。
- 更新包、Upgrade Manager、真实硬件和生产部署尚未完成完整端到端验收。

## 15. 后续开发建议

1. 为摄像头录制建立独立的媒体时长验收测试：使用 `ffprobe` 校验原始 mp4 和 playable H.264，要求 `duration >= requested_duration`，并记录首帧、停止、writer FPS 和转码时间。
2. 将后端、RQ worker、MediaMTX 和计划调度器交给 systemd/supervisor 管理，记录退出码并自动拉起；启动恢复必须有幂等日志和恢复结果。
3. 为 `queued/processing/interrupted/recording` 建立完整状态机，明确每种状态在后端重启、Redis 重启、worker 重启和摄像头断流时的恢复动作。
4. 增加不依赖 GPU 的队列恢复、录制状态机、计划冲突、日报更新、批量下载和路径安全测试。
5. 统一数据库迁移策略，明确 create_all、startup_migrations 和 Alembic 的职责。
6. 收敛算法入口和历史目录，保证生产入口始终是 `python -m algorithms.tools.run_video_pipeline`。
7. 为日报建立“视频完成/删除/重入队/失败恢复”事件驱动或可靠轮询机制，并保存 report revision、summary hash 和更新时间。
8. 完善磁盘配额和清理策略，保留失败任务现场但避免 jobs、results、视频和日志无限增长。
9. 将技术调试密码替换为后端鉴权、部署层认证或角色权限。
10. 完成树莓派 -> MediaMTX -> OpenCV -> 录制 -> RQ -> GPU pipeline -> PostgreSQL -> 报告 -> 浏览器的生产端到端验收，并保留可复现测试清单。

## 16. 修改代码时必须遵守的规则

### 修改前

- 先阅读本文件和 `AI_RULESv2.MD`。
- 检查 `git status`，不覆盖、回退、删除或清理其他开发者的修改。
- 先定位真实调用方、入口、schema、模型、路由和结果导入，再修改代码。
- 明确区分正式链路和 history/old/legacy 链路。
- 只解决用户提出的问题，不顺便重构无关模块。

### 后端、数据库、API

- 路由负责校验和响应，业务逻辑放 service，数据库访问放 repository。
- 长算法、录制后的处理和报告生成必须异步进入 RQ，不阻塞 FastAPI 事件循环。
- Session 使用 `get_db()` 或显式 `SessionLocal`，并在 `finally` 关闭。
- 修改数据库字段必须同步模型、幂等迁移、结果导入、API schema 和前端字段。
- 不清空数据库、不删除业务记录、不修改无关 video_id；测试删除必须记录目标和前后统计。
- 不向前端返回绝对路径、模型路径、密码、数据库连接串或内部堆栈。
- 使用项目相对路径和 `/static/...` URL。
- API 路径统一 `/api/...`，不得产生 `/api/api/...`。

### 摄像头、计划和恢复

- 树莓派推流地址不能是 `127.0.0.1`；后端 OpenCV 读取地址才使用本机回环地址。
- 预览、标定、正式录制和算法处理必须状态隔离。
- 修改 `duration_minutes`、watchdog、writer、转码或 recovery 时，必须用 ffprobe 验证真实媒体文件。
- 任何启动恢复逻辑必须幂等，不能重复创建 VideoTask、重复入队或让旧 token 覆盖新结果。
- 测试计划必须考虑时间冲突、设备占用、后端重启、Redis 重启和 worker 重启。

### 算法和路径

- 正式入口固定为 `python -m algorithms.tools.run_video_pipeline`。
- 路径必须来自 `algorithms/config/paths.py` 或 `backend/app/paths.py`，禁止新增机器绝对路径。
- 执行前确认标定 JSON、模型权重、TensorRT engine 和 vendor 依赖可读。
- 保持 `pipeline_state.json`、`pipeline_result.json`、`error_conclusion.txt` 和 CSV 字段/相对路径约定。
- 区分 warning 与真实失败；失败时保留 job、日志和现场。

### 前端和验证

- 复用 Vue/Arco 既有布局和视觉风格。
- 摄像头预览统一使用 `useCameraJpegFramePolling`，不要引入未经确认的播放链路。
- 页面按钮变更必须进行真实浏览器验证：DOM、点击、请求、响应、状态变化和错误提示。
- 前端构建必须使用项目 Conda 环境的 Node/npm，至少执行 `npm run build` 或相关类型检查。
- 测试脚本、截图、临时 JSON、日志和中间文件必须放 `/tmp/` 或项目外，例如 `~/Code/yangzt/test/`。
- 不修改 `data/` 做测试，除非用户明确要求真实业务测试；真实测试必须保留任务 ID 和审计文件。
- 不执行 `git commit`、`git push`、`git reset` 或 `git checkout`，除非用户明确要求。

## 17. 常用入口速查

```text
FastAPI app:       backend/app/main.py
Frontend entry:    frontend/src/main.ts
Frontend router:   frontend/src/router/index.ts
DB models:         backend/app/repositories/models.py
DB session:        backend/app/repositories/db.py
Startup migration: backend/app/repositories/startup_migrations.py
Video router:      backend/app/routers/stream_router.py
Camera router:     backend/app/routers/camera_yzt_router.py
Camera service:    backend/app/services/camera_yzt/camera_yzt_service.py
Schedule service:  backend/app/services/camera_yzt/camera_record_schedule_service.py
Queue:             backend/app/workers/queue.py
Worker:            backend/app/workers/worker_main.py
Pipeline handler:  backend/app/workers/handlers/algorithm_video_pipeline.py
CLI bridge:        backend/app/services/algorithm_cli_service.py
Result import:     backend/app/services/algorithm_result_service.py
Algorithm CLI:     algorithms/tools/run_video_pipeline.py
Calibration:       algorithms/core/adjust_calibration.py
Perspective:       algorithms/core/adjust.py
Detection/tracker: algorithms/core/grounded_sam_mmdet_batch.py
Fish filter:       algorithms/core/filter_complete_fish.py
Measurements:      algorithms/core/fish_condition.py
Daily report:      backend/app/services/report_pdf_service.py
Report stats:      backend/app/services/report_stats_service.py
Video report:      backend/app/services/video_report_pdf_service.py
Dev start:         start_dev.sh
Dev stop:          stop_dev.sh
MediaMTX:          mediamtx/mediamtx.yml
Upgrade manager:   scripts/start_upgrade_manager.sh
```
