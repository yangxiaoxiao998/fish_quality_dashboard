# MinIO 阶段一盘点结果与数据契约

## 1. 适用范围

本文件是 `minioPlan.md` 阶段一“盘点、基线和数据契约”的正式产物。阶段一只完成盘点、基线、接口边界和验证用例设计，不切换 MinIO，不修改现有业务数据，不改变 Local Storage 行为。

测试产生的临时响应、浏览器调试信息和日志保存在项目外的 `/tmp/`，不纳入本项目。

## 2. 当前运行基线

采集时间：2026-08-26，当前开发环境。

运行入口：

```text
Backend:  http://127.0.0.1:8000
Frontend: http://127.0.0.1:5173
Database: PostgreSQL fishdb（由 DATABASE_URL 注入）
Queue:    Redis / RQ fish
```

接口基线：

| 接口 | 结果 |
|---|---:|
| `GET /api/health` | HTTP 200 |
| `GET /api/videos?page=1&page_size=1` | `total=75` |
| `GET /api/reports/history?page=1&page_size=1` | `total=93` |
| `GET /api/camera_yzt/schedules?page=1&page_size=1` | `total=48` |
| `GET /` | HTTP 200 |
| `GET /admin/report-overview` | HTTP 200 |

当前数据库业务基线至少包括：

- `video_task`：75 条视频任务；
- `detection_report`：93 条报告记录；
- `camera_record_schedule`：48 条计划记录；
- `fish`：具体数量必须通过数据库只读查询采集，不从页面推断；
- `video_task.video_id` 使用 `YYYYMMDD_编号` 格式；
- 养鱼仓字段为 `video_task.aquaculture_bin`；
- 当前默认数据库连接由环境变量提供，源码不保存密钥。

## 3. 目录与职责盘点

### 3.1 后端入口和数据访问

| 目录/文件 | 当前职责 | 阶段二以后对象存储边界 |
|---|---|---|
| `backend/app/routers/stream_router.py` | 上传视频、写入 `VideoTask.source_path`、入队算法 | 上传完成后写入 raw 对象，业务 API 保持兼容 |
| `backend/app/routers/fish_router.py` | 视频列表、播放、视频时长、鱼体列表和图片 URL | 播放/图片 URL 改为对象服务生成，缺对象必须明确报错 |
| `backend/app/routers/report_router.py` | 报告生成、历史、预览、下载、合并报告 | 报告文件由报告对象 Key 定位 |
| `backend/app/routers/camera_yzt_router.py` | 摄像头、录制、计划和历史 | 录制临时文件上传 raw 对象 |
| `backend/app/routers/calibration_router.py` | 标定照片和标定结果 | 照片/结果进入 results 或 config 对象 |
| `backend/app/repositories/models.py` | `VideoTask`、`Fish`、`DetectionReport` 等 ORM | 保留业务表，增加对象元数据关系或 Key 字段 |
| `backend/app/services/path_service.py` | 本地相对路径和 `/static` URL 转换 | 阶段二建立 Storage Adapter，不让 MinIO Key 进入本地路径转换器 |
| `backend/app/workers/queue.py` | 算法队列、源视频存在性检查 | 队列前校验对象 ready，下载到 job 临时目录 |
| `backend/app/workers/handlers/algorithm_video_pipeline.py` | 算法执行、结果导入和日报触发 | 结果对象上传成功后再导入/标记完成 |
| `backend/app/services/camera_yzt/camera_yzt_service.py` | OpenCV 拉流、VideoWriter、本地录制、转码 | 本地只作为录制和处理缓存 |
| `backend/app/services/video_playback_service.py` | ffprobe、ffmpeg、H.264/WebM 播放副本 | 播放副本对象化，仍需本地转码缓存 |
| `backend/app/services/algorithm_result_service.py` | 读取和导入 `pipeline_result.json`、CSV | JSON/CSV 上传 results 后再作为结果源 |
| `backend/app/services/report_stats_service.py` | 汇总、统计、视频/鱼明细和时长兜底 | 统计输入必须绑定快照和对象版本 |
| `backend/app/services/video_report_pdf_service.py` | 单视频/合并 PDF、图表和占位符替换 | PDF 和图表对象化，输入改为统一快照 |
| `backend/app/services/report_docx_service.py` | 日报 DOCX、图片/图表插入 | DOCX、图表和输入对象建立引用关系 |

### 3.2 当前本地数据目录

