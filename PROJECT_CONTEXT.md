
# 鱼品质智能检测系统项目背景

本文档基于当前源码、配置和启动/打包脚本整理，作为后续开发任务的项目上下文。文档描述的是当前实现，不代表所有旧文件和旧注释仍然属于正式主流程；大型三方源码、二进制模型、`node_modules` 和构建产物不作为业务源码入口。具体修复过程和验证结果记录在 `requireLog.md`。

## 1. 项目目标

系统只接收树莓派摄像头经 MediaMTX 推送并由后端录制的视频，形成可追溯的视频任务、鱼体检测结果、图片、测量数据和 PDF/DOCX 报表；当前不提供本地视频文件上传。

正式业务目标链路为：

```text
树莓派摄像头
  -> RTMP 推流到 MediaMTX（fish_camera）
  -> FastAPI 摄像头服务
  -> FFmpeg 10 秒 TS 分段录制 / 合并 H.264 MP4 / 整视频算法任务入队
  -> 抽帧、透视矫正、检测/分割/跟踪
  -> track_id / fish_uid / 鱼体代表关键帧
  -> 完整性判断
  -> 体长、去尾体长、体高、体重、肥满度、腹部颜色、体表破损率、弓背指标
  -> pipeline_result.json 与长期结果目录
  -> 导入 PostgreSQL
  -> Vue3 首页、视频报告、数据报表、系统设置和运维页面展示
```

## 2. 技术栈

- 前端：Vue 3.5、TypeScript、Vite 7、Vue Router、Axios、Arco Design Vue、ECharts、Sass。
- 后端：Python 3.10+、FastAPI、Uvicorn、Pydantic Settings、SQLAlchemy 2、PostgreSQL、Redis、RQ。
- 视频：MediaMTX；RTMP/RTSP/HLS/WebRTC；OpenCV；ffmpeg/ffprobe。
- 算法：PyTorch、GroundingDINO、SAM/SAM-HQ、MMDetection 相关组件、OpenCV、SciPy；完整性使用 ViT；体重使用 CatBoost；体表破损使用 Mask R-CNN；鱼体几何测量由 `algorithms/core/fish_condition.py` 完成。
- 桌面端：Electron 36 + electron-builder；生产前端以 AppImage 交付。
- 打包：后端 PyInstaller onedir；Linux 统一交付目录包含后端、模型、MediaMTX、前端 AppImage 和启动脚本。

## 3. 目录结构

```text
test3/
├── backend/
│   ├── app/main.py                 FastAPI 入口与生命周期
│   ├── app/routers/                API 路由
│   ├── app/services/               摄像头、算法、报表、备份、升级等服务
│   ├── app/workers/                RQ 队列、worker 与算法任务 handler
│   ├── app/repositories/           SQLAlchemy 模型、数据库、仓储、启动迁移
│   ├── app/schemas/                Pydantic 请求/响应模型
│   ├── app/config/                 已移除的空配置占位目录（运行参数在 Settings/环境变量）
│   ├── migrations/                 已移除；schema 由 startup_migrations 统一管理
│   ├── packaging/                  PyInstaller spec、构建和运行脚本
│   └── tools/                      建表、导入、诊断、清理和测试工具
├── algorithms/
│   ├── tools/run_video_pipeline.py 整视频算法编排入口
│   ├── tools/video_to_frames.py    视频抽帧
│   ├── core/                       标定、矫正、检测、跟踪、测量和质量算法
│   ├── config/                     路径、状态、命名、清理和标定边界配置
│   ├── pth/                        模型权重及 BERT/CatBoost 等模型资源
│   └── vendor/                     GroundingDINO、Grounded-SAM 等第三方源码
├── frontend/
│   ├── src/main.ts                 Vue 入口
│   ├── src/App.vue                 根组件和技术调试访问弹窗
│   ├── src/router/index.ts         页面路由和管理员访问控制
│   ├── src/views/                  首页、Dashboard、视频报告和管理员页面
│   ├── src/components/             鱼体、视频流、摄像头、报表、后台组件
│   ├── src/api/                    新 API 封装
│   └── src/services/api.ts         旧业务兼容 API 封装
├── desktop/main.cjs               Electron kiosk 壳、本地静态服务器和后端代理
├── mediamtx/                      MediaMTX 可执行文件与 mediamtx.yml
├── scripts/                       发布、升级、备份和桌面启动测试脚本
├── packaging/linux-release/       Linux 统一交付包模板
  ├── data/                          运行期数据库路径、摄像头视频、jobs、results、报告、日志和配置
├── start_dev.sh / stop_dev.sh     开发环境一键启动/停止
└── dist/、desktop/dist/            已生成的后端/桌面发布产物，不是源码主入口
```

### 3.1 关键模块与函数

- 应用组装：`app.main.create_app()` 只组装 middleware、`/static` 挂载和 router；`app.main.lifespan()` 负责建表、启动迁移、队列恢复、摄像头中断恢复、录制计划 scheduler 和 rsync scheduler。
- 摄像头与队列：`camera_yzt_service.stop_stream()` 结束摄像头任务；`queue.enqueue_algorithm_video_pipeline_from_video_task()` 写 generation token 并入队；`handlers.algorithm_video_pipeline.run_algorithm_video_pipeline()` 负责状态、取消、CLI、结果导入和报表后处理。
- 算法进程：`algorithm_cli_service.run_calibration_cli()`、`run_video_pipeline_cli()` 构造 Python/冻结模式命令、限制超时、记录 CLI 日志并读取发布结果；`algorithm_result_service.import_pipeline_result_payload_to_db()` 将 JSON 映射到数据库。
- 摄像头服务：`CameraServiceYzt.start_stream()` 管理 Pi 推流和后端 capture loop；`_capture_loop()` 读取 MediaMTX、写 JPEG/MP4、处理断流和定时 watchdog；`stop_stream()` 关闭资源并按正式/预览模式决定是否入队；`get_status()`、`sync_stream()`、`recover_interrupted_recordings_on_startup()` 支持状态恢复和 desync 排查。
- 报表与展示：`report_stats_service.get_fish_quality_summary/charts/fishes()` 统一统计口径；`report_pdf_service.generate_daily_fish_quality_pdf()` 生成日报；`video_report_pdf_service.generate_video_pdf_report()` 和 `generate_combined_video_pdf_report()` 生成视频/合并报表；`fish_router` 负责视频、鱼体、播放和导出 API。
- 算法函数：`adjust_calibration.main()` 处理视觉矫正；`run_video_pipeline.py` 调用 `video_to_frames`、`adjust`、`grounded_sam_mmdet_batch_fix.main()`、`filter_complete_fish.main()`、`fish_condition.main()` 和 `arched-back.py`；`publish_results()` 负责结果目录的临时发布和原子切换。

## 4. 核心业务流程

### 4.1 摄像头视频任务

1. 前端调用 `POST /api/camera_yzt/start`，后端创建摄像头任务并分配统一 `video_id`。
2. 树莓派向 MediaMTX 推送；OpenCV capture loop 只负责 JPEG 预览和断流状态，独立 FFmpeg 将流按 10 秒写入 `data/uploads/videos/segments/<video_id>/segment_*.ts`。
3. `POST /api/camera_yzt/stop` 先结束 FFmpeg，再验证有效分段并合并为 `data/uploads/videos/<video_id>.mp4`，随后调用 `enqueue_algorithm_video_pipeline_from_video_task(video_id=...)`；同一 ID 用于任务、jobs、results、JSON 和报告。
4. worker 调用整视频 pipeline；成功后导入 PostgreSQL，生成视频报告和日报。
5. 本系统不提供 `POST /api/videos`、`POST /api/stream/upload` 本地视频上传流程；这些旧入口和同步文件算法入口已移除。

### 4.2 摄像头录制

