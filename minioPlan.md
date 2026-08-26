# MinIO 改造实施计划

## 0. 文档目的

本计划用于指导 Fish Quality Dashboard 从“本地 `data/` 文件存储”逐步改造成“PostgreSQL 保存业务数据和对象元数据、MinIO 保存视频/图片/报表/算法结果”的稳定架构。

本次改造必须满足：

- 不改变现有业务 ID、视频 ID 和报告 ID 的含义。
- 不把 PostgreSQL 当作视频、图片和 PDF 的存储介质。
- 不把 MinIO 当作设置、任务状态或统计查询数据库。
- 新旧存储可以并行运行，迁移失败可以重试和回滚。
- 文件迁移后的每个浏览器入口都必须进行真实点击、请求、响应和数据一致性验证。
- 不通过“显示 0”掩盖文件缺失、对象缺失或媒体解析失败。
- 报表必须使用统一的数据快照，确保页面、PDF、数据库和 MinIO 对象内容一致。
- 所有长期运行任务都必须具备幂等、可恢复、可审计和可观测能力。

本文件只描述计划和验收标准。未完成相应阶段的设计、代码、测试和回滚验证前，不应直接切换生产存储。

## 1. 当前系统事实和风险

当前系统的核心关系是：

```text
VideoTask / Fish / DetectionReport
        |
        | 保存相对于项目 data/ 的路径或历史路径
        v
data/uploads/、data/results/、data/reports/、data/jobs/
```

当前已发现的风险：

- 多个项目副本可能连接同一个 PostgreSQL 数据库，但各自使用不同项目目录下的 `data/`。
- 数据库中的 `source_path` 只代表路径字符串，不代表文件一定存在。
- 历史任务可能保存了旧项目目录的绝对路径或已经失效的相对路径。
- 计划录制可能已经写入数据库任务和算法结果，但源视频文件不在当前项目目录。
- 数据报表、视频 PDF、日报 PDF、自定义合并 PDF 可能使用不同的统计口径。
- 自定义合并报表存在过视频明细、养鱼仓、结论和指标缺失的问题。
- 报表图表依赖中间运算结果，图表文件存在不等于图表内容正确。
- 算法、摄像头、标定、备份和升级流程依赖多个 JSON 文件，不能直接把这些 JSON 随意放进 MinIO。
- `data/`、日志、PID、构建缓存、模型缓存和备份包不能进入 Git，也不能误上传到对象存储生产 Bucket。

## 2. 配置 JSON 的存储设计

### 2.1 配置分类

配置 JSON 不应统一迁移到 MinIO。应先按用途分类：

| 配置类别 | 示例 | 推荐存储 | 原因 |
|---|---|---|---|
| 机器部署配置 | 数据库地址、Redis、MinIO 地址、端口、密钥 | 环境变量或部署密钥文件 | 启动前必须可读，不能依赖业务数据库或 MinIO |
| 当前摄像头运行配置 | 摄像头地址、分辨率、FPS、自动推流设置 | PostgreSQL 配置表 + 本地受控缓存 | 需要页面修改、审计、版本和恢复 |
| 摄像头临时状态 | 当前录制状态、首帧时间、watchdog 状态 | PostgreSQL/Redis | 需要并发控制和恢复，不适合对象存储 |
| 视觉标定配置 | `current_calibration.json` | PostgreSQL 版本表，MinIO 保存原始快照 | 需要版本、回滚、审核和文件快照 |
| 算法阈值配置 | 完整性、破损、颜色、尾部模型参数 | PostgreSQL 版本表，MinIO 保存导出 JSON | 需要与算法任务绑定，保证结果可追溯 |
| 任务输入快照 | 某次算法运行所使用的全部配置 | MinIO JSON 快照 + PostgreSQL 对象元数据 | 任务必须能复现，不能依赖后来被修改的配置 |
| 备份和升级清单 | backup manifest、update manifest | MinIO 备份 Bucket + PostgreSQL 记录 | 属于归档文件，需要校验和生命周期 |

### 2.2 配置表设计

建议增加统一配置版本表，例如 `system_config_revision`：

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

建议的 `config_type`：

```text
camera_settings
focus_settings
calibration
algorithm_thresholds
algorithm_runtime
report_template_settings
backup_settings
```

设计要求：

- `content_json` 是查询和启动时的结构化副本。
- `object_key` 是不可变的原始 JSON 快照。
- 每次修改必须生成新版本，不覆盖历史版本。
- 每个 `VideoTask` 和算法任务保存使用的 `config_revision_id`。
- 当前配置激活必须是事务性的，不能出现两个 active 版本。
- 摄像头和算法启动前读取 PostgreSQL 的 active 版本，并生成任务配置快照。
- 本地 JSON 只能作为缓存，不是最终事实来源。
- MinIO 不可用时：已经运行的任务继续使用本地快照；新任务应阻止启动并给出明确错误。
- 配置内容不得包含数据库密码、MinIO Secret Key 或其他部署密钥。

