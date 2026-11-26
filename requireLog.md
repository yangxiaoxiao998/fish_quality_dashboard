
# 第 12 章问题修复记录

更新时间：2026-10-16

本文按 `PROJECT_CONTEXT.md` 第 12 章逐项记录本轮处理结果。状态含义：

- `已修复`：源码已经修改，并完成了与影响范围匹配的静态/构建/单元级验证。
- `部分修复`：主要风险已经降低，但仍需要真实 PostgreSQL、Redis、树莓派、MediaMTX 或生产认证环境验证。
- `保留/待验收`：当前行为是有意保留，或者没有安全条件在本地完成验证。

本轮没有使用 Git 回滚；工作区不是 Git 仓库。生成的前端 `frontend/docs/` 构建产物由 `npm run build` 更新，业务源码修改均使用补丁方式完成。

## 1. 页面和旧入口

### 1.1 `/data-input` 重复页面

状态：`已修复`。

处理：

- 删除无正式路由引用的 `frontend/src/views/DataInputLite.vue`。
- 当前工作区中的正式 `DataInput.vue` 已被前序清理删除，路由不再注册 `/data-input`。
- 修改 `frontend/src/views/ScreenHome.vue`，“录入”按钮统一进入 `/dashboard`，避免首页跳转到 404。
- 清理 `DataInput` 相关 composable、样式和注释中的失效引用。
- `PROJECT_CONTEXT.md` 已改为记录当前真实入口。

验证：前端 `vue-tsc -b && vite build` 通过。

### 1.2 废弃 handler、旧页面和旧脚本

状态：`已修复`。

处理：

- 删除 `fish_pipeline.py`、`stream_ingest.py` 旧 handler 和旧摄像头逐帧上传脚本。
- 删除 `enqueue_fish_pipeline`、`enqueue_stream_ingest` 废弃队列函数。
- 删除只测试旧队列入口的禁用测试。
- 删除/清理旧数据搜索页、旧视频历史页、旧 902 pipeline 等前序清理项的路由与文档引用。

验证：`backend/app`、`frontend/src`、`algorithms` 中不再存在这些旧 handler 的正式 import 或调用。

## 2. 配置和启动

### 2.1 空配置文件

状态：`已修复`。

处理：

- 删除空的 `backend/app/config/app.yaml`。
- 删除空的 `backend/app/config/thresholds.yaml`。
- 删除空的 `backend/app/infra/threshold_loader.py`。
- 删除 PyInstaller spec 对空配置目录的打包引用。
- 更新 `settings.py` 注释，明确当前参数来源是 Settings、环境变量和算法 JSON 配置。

### 2.2 `FISH_ALGO_MODE` 遗留配置

状态：`已修复`。

处理：

- 从正式 `start_dev.sh`、后端启动脚本和 legacy_disabled 诊断脚本移除 `FISH_ALGO_MODE` 设置和导出。
- 更新 worker、后端启动脚本和项目文档，明确正式流程固定使用真实算法 CLI。

### 2.3 树莓派地址冲突

状态：`已修复`，目标机仍需验收。

处理：

- `start_dev.sh`、`pi_runtime_config.py`、源码自动启动脚本统一默认 `http://raspberrypi.local:9000`。
- 固定 IP 只能通过 `FISH_PI_BASE_URL` / `FISH_PI_HOST` 显式覆盖。
- 推流目标仍与后端读取地址分离：树莓派使用主机局域网 `camera_push_url`，后端使用本机 `127.0.0.1` 的 `camera_read_url`。

### 2.4 桌面启动器旧路径

状态：`已修复`，移动机器仍需重新生成桌面入口。

处理：

- 项目内 `.desktop` 文件改到 `/home/dsl/Code/yangzt/test3`。
- `/home/dsl/Desktop/鱼品质智能检测系统.desktop` 同步改到当前目录。
- `launch_fish_quality.sh` 保留 `FISH_QUALITY_PROJECT_ROOT` 覆盖能力，跨机器部署应使用该变量或重新生成 `.desktop` 文件。

## 3. 摄像头任务、任务 ID 和算法入口

### 3.1 移除本地文件上传和重复执行算法

状态：`已修复`。

处理：

- 删除 `POST /api/videos`、`POST /api/stream/upload` 文件上传入口。
- 删除 `VideoWall.vue`、文件选择器和前端上传 API；Dashboard 只显示摄像头输入和结果/历史视频。
- 删除同步 `POST /api/algorithm/videos` 文件输入入口。
- 摄像头停止后只轮询同一个摄像头 `video_id` 的 `/api/videos/{video_id}` 状态。
- 前端算法状态显示改为 queued/processing/done/failed 的后台任务状态。
- `stream_router.py`、摄像头 service、前端兼容 API 的过时说明已更新。

验证：前端构建通过；源码搜索确认前端没有文件 input、上传 API 或 `runAlgorithmVideo` 调用。

### 3.2 `video_id` 一致性

状态：`已修复`。

处理：

- 摄像头正式任务使用 `enqueue_algorithm_video_pipeline_from_video_task(video_id=...)`。
- 算法结果导入不会创建新的任务 ID。
- 同步文件算法入口已移除；算法只由摄像头任务进入 RQ pipeline。
- 摄像头停止返回和日志说明改为沿用同一个 `video_id`。

## 4. 数据库结果导入

### 4.1 `source_type` 字段移除

状态：`已修复`。

处理：

- 系统现在只有摄像头输入，删除 `VideoTask.source_type` 模型字段及所有 API/前端业务引用。
- `run_video_source_type_removal_migration()` 会在旧 PostgreSQL 中执行 `ALTER TABLE video_task DROP COLUMN IF EXISTS source_type`。
- 摄像头历史接口不再按来源字段过滤，所有视频任务都视为摄像头任务。
- 删除本地文件上传接口、同步文件算法入口和前端文件选择功能。

### 4.2 重复导入残留旧鱼

状态：`已修复`。

处理：

- `done/complete` 结果导入前先完整校验鱼记录和重复 `fish_id`。
- 校验通过后，在同一数据库事务内删除该 `video_id` 的旧 `fish`，再写入当前结果。
- 重新导入鱼数量减少时不会保留旧鱼。
- 重新计算 `video_task.total_fish`、`processed_fish`、完整/不完整/失败/损伤摘要。
- 同时重建该视频可选的 `stats_5min` 汇总。

### 4.3 部分鱼导入被静默当成成功

状态：`已修复`。

处理：