1. 前端调用 `POST /api/camera_yzt/start`。
2. 后端通知树莓派 `/camera/start`，传递推流目标、分辨率、FPS 和对焦参数。
3. 树莓派把视频推到主机可访问的 `camera_push_url`，MediaMTX 的 `fish_camera` 路径接收发布。
4. 后端 OpenCV 固定从本机 `FISH_CAMERA_READ_URL` 读取 MediaMTX 流，默认 `rtmp://127.0.0.1:1935/fish_camera`。
5. 后端 capture loop 保存 JPEG 最新帧；独立 FFmpeg 使用源流编码直拷贝生成短 TS 分段，避免单个 MP4 在断电时整体损坏，同时创建摄像头 `video_task`。
6. `POST /api/camera_yzt/stop` 停止采集和 FFmpeg，忽略损坏/不完整尾分段，合并有效分段为 MP4，测量真实视频时长，再入队同一个 `video_id` 的整视频算法任务。
7. 后端启动时扫描 `recording/running` 残留任务；若存在有效分段则自动合并、更新任务路径和时长并重新入队算法，只有无有效分段或恢复失败时才标记 `interrupted`。
8. 录制开始前按磁盘剩余容量和计划时长执行准入；低于 20 GB 或 15% 显示预警，低于 8 GB 或 5% 显示严重预警，低于 2 GB 或 2% 禁止新录制。录制中每约 20 秒复查，达到禁止阈值时安全停止并合并已有分段。
9. 状态接口返回 `storage` 和 `recording_segments`；Dashboard 持续提示用户插入 U 盘/移动硬盘，并复用“系统设置 → 数据备份”的 rsync 迁移功能。`scripts/rsync_data_backup.sh` 明确排除 `uploads/videos/segments/`，不得迁移正在写入的分段；正常合并后的 MP4 和其它历史数据按原配置同步。
10. 前端通过 `GET /api/camera_yzt/frame` 轮询最新 JPEG，通过 status/history/video 接口显示状态和历史。

### 4.3 标定与视觉矫正

1. 标定页面可独立抓取一帧，或使用 `/api/algorithm/calibration/capture` 完成抓帧并标定。
2. `algorithms.core.adjust_calibration` 使用 GroundingDINO + SAM/SAM-HQ 检测传送带并生成透视矩阵。
3. 当前标定写入 `data/calibration/current_calibration.json`，历史会话写入 `data/calibration/sessions/<calibration_id>/`。
4. `mm_per_pixel` 由传送带真实长度和传送带本体像素长度计算；安全边界来自 `algorithms/config/adjust_calibration_margin.json`。
5. 视频 pipeline 的 `adjust batch` 读取 current calibration，将 raw 帧写入 adjusted 帧目录。标定必须先存在，否则正式视频算法会失败。

## 5. 数据库设计

数据库连接由环境变量 `DATABASE_URL` 提供，当前启动脚本默认 PostgreSQL `fishdb`；运行期不使用 SQLite。模型定义集中在 `backend/app/repositories/models.py`，SQLAlchemy engine/session 位于 `backend/app/repositories/db.py`。

- `video_task`：摄像头视频任务主表。保存 `video_id`、摄像头推流/录制路径、状态、处理进度、算法结果路径、鱼统计、失败阶段、定时录制信息和生命周期时间；不再保存 `source_type`。
- `fish`：视频级鱼体结果表，以 `fish_id` 为主键，关联 `video_task.video_id`。保存 `track_id/fish_uid`、代表帧、图片路径、完整性状态、体长、去尾体长、体高、预测体重、肥满度、腹部颜色、破损率和错误信息。新流程同时回填旧字段以兼容旧页面。
- `frame_fish_detection`：逐帧检测记录，保存 bbox、中心点、置信度、track/fish ID、crop/mask/矫正图片路径。当前正式 pipeline 主要通过结果导入，旧的后端独立抽帧接口不再维护该流程。
- `video_frame`：视频帧元数据兼容表，包含帧号、时间和图片路径。
- `stats_5min`：五分钟统计桶，保存鱼数量、体长、体高、体重、肥满度累计值和异常数量。表和查询接口仍存在，但当前正式整视频导入链路没有发现调用 `stats_repo.upsert_stats_increment()` 的业务入口，因此不能把它当作当前实时统计真值。
- `detection_report`：日报、视频报表、合并自定义报表的历史记录，保存 PDF 路径、统计快照、状态和备份状态。
- `operation_log`：后端中间件和报表服务记录的操作审计日志。
- `camera_record_schedule`：未来录制计划、冲突、实际开始/停止和取消信息。

### 5.1 数据库存储与文件存储边界

系统不是把所有数据都存进 PostgreSQL，而是“数据库元数据 + `data/` 文件产物”双层存储：

```text
PostgreSQL
  ├── video_task       任务、状态、统计摘要、结果路径
  ├── fish             每条鱼的身份、状态、测量值、图片路径
  ├── detection_report 报表记录和统计快照
  ├── operation_log    操作审计
  └── camera_record_schedule 未来录制计划

data/
  ├── uploads/videos/segments/<video_id>/  录制中的 10 秒 TS 分段和 ffmpeg 日志
  ├── uploads/videos/<video_id>.mp4        正常停止或异常恢复后合并的源视频
  ├── uploads/         摄像头视频、标定照片、可播放转码副本
  ├── jobs/<video_id>/ 算法中间目录和状态文件
  ├── results/<video_id>/ 长期 JSON、图片、标注视频、CSV、报告
  ├── reports/         日报和自定义报表文件
  ├── calibration/     当前标定和历史会话
  ├── logs/            后端、算法、运行日志
  └── config/          摄像头、对焦等运行配置
```

数据库中的图片、视频、CSV、PDF 路径按约定应保存为相对于 `data/` 的 POSIX 路径，例如 `data/results/<video_id>/...`；前端 URL 应通过 `to_static_url()` 转为 `/static/results/<video_id>/...`；本地文件访问应通过 `to_abs_data_path()`。数据库备份不能替代 `data/` 目录备份，文件丢失会使数据库记录变成不可播放/不可预览的悬挂记录。

### 5.2 当前写入链路

1. 摄像头正式录制创建 `video_task`，保存 `source_path`，停止后沿用同一个 `video_id` 入队 RQ。
2. `algorithm_video_pipeline` 调用 CLI，CLI 发布 `data/results/<video_id>/reports/pipeline_result.json`。
3. `algorithm_result_service.import_pipeline_result_payload_to_db()` 更新 `video_task`，仅在视频结果状态为 `done/complete` 时全量替换 `fish`。
4. 导入成功后生成视频报表、日报，并通过数据库记录保存报告路径。

当前导入实现存在三个必须记住的事实：

- `video_task.source_type` 已从模型和数据库 schema 移除；启动迁移会在旧数据库中删除遗留列。
- 鱼记录导入前会校验并全量替换同一 `video_id` 的旧鱼；非法、重复或类型错误的记录会使事务失败，不会提交部分结果。
- 报告只在结果导入完整成功后生成；`fish_skipped > 0` 不再返回完整成功。

启动时 `app.main.lifespan` 会先 `create_all` 补齐缺失基础表，再执行多组 startup migration 和路径迁移。当前 schema 策略明确为自定义幂等前向迁移；多组 startup migration 失败时会记录日志并继续启动，可能出现“服务已 ready、数据库字段仍未完成”的状态，因此健康检查和真实 PostgreSQL 验收仍然重要。

## 6. API 设计

FastAPI 应用入口是 `backend/app/main.py`，大多数路由注册在 `/api` 前缀下；系统路由自身带 `/api/system` 前缀。

核心接口分组：

