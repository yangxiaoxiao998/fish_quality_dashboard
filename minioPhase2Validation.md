# MinIO 阶段二实施与验收记录

## 1. 本阶段范围

本阶段完成“配置版本化和对象存储基础设施”的可回滚基础实现，默认存储仍为 Local，未把生产文件切换到 MinIO。

本阶段完成：

- `storage_backend=local|minio` 配置开关；
- MinIO 环境变量配置项；
- Local/MinIO `ObjectStorage` 适配边界；
- 本地原子写入、SHA256、文件大小、Content-Type 和预签名 URL 契约；
- `stored_object` 元数据表；
- `system_config_revision` 配置版本表；
- 配置版本创建、激活、查询接口；
- 摄像头设置保存自动创建并激活配置版本；
- 标定配置保存自动创建并激活配置版本；
- 本地摄像头、焦距、标定 JSON 的启动 bootstrap；
- Object Key 路径穿越校验；
- 启动时幂等创建阶段二表。

## 2. 数据库基线保护

阶段二启动迁移后只新增表，不删除和修改现有业务记录。

验证结果：

```text
video_task:       75
fish:             440
detection_report: 93
stored_object:    0
```

新增表：

```text
stored_object
system_config_revision
```

配置 bootstrap 结果：

```text
camera_settings/default  version=1  active=true
focus_settings/default   version=1  active=true
calibration/default      version=1  active=true
```

## 3. 接口验证

| 接口 | 结果 |
|---|---|
| `GET /api/storage/health` | `200`, `backend=local`, `minio_enabled=false` |
| `GET /api/storage/config-revisions/active?config_type=camera_settings` | 返回 active revision |
| `GET /api/storage/config-revisions/active?config_type=calibration` | 返回 active revision |
| `GET /api/storage/config-revisions?config_type=camera_settings` | 返回版本列表 |
| `POST /api/storage/config-revisions` | 创建测试版本成功 |
| `POST /api/storage/config-revisions/{id}/activate` | API 已注册并支持激活 |
| `GET /api/videos?page=1&page_size=1` | 业务数据仍正常返回 |
| `GET /api/health` | `200` |

测试创建的 `stage2_test` 配置版本已删除，未保留测试业务数据。

## 4. 浏览器点击验证

使用真实 Chrome CDP，完成以下验证：

### 摄像头设置

1. 打开 `/admin/system-settings?tab=camera-settings`。
2. 输入技术调试密码并进入页面。
3. 检查摄像头设置页面和当前设置控件存在。
4. 点击“应用设置”。
5. 页面显示：

```text
摄像头设置已应用
摄像头设置已保存，将在下次启动时生效
```

6. 查询配置版本接口，结果为：

```text
camera_settings revisions: 2
active version: 2
```

说明浏览器保存行为已连接到配置版本表。

### 视觉标定

1. 打开 `/admin/system-settings?tab=visual-calibration`。
2. 输入技术调试密码并进入页面。
3. 页面成功显示当前标定状态：`已标定`。
4. 页面显示标定 ID、标定文件、变换矩阵、图片预览入口和操作按钮。
5. 未点击“清除当前标定”“执行视觉矫正”等会改变实际业务数据的按钮。

### 报表和数据页面回归

- 首页加载成功；
- 数据报表加载成功；
- 报表总览加载成功；
- 养鱼仓筛选成功；
- 分页仍显示省略号；
- 视频预览弹窗可以打开；
- PDF Tab 可以打开；
- 浏览器 Console 未发现新增 JavaScript 错误。

## 5. 阶段一缺陷处理

### 已处理

- 算法结果接口 `GET /api/algorithm/videos/{video_id}/result` 已重新请求验证，当前返回 `200`，不再复现阶段一记录的 500。
- Upgrade Manager 的 8012 端口在当前启动流程中正常监听，当前服务没有端口冲突。
- 摄像头设置保存现在生成配置版本并激活。
- 标定配置保存路径已经接入配置版本服务。
- Object Key 增加了绝对路径和 `..` 路径段校验。

### 保留为历史数据缺陷

- 旧日志中存在 LED 502，这是树莓派当前不可达导致，不属于 MinIO 阶段二代码错误；当前阶段没有硬件可连接，不能伪造成功。
- 历史任务的源视频文件缺失仍会明确返回文件不存在，不通过 0 秒掩盖问题。
- 历史日志中的异常记录来自此前运行，不作为当前进程新增错误；阶段二启动后以新日志和当前请求为准。

## 6. 当前限制

- 当前环境没有安装 `minio` Python 包；因此未连接真实 MinIO 服务。
- `storage_backend` 仍为 `local`，这是阶段二要求的可回滚默认行为。
- 未执行真实 MinIO Bucket 创建、上传、Range 播放和预签名 URL 验证。
- 这些内容属于阶段二剩余验收项，必须在测试 MinIO 实例可用后完成，不能用 Local 结果代替。

## 7. 阶段二完成标准

阶段二代码基础完成，但在真实 MinIO 测试实例可用前，不应宣称阶段二全部生产验收完成。下一步必须：

1. 安装并锁定 `minio>=7.2.0` 依赖。
2. 准备测试 MinIO endpoint 和非生产密钥。
3. 创建测试 Bucket 和最小权限账号。
4. 执行上传、存在性、metadata、SHA256 和预签名 URL 测试。
5. 通过浏览器测试视频、图片、PDF 和失败对象提示。
6. 对照 PostgreSQL 基线再次核对无业务数据变化。