- 结果结构非法、鱼记录不是对象、`fish_id` 重复时直接抛出异常，事务不提交。
- 返回值只有在没有跳过记录时才是 `ok=True`。
- 同步算法接口在导入失败时不生成成功报表，并返回明确的 `import_result` / `report_result`。
- RQ worker 只有结果导入成功后才继续生成报表和清理输入副本。

## 5. 数据读取和统计口径

### 5.1 JSON、数据库、报表多数据源

状态：`部分修复，已明确后续收敛步骤`。

处理：

- 结果导入完成后，报表汇总和图表优先使用数据库 `fish`；只有数据库没有鱼记录时才读取 JSON 作为未导入结果 fallback。
- 鱼明细接口在数据库已有记录时不再用旧 JSON 覆盖数据库字段。
- `GET /api/algorithm/videos/{video_id}/result` 仍保留 JSON 优先、数据库 fallback，因为该接口本身用于查看算法原始交付物。
- 单视频/合并视频 PDF 构建在有数据库鱼记录时优先使用数据库 payload，避免报告继续使用旧 JSON 鱼列表；报告 URL 写回 JSON 仅作为算法交付物元数据。
- 项目文档明确了各 API 的读取优先级。

剩余：需要真实数据验收 JSON、数据库、PDF 是否完全一致，并清理历史数据库中已经产生的错误记录。

后续步骤已写入 `PROJECT_CONTEXT.md` 第 12.1 项 12：备份、扫描结果目录、建立 JSON/数据库/报告对账表、批量重导入、重生成报告、通过对账后关闭普通 API 的 JSON fallback。

### 5.2 新旧测量字段混用

状态：`已修复主要查询路径`。

处理：

- `/api/fishes` 的长度、体重、体高筛选改用新字段并回退旧字段：`coalesce(new, old)`。
- 结果导入继续双写新旧字段，兼容历史页面。
- 报表统计使用 `body_length_cm`、`body_length_no_tail_cm`、`body_height_cm`、`pred_weight_g`。

剩余：旧数据库数据需要执行一次数据回填/质量检查，不能仅依赖代码回退。

后续步骤已写入 `PROJECT_CONTEXT.md` 第 12.1 项 13：先备份，回填新字段，统一新字段查询，做单位/非负/完整性质量检查，经过兼容窗口后再删除旧字段。

### 5.3 日期口径不一致

状态：`已修复主要报表路径`。

处理：

- 报表汇总继续以 `VideoTask.created_at` 作为视频拍摄/录制日期。
- 日报鱼记录按当天视频的 `video_id` 关联 `fish`，不再按 `Fish.created_at` 过滤。
- `/api/stats/distribution` 改为按 `VideoTask.created_at` 过滤和聚合。
- 鱼数量趋势按视频创建时间分桶，而不是算法导入时间。

## 6. 统计表、逐帧表和删除

### 6.1 `stats_5min` 未接通

状态：`已修复导入链路，实时链路待确认`。

处理：

- 每次完整算法结果导入后，按当前数据库鱼记录重建该视频的五分钟统计。
- 手工删除鱼时清除该视频旧的五分钟统计，避免继续展示过期统计。
- `/api/stats` 继续保留查询接口。

剩余：如果产品需要录制过程中的实时五分钟统计，应在 capture loop/实时算法阶段明确调用增量写入；当前实现是算法完成后的重建统计。

难度评估：算法完成后的重建属于低难度，已完成；录制过程实时统计属于中等难度，关键难点是 `track_id/fish_uid` 去重、重复任务幂等、断流/重试和五分钟时区边界。具体设计步骤已写入 `PROJECT_CONTEXT.md` 第 12.1 项 14。当前先保留结果完成后重建，避免未经定义就按每帧写库造成重复计数。

### 6.2 删除视频留下孤儿数据

状态：`已修复`。

处理：删除单个或批量视频时现在同时处理：

- `DetectionReport`、`Fish`、`Stats5Min`。
- `VideoFrame`、`FrameFishDetection`。
- 已关联的 `CameraRecordSchedule`（清空其 `video_id`，保留计划历史）。
- `source_path`、`result_dir` 和报表文件；只删除 `data/` 内路径。
- 单条/批量删除鱼后同步刷新视频任务摘要并清除过期五分钟统计。

## 7. 路径、静态文件和安全

### 7.1 结果绝对路径

状态：`已修复新导入路径`。

处理：

- 算法结果导入的 `input_video`、`original_input_video`、结果目录、CSV、图片目录统一限制为 `data/` 相对 POSIX 路径。
- `crop_image_path` 同样不再持久化项目外绝对路径。
- 外部 URL 继续通过 `to_static_url()` 生成。

剩余：历史数据库中已存在的绝对路径需要运行一次路径迁移工具；本轮未连接生产库自动修改历史数据。

后续步骤已写入 `PROJECT_CONTEXT.md` 第 12.1 项 15：停止写入并备份，扫描所有路径格式，只转换真实存在且位于 `data/` 内的文件，事务更新并输出缺失/越界清单，最后验证静态 URL 和专用下载接口。

### 7.2 `/static` 和 CORS

状态：`已修复主要暴露面，认证需部署配置`。

处理：

- 不再挂载整个 `data/` 根目录。
- 只公开 `uploads`、`results`、`reports`、`calibration` 四个用户需要的媒体目录。
- 日志、配置、备份、jobs 中间文件不再通过静态 URL 直接读取。
- CORS 改为 `CORS_ALLOWED_ORIGINS`，默认只允许本机 Vite 地址。
- 增加可选 `FISH_API_KEY` 后端 API 鉴权，前端通过 `VITE_API_KEY` 发送 `X-API-Key`。
- 前端技术密码支持 `VITE_TECHNICAL_DEBUG_PASSWORD` 构建时注入。

剩余：当前 API key 是单密钥机制，不是多用户 RBAC；生产远程部署仍需正式认证、授权和静态媒体访问控制。

## 8. 健康检查、迁移和部署

### 8.1 数据库健康检查

状态：`已修复`。

`GET /api/health/database` 现在额外检查：

- `video_task`、`fish` 关键字段是否缺失。
- `fish`、逐帧记录、报表是否存在孤儿记录。
- `video_task.total_fish` 与实际鱼行数是否不一致。
- 已记录的 `pipeline_result_path` 文件是否存在。
- 摄像头任务总数和任务状态分布。

### 8.2 Alembic 空链路

状态：`已修复为明确的单一策略`。

处理：