- 健康与 worker：`GET /api/health`、`/api/ready`、`/api/health/database`、`/api/worker/status`。
- 视频任务：`GET /api/videos`、`GET /api/videos/{video_id}`、`POST /api/videos/{video_id}/stop`、`POST /api/videos/{video_id}/requeue`、播放/转码/WebM/导出/删除接口。创建任务只走摄像头 `camera_yzt` 链路。
- 算法：`POST /api/algorithm/calibration`、`GET/POST /api/algorithm/calibration/config`、`GET /api/algorithm/calibration/status`、`POST /api/algorithm/calibration/capture`、`POST /api/algorithm/calibration/capture-frame`、`GET /api/algorithm/videos/{video_id}/result`、`GET /api/algorithm/videos/{video_id}/error-conclusion`、结果发现和导入接口。直接文件算法入口 `/api/algorithm/videos` 已移除。
- 鱼体：`GET /api/videos/{video_id}/fishes`、`GET /api/fishes`、`GET /api/fishes/{fish_id}`、批量删除、图片列表和导出接口。
- 摄像头：`/api/camera_yzt/settings`、`start`、`stop`、`preview/start`、`preview/stop`、`status`、`frame`、`history`、`video`、对焦、重置、同步和录制计划接口。
- 报表：`/api/reports/fish-quality/summary`、`charts`、`fishes`、日报/视频 PDF 生成、历史列表、预览、下载、重生成、删除、批量下载、自定义 PDF、平台使用统计和操作日志。
- 标定兼容接口：`/api/calibration/status`、`save`、`clear`、`margin-config` 保留历史兼容；`/api/calibration/perspective` 已返回 410，正式流程使用 `/api/algorithm/calibration*`。
- 运维：`/api/ops/algorithm-tasks/*`、`/api/ops/algorithm-runtime/*`，用于任务监控、失败标记、重试和运行时依赖检查。
- 系统服务：`/api/system/backup/*`、`/api/system/update/*`；另有 LED、系统升级、rsync 备份接口。

FastAPI 只将 `data/uploads`、`data/results`、`data/reports`、`data/calibration` 挂载到对应的 `/static/...` 路径；日志、配置、备份、jobs 中间文件不再通过静态根目录公开。CORS 由 `CORS_ALLOWED_ORIGINS` 控制，默认只允许本机 Vite 地址。部署时可设置 `FISH_API_KEY`，前端通过 `VITE_API_KEY` 发送 `X-API-Key`；未设置时保持本地开发兼容。前端 API 默认使用同源路径，Vite 开发代理或 Electron 本地服务器负责转发到 FastAPI。

### 6.1 系统读取数据库和结果的实际优先级

前端不是所有页面都直接读取同一张表，当前调用口径如下：

- `GET /api/videos`：主任务、鱼数量和测量平均值来自 `video_task/fish`；不会静默读取 JSON。
- `GET /api/videos/{video_id}`：任务基础信息来自 `video_task`，但 `total_fish` 会重新按 `fish` 表计数，并在不一致时回写 `video_task.total_fish`。
- `GET /api/videos/{video_id}/fishes`：主要读取 `fish` 表的新字段。
- `GET /api/fishes`：读取 `fish` 表的新测量字段；结果导入在兼容窗口内双写旧/新字段，但查询不再回退旧字段。
- `GET /api/algorithm/videos/{video_id}/result`：返回算法原始 JSON；JSON 不存在时才从 `video_task + fish` 拼装数据库 fallback。
- `/api/reports/fish-quality/summary`、charts、fish 明细、日报和视频列表：以数据库为真值；未导入 JSON 只通过结果发现和对账流程暴露。
- `/api/stats`：读取算法完成后重建的 `stats_5min`；它不是录制过程中的实时统计。
- 图片、视频、CSV、PDF、标注视频：数据库只返回路径，实际由 `/static/...` 或专用 FileResponse 从 `data/` 读取。

数据调用口径：算法结果 JSON 是原始交付物，导入后的 `video_task/fish` 是系统查询真值，`detection_report.summary_json` 是生成报告时的历史快照。新功能必须明确读取哪一层，不能无说明地混合来源。

## 7. 前端页面功能

- `/`：`ScreenHome.vue` 开屏首页，进入数据录入、报表或设置。
- `/dashboard`：`Dashboard.vue`，主检测大屏，包含视频/摄像头输入、实时状态、鱼体统计、结果展示和摄像头设置入口。
- 数据录入入口：原 `/data-input` 及其页面组件已删除，首页“录入”现在进入 `/dashboard`；当前录制和视频输入能力由 `Dashboard.vue` 承载。
- `/video-report/:videoId?`：单视频检测报告大屏。
- `/admin/data-report`：后台数据报表工作区，包含鱼品质汇总、视频列表、鱼体明细、日报/视频报表、原始/矫正图片导出和历史报表操作。
- `/admin/report-overview`：报表总览和视频任务管理。
- `/admin/system-settings`：系统设置入口，包含摄像头设置、视觉标定、数据备份和软件更新等页面。
- `/admin/system-ops`：系统运维，包含算法任务监控、平台使用统计、失败信息和操作日志。
- 技术调试访问：进入系统设置或系统运维会经 `technicalDebugAuth` 请求密码；这属于前端访问控制，不是后端认证机制。

前端入口为 `frontend/src/main.ts`；核心 API 封装在 `frontend/src/api/`，旧页面兼容调用保留在 `frontend/src/services/api.ts`。页面状态和轮询主要在组件/composable 内部完成。管理员入口由 `frontend/src/utils/technicalDebugAuth.ts` 控制，当前口令直接写在前端为 `123456`，不是后端认证。

## 8. 算法流程

`algorithms/tools/run_video_pipeline.py` 是正式整视频流程编排器，顺序为：

1. 读取已经生成的 `current_calibration.json`；正式 pipeline 本身不再执行标定，标定必须先通过独立的 `adjust_calibration` 接口/CLI 完成。
2. `video_to_frames`：将任务输入视频抽成 `data/jobs/<video_id>/frames/raw/`，使用 12 位帧号。
3. `core.adjust batch`：透视矫正到 `frames/adjusted/`，默认清理 raw 帧。
4. `core.grounded_sam_mmdet_batch_fix`：检测、分割、跟踪，生成 annotated frames、首次匹配样本和 `grounded_sam.mp4`；跟踪 ID 是鱼身份基础。
5. `core.filter_complete_fish`：按 ViT 完整性判断和管理员阈值分到 complete/incomplete；`force_all_complete` 可跳过完整性模型。
6. `core.fish_condition`：对完整鱼 crop/mask 进行旋转、中心线/轮廓几何分析，得到含尾全长、去尾体长、体高；调用 CatBoost 根据体长和体高预测体重；按 `pred_weight_g / body_length_cm^3 * 100` 计算肥满度；调用腹部颜色逻辑生成 HEX 色；可调用 wound 模型计算体表破损率。
7. `core/arched-back.py`：计算并回填弓背指标和可视化关键点。
8. `publish_results`：以 `.partial` 临时目录原子发布到 `data/results/<video_id>/`，生成 `pipeline_result.json` 和 `error_conclusion.txt`。
9. 后端 `algorithm_result_service` 解析结果 JSON，写入 `video_task` 和 `fish`；完整鱼的测量字段按 `complete` 状态导入，`incomplete`/`failed` 记录保留状态和错误信息。

ID 约束：`run_video_pipeline.py` 在没有 `--video-id` 时会自动生成 `video_YYYYMMDD_HHMMSS_<8位uuid>`；RQ 摄像头 handler 会显式传入已创建的 `video_id`，并拒绝 CLI/JSON 返回不同 ID 的结果，因此正式任务必须保持数据库、jobs、results、JSON 和报告使用同一 ID。`/api/algorithm/videos/{video_id}/result` 只读取原始结果，不是文件算法执行入口。

旧的 `seg_track.py` 是独立实验/旧入口；旧的 `fish_pipeline.py`、`stream_ingest.py` handler 和对应的废弃队列函数已删除，不应重新引入旧的逐鱼流程。

## 9. 摄像头流程

地址语义必须保持区分：

- `camera_push_url`：给树莓派使用，必须是树莓派能访问的主机局域网地址，自动模式通常为 `rtmp://<主机网卡IP>:1935/fish_camera`。
- `camera_read_url`：给后端主机 OpenCV 使用，默认 `rtmp://127.0.0.1:1935/fish_camera`。
- MediaMTX `fish_camera` path 的 source 是 publisher，监听 RTMP `:1935`，同时提供 RTSP `:8554`、HLS `:8888`、WebRTC `:8889`。
- 树莓派 API 默认统一为 `raspberrypi.local:9000`；如目标网络不支持 mDNS，必须通过 `FISH_PI_BASE_URL`/`FISH_PI_HOST` 显式覆盖，不能在不同启动脚本中保留不同固定 IP。

启动时 `camera_yzt_service` 会查询树莓派状态，必要时启动推流，等待 OpenCV 获取第一帧；正常录制才创建 `video_task`，并启动独立 FFmpeg 写 10 秒 TS 分段。预览/标定模式不写分段、不建任务、不入队。定时录制在收到首帧后才武装 watchdog，避免把设备启动等待时间计入用户设置时长。正常停止后有效分段合并为 MP4；异常重启后启动恢复流程会自动抢救已落盘分段并继续算法任务。