`backend/app/paths.py` 将项目根目录定义为 `backend/app/paths.py` 的上级项目目录，将本地数据根目录定义为：

```text
<project-root>/data/
```

当前职责：

```text
data/uploads/videos/       上传和摄像头视频
data/uploads/calibration/  标定照片
data/results/<video_id>/   发布结果、报告和图表
data/jobs/<video_id>/      算法中间目录和任务现场
data/reports/              日报、合并报告等
data/config/               摄像头和焦距本地 JSON
data/calibration/          当前标定 JSON
data/logs/                 日志
data/tmp/                  临时图片和临时报告图表
```

阶段二不能直接把整个 `data/` 目录上传。必须按对象类型拆分，`logs/`、`tmp/` 和失败现场需要独立保留策略。

## 4. 路径、读写和媒体盘点

### 4.1 已识别的本地路径字段

后端业务表和响应中已识别：

```text
VideoTask.source_path
VideoTask.result_dir
VideoTask.pipeline_result_path
VideoTask.error_conclusion_path
VideoTask.grounded_video_path
VideoTask.measurement_csv_path
Fish.original_image_path
Fish.corrected_image_path
Fish.mask_image_path
Fish.crop_image_path
DetectionReport.file_path
```

这些字段当前主要保存相对于 `data/` 的路径；历史数据可能保存过绝对路径或来自另一份项目副本的路径。MinIO 改造时必须先识别路径来源，不得把绝对路径直接转换成 Object Key。

### 4.2 当前路径转换规则

`backend/app/services/path_service.py` 当前支持：

- `data/<relative>`；
- `uploads/<relative>`；
- `results/<relative>`；
- `/static/<relative>`；
- `data/` 下的绝对路径。

不属于当前 `DATA_DIR` 的绝对路径会被拒绝或无法解析。这个行为必须保留在 Local Adapter 中，MinIO Adapter 使用独立的 Bucket + Object Key 解析，不能复用绝对路径语义。

### 4.3 JSON、媒体和文件读写点

已确认的读写类别：

| 类别 | 主要位置 | 当前行为 |
|---|---|---|
| 摄像头设置 JSON | `camera_yzt_service.py`、`data/config/camera_settings.json` | 启动/页面保存时读取和写入 |
| 焦距设置 JSON | `camera_yzt_service.py`、`data/config/focus_settings.json` | 页面读取和保存 |
| 标定 JSON | `calibration_config_service.py`、`algorithm_router.py`、`data/calibration/` | 算法和标定前读取，页面修改 |
| 算法结果 JSON | `algorithm_result_service.py`、`report_stats_service.py`、`video_report_pdf_service.py` | 读取 `pipeline_result.json` |
| 图表元数据 JSON | `video_report_charts.py` | 写入 `chart_meta.json` |
| 备份/升级 JSON | `backup/`、`update/` 服务 | 运行状态、manifest 和升级信息 |
| 视频媒体 | 摄像头服务、播放服务、报告统计 | OpenCV/ffmpeg/ffprobe 读取、写入、转码 |
| 报告文件 | `report_router.py`、报告服务 | DOCX/PDF 生成、预览和下载 |

媒体处理工具已确认包括：

```text
OpenCV VideoCapture
OpenCV VideoWriter
ffprobe
ffmpeg
```

阶段三必须保证算法不会逐帧通过 MinIO 网络读取，而是下载到本地 job 临时目录后处理。

## 5. 配置 JSON 数据契约

### 5.1 配置分类

| config_type | 当前来源 | 阶段一结论 |
|---|---|---|
| `camera_settings` | `data/config/camera_settings.json` | 运行配置，需版本化 |
| `focus_settings` | `data/config/focus_settings.json` | 运行配置，需版本化 |
| `calibration` | `data/calibration/current_calibration.json` | 算法输入，必须绑定任务版本 |
| `algorithm_thresholds` | 算法配置和阈值加载器 | 必须绑定算法任务快照 |
| `algorithm_runtime` | 环境变量、算法路径和运行参数 | 部署配置与任务快照分离 |
| `report_template_settings` | 报告模板和报告格式逻辑 | 报告版本必须可追溯 |
| `backup_settings` | 备份服务配置 | 不进入前端可读配置 |

### 5.2 阶段二推荐契约

```text
system_config_revision
----------------------
revision_id
config_type
config_key
version
content_json
object_bucket
object_key
sha256
is_active
created_by
created_at
activated_at
superseded_at
```