- 删除空的 `alembic.ini`、`migrations/env.py`、模板和 README。
- 删除 `alembic` Python 依赖及 lock 中对应条目。
- 升级脚本不再执行 `alembic upgrade/downgrade`，改为记录 `startup-migrations` 状态；实际 schema 变更由 FastAPI lifespan 的幂等 `startup_migrations` 执行。
- 明确当前策略只保证前向迁移，不自动 downgrade；生产回滚使用数据库备份恢复。

### 8.3 仍需真实环境验证

以下项目已经有代码处理，但本轮未连接真实服务：

- PostgreSQL 实例中的历史数据回填、绝对路径迁移和遗留 `source_type` 列删除。
- Redis/RQ 真实任务重试、取消、重启恢复。
- 树莓派、MediaMTX 到 OpenCV 首帧和断流重连。
- 真实 GPU 算法、报告生成、浏览器/Electron 视频播放。
- 数据库备份恢复和更新回滚。

## 10. 本次补充：仅摄像头输入与开发测试方式

### 10.1 文件上传功能

状态：`已移除`。

- 删除 `VideoWall.vue` 及其文件选择、上传、上传后同步算法调用。
- 删除后端 `POST /api/videos`、`POST /api/stream/upload` 文件上传入口。
- 删除前端 `uploadVideo` 和同步 `runAlgorithmVideo` 封装。
- Dashboard 空闲状态只等待摄像头输入；视觉标定只支持从当前摄像头抓拍，不提供本地图片上传按钮。

### 10.2 `source_type` 字段

状态：`已移除，待真实数据库启动验证`。

- `video_task` ORM 模型、session repo、平台统计、任务监控和视频 API 已移除 `source_type`。
- startup migration 会删除旧数据库列。
- `requireLog.md` 中此前关于“保留 upload/rtsp 来源”的历史记录是旧阶段记录；当前以本节和 `PROJECT_CONTEXT.md` 为准。

### 10.3 开发版本测试

开发测试不使用 `dist/`：

```bash
CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full ./start_dev.sh
```

访问：

- 前端：`http://127.0.0.1:5173`
- FastAPI 文档：`http://127.0.0.1:8000/docs`
- 就绪检查：`http://127.0.0.1:8000/api/ready`
- 数据库诊断：`http://127.0.0.1:8000/api/health/database`

功能测试流程：

1. 打开首页“录入”，进入 `/dashboard`。
2. 打开摄像头预览，确认 JPEG 首帧和树莓派/MediaMTX 状态。
3. 开始正式录制，确认创建 `video_id`。
4. 停止录制，确认状态依次为 queued/processing/done 或 failed。
5. 打开视频报告、数据报表和系统运维，检查同一 `video_id` 的鱼数量、报告和错误信息。
6. 使用 `/api/health/database` 检查 schema、孤儿数据、结果文件和任务摘要。

源码修改后的最低检查：

```bash
python -m compileall -q backend/app
bash -n start_dev.sh backend/app/services/update/update.sh
cd frontend && env PATH=/home/dsl/miniconda3/envs/full/bin:$PATH npm run build
```

只有开发版功能和页面验收通过后，才执行发布打包脚本生成 `dist/`。开发调试阶段不要手工修改 `dist/`，因为它可能包含删除前的旧代码；发布时重新构建整个交付包。

## 9. 本轮验证结果

已通过：

1. `python -m compileall -q app`：后端所有 Python 源码编译通过。
2. `bash -n start_dev.sh backend/app/services/update/update.sh scripts/install_source_autostart.sh desktop/launch_fish_quality.sh`：启动/升级脚本语法通过。
3. `env PATH=/home/dsl/miniconda3/envs/full/bin:$PATH npm run build`：前端 `vue-tsc -b` 和 Vite 构建通过；仍有大 chunk 警告，但不是构建失败。
4. SQLite 内存数据库一致性测试：验证 `rtsp` 来源不被结果导入覆盖，首次导入 2 条鱼、重导入 1 条鱼后数据库只保留 1 条，任务摘要同步为 1。
5. FastAPI 路由组装测试：仅存在 `/static/uploads`、`/static/results`、`/static/reports`、`/static/calibration`，不再挂载 `/static` 根目录。
6. 健康诊断测试：空 SQLite schema 建表后 `/api/health/database` 能返回字段诊断和孤儿数据诊断。
7. 源码搜索确认正式 backend 不再引用 Alembic、`FISH_ALGO_MODE`、整棵 `/static` 挂载或上传后的同步算法调用；legacy/dist 生成物只作为历史产物处理。

未执行/需要真实环境：

1. 当前环境没有项目实际 PostgreSQL 数据库连接信息，未修改真实历史数据，也未执行历史绝对路径迁移；旧 `source_type` 列会在现场启动迁移中直接删除，不保留历史值。
2. 未启动真实 Redis/RQ、GPU 算法、MediaMTX 或树莓派，因此没有做端到端录制和算法验收。
3. `uv lock --offline` 因本机缓存缺少 `aiofiles` 无法重新解析；`backend/uv.lock` 已按 `pyproject.toml` 手工移除 Alembic 条目，联网环境应重新运行 `uv lock` 做最终一致性校验。
4. 前端构建会更新 `frontend/docs/` 构建产物；生产发布前应按项目发布流程重新打包，而不是只依赖当前工作区产物。

## 11. 本次“仅摄像头输入”补充验证

状态：`已完成源码收敛，真实 PostgreSQL 迁移待现场验证`。

- `VideoWall.vue`、文件选择器、`uploadVideo`、同步 `runAlgorithmVideo` 和后端文件上传路由已移除。
- `VideoTask.source_type` 已从 ORM、查询接口、平台统计、运维监控和前端类型移除。
- 旧数据库通过启动时 `run_video_source_type_removal_migration()` 删除遗留列；当前源码 SQLite schema 检查确认模型不再有该列。
- `/api/algorithm/videos` 文件输入入口已移除；摄像头停止后的 RQ pipeline 是唯一算法任务入口。
- 前端平台页面不再展示上传视频来源；摄像头任务作为唯一任务来源。
- 前端构建、Python 编译、Shell 语法、摄像头-only 后端路由/schema 检查均通过。
- FastAPI 路由检查确认 `/api/videos` 只保留 GET 查询，`/api/stream/upload` 和 `/api/algorithm/videos` 没有注册。

当前只保留 `image_source_type` 这一图片展示字段，表示 corrected/original/mask 图片来源；它与已删除的 `video_task.source_type` 无关。

现场注意：升级已有 PostgreSQL 时，首次启动会执行 `ALTER TABLE video_task DROP COLUMN IF EXISTS source_type`。执行前应备份数据库；该字段不再用于业务，历史值不会保留。