### 2.3 配置读取顺序

生产读取顺序应固定为：

```text
环境变量/密钥文件
        -> PostgreSQL active config revision
        -> MinIO immutable config snapshot 校验
        -> 本地只读缓存
        -> 业务启动
```

如果校验失败，不得静默使用旧配置。必须记录：

- 配置类型；
- 期望版本；
- MinIO Bucket 和 Object Key；
- SHA256；
- 错误阶段；
- 操作人或任务 ID。

## 3. 对象存储和数据库设计

### 3.1 Bucket

初始建议使用以下 Bucket：

```text
fish-raw
fish-results
fish-reports
fish-config
fish-backups
fish-temp
```

Bucket 权限原则：

- 后端服务账号只拥有业务所需权限。
- 前端不保存 MinIO Access Key 或 Secret Key。
- 浏览器通过后端鉴权后获得短期预签名 URL。
- 配置、备份 Bucket 不允许浏览器直接访问。
- 生产 Bucket 必须开启版本控制或通过不可变 Object Key 保留版本。

### 3.2 Object Key

Object Key 必须使用业务 ID 和日期，不能使用机器绝对路径：

```text
videos/{yyyy}/{mm}/{dd}/{video_id}/source.mp4
videos/{yyyy}/{mm}/{dd}/{video_id}/playable.mp4
videos/{yyyy}/{mm}/{dd}/{video_id}/grounded.mp4
results/{yyyy}/{mm}/{dd}/{video_id}/pipeline_result.json
results/{yyyy}/{mm}/{dd}/{video_id}/measurements/result.csv
results/{yyyy}/{mm}/{dd}/{video_id}/fish/{fish_id}/crop.jpg
results/{yyyy}/{mm}/{dd}/{video_id}/fish/{fish_id}/mask.png
reports/video/{yyyy}/{mm}/{dd}/{video_id}/report.pdf
reports/video/{yyyy}/{mm}/{dd}/{video_id}/report.docx
reports/daily/{yyyy}/{mm}/{dd}/report.pdf
reports/custom/{report_id}/report.pdf
config/{config_type}/{version}.json
```

### 3.3 对象元数据表

建议增加 `stored_object` 表：

```text
stored_object
-------------
object_id
owner_type
owner_id
bucket
object_key
content_type
size_bytes
etag
sha256
status
source_local_path
created_at
uploaded_at
verified_at
deleted_at
last_error
```

`status` 至少包括：

```text
pending
uploading
ready
verifying
failed
missing
deleting
deleted
```

业务表可以先增加对象 Key 字段兼容旧代码，最终逐步统一到 `stored_object` 关系。

## 4. 报表正确性设计

### 4.1 统一报告数据快照

报表生成不能一边查询数据库、一边读取不确定的 JSON、一边读取已经变化的文件。应建立 `report_data_snapshot`：

```text
report_data_snapshot
--------------------
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

生成报表时固定流程：

```text
锁定视频和配置版本
  -> 查询数据库
  -> 读取 pipeline_result.json
  -> 校验源对象和结果对象
  -> 计算 summary、video_rows、fish_rows
  -> 保存 snapshot
  -> 从 snapshot 生成图表
  -> 从 snapshot 生成 DOCX/PDF
  -> 校验 PDF 占位符、数字和图表
  -> 上传 MinIO
  -> 写 DetectionReport