源码版与 Linux 交付版的数据边界必须保持独立：源码版使用项目根目录 `data/`，交付版使用 `dist/fish-quality-linux-x86_64/backend/data/`。发布组装脚本必须保留交付目录现有 `data/`、`backups/` 和 `config/backend.env`，不得用源码数据覆盖生产数据。磁盘预警以当前运行实例的 `DATA_DIR` 所在文件系统为准；检测到 `/media/$USER`、`/run/media/$USER` 或 `/mnt` 下可写移动盘时提示可立即迁移，实际同步沿用现有 rsync 配置和接口。

## 10. 管理员设置后台服务流程

管理员页面由前端路由收敛到 `/admin/system-settings` 和 `/admin/system-ops`，访问前需要前端技术调试密码。

- 摄像头设置：读写 `/api/camera_yzt/settings`；保存分辨率、FPS、推流目标；对焦设置走 `/focus/settings`、`/focus/photo`、`/focus/confirm`。
- 视觉标定：从摄像头独立抓帧，调用 `/api/algorithm/calibration/capture-frame`；执行标定调用 `/api/algorithm/calibration` 或组合接口；读取 `/status`；真实传送带长度保存到 `/api/algorithm/calibration/config`；边界和传送带方向保存到 `/api/calibration/margin-config`，最终写入 `algorithms/config/adjust_calibration_margin.json`。
- 录制计划：前端批量创建、查看、取消 `/api/camera_yzt/schedules`，后端 scheduler 在 lifespan 启动后负责到点启动/停止录制。
- 数据备份：`/api/system/backup/*` 使用系统备份服务；`/api/system/backup/rsync/*` 使用 rsync 定时备份服务，lifespan 会启动其 scheduler。
- 软件更新：上传更新包、查询状态、执行更新走 `/api/system/update/*`；独立 Upgrade Manager 监听 8012，负责冻结模式下的停服、备份、替换、启动和回滚。
- 算法任务监控：`/api/ops/algorithm-tasks/*` 查看 queued/processing/done/failed 任务，支持取消标记和重试。当前算法是 CLI 子进程模式，`preload` 明确返回不支持常驻预热。
- 操作审计：FastAPI middleware 对重要 POST/PUT/PATCH/DELETE 自动写 `operation_log`，报表服务还会写更详细的业务操作记录。

## 11. 已完成功能

- FastAPI 路由、静态数据挂载、CORS 和启动生命周期已建立。
- PostgreSQL/SQLAlchemy 模型、启动建表、历史字段兼容、结果导入和多组迁移已建立。
- Redis/RQ `fish` 队列、任务 generation token、取消、重试和启动恢复已建立。
- 树莓派 RTMP 录制可接入整视频 pipeline；摄像头有预览、对焦、定时录制、状态恢复和 desync 诊断能力。
- MediaMTX 本地流媒体服务和多协议输出已配置。
- 透视标定、批量矫正、检测/分割/跟踪、完整性判断、几何测量、体重估计、肥满度、颜色、体表破损和弓背算法已在 pipeline 中串联。
- 结果长期发布、pipeline JSON、错误结论、静态图片和标注视频访问已建立。
- 视频品质报告、日报、自定义合并 PDF、历史报表、下载、重生成和 CSV 导出已建立。
- Vue3 首页、Dashboard、视频检测报告、数据报表、摄像头设置、视觉标定、备份、更新、算法任务监控和平台使用页面已建立。
- Electron kiosk 桌面壳、同源代理、单实例、维护模式和 Linux AppImage 生成已建立。
- PyInstaller onedir 后端打包、统一 Linux 交付目录、运行自检和更新包脚本已建立。
- 源码开发机支持 `fish-quality.target` systemd user 自动启动：MediaMTX、RQ Worker、Upgrade Manager、FastAPI、Vite 分别由 `Restart=always` 守护；Electron 由 XDG autostart 在桌面登录后打开。安装入口为 `scripts/install_source_autostart.sh`。

## 12. 未完成任务与当前可能存在的问题

当前源码已经完成摄像头-only 收敛、历史迁移工具、数据库真值查询和只读对账流程。以下只保留真实未验证项或已发现的存量数据问题：

- 本机 `fishdb` 已执行备份后的历史迁移：旧 `video_task.source_type` 已删除，31 个真实存在的路径已转成 `data/` POSIX 路径；3242 个缺失文件、116 个越界路径和 10 个任务摘要不一致保留在迁移报告中，不能自动猜测修复。
- 全量 JSON/数据库/PDF 对账工具已完成；本机 9 个历史结果通过，`20260916_0009` 仍有鱼记录差异，其他大量历史任务缺少已清理的 JSON/文件。单视频 `20260916_0001` 已通过严格对账。
- 旧测量字段仍保留兼容窗口。启动迁移和 CLI 只在新字段为空时回填，所有正式查询、报表和统计读取新字段；经过完整发布周期后才能通过新的 startup migration 删除旧列。
- `stats_5min` 当前只在算法导入完成后按数据库鱼记录重建；没有实时逐帧统计。若以后接实时统计，必须先定义 `fish_uid/track_id` 事件幂等、时间桶、重试、跨桶和断流规则。
- 真实端到端仍待验收：树莓派首帧和推流、MediaMTX 到 OpenCV、断流重连、定时录制、GPU 算法、RQ 重试/取消/恢复、报告失败状态、浏览器/Electron 播放、删除和备份恢复。
- MediaMTX 源码配置已将 RTSP UDP 端口与 FastAPI `8000` 分离；已有发布版服务可能仍使用旧配置，重新发布前必须用源码重新打包并在目标机确认端口、防火墙和匿名发布/读取策略。
- `FISH_API_KEY` 仍是可选单密钥，MediaMTX 仍允许匿名发布/读取；正式远程部署需要服务端认证、授权和媒体访问控制。
- CLI 每个任务重新加载模型，长视频吞吐、显存释放和 GPU 失败恢复仍需真实设备评估。
- `dist/` 仍可能是旧发布产物。本轮只完成开发版源码验收和 `frontend/docs/` 构建，未在真实摄像头/GPU 链路通过前重新生成 `dist/`。

### 12.1 优先建议

1. 对迁移报告中的缺失路径、越界路径、10 个摘要差异和 `20260916_0009` 记录差异做业务确认；只对确认的 `video_id` 备份后重导入和重生成 PDF。
2. 用真实测试 PostgreSQL/Redis 完成重复导入、减少鱼数量、坏记录回滚、RQ 重试/取消/重启恢复和删除孤儿检查。
3. 在兼容窗口内继续保留旧测量列，但只写入新列并同步旧列；发布周期后通过 startup migration 删除旧列。
4. 保持 `stats_5min` 完成后重建，不在没有事件幂等设计前恢复增量写入。
5. 连接树莓派和 MediaMTX，验收首帧、断流、定时录制、同一 `video_id`、GPU pipeline、PDF、浏览器/Electron 播放和备份恢复。
6. 生产部署设置 `FISH_API_KEY`、收紧 MediaMTX 权限并增加正式用户认证；确认源码 MediaMTX 端口配置后再重新打包发布。
7. 开发验证命令：`python -m compileall -q backend/app`、指定 shell `bash -n`、Node 20 下 `npm run build`；完成真实开发版验收后再运行发布脚本生成 `dist/`。

### 12.2 数据库修复后的验收清单

完成数据库相关修改后，至少验证以下场景：

1. 新摄像头录制完成后，`video_task` 不再包含 `source_type`；旧数据库启动后应完成遗留列删除。
2. 重新导入同一 `video_id`，数据库鱼数量、JSON 鱼数量、`video_task.total_fish` 和报表统计一致；减少鱼数量的重跑不会残留旧鱼。
3. 人为构造一条鱼导入失败，任务不能被标记为完整 `done`，报告不能把部分结果当成完整成功。
4. 视频完成时间跨越自然日时，日报、视频列表、鱼明细按同一个视频拍摄日期返回。
5. 删除视频后，关联的 `fish`、报表、统计、逐帧记录、计划引用和 `data/` 文件没有残留或悬挂路径。
6. 结果 JSON 缺失、数据库记录缺失、图片/视频文件缺失时，API 返回可区分的状态，而不是静默显示空数据。
7. `/api/health/database` 能报告数据库连接、schema 版本、关键表/字段、孤儿数据和文件一致性；不要只报告表存在。
8. 在真实 PostgreSQL、Redis、文件目录和权限环境中运行，不以 SQLite 或空目录测试替代生产验收。