当前剩余高风险：

- `FISH_API_KEY` 是可选单密钥，不是用户/角色权限系统；远程生产环境必须设置并进一步增加正式认证。
- `/static/uploads`、`/static/results`、`/static/reports`、`/static/calibration` 仍是公开媒体目录；如果媒体本身需要权限，应改为受控下载接口。
- `stats_5min` 当前是算法完成后的重建统计，不是录制过程中的实时统计。
- 旧 `dist/` 构建产物仍可能包含已经删除的旧代码；重新构建发布包后才会与当前源码一致。

## 12. 本次持续开发（2026-10-16）

### 12.1 摄像头-only 残留和统一 video_id

状态：`已修复源码，待真实任务验收`。

处理：

- 删除操作审计中已不存在的 `/api/upload`、同步文件算法 POST 入口记录逻辑；旧兼容路由不再提示调用已删除的文件算法入口。
- 旧 `/api/calibration/perspective` 改为隐藏的 410 兼容路由，不再声明或接受 `UploadFile`；正式标定继续只从摄像头抓帧。
- 报表上下文、日报快照和格式化工具删除 `upload_video_total`、`rtsp_video_total` 及来源中文映射，只保留摄像头任务统计。
- `/api/fishes`、摄像头视频鱼列表、鱼详情、筛选、视频页聚合和数据库诊断统一读取 `body_length_cm`、`body_length_no_tail_cm`、`body_height_cm`、`pred_weight_g`；旧 ORM 字段仍保留在兼容窗口，并由旧仓储写入时双写。
- RQ 整视频 handler 检测 CLI/JSON 返回的 `video_id` 与摄像头任务不一致时立即失败，不再创建新的孤儿 `video_task`；结果导入服务增加 `expected_video_id` 校验。

验证：`python -m compileall -q backend/app backend/tools` 通过；路由源码扫描确认没有视频上传 POST、同步文件算法 POST 或可见标定图片上传参数。

未完成：尚未连接树莓派、MediaMTX、Redis/RQ 和 GPU 做真实停止、重试、播放和报告链路验收。

### 12.2 历史数据库迁移工具和路径规范

状态：`已完成工具源码，待真实 PostgreSQL 执行`。

处理：

- 新增 `backend/tools/migrate_legacy_database.py`，默认 dry-run，`--apply` 才执行旧 `video_task.source_type` 删除、数据库路径规范化、结果 JSON 路径规范化和新测量字段回填。
- `path_migrations.py` 扫描任务、鱼、逐帧、检测和报表路径；只有文件真实存在且在当前 `data/` 内才转换成 `data/<relative POSIX path>`，缺失、越界和非法路径原值保留并进入 JSON 清单。
- 迁移报告输出缺失文件、越界路径、孤儿鱼/逐帧/统计/报表记录、任务摘要不一致和新旧测量字段差异。
- 启动迁移不再把越界路径静默置空；新增幂等测量回填，只在新字段为空且旧字段有值时写入，保留旧字段供兼容周期使用。
- `to_data_relative()` 统一返回带 `data/` 前缀的 POSIX 路径，已有 `to_abs_data_path()` / `to_static_url()` 兼容读取。

验证：Python 编译通过；未在本地真实 PostgreSQL 上执行 `--apply`，因为当前尚未确认可安全使用的历史测试库和备份。

### 12.3 JSON、数据库、PDF 对账

状态：`已完成只读对账流程源码，待真实数据验收`。

处理：

- 新增 `backend/app/services/result_reconciliation_service.py` 和 `backend/tools/reconcile_results.py`，按 `video_id` 对比 pipeline JSON 鱼身份/状态/新测量字段、数据库鱼行/任务摘要、`detection_report.summary_json` 和 PDF/DOCX 文件存在性。
- 新增 `GET /api/algorithm/results/reconcile`；对账报告区分待导入、缺 JSON、缺数据库任务、JSON/数据库计数差异、鱼指标差异、报表快照差异和缺失报表文件。
- 普通报表汇总、图表、鱼明细、日报和视频列表不再静默读取 JSON；数据库已导入且鱼数为 0 时仍以数据库为真值。JSON 保留给原始算法结果、发现未导入结果和排障。
- 视频 PDF 生成在数据库存在任务但鱼列表为空时也采用数据库 payload，避免旧 JSON 把零鱼任务重新显示成有鱼。

验证：Python 编译通过；尚未用真实 PostgreSQL 数据、历史结果目录和可用 LibreOffice 对账并抽查 PDF 内容。

### 12.4 `stats_5min`

状态：`保持完成后重建，禁止未定义的实时增量`。

处理：

- 保留算法完成后按数据库鱼记录重建五分钟桶的逻辑。
- `stats_repo.upsert_stats_increment()` 现在显式拒绝没有事件幂等键的增量调用，避免未来误接入逐帧累加。
- 未实现实时统计；后续若需要，必须先定义 `(video_id, fish_uid/track_id)` 事件键、五分钟时区、重试幂等、跨桶和断流规则。

验证：静态搜索确认正式代码没有调用增量函数；真实断流、重试和跨桶验收待现场环境。

### 12.5 本机真实 PostgreSQL/Redis 结果

状态：`部分完成，历史数据问题已输出清单`。

过程和验证：

- 确认本机 PostgreSQL 14、Redis、ffprobe、LibreOffice 可用；`fishdb` 有 131 个 `video_task`、2801 条 `fish`、178 条 `detection_report`，旧 `video_task.source_type` 列存在。
- 执行迁移前备份：`/tmp/opencode/test3_fishdb_before_20261016.dump` 和 `/tmp/opencode/test3_data_results_before_20261016.tar.gz`。
- 先执行 dry-run，再执行 `--apply`；应用报告为 `/tmp/opencode/test3_legacy_migration_apply.json`。旧 `source_type` 已删除，31 个真实存在的数据库路径已转换，3242 个缺失文件和 116 个越界路径未被猜测修改，10 个任务摘要不一致和无孤儿记录已输出。
- PostgreSQL 复查确认 `video_task.source_type` 不存在；Redis `PING` 返回 `PONG`。
- 执行全量对账报告 `/tmp/opencode/test3_reconciliation.json`：9 个历史结果完全通过，其他任务主要因历史 `pipeline_result.json` 或源文件已清理而待处理，`20260916_0009` 仍有实际 JSON/数据库鱼记录差异，不能标记全量通过。
- 使用真实数据库新字段生成并保存视频 PDF；`20260916_0001` 的 JSON、数据库、`detection_report` 快照和 PDF 文件对账通过，严格对账命令返回 `ok=True`。

