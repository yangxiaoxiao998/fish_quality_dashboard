# Camera service

当前正式摄像头链路不使用本目录中的旧 USB 逐帧上传设计。

```text
树莓派 /camera/start
  -> RTMP 推送到主机 MediaMTX fish_camera
  -> camera_yzt_service 从本机 camera_read_url 读取
  -> JPEG 预览 + MP4 正式录制
  -> stop 后使用同一 video_id 入队整视频算法
```

正式接口位于 `backend/app/routers/camera_yzt_router.py`：

- `POST /api/camera_yzt/start`
- `POST /api/camera_yzt/stop`
- `GET /api/camera_yzt/status`
- `GET /api/camera_yzt/frame`
- `GET /api/camera_yzt/history`
- `GET /api/camera_yzt/video/{video_id}`

旧的本地 USB `camera_capture.py` 和逐帧检测链路已经删除。树莓派 API 地址、推流地址和后端读取地址由 `pi_runtime_config.py`、环境变量及 `camera_yzt_service.py` 统一管理。