## 13. 修改代码时必须遵守的规则

1. 先阅读并沿用现有入口和服务边界；不要把算法逻辑重新写进 router，也不要绕过 RQ 直接在 FastAPI 请求线程运行长视频算法。
2. 正式算法入口固定使用 `app.services.algorithm_cli_service` 调用 `algorithms.tools.run_video_pipeline` 或 `algorithms.core.adjust_calibration`；不要重新启用已废弃的 mock/逐鱼 pipeline。
3. 所有视频任务必须保持同一个 `video_id` 在 `video_task`、`data/jobs/<video_id>`、`data/results/<video_id>`、pipeline JSON 和报表之间一致；摄像头任务入队时必须沿用已创建的 ID。
4. 不要写死机器绝对路径、用户目录、网卡地址或模型路径。使用 `app.runtime_paths`、`app.paths`、`algorithms.config.paths` 和环境变量覆盖机制。
5. 新增或修改持久化字段时，同时考虑 SQLAlchemy 模型、`startup_migrations`、结果导入、API 响应、前端类型和报表统计口径。
6. 路径写入数据库、CSV、JSON 时优先使用相对 `data/` 的 POSIX 路径；对外图片/视频必须经过 `to_static_url`，不要直接暴露不必要的绝对路径。
7. 严格区分摄像头推流地址和后端读取地址：树莓派使用 `camera_push_url`，后端使用本机 MediaMTX `camera_read_url`；不能把 `127.0.0.1` 作为树莓派推流目标。
8. 预览/标定专用摄像头会话不得创建 `video_task`、写正式视频或入队算法，除非接口明确是正式录制。
9. 长耗时前端请求必须使用 `httpLong` 或显式 timeout；算法、标定、转码和报表接口需要处理 queued/processing/done/failed 及超时状态。
10. 算法失败时不要导入半成品结果；保持 `video_task.status=failed`、记录 `failed_stage/error_message/last_error`，保留排障日志和必要现场。
11. 算法已入库但报表失败时，不要把算法任务改成 failed；分别表达算法完成和报告生成失败。
12. 删除视频/鱼体/报表时同时处理数据库关联、结果目录、源视频、报告文件和日报刷新，且只能删除 `data/` 范围内的路径。
13. 任何涉及权限、更新、备份、摄像头控制和运维任务的接口，都要考虑操作日志、异常回滚、并发/重复请求和跨进程 worker 状态。
14. 修改前端路由或后台入口时维护旧路由重定向，除非明确批准移除外部链接兼容；不要把 legacy 文件误当作正式实现。
15. 默认使用 ASCII 编辑文件，保留现有中文业务文案和 UTF-8 文件；手工修改必须使用补丁方式，避免无关格式化和构建产物噪音。
16. 完成修改后至少运行与影响范围匹配的检查：Python 导入/单测、前端 `npm run build`、算法 CLI `--help` 或 dry-run、API 路由检查、数据库迁移检查；涉及摄像头/部署时还要验证启动脚本和健康检查。
17. 不要提交数据库密码、模型权重、运行日志、PID、用户数据或临时 `data/` 内容；发布产物和源码变更分开审查。
18. 当前所有正式任务来自摄像头；不要重新添加 `video_task.source_type` 或本地文件上传入口，除非产品重新定义多来源数据模型。
19. 结果导入必须以 `video_id` 为事务边界；导入前后要对账鱼记录，明确处理旧鱼、跳过鱼、重复导入和报告状态，不得把部分成功静默当成完整成功。
20. 所有面向用户的统计必须明确数据源和时间字段：视频范围用 `VideoTask.created_at`，鱼指标用统一的新字段；不要在不同页面混用 `Fish.created_at` 和 `VideoTask.created_at`。
21. 新增 API 时先确认它读取的是数据库、pipeline JSON 还是报表快照，并在代码/文档中保持同一优先级；不要无说明地混合来源。
22. 删除或重跑视频时必须同时处理数据库关联行、JSON/结果目录、源视频、转码副本、报告文件和统计数据，并验证没有孤儿记录。
23. 数据库中的路径必须是相对 `data/` 的 POSIX 路径；禁止把服务器绝对路径写入 API 可见字段，禁止通过 `/static` 暴露日志、配置、备份和未授权数据。
24. 任何 schema 变更都必须有可重复的迁移、回滚/恢复方案和真实 PostgreSQL 验证；不能只依赖 `create_all` 或启动时静默捕获迁移异常。

## 14. 稳定性专项剩余任务与实施计划

当前剩余任务主要集中在稳定性补全和真实故障验收，不是历史记录处理。

### 14.1 剩余任务

1. `FISH_CAMERA_RECONNECT_GRACE_SECONDS` 已正式接入，默认 180 秒；恢复期间保持同一个 `video_id`、视频文件、写入器和 `video_task`，恢复后继续录像，超时才失败。
2. 已增加换摄像头、同 IP、ARP/DHCP 变化的解析 IP、邻居表 MAC、状态事件和首帧重连诊断；真实换机仍需现场验证。
3. 已增加 `backend/tools/diagnose_stability.py`、`scripts/accept_restart_recovery.sh`，覆盖后端、RQ、MediaMTX 重启后的就绪和状态验收；真实进程重启仍需现场跑新 `video_id`。
4. 已增加 Linux 进程级 watchdog；它只恢复用户态服务退出或 readiness 失败。系统整体卡死仍需部署级硬件 watchdog，普通 Python/systemd 无法恢复内核死机。
5. 已增加 `scripts/stability_fault_injection.sh` 和 reconnect 状态机烟测，覆盖断流、拔电恢复、换机、断网、进程重启和定时录制的可执行验收步骤；GPU、报告、播放和真实硬件故障仍待现场。
6. 本轮未修改或重新生成 `dist/`；稳定性专项现场通过后再生成。

### 14.2 为什么需要专项处理

摄像头故障不是单一错误，可能是：

- 树莓派进程停止；
- 摄像头断电；
- RTMP 发布中断；
- MediaMTX 仍运行但没有帧；
- OpenCV 读流阻塞；
- 更换设备后 IP 相同但 ARP 仍指向旧 MAC；
- Linux 进程退出或整个系统内核卡死。

系统还必须决定：断流期间是否继续等待、录像时间是否包含断流空档、恢复后是否继续同一个视频任务、超时后是否保留半成品。这些状态需要和 RQ、MediaMTX、数据库、定时录制同时保持一致。

其中“同 IP 换摄像头”尤其不是后端单独能完全解决的问题，因为 IP 地址无法证明设备身份。后端只能通过 HTTP 重试、重新探测 RTMP、重新请求树莓派推流和检查首帧来恢复；ARP/DHCP 缓存仍属于操作系统和网络层。

### 14.3 当前端口和验收边界

当前 `8001` 是临时源码开发端口，现有系统实际使用 `8000`。历史数据不是本专项重点，后续稳定性测试应使用新录制产生的新 `video_id`。

稳定性代码补全后，先完成 Python 编译、Shell 语法、开发服务健康检查和故障注入测试；再连接真实树莓派、MediaMTX、Redis/RQ 和 GPU 做现场验收。现场验收通过后，才允许重新生成 `dist/`。

### 14.4 现场故障注入测试用例

本节是稳定性专项的现场执行清单。所有涉及录制的用例必须先启动一条新的正式录制，并记录本次新生成的 `video_id`；不得使用历史 `video_id` 代替。本节默认开发后端端口为 `8000`，`8001` 只作为临时源码开发端口，不作为本轮验收口径。

#### 14.4.1 公共前置条件

1. 使用当前源码启动开发版，不依赖旧 `dist/`：

   ```bash
   cd /home/dsl/Code/yangzt/test3
   CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full ./start_dev.sh
   ```