未完成：历史缺失文件、摘要差异和 `20260916_0009` 的鱼记录差异需要业务确认后重导入或标记，不自动覆盖数据库真值。

### 12.6 开发启动、MediaMTX 和前端构建

状态：`开发版基础验收通过，真实摄像头/GPU链路待现场`。

过程和验证：

- 按要求执行 `CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full ./start_dev.sh`；PostgreSQL/Redis 预检通过，但发现已有发布版 MediaMTX 使用 `:8000` UDP RTP 端口，与 FastAPI 默认端口冲突。
- 将源码 `mediamtx/mediamtx.yml` 的 RTSP UDP/RTP、RTCP、多播和 SRTP 端口迁移到 `10000` 起的独立端口，并更新 `mediamtx/readme.md`；未修改 `dist/`。开发脚本增加对已监听 MediaMTX 和 Upgrade Manager 的复用，避免开发启动误报端口占用。
- 在已有发布版占用 `8000` 的条件下，以 `BACKEND_PORT=8001 FRONTEND_PORT=5173` 启动源码开发版；源码 FastAPI、RQ Worker 和 Vite 均启动成功。可访问前端 `http://127.0.0.1:5173`，源码 API 文档为 `http://127.0.0.1:8001/docs`。
- `GET /api/ready` 返回 `{"status":"ready"}`；`GET /api/health/database` 返回 PostgreSQL connected、schema 完整、无孤儿记录，并如实报告 10 个摘要不一致和 115 个结果文件缺失。
- OpenAPI 检查确认 `/api/videos` 只有 GET，`/api/stream/upload`、精确 `/api/algorithm/videos` 和可见 `/api/calibration/perspective` 均不存在；软件更新 `POST /api/system/update/upload` 仍存在；新增对账 GET 路由已注册；schema 无 `source_type`。
- 实际 HTTP 复查：`POST /api/videos` 返回 405、`POST /api/stream/upload` 和 `POST /api/algorithm/videos` 返回 404、旧标定接口返回 410，软件更新上传路由未被误删。
- 发现历史越界路径仍可能存在于数据库后，补充 API 边界：视频列表、摄像头视频和算法数据库 fallback 现在只输出 `data/` 相对路径，项目外绝对路径返回空；复查 `20261014_0001` 已不再暴露旧 `dist/` 绝对路径。
- 原始算法结果接口同步清理绝对文件路径字段，只保留 `data/` 相对路径和 `/static/...` URL；复查 `20260916_0001` 没有绝对路径值。

### 12.7 稳定性专项计划归档

已将当前稳定性专项的剩余任务、实施顺序、摄像头故障类型、同 IP 换机边界、Linux 卡死边界、`8000/8001` 端口说明和 `dist/` 发布前置条件补充到 `PROJECT_CONTEXT.md` 第 14 章。历史记录不作为本专项测试重点，后续现场测试使用新录制的 `video_id`。
- Node 20.20.2 下执行 `npm run build` 通过（`vue-tsc -b` + Vite）；只有既有大 chunk 警告，构建更新了 `frontend/docs/`。

未完成：当前未连接真实树莓派推流，无法完成首帧、断流重连、定时录制、真实 GPU pipeline、RQ 重试/取消、浏览器/Electron 播放和备份恢复；因此本轮不重新生成 `dist/`。

## 13. 本次持续开发（2026-10-20）

### 13.1 断流恢复窗口与前端状态

状态：`已修复源码，待真实摄像头现场验收`。

处理：

- `FISH_CAMERA_RECONNECT_GRACE_SECONDS` 正式作为恢复窗口配置，默认从 120 秒调整为 180 秒；`start_dev.sh` 和源码自动启动环境同步默认值。
- 后端新增 `reconnecting` 运行态。断流恢复期间保留原 `video_id`、原视频文件、原 `VideoWriter` 和原 `video_task`，恢复后继续写入，只有超过恢复窗口才进入失败处理。
- 启动等待首帧也纳入同一恢复窗口，并按间隔重新请求树莓派 `/camera/start`，不在短暂断流时立即终止任务。
- `/api/camera_yzt/status` 增加恢复窗口、已等待秒数、剩余秒数和 Pi 地址解析诊断字段。
- Dashboard 在 `reconnecting` 状态继续显示当前录制画面和同一任务，不写结束时间、不清空 `video_id`；用户仍可点击按钮主动停止。

验证：`python -m compileall -q backend/app` 通过；真实拔电、断网、换机和恢复仍需现场使用新录制 `video_id` 验收。

### 13.2 同 IP 换机与网络状态诊断

状态：`已修复源码，待真实换机现场验收`。

处理：

- Pi 解析诊断增加当前解析 IP、缓存状态和 Linux `ip neigh` ARP/邻居表状态。
- `/api/camera_yzt/status` 增加 `pi_resolution`、`pi_arp`、`pi_address_changed`、`pi_mac_changed`；同一个 IP 但 MAC 变化时写入 `pi_arp_mac_changed` 事件，便于识别 DHCP/ARP 缓存指向旧设备。
- 保留 HTTP 重试、mDNS/getent 解析失效和重新请求 `/camera/start` 的既有恢复链路；后端不擅自 flush ARP，网络缓存清理仍由部署/运维侧决定。

验证：本机 `10.42.0.2` 当前邻居状态为 `REACHABLE`，MAC 可读，`pi_mac_changed=false`；真实换机需用新视频任务观察 IP 不变、MAC 变化和首帧恢复。

### 13.3 重启诊断、进程 watchdog 与故障注入

状态：`已修复源码，待真实重启/故障现场验收`。

处理：

- 新增 `backend/tools/diagnose_stability.py`，检查 `/api/ready`、数据库健康、摄像头状态契约、RQ worker、MediaMTX RTMP/RTSP/HLS 端口和 Redis。
- 新增 `scripts/accept_restart_recovery.sh` 和 `scripts/stability_fault_injection.sh`，按新录制 `video_id` 覆盖后端/RQ/MediaMTX 重启、断流恢复、定时录制、换机和断网现场验收步骤；默认不自动拔电、断网或删除数据。
- 新增 `scripts/process_watchdog.sh` 与 `fish-quality-watchdog.service.in`，监控后端 readiness、MediaMTX 和 RQ Worker 用户态服务并请求 systemd user 重启。
- watchdog 明确只处理用户态进程退出/失去就绪；普通 systemd/Python 无法恢复 Linux 内核卡死，硬件 watchdog 只能作为部署级方案，需现场单独配置。
- 新增 `backend/tools/test_reconnect_state_machine.py`，验证断流窗口进入 `reconnecting`、同一 `video_id` 保留、恢复后回到 `running`。