约束：

- PostgreSQL 保存 active 配置和结构化查询副本；
- MinIO 保存不可变 JSON 快照；
- 本地 JSON 仅作为受控缓存；
- 任务启动时保存使用的配置版本；
- 密码、Access Key、Secret Key 只允许由环境变量或部署密钥注入；
- MinIO 不可用时不能静默回退到未知旧配置；
- 配置激活、回滚和读取必须写操作日志。

## 6. 报表和图表数据契约

### 6.1 报表输入来源

当前报告链路涉及：

```text
VideoTask
Fish
pipeline_result.json
视频媒体元数据
图表输入鱼记录
报告模板占位符
```

当前报告相关服务：

- `report_stats_service.py`：统计和报告 context；
- `video_report_pdf_service.py`：视频报告、合并报告、PDF；
- `report_pdf_service.py`：日报 PDF；
- `report_docx_service.py`：DOCX 和图片/图表插入；
- `video_report_charts.py`：六类图表和 `chart_meta.json`；
- `report_router.py`：生成、历史、预览、下载和重命名。

### 6.2 报表必须保持的数据关系

```text
report_id
  -> selected video_ids
  -> VideoTask / Fish snapshot
  -> pipeline result object versions
  -> chart input and chart objects
  -> generated DOCX/PDF object
```

阶段四必须建立报告快照，至少包括：

```text
snapshot_id
report_id
report_type
video_ids
config_revision_ids
summary_json
video_rows_json
fish_rows_json
chart_input_json
source_object_ids
sha256
created_at
```

### 6.3 图表 Key

当前已识别的图表 Key：

```text
chart_body_length_distribution
chart_body_length_without_tail_distribution
chart_body_weight_distribution
chart_condition_factor_distribution
chart_surface_damage_distribution
chart_large_yellow_croaker_body
chart_meta
```

图表验收不能只判断 PNG 存在，必须同时核对：

- 输入样本数量；
- 有效数值数量；
- 分布区间；
- 图表标题和单位；
- `chart_meta.json` 与报告快照一致；
- PDF 中图表图片数量和位置。

## 7. 阶段一浏览器用例清单

浏览器测试使用真实 Chrome CDP 环境；测试脚本、截图、Network dump 和日志放在项目外。每个用例需要记录 DOM、点击、网络请求、响应、页面数据、Console 和数据库/文件基线。

| 用例 | 操作 | 预期 |
|---|---|---|
| B01 | 打开首页 `/` | 页面加载完成，无新增 JS 错误 |
| B02 | 从数据管理进入“数据报表” | 路由正确，列表接口成功，表格显示或明确空态 |
| B03 | 在数据报表点击日期查询 | 发起 `/api/videos`，返回数据与页面总数一致 |
| B04 | 在数据报表点击视频编号 | 打开源视频预览；存在文件时可播放，缺失时明确提示 |
| B05 | 在数据报表点击“查看报表” | 打开 PDF tab 或显示明确的未生成提示 |
| B06 | 打开“报表总览” | 视频报表、当日报表、自定义报表页签正常显示 |
| B07 | 报表总览点击分页 | 请求 page/page_size 正确，页码和表格数据更新 |
| B08 | 报表总览点击 PDF 报表 | PDF tab 打开，预览接口返回有效 PDF 或明确错误 |
| B09 | 报表总览输入养鱼仓并查询 | 请求包含筛选参数，页面数据全部满足筛选条件 |
| B10 | 打开历史日报/PDF | 历史列表、预览和下载入口可用或有明确空态 |
| B11 | 选择两个视频进入合并报表 | 生成入口存在，失败时提示具体原因，不静默生成空报告 |
| B12 | 检查浏览器 Console 和 Network | 无新增 JS 错误；关键请求无异常 4xx/5xx |

## 8. 阶段一执行边界

阶段一完成后才允许进入阶段二：

- 盘点文档已提交；
- 业务表、文件路径、配置 JSON 和报告输入关系已明确；
- API 基线已记录；
- B01-B12 已执行并记录；
- 未修改生产数据；
- 未切换 MinIO；
- 未删除本地文件；
- 未把 `data/`、日志、缓存、密钥和模型上传 Git。

如果浏览器验证发现缺文件、空报告或数据不一致，应先登记为基线缺陷，不能在阶段二用 MinIO 迁移掩盖该问题。