2. 确认基础服务：

   ```bash
   curl --noproxy '*' --fail http://127.0.0.1:8000/api/ready
   curl --noproxy '*' --fail http://127.0.0.1:8000/api/health/database
   PYTHONPATH=backend python backend/tools/diagnose_stability.py
   ```
3. 确认 `FISH_CAMERA_RECONNECT_GRACE_SECONDS=180`；如现场需要缩短测试时间，只允许在测试环境显式设置更小值，并在测试记录中注明。生产/正式验收必须使用默认 180 秒。
4. 每条测试记录至少保存：测试编号、日期时间、摄像头设备序列号或 MAC、主机 IP、Pi IP、录制 `video_id`、断故障开始时间、恢复时间、最终任务状态、后端日志、`/api/camera_yzt/status` 响应和结果文件路径。
5. 每条录制用例的通用检查：

   ```text
   GET /api/camera_yzt/status
   GET /api/videos/{video_id}
   GET /api/algorithm/videos/{video_id}/result
   ```

#### 14.4.1.1 本轮已执行的自动化测试

以下命令已在当前开发环境执行并通过：

```bash
cd /home/dsl/Code/yangzt/test3
python -m compileall -q backend/app backend/tools
bash -n start_dev.sh backend/app/services/update/update.sh \
  scripts/install_source_autostart.sh desktop/launch_fish_quality.sh \
  scripts/process_watchdog.sh scripts/stability_fault_injection.sh \
  scripts/accept_restart_recovery.sh

DATABASE_URL=postgresql://fish:fish@127.0.0.1:5432/fishdb \
REDIS_URL=redis://127.0.0.1:6379/0 \
PYTHONPATH=backend \
/home/dsl/miniconda3/envs/full/bin/python \
  backend/tools/test_reconnect_state_machine.py

curl --noproxy '*' --fail http://127.0.0.1:8000/api/ready
curl --noproxy '*' --fail http://127.0.0.1:8000/api/health/database
PYTHONPATH=backend python backend/tools/diagnose_stability.py
bash scripts/stability_fault_injection.sh preflight

(cd frontend && env PATH=/home/dsl/miniconda3/envs/full/bin:$PATH npm run build)
```

本轮还在摄像头空闲状态下实际执行并通过：

```bash
systemctl --user restart fish-quality-backend.service
curl --noproxy '*' --fail http://127.0.0.1:8000/api/ready

systemctl --user restart fish-quality-worker.service
curl --noproxy '*' --fail http://127.0.0.1:8000/api/worker/status

systemctl --user restart fish-quality-mediamtx.service
ss -lnt '( sport = :1935 or sport = :8554 or sport = :8888 )'
```

这些自动化结果只证明源码、状态机、健康接口和空闲态进程重启正常，不能替代真实摄像头、GPU 或浏览器现场用例。

#### 14.4.2 录制与断流恢复

**ST-01 正常新视频基线【需要手动测试】**

- 目的：建立本轮现场验收的真实新 `video_id` 基线。
- 步骤：开始正式录制，确认首帧、`state=running`、`stream_ready=true`；录制至少 60 秒后手动停止。
- 预期：同一个 `video_id` 贯穿录制、停止、RQ job、结果目录、数据库和报告；状态按 `recording -> queued/processing -> done 或 failed` 变化。
- 通过：视频可播放，`frames_written > 0`，任务状态和源视频路径存在，失败时有明确 `failed_stage/last_error`。
- 终端检查：录制开始/停止通过 Dashboard；停止后替换实际新 ID 执行：
  ```bash
  VIDEO_ID=<新视频ID>
  curl --noproxy '*' --fail "http://127.0.0.1:8000/api/videos/${VIDEO_ID}"
  curl --noproxy '*' --fail "http://127.0.0.1:8000/api/algorithm/videos/${VIDEO_ID}/result"
  ```

**ST-02 树莓派摄像头进程停止后恢复【需要手动测试】**

- 目的：验证 Pi 端推流进程短暂停止时不结束当前任务。
- 步骤：开始新录制并记录 `video_id`；确认 `state=running` 后停止 Pi 摄像头进程，保持故障不超过 180 秒；观察 `/api/camera_yzt/status`；随后恢复 Pi 摄像头进程。
- 预期：断流期间后端进入 `state=reconnecting`，`current_video_id` 不变，`reconnect_remaining_seconds` 递减，前端仍显示录制界面；恢复后回到 `running`，`frames_written` 继续增长。
- 通过：任务未被提前标记 `failed`，未生成第二个 `video_id`，恢复后的录像仍由原任务继续写入。
- 终端监控：
  ```bash
  while sleep 2; do
    curl --noproxy '*' --silent http://127.0.0.1:8000/api/camera_yzt/status
    printf '\n'
  done
  ```

  Pi 端停止/恢复命令取决于现场服务名；可用 Pi API 或 `ssh pi@<PI_IP> 'sudo systemctl stop/start <camera-service>'` 执行。

**ST-03 摄像头拔电后在窗口内恢复【需要手动测试】**

- 目的：验证真实摄像头掉电/上电恢复。
- 步骤：开始新录制；拔掉摄像头或 Pi 电源，记录断流时间；在 180 秒内恢复供电并等待首帧。
- 预期：断电期间保持 `reconnecting` 和原 `video_id`；恢复后首帧重新出现，`frames_written` 增长，事件日志包含恢复事件。
- 通过：恢复后可以正常手动停止并入队；视频文件可播放；断流空档不伪造视频帧。
- 终端监控同 ST-02；拔电和上电必须由现场人员执行，不能由本机命令代替。

**ST-04 断网后恢复【需要手动测试】**

- 目的：验证主机与 Pi 之间网络短暂中断。
- 步骤：开始新录制；断开网线或临时禁用摄像头网络链路；在 180 秒内恢复网络。
- 预期：后端不立即停止任务，状态进入 `reconnecting`；Pi HTTP 重试、RTMP 重新探测和 OpenCV 重新读流继续执行；恢复后同一 `video_id` 继续录像。
- 通过：无新任务、无重复结果目录、无错误结束时间；`event_history` 能看到重连开始和恢复事件。
- 终端操作（确认接口名后执行，需现场授权）：
  ```bash
  sudo ip link set "${FISH_CAMERA_NETWORK_INTERFACE:-enp3s0}" down
  sudo ip link set "${FISH_CAMERA_NETWORK_INTERFACE:-enp3s0}" up
  ```

**ST-05 超过恢复窗口后失败【自动化状态机已执行；完整用例需要手动测试】**

- 目的：验证超时边界，防止任务无限等待，并验证半成品视频不会被静默遗弃。
- 步骤：开始新录制并制造持续断流，超过 `FISH_CAMERA_RECONNECT_GRACE_SECONDS`；不要手动点击停止。
- 预期：后端从 `reconnecting` 转为超时封存，原 `video_id` 保留；先关闭写入器并封存视频；有有效帧时生成可播放副本并沿用原 `video_id` 入队算法，没有有效视频文件时才标记 `failed`；不创建第二个任务。
- 通过：超时处理时间大于等于配置窗口；有 `frames_written > 0` 时任务进入 `queued/processing/done` 或明确的算法 `failed`，源视频和 `pipeline_result.json` 使用同一个 `video_id`；无有效文件时才为 `failed`，`last_error` 明确包含恢复窗口超时原因。
- 自动化已执行的替代烟测：
  ```bash
  DATABASE_URL=postgresql://fish:fish@127.0.0.1:5432/fishdb \
  REDIS_URL=redis://127.0.0.1:6379/0 PYTHONPATH=backend \
  /home/dsl/miniconda3/envs/full/bin/python \
    backend/tools/test_reconnect_state_machine.py
  ```

  真正超过 180 秒并验证半成品封存，必须用摄像头现场执行。

#### 14.4.3 同 IP 换摄像头与 ARP/DHCP

**ST-06 同 IP 换机且 MAC 变化【需要手动测试】**