验证：backend 编译通过；全部指定 Shell 语法通过；Node 20 前端构建通过；重启状态机烟测在 Conda `full` 环境通过；`8000` 上 `/api/ready`、`/api/health/database` 和稳定性诊断通过。

### 13.5 Dashboard 与管理员布局调整

状态：`已修复源码，待浏览器手工确认`。

处理：

- Dashboard 移除 `useFixedScreen(1920, 1080)` 固定缩放，改为 `100%` 宽高和 `100dvh` 最小高度，随浏览器窗口拉伸占满。
- 管理员顶部 header 增加完整标题“鱼品质智能检测系统”；左侧栏保留鱼图标，不再挤压长标题。
- “退出管理员”改为清除管理员 session 后直接 `router.replace('/')`，返回首页，不再刷新后停留在管理员路由。

验证：Node 20 下 `npm run build`（`vue-tsc -b` + Vite）通过；仅保留既有大 chunk 警告，未修改 `dist/`。

### 13.7 报表日期范围占位文案

状态：`已修复源码`。

- `admin/data-report` 和 `admin/report-overview` 的范围日期选择器改为分别显示“开始日期”和“结束日期”，不再把两个输入框拆成“选”“择”两个字。
- 查询逻辑未改变，仅调整 Arco RangePicker 的 `placeholder` 数组参数。

验证：Node 20 下前端 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.8 视频日期与编号联动

状态：`已修复源码，待浏览器手工确认`。

- Dashboard 右侧“视频信息”由文本输入改为按当前日期加载的视频编号下拉框，列表严格按录制日期过滤，不再补入其它日期的 video_id。
- 单视频报告入口读取路由中的 `date`，进入详情时显示被选视频的对应日期，而不是默认今天。
- 单视频报告切换日期时清空旧 video_id 并重新加载任务；路由视频不再跨日期强行追加到当前日期列表。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.11 EmbeddedVideoReport Stylelint 修复

状态：`已修复源码`。

- 修复 `EmbeddedVideoReport.vue` 的 `at-rule-empty-line-before`：`@media` 前补空行。
- 修复 `media-feature-range-notation`：将 `@media (max-width: 760px)` 改为项目统一的 `@media (width <= 760px)`。
- Node 20 下 `npm run build` 已重新通过。

### 13.12 鱼体状态饼图图例位置

状态：`已修复源码`。

- “完整鱼/不完整鱼”图例改为右侧纵向排列。
- 饼图中心左移，保留图例空间，避免小宽度报告卡片中图例压住图形。
- Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.13 可视化统计图内容居中

状态：`已修复源码`。

- “可视化统计分析”中的图表卡片和 ECharts 容器增加水平居中布局，图表最大宽度限制为 `520px`，小屏时自动单列。
- 图表标题保持全宽，避免居中后标题错位；ECharts 仍通过 `ResizeObserver` 响应容器尺寸变化。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.14 日报/范围报告图表统一

状态：`已修复源码`。

- 日报和日期范围报告的图表卡片统一为单视频报告的普通 `chart-card + chart-title + chart-canvas` 结构，不再混用 Arco Card header/body 导致图表宽度不同。
- 统一使用两列等宽布局、小屏单列、240/280px 图表高度、最大 520px 宽度和同一套 X 轴标签策略。
- `useReportCharts.ts` 的柱状图统一过滤空桶、按有效桶计算百分比、`containLabel`、标签旋转/隐藏和右侧状态图例；三类报告共用 ECharts 容器 ResizeObserver。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.15 无尾体长图表补齐

状态：`已修复源码`。

- 单视频报告补回“无尾体长分布 (cm)”图表卡片，不再把该位置注释或误显示为体高。
- 单视频报告无尾体长图表读取 `body_length_no_tail_cm`，兼容 `body_length_without_tail`；体高图表继续读取 `body_height_cm`。
- 日报和范围报告统一显示“体长分布”和“无尾体长分布”；共享图表配置的无尾体长 X 轴改为“无尾体长区间（cm）”。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.16 范围报告跳转与无尾体长数据源修复

状态：`已修复源码，待浏览器手工确认`。

- 日期范围报告“查看报表”现在跳转到 `/admin/data-report?type=daily-videos&video_id=<id>&date=<视频日期>`，并能自动选中对应日期和视频；“跳转大屏”统一跳转 `/dashboard?mode=result&video_id=<id>`。
- 当日报告的相同入口也统一带上日期和 `/dashboard` 路由。
- 修复后端 `report_stats_service.get_fish_quality_charts()`：`body_length_without_tail_distribution` 原错误使用 `height_values/body_height_cm`，现改为 `no_tail_values/body_length_no_tail_cm`。
- 7 张图的布局采用第一张状态/趋势图横跨两列，其余 6 张两列排列，避免最后一张单独占行。

验证：针对 `20261013_0002` 查询 `/api/reports/fish-quality/charts?video_id=...`，无尾体长分布与体高分布已返回不同区间；Node 20 前端构建通过，仅保留既有大 chunk 警告。

### 13.17 范围汇总肥满度异常值修复

状态：`已修复源码，历史异常值不改库`。

- 发现 2026-09-01 至 2026-11-01 汇总的 `avg_condition_factor=26582882683216.54` 来源于历史鱼记录的异常体长值（例如 `body_length_no_tail_cm=0.0002`），不是正常百分比。
- `report_stats_service` 现在只纳入 `0 < condition_factor <= 100` 的有效百分比值，过滤异常超大值；鱼明细报告同样不再展示无效肥满度。
- 日报和范围报告的“平均肥满度”显示纯指数数值（例如 `1.26`），不再附加 `%` 单位；算法原始字段和历史数据库不直接改写，异常记录保留供排查。

验证：重启 `8000` 后查询同一范围，`avg_condition_factor` 从异常值变为 `1.26`，前端显示为 `1.26`；Node 20 下前端构建通过。

### 13.10 报告目录、图表和媒体资源显示

状态：`已修复源码，历史缺失媒体需数据现场确认`。