```

### 4.2 必须检查的报表字段

视频报表、日报和自定义合并报表均必须检查：

- 视频编号；
- 养鱼仓名称；
- 视频状态；
- 视频创建时间、开始时间、结束时间；
- 视频真实时长；
- 处理耗时；
- 总鱼数；
- 完整、不完整、失败、破损鱼数量；
- 平均、最小、最大体长；
- 去尾体长；
- 体高；
- 体重；
- 肥满度；
- 表面破损率；
- 结论中的视频数量、鱼数量、时长和质量判断；
- 图表数据总数、分布区间和图表图片。

### 4.3 自定义合并报表专项验收

至少选择两个已命名养鱼仓的视频，例如：

```text
20260826_0004 -> A
20260825_0020 -> B
```

必须验证：

- PDF 中两个视频均出现；
- A、B 名称均出现；
- 两个视频鱼数分别正确；
- 合计鱼数等于两者之和；
- 每个视频的时长不是把处理耗时误当媒体时长；
- 平均值按鱼记录计算，不是按视频简单平均；
- 结论包含视频数量和鱼数量；
- 图表样本数等于快照中的有效鱼记录数；
- PDF 中不存在 `{{placeholder}}`；
- PDF 中不存在不应出现的 `--`；
- 报表名称、报告 ID 和 MinIO Object Key 对应一致。

## 5. 浏览器验证总原则

每次对象路径或 API 迁移都必须做真实浏览器验证，不能只执行构建或 curl。

每个用例记录：

- 浏览器地址；
- 操作步骤；
- 点击前 DOM；
- 点击后的网络请求；
- HTTP 状态码；
- 响应关键字段；
- 页面文本或表格数据；
- 浏览器 Console 错误；
- 截图或测试记录的位置；
- 数据库和 MinIO 对象校验结果。

禁止把截图、临时 JSON、CDP 脚本和测试日志写入本项目目录，应写到项目外的测试目录。

## 6. 五阶段改造计划

## 阶段一：盘点、基线和数据契约

### 目标

- 盘点所有本地文件路径、数据库字段、配置 JSON、报告模板和浏览器入口。
- 建立 Local Storage 与 MinIO Storage 的统一接口契约。
- 固化当前系统基线，确保迁移前后可以比较。
- 明确数据库、项目目录和实际媒体文件之间的关系。

### 需要智能提取阅读的目录

必须递归扫描并建立清单：

```text
backend/app/routers/
backend/app/services/
backend/app/repositories/
backend/app/workers/
backend/app/templates/
algorithms/config/
algorithms/core/
algorithms/tools/
frontend/src/api/
frontend/src/views/admin/report/
frontend/src/components/report/
data/ 仅扫描元数据，不把业务数据提交 Git
```

智能提取内容至少包括：

- `Path`、`source_path`、`result_dir`、`file_path`、`*_url`；
- `pipeline_result.json`、CSV、PDF、DOCX 的读取和写入点；
- 所有 `json.load`、`json.dump`、`read_text`、`write_text`；
- 所有 `ffprobe`、`ffmpeg`、OpenCV 视频读取点；
- 所有报告占位符和图表 Key；
- 所有前端视频、图片、PDF URL；
- 所有配置写入 API 和启动读取点。

### 注意事项

- 不修改业务代码。
- 不扫描或上传数据库密码、MinIO 密钥和真实媒体内容。
- 形成 `storage_inventory`、`config_inventory`、`report_inventory` 和 `browser_case_inventory`。
- 记录每个旧路径的来源项目目录和当前可见性。

### 阶段验收

- 得到完整路径和配置清单；
- 得到当前报表的数字基线；
- 得到视频、鱼、报表数量基线；
- 运行首页、数据报表、报表总览、视频预览和 PDF 预览浏览器冒烟用例；
- 所有基线数据保存到项目外测试目录。

## 阶段二：配置版本化和对象存储基础设施

### 目标

- 部署 MinIO 开发/测试实例；
- 建立 Bucket、账号、权限、生命周期和备份策略；
- 建立 `system_config_revision` 和 `stored_object` 迁移；
- 实现 Local/MinIO 双后端接口；
- 实现对象上传、下载、存在性、元数据、SHA256 和预签名 URL。

### 注意事项

- MinIO 密钥只通过环境变量或部署密钥注入；
- 不允许把 MinIO 服务地址写死在前端；
- 所有上传必须临时文件 + 原子完成标记；
- 上传中断不得写成 `ready`；
- 对象 Key 不包含绝对路径和用户隐私；
- 配置版本激活必须可回滚；
- 不能在该阶段切换默认存储。

### 浏览器验收

- 打开摄像头设置，读取 active 配置；
- 修改配置并保存，刷新后值不丢失；
- 打开标定页面，读取当前配置；
- 修改后生成新配置版本；
- 查看操作日志，能看到配置版本和操作人；
- 使用失效对象 URL，页面必须显示明确错误，不得显示空白。

## 阶段三：视频、图片和算法结果双写迁移

### 目标

- 新上传视频和计划录制视频写入 MinIO；
- 保留本地临时文件用于录制、转码和算法处理；
- 算法结果、鱼体图片、CSV 和 pipeline JSON 上传 MinIO；
- 数据库保存对象 Key、大小、SHA256、媒体时长和状态；
- 支持失败重试、断点恢复和旧数据迁移。

### 注意事项

- 录制完成必须先验证文件存在、大小大于 0、ffprobe 可读；
- 计划录制任务必须区分：未启动、首帧失败、空文件、上传失败、算法失败；
- `video_duration_seconds` 必须来自实际媒体探测，不能使用计划时长代替；
- MinIO 对象不存在时，页面显示“对象缺失”，不能显示 `0 秒`；
- 算法从 MinIO 下载到 job 临时目录，不逐帧通过网络读取；
- 不自动删除旧目录，迁移完成并通过校验后再制定清理策略。

### 浏览器验收

- 上传视频后查看列表、视频预览、任务状态；
- 启动计划录制并验证计划状态、视频任务状态和文件对象；
- 点击视频编号，确认 Range 播放成功；
- 查看鱼体图片、矫正图、mask 和算法结果；
- 点击下载和预览 PDF/DOCX；
- 删除或对象缺失时页面显示明确错误；
- 刷新页面、重新登录和重新打开同一对象 URL，数据仍一致。

## 阶段四：报表快照、迁移和正确性验收

### 目标

- 建立统一报告数据快照；
- 统一页面、PDF、日报、视频报告和自定义报告统计口径；
- 修复视频历史信息、养鱼仓、结论、时长、指标和图表缺失；
- 将报告和图表上传 MinIO；
- 建立 PDF 内容自动校验和浏览器人工校验。

### 注意事项

- 报表生成前锁定视频、配置版本和结果对象；
- 不允许报告生成过程中混用新旧版本 JSON；
- 合并报表必须按视频分组后再计算视频明细，合计值从明细或鱼记录推导；
- 平均值必须明确按鱼平均还是按视频平均；
- 图表生成必须使用快照中的输入数据，并保存图表输入摘要；
- PDF 生成后必须检查占位符、页数、关键文本和图片数量；
- 报告写入数据库前必须确认 MinIO 对象已 `ready`。

### 浏览器验收

- 生成单视频报告并核对视频编号、养鱼仓、时长、鱼数、结论和图表；
- 选择多个视频生成合并报表；
- 跨页选择后生成合并报表；
- 在 PDF 预览页检查每个视频历史信息；
- 检查报表名称修改后 PDF 内容同步更新；
- 批量下载多个报告并检查文件名和内容；
- 检查报告总览、数据报表、PDF 三处数字一致；
- 检查图表不是空白、图例正确、数量与快照一致。

## 阶段五：切换、稳定性、备份和回滚

### 目标

- MinIO 成为长期主存储；
- 本地只保留必要缓存和失败现场；
- 完成旧数据迁移、对象完整性扫描和孤儿对象清理策略；
- 完成断电、断网、MinIO 重启、PostgreSQL 重启、Worker 重启和后端重启测试；
- 建立监控、告警、备份恢复和版本回滚流程。

### 注意事项

- 切换必须有明确 feature flag 和回滚开关；
- 切换前冻结生产迁移窗口并备份 PostgreSQL 和 MinIO；
- 不在没有恢复演练的情况下删除本地源文件；
- 数据库和对象存储之间不能假设事务一致，必须有对账任务；
- 对账发现缺对象时标记 `missing` 并告警，不自动删除业务记录；
- MinIO 磁盘、对象数量、失败上传、预签名失败和报告生成失败需要监控。

### 最终验收

- 新任务连续运行至少一个完整业务周期；
- 计划录制、上传、算法、报告、下载和删除流程通过；
- 重启后任务状态和对象状态恢复正确；
- 备份可以恢复到独立测试环境；
- 任意视频可以从数据库对象 Key 定位到 MinIO 文件；
- 任意报告可以从报告记录定位到快照和全部输入对象；
- 页面 Console 无新增错误，关键接口无 4xx/5xx；
- 旧 Local 模式仍可通过配置开关回滚。

## 7. 长期稳定运行要求

### 幂等

- 相同 `video_id` 和对象 Key 重试上传不能产生重复业务记录；
- 相同报告快照重复生成不能产生错误的重复报表；
- Worker 重试不能覆盖更新任务的结果；
- 计划调度器重启不能重复创建视频任务。

### 可观测

每次对象操作记录：

- `video_id`、`report_id` 或 `fish_id`；
- Bucket 和 Object Key；
- 操作类型；
- 开始和结束时间；
- 字节数；
- SHA256/ETag；
- 重试次数；
- 错误阶段和错误消息。

### 数据对账

至少每天执行：

```text
数据库 ready 对象 -> MinIO 是否存在
MinIO 对象 -> 是否存在业务引用
视频对象 -> ffprobe 是否可读
报告对象 -> PDF 是否可读
报告快照 -> PDF 关键数字是否一致
```

### 回滚

必须支持：

- 关闭 MinIO 主存储开关，回到 Local；
- 使用旧 `source_path` 播放仍存在的文件；
- 从 MinIO 下载对象恢复到本地缓存；
- 报表从历史快照重新生成；
- 配置回滚到上一版本；
- 保留迁移失败记录，不删除数据库业务数据。

## 8. 推荐执行顺序

```text
阶段一：盘点和基线
  -> 阶段二：配置版本化和 MinIO 基础设施
  -> 阶段三：视频/图片/算法结果双写
  -> 阶段四：报表快照和内容正确性
  -> 阶段五：主存储切换和稳定性验收
```

任何阶段失败时：

1. 保留失败现场和日志；
2. 记录任务 ID、对象 Key 和数据库状态；
3. 不删除源文件；
4. 先恢复 Local 模式；
5. 修复并重新执行该阶段验收；
6. 不跨阶段继续推进。