- 目的：验证旧摄像头替换为新摄像头、IP 保持不变时的诊断能力。
- 步骤：开始新录制；记录原 Pi IP 和 MAC；停止原设备或替换同 IP 新设备；必要时按现场网络规范清理/等待邻居缓存更新；恢复推流。
- 预期：`pi_resolution.resolved_ip` 可以保持不变，但 `pi_arp.mac` 发生变化并产生 `pi_mac_changed=true` 或 `pi_arp_mac_changed` 事件；后端重新请求 `/camera/start` 并等待新首帧。
- 通过：后端不把“IP 相同”误判为“设备身份相同”；新设备恢复后原任务是否继续必须以现场产品决策为准，但诊断必须明确报告 MAC 变化和首帧状态。
- 终端诊断：
  ```bash
  ip neigh show <PI_IP>
  curl --noproxy '*' --fail http://127.0.0.1:8000/api/camera_yzt/status
  PYTHONPATH=backend python backend/tools/diagnose_stability.py
  ```

  换机、拔插和邻居缓存变化必须由现场人员执行。

**ST-07 DHCP 地址变化【需要手动测试】**

- 目的：验证 Pi 从旧 IP 获得新 IP 后，mDNS/解析器和 HTTP 重试能恢复。
- 步骤：开始新录制；让 Pi 重新获取 DHCP 地址；确认新地址可通过 `avahi-resolve-host-name` 或 `getent` 解析；恢复推流。
- 预期：`pi_resolution.resolved_ip` 更新，事件历史出现 `pi_resolved_ip_changed`；旧缓存失效后，HTTP 请求转向新地址。
- 通过：新 IP 可达，Pi 状态可读，流恢复或给出明确失败原因；不能长期使用旧缓存地址。
- 终端诊断：
  ```bash
  avahi-resolve-host-name -4 raspberrypi.local || true
  getent ahostsv4 raspberrypi.local || true
  curl --noproxy '*' --fail http://127.0.0.1:8000/api/camera_yzt/status
  ```

  DHCP 续租和实际推流恢复必须由现场网络环境执行。

#### 14.4.4 后端、RQ、MediaMTX 重启恢复

**ST-08 后端进程重启【空闲态自动化已执行；录制态需要手动测试】**

- 目的：验证 FastAPI 进程重启后的启动恢复和残留流清理。
- 步骤：开始新录制并记录 `video_id`；执行 `systemctl --user restart fish-quality-backend.service`；等待 `/api/ready` 恢复；检查数据库任务、Pi 状态和 MediaMTX。
- 预期：后端恢复后 ready；启动恢复逻辑识别并处理旧的 `recording` 任务，不伪造成功、不重复入队、不创建新 `video_id`；Pi 残留推流按日志记录清理结果。
- 通过：新请求可正常开始新录制；旧任务状态符合启动恢复策略，所有处理决定都有 `event_history` 或后端日志。
- 本轮已执行空闲态命令并通过：
  ```bash
  systemctl --user restart fish-quality-backend.service
  curl --noproxy '*' --fail http://127.0.0.1:8000/api/ready
  ```

  带有正在录制新 `video_id` 的重启场景仍需手动执行。

**ST-09 RQ Worker 重启【空闲态自动化已执行；任务态需要手动测试】**

- 目的：验证算法任务在 worker 重启后的队列恢复。
- 步骤：完成一条新录制并确认任务进入 `queued` 或 `processing`；执行 `systemctl --user restart fish-quality-worker.service`；观察 `/api/worker/status` 和 `/api/videos/{video_id}`。
- 预期：Redis 中未完成任务可被重新发现/恢复；同一个 job 或同一 `video_id` 不产生重复结果；worker 心跳恢复。
- 通过：任务最终进入 `done` 或明确 `failed`；不出现重复鱼记录、重复报告或不同 `video_id` 的结果目录。
- 本轮已执行空闲态命令并通过：
  ```bash
  systemctl --user restart fish-quality-worker.service
  curl --noproxy '*' --fail http://127.0.0.1:8000/api/worker/status
  ```

  带有 queued/processing 新 `video_id` 的任务恢复仍需手动执行。

**ST-10 MediaMTX 重启【空闲态自动化已执行；录制态需要手动测试】**

- 目的：验证 MediaMTX 重启导致 RTMP/RTSP 流中断时的恢复。
- 步骤：开始新录制；执行 `systemctl --user restart fish-quality-mediamtx.service`；观察端口 `1935/8554/8888`、后端状态和 Pi 推流；等待 MediaMTX 恢复。
- 预期：后端进入 `reconnecting`，保持原 `video_id`；MediaMTX 端口恢复后重新收到推流，后端恢复 `running`。
- 通过：无提前失败、无重复任务、恢复后 `frames_written` 继续增长；若超过窗口，按 ST-05 规则失败。
- 本轮已执行空闲态命令并通过：
  ```bash
  systemctl --user restart fish-quality-mediamtx.service
  ss -lnt '( sport = :1935 or sport = :8554 or sport = :8888 )'
  ```

  正在录制时重启 MediaMTX 并验证同一 `video_id` 恢复仍需手动执行。

**ST-11 用户态 watchdog【本轮未执行，需要手动/部署测试】**

- 目的：验证 Linux 进程级 watchdog 能恢复用户态服务。
- 步骤：确认 `fish-quality-watchdog.service` 已加载；停止或杀掉后端、RQ Worker、MediaMTX 中任一用户态进程；观察 watchdog 日志和服务状态。
- 预期：watchdog 发现 readiness 或服务 inactive，并请求对应 systemd user 服务重启；系统内核正常时服务恢复。
- 通过：服务恢复时间符合现场目标；日志明确记录发现、重启请求和恢复结果。
- 边界：不得把普通 systemd/watchdog 的通过结果解释为能够恢复内核死机；硬件 watchdog 另按部署方案验收。
- 本轮未执行 watchdog 杀进程注入；部署后命令：
  ```bash
  systemctl --user status fish-quality-watchdog.service --no-pager
  journalctl --user -u fish-quality-watchdog.service -f
  ```

  具体验证需要现场授权停止用户态服务；内核卡死不能用本测试替代。

#### 14.4.5 定时录制、GPU、报告与播放

**ST-12 定时录制到点停止【需要手动测试】**

- 目的：验证定时录制与断流恢复窗口不互相破坏。
- 步骤：用新任务设置短时定时录制；首帧到达后确认 `scheduled_stop_at` 和 `remaining_seconds`；可选在窗口内制造一次短断流；等待计划时间到达。
- 预期：断流恢复期间不提前结束；到达计划时间后由定时 watchdog 停止，原 `video_id` 入队算法。
- 通过：`auto_stop_triggered=true`，停止原因和来源正确，视频文件存在并进入后续处理。
- 终端启动/监控：
  ```bash
  curl --noproxy '*' -X POST http://127.0.0.1:8000/api/camera_yzt/start \
    -H 'Content-Type: application/json' \
    -d '{"duration_minutes":1}'
  curl --noproxy '*' --fail http://127.0.0.1:8000/api/camera_yzt/status
  ```

  真实定时录制必须由现场摄像头执行，返回的新 `video_id` 要单独记录。

**ST-13 GPU 算法真实执行【需要手动测试】**

- 目的：验证新录制视频从 RQ 到 GPU pipeline 的完整链路。
- 步骤：完成新录制；确认标定存在；等待 worker 执行 `algorithm_video_pipeline`；检查 CLI 日志、`pipeline_result.json`、数据库导入和鱼记录。
- 预期：CLI 使用原 `video_id`；结果目录为 `data/results/<video_id>/`；算法失败时任务为 `failed` 并有失败阶段，不导入半成品。
- 通过：JSON、数据库鱼记录、任务摘要和报告数据源一致；不产生孤儿 `video_task`。
- 终端检查：
  ```bash
  VIDEO_ID=<新视频ID>
  curl --noproxy '*' --fail "http://127.0.0.1:8000/api/videos/${VIDEO_ID}"
  curl --noproxy '*' --fail "http://127.0.0.1:8000/api/algorithm/videos/${VIDEO_ID}/result"
  PYTHONPATH=backend python backend/tools/diagnose_stability.py
  ```

  GPU pipeline 本身无法在当前无真实摄像头/GPU链路的条件下代替现场执行。

**ST-14 报告生成与失败隔离【需要手动测试】**