- 报告目录标题和条目字号统一调整为 `16px`。
- EmbeddedVideoReport 图表改为等宽两列、容器 `min-width: 0`、ECharts `ResizeObserver` 自适应；柱状图增加 `containLabel`、标签隐藏/旋转和底部空间，减少 X 轴溢出；鱼体状态饼图图例和标签限制在卡片内。
- 视频和图片继续使用 `normalizeReportUrl()` 转为同源 `/static/...` 相对 URL；视频 fallback 先通过 HEAD 检查文件存在，不再对缺失历史文件渲染 404 视频。
- 本机检查 `20261014_0001` 的 Grounded-SAM 和 playable 视频均返回 HTTP 404，`data/` 中仅有报告文件；该记录的图片/视频缺失属于历史文件已清理，无法由前端路径转换恢复，新增视频需确认源视频和结果媒体文件存在。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.9 现场用例自动化预检结果

状态：`自动化可执行部分已完成，真实设备用例待现场`。

- 已执行并通过：Python 编译、指定 Shell 语法、重连状态机烟测、`/api/ready`、`/api/health/database`、摄像头状态诊断、稳定性预检和 Node 20 前端构建。
- 已执行并通过：摄像头空闲状态下重启 FastAPI、RQ Worker、MediaMTX；后端 ready、worker heartbeat 和 MediaMTX `1935/8554/8888` 端口均恢复。
- 未执行：真实新录制、Pi 停止/拔电、断网、同 IP 换机、DHCP 变化、180 秒超时封存、录制中进程重启、watchdog 杀进程、GPU、报告和浏览器/Electron 播放。
- `PROJECT_CONTEXT.md` 第 14.4 节已为 ST-01 至 ST-15 标注自动化/手动状态，并补充对应终端命令。

### 13.6 管理员标题与半成品视频处理

状态：`已修复源码，待真实断流现场验收`。

处理：

- 管理员标题“鱼品质智能检测系统”移动到整个屏幕顶部 header 左上角，强制单行显示；删除左侧空的 `sidebar-header` 标题容器，侧栏从 header 下方开始显示。
- 恢复“退出管理员”行为：清除管理员 session 后直接 `router.replace('/')` 返回首页，不再刷新后停留在管理员页面。
- 断流恢复窗口超时不再直接遗弃半成品视频。后端会封存同一 `video_id` 的写入文件；有有效帧时生成可播放副本并沿用原 `video_id` 入队算法，没有有效文件时才标记 `failed`。
- 超时原因保留在任务错误字段/事件日志中，便于区分“录制中断但已继续处理”和“没有可处理视频文件”。

验证：`python -m compileall -q backend/app` 和指定 Shell 语法检查通过；Node 20 前端构建通过，仅有既有大 chunk 警告。真实超时封存、转码和 RQ 入队仍需现场新 `video_id` 验收。

### 13.4 前端交互调整

状态：`已修复源码，待浏览器手工确认`。

处理：

- `frontend/src/layout/AdminLayout.vue` 将数据管理侧栏标题、菜单文字和图标字号分别调大一级，保持数据报表、报表总览等入口一致。
- 移除 Dashboard 开始录制前的独立“完整性设置”弹窗；完整性检测改为放在“光圈设置”右侧的单选项，默认“是，检测完整性”。
- 完整性选项保存到浏览器 `localStorage`，键为 `fish_quality_integrity_detection_enabled`；用户选择“否”后，后续录制任务统一透传 `force_all_complete=true`，浏览器重启后仍保持选择。
- `frontend/src/views/admin/system-ops/AlgorithmTaskMonitor.vue` 的日期选择器增加 `@change` 自动加载任务，不再要求额外点击“查询”。

验证：Node 20 下 `npm run build`（`vue-tsc -b` + Vite）通过；仅保留既有大 chunk 警告。需要在 `http://127.0.0.1:5173/dashboard` 和 `/admin/system-ops` 手工确认视觉布局与持久化行为。

### 13.19 报表总览表格与肥满度口径

状态：`已修复源码，待浏览器视觉确认`。

- 报表总览的视频报表、当日报表、自定义报表表格改为占满内容区，使用固定布局和列内容省略；标题/编号保持左对齐，操作列保留稳定宽度，避免少列时整张表缩在左侧或出现大块空白。
- 肥满度采用养殖鱼类常用 Fulton 指数：`K = 100 × W / L³`（W 为克，L 为厘米）。K 是经过 100 倍缩放的体型指数，不按百分比单位显示，例如 `1.26`。
- 范围汇总过滤 `condition_factor <= 0`、非有限值和 `>100` 的异常记录，避免极小体长历史脏数据把平均值放大；不直接改写历史数据库。

验证：范围 `2026-09-01` 至 `2026-11-01` 的平均肥满度由异常值修复为 `1.26`，前端显示 `1.26`；Node 20 前端构建通过。

### 13.20 报表总览表格与肥满度展示口径调整

状态：`已修复源码`。

- 报表总览三类表格占满内容区，采用固定表格布局；标题/编号左对齐、操作列稳定、长内容省略，避免少列表格缩在左侧。
- 肥满度前端不再显示 `%` 单位，显示 Fulton 指数数值（例如 `1.26`）；后端继续过滤异常值并保留历史脏数据不改库。
- 修复 `EmbeddedVideoReport.vue` 的 Stylelint 空行错误。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.21 DailyReportToday Stylelint 修复

状态：`已修复源码`。

- 移除 `DailyReportToday.vue` 媒体查询内部多余空行，修复 `rule-empty-line-before`。
- Node 20 下 `npm run build` 已重新通过。

### 13.22 DateRangeReport Stylelint 修复

状态：`已修复源码`。

- 移除 `DateRangeReport.vue` 媒体查询内部多余空行，修复 `rule-empty-line-before`。
- Node 20 下 `npm run build` 已重新通过。

### 13.23 首页与管理员 Header 对齐

状态：`已修复源码`。

- 首页 `LayoutHeader` 右侧导航按钮中心线固定到 `30px`，与管理员 60px header 的按钮顶部距离统一。
- Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.24 报表总览表格等宽布局

状态：`已修复源码`。

- 移除 ReportOverview 各列固定宽度，配合 `table-layout: fixed` 让视频报表、当日报表、自定义报表按当前列数等宽铺满内容区。
- 保留文本省略和稳定的表格高度，避免少列时内容全部挤在左侧。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

### 13.25 序号/选择列固定宽度

状态：`已修复源码`。

- ReportOverview 表格继续保持其余业务列等宽铺满。
- “序号”和“选择”列固定为 `72px`，避免复选框和序号被等宽分配得过大。

验证：Node 20 下 `npm run build` 通过，仅保留既有大 chunk 警告。

## 14. 本次摄像头设置拍照显示修复（2026-10-23）

状态：`已修复并重新打包，真实摄像头拍照链路已验证`。

处理：

- 对焦照片统一保存到 `data/calibration/focus/`，对应公开静态地址为
  `/static/calibration/focus/original.jpg` 和 `/static/calibration/focus/focused.jpg`；该目录已由 FastAPI 和 Electron 同源代理共同支持。
- 修复 `camera_yzt/focus/confirm` 成功分支使用局部未定义 `time` 导致确认对焦返回 409 的问题，将 `asyncio`、`time` 提升为模块级导入。
- 增加 `backend/tools/test_focus_photo.py`，覆盖原始拍照、确认对焦、旧版树莓派兼容重启分支，以及静态 JPEG 返回和解码。
- 按既有打包流程停服并替换原目录 `dist/fish-quality-linux-x86_64/`，保留原运行数据和配置，不使用新的交付目录。

验证：

- `test_focus_photo.py` 通过；原始、成功确认和兼容确认三条路径的图片均返回 HTTP 200、`image/jpeg`。
- 真实树莓派/MediaMTX 预览已验证：获取 960x540 首帧，`POST /api/camera_yzt/focus/photo` 返回静态 URL，图片成功解码；预览停止后 `video_id=null`、`frames_written=0`，未创建任务或算法作业。
- 前端 AppImage 已重新生成：`desktop/dist/fish-quality-desktop-1.0.0-x86_64.AppImage`。
- PyInstaller 后端已重新生成并通过 `doctor --deep`，算法依赖检查全部通过。
- 正式交付目录：`dist/fish-quality-linux-x86_64/`。

复用打包命令（在项目根目录执行）：

```bash
./dist/fish-quality-linux-x86_64/stop.sh || true

CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full \
INCLUDE_ALGORITHM_ASSETS=1 \
./backend/packaging/build_backend.sh

CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full \
./desktop/build_linux.sh appimage

./scripts/build_linux_release.sh
```

替换前，原交付目录中的 `backend/data/`、`backend/config/` 备份到
`/tmp/opencode/test3-before-release-replace/`；组装完成后恢复，以免同步覆盖运行数据。

本轮以上打包命令均执行成功；原交付目录的后端和 AppImage 与新构建输出逐字节一致，
数据与配置恢复后经校验无差异。已清理此前多生成的 `camera-fix` 目录；当前保持停服状态。

## 15. 摄像头默认分辨率调整（2026-10-23）

- 摄像头设置的高分辨率预设和默认选项改为 `1980x1020`，保留 `960x540` 选项。
- 页面初始值、请求回退值、预览比例、后端设置和预览请求默认值同步调整；当前源码及打包版保存的摄像头分辨率也更新为 `1980x1020`。
- 前端构建、拍照回归测试、Python 编译、默认配置读取检查均通过。
- 按第 14 节命令重新打包，替换原 `dist/fish-quality-linux-x86_64/`，恢复原数据和配置；后端和 AppImage 与新构建输出逐字节一致，`doctor --deep` 通过。
- 已通过桌面快捷方式启动新版，`GET /api/camera_yzt/settings` 实际返回 `width=1980`、`height=1020`、`fps=25`；`GET /api/ready` 返回 ready。本次未验证该分辨率下的真实摄像头取帧。

### 15.1 真实设备启动失败排查

状态：`1980x1020 推流失败已复现，等待树莓派 SSH 访问后继续修复`。

- 用户报告任务 `20261023_0004` 启动期间 Pi `/camera/start` 返回 HTTP 500，进程立即退出，提示查看 `/home/fish/Desktop/pi/log.txt`。
- 同一推流地址、25 FPS 和对焦参数下对比：`1980x1020` 返回 500；`1920x1020` 和 `1920x1080` 返回 200。立即执行 ffprobe 时未读到发布流，不将该项作为首帧通过证据。
- 随后通过后端预览接口独立验证 `1920x1080`，成功获取并解码 `(1080, 1920, 3)` 的 JPEG 帧；测试完成已停止预览，写入帧数为 0，未入队算法。
- 用户明确要求保留 `1980x1020`，因此没有恢复默认分辨率或静默缩放；需要继续检查 Pi 端日志和编码器启动参数。
- 当前 Pi OpenAPI 没有日志读取或程序管理接口；`ssh -o BatchMode=yes fish@10.42.0.2` 返回 `Permission denied (publickey,password)`，用户选择配置 SSH 后重试，最近一次重试仍未通过认证。

### 15.2 Pi 编码器修复与真实取帧验收

状态：`已修复并部署到当前树莓派，1980x1020 实际预览和拍照通过`。

- SSH 配置后已能登录 `fish@10.42.0.2`。设备为 Raspberry Pi 4 Model B，摄像头 imx708_wide；日志明确显示硬件编码器 `h264_v4l2m2m` 拒绝 1980 宽度：`asked for 1980x1020, got: 1920x1020`，随后 `unable to open video codec: -22`。
- 修改 Pi `/home/fish/Desktop/pi/main_pi.py`：宽度超过 1920 或高度超过 1080 时使用 `libx264` 软件编码（ultrafast、zerolatency），保留请求的真实尺寸；其余尺寸仍使用原硬件编码器。
- 编码输出使用带时间戳的 MPEG-TS 管道，FFmpeg 通过 `-c:v copy` 转发 RTMP，避免第二次编码和裸 H.264 无 PTS 导致的封装失败。每秒输出关键帧。
- Pi 原文件备份：`/home/fish/Desktop/pi/main_pi.py.before-1980-fix-20261023`。项目内保存可复用补丁 `packaging/pi-camera-1980.patch`，在相同版本 Pi 源码目录可用 `patch -p1 --dry-run < pi-camera-1980.patch` 检查后应用。
- Python 编译通过，已重启 Pi `fishpi.service` 并确认 active。本次仅修改 Pi 程序，桌面与后端打包文件无需再次替换。
- 实际从运行中的打包后端启动预览，成功解码 JPEG `(1020, 1980, 3)`；拍照接口及静态图片返回 HTTP 200，照片同为 1980x1020。
- 第二次独立预览持续观察约 35 秒，running 状态帧计数为 `207 -> 332 -> 458 -> 583`（约每 5 秒采样），稳定阶段约 25 FPS；采样温度 67.2 摄氏度，软件编码进程 CPU 约 133%（跨核累计）。该短时验收不代表长时间温控验收。
- 测试结束已停止预览，`frames_written=0`、`queued=false`，未创建正式录制任务；保留默认 1980x1020。