- 目的：验证算法完成与报告失败的状态隔离。
- 步骤：对新完成任务生成视频报告/日报；必要时模拟报告依赖失败，再恢复依赖重试。
- 预期：算法已完成时不因报告失败被改成算法 `failed`；报告状态单独可见，重试不重复导入鱼记录。
- 通过：报告文件路径、数据库报告记录和任务状态一致；报告失败有明确错误，不静默显示空报告。
- 终端生成命令（用新任务 ID）：
  ```bash
  VIDEO_ID=<已完成视频ID>
  curl --noproxy '*' --fail -X POST \
    http://127.0.0.1:8000/api/reports/fish-quality/video-pdf/generate \
    -H 'Content-Type: application/json' \
    -d "{\"video_id\":\"${VIDEO_ID}\"}"
  ```

  报告依赖失败隔离和重试仍需手动注入失败条件。

**ST-15 浏览器/Electron 播放与导出【需要手动测试】**

- 目的：验证新录制视频的播放、结果视频、图片和报告访问链路。
- 步骤：用新 `video_id` 打开 Dashboard、视频报告、浏览器播放接口和 Electron 页面；检查原始视频、H.264 playable 副本、标注视频、鱼图片、PDF/CSV。
- 预期：URL 使用 `/static/...` 或受控文件接口；浏览器可播放 H.264/yuv420p 副本；缺失文件时 API 返回明确状态。
- 通过：桌面端和浏览器端均能播放/预览/下载；不存在服务器绝对路径泄露；删除或失败任务不会留下无法解释的悬挂链接。
- 终端/浏览器检查：
  ```bash
  VIDEO_ID=<新视频ID>
  curl --noproxy '*' --fail "http://127.0.0.1:8000/api/videos/${VIDEO_ID}"
  curl --noproxy '*' --fail "http://127.0.0.1:8000/api/algorithm/videos/${VIDEO_ID}/result"
  ```

  浏览器和 Electron 实际播放、图片、PDF、CSV 下载必须手动完成。

### 14.4.6 本轮执行结果（2026-10-20）

已执行并通过：

1. 源码编译：`python -m compileall -q backend/app backend/tools`。
2. 指定 Shell 语法检查：`start_dev.sh`、升级脚本、自动启动脚本、桌面启动脚本、watchdog 和故障注入脚本。
3. 重连状态机烟测：验证进入 `reconnecting`、保持同一 `video_id`、恢复到 `running`。
4. `GET /api/ready`、`GET /api/health/database`、`GET /api/camera_yzt/status`。
5. `backend/tools/diagnose_stability.py` 和 `scripts/stability_fault_injection.sh preflight`。
6. Node 20 前端 `npm run build`。
7. ST-08 后端空闲态重启后 ready 恢复。
8. ST-09 RQ Worker 空闲态重启后 worker 心跳恢复。
9. ST-10 MediaMTX 空闲态重启后 `1935/8554/8888` 端口恢复。

尚未执行、必须现场手动完成：

- ST-01 至 ST-07：真实新录制、Pi 进程停止、拔电、断网、同 IP 换机和 DHCP 变化。
- ST-05 的真实 180 秒超时封存、半成品转码和 RQ 入队。
- ST-08 至 ST-10 的“正在录制/正在处理”状态下重启恢复。
- ST-11 watchdog 杀进程注入；当前 watchdog 服务尚未启动，不将其误报为通过。
- ST-12 定时录制、ST-13 GPU pipeline、ST-14 报告失败隔离、ST-15 浏览器/Electron 播放导出。

### 14.5 现场通过标准与停止条件

现场稳定性专项至少满足以下条件才可标记通过：

1. ST-01 至 ST-05 通过：断流窗口内原 `video_id` 保持不变，恢复后继续录像，超时才失败。
2. ST-06 至 ST-07 通过：同 IP 换机和 DHCP 变化能够通过 IP/MAC/邻居表/事件日志区分，不能只看 IP。
3. ST-08 至 ST-11 通过：后端、RQ、MediaMTX 用户态重启后可恢复或给出明确不可恢复原因。
4. ST-12 至 ST-15 通过：定时录制、GPU 算法、数据库导入、报告和播放使用同一新 `video_id` 串联成功。
5. 任一用例出现重复 `video_id`、结果目录串任务、静默失败、绝对路径泄露、前端提前显示结束录制或数据库孤儿记录，立即停止后续测试并先修复根因。
6. 现场验收完成后，将每条用例结果、实际 `video_id`、日志位置和未通过原因回填到 `requireLog.md`；全部通过前不得重新生成 `dist/`。

### 15. 打包命令行

以下流程是完整的 Linux x86_64 清洁构建流程，不依赖旧的 `dist/backend`、`desktop/dist` 或统一交付目录。即使旧打包产物全部删除，只保留源码、Conda `full` 环境、模型目录与 MediaMTX，也可以重新生成交付版。版本号不变时无需修改 `version.json` 与 `desktop/package.json`。

#### 15.1 从零重新打包

```bash
cd /home/dsl/Code/yangzt/test3

# 如果旧交付版正在运行，先停止；目录不存在时忽略。
./dist/fish-quality-linux-x86_64/stop.sh 2>/dev/null || true

# 删除生成物。注意：若旧交付目录含客户数据，先备份 backend/data 和 backups。
rm -rf build/pyinstaller-backend
rm -rf dist/backend
rm -rf desktop/dist
rm -rf dist/fish-quality-linux-x86_64

# 1. PyInstaller 后端；复制 algorithms/pth、vendor、config，并执行 doctor --deep。
CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full \
INCLUDE_ALGORITHM_ASSETS=1 \
./backend/packaging/build_backend.sh

# 2. 构建 Vue 前端和 Electron AppImage。
CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full \
./desktop/build_linux.sh appimage

# 3. 从零组装统一交付目录。
./scripts/build_linux_release.sh

# 4. 启动并检查。
./dist/fish-quality-linux-x86_64/start.sh
curl --noproxy '*' --fail http://127.0.0.1:8000/api/ready
curl --noproxy '*' --fail http://127.0.0.1:8012/api/upgrade/health
```

`build_linux_release.sh` 会自动创建 `backend/data/logs/runtime`、`data/run`、`data/uploads/videos/playable`、`data/jobs`、`data/results`、`backups` 和 `config/backend.env`，因此目标交付目录可以完全不存在。

如果交付目录中已有客户数据，需要“保留数据重打包”，不要删除 `dist/fish-quality-linux-x86_64`；只清理 `build/pyinstaller-backend`、`dist/backend`、`desktop/dist`，然后运行三个构建脚本。组装脚本会保留已有的 `backend/data/`、`backend/backups/` 和 `backend/config/backend.env`。

#### 15.2 更换程序 Logo

唯一源 Logo 文件为：

```text
frontend/src/assets/images/software_logo.png
```

建议使用真实 PNG 格式（不要只把 JPG 后缀改成 `.png`），透明背景或完整的正方形底图均可，至少 `1024 x 1024`。生成脚本会保留完整画面，并为 Linux Dock 留出安全边距，避免系统图标蒙版裁掉边缘。替换后执行：

关掉程序

/home/dsl/Code/yangzt/test3/dist/fish-quality-linux-x86_64/stop.sh

```bash
cd /home/dsl/Code/yangzt/test3

# 生成 16/24/32/48/64/128/256/512/1024 各尺寸 Linux 图标。
/home/dsl/miniconda3/envs/full/bin/python desktop/scripts/generate_linux_icons.py

# 重新构建 AppImage并组装交付目录。
CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full ./desktop/build_linux.sh appimage
./scripts/build_linux_release.sh
```

图标会进入 AppImage、交付目录 `fish-quality-desktop.png`、交付目录各尺寸 `icons/`、桌面快捷方式、Linux 应用菜单和 Dock。组装交付目录时会自动同步当前用户的桌面快捷方式、各尺寸 hicolor 图标并标记桌面启动器为可信；在客户机首次启动交付版时也会再次自动注册。若桌面环境仍显示旧缓存，再执行：

```bash
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
rm -f ~/.cache/icon-cache.kcache 2>/dev/null || true
```

然后注销并重新登录桌面会话。不要只替换交付目录中的单张 PNG，因为 AppImage 内部图标必须通过重新构建才能更新。
