# Camera 模块

本模块用于硬件接入摄像头输入功能。

## 功能说明

打开摄像头，向后端传输每秒25帧的图片。
具体流程：
1. 硬件设备运行 camera/camera.py 客户端
2. 客户端捕获摄像头视频流
3. 按 25fps 采样帧
4. 每帧图片通过 API 发送到后端 /api/camera/detect
摄像头录制 → camera_yzt_service
    ↓
VideoTask(source_type="rtsp", source_path=...)
    ↓
/api/videos 返回所有视频 → VideoHistory.vue 显示
    ↓
点击播放 → /static/uploads/videos/{video_id}.mp4
5. 后端对每帧图片进行鱼体检测和分析

## 目录结构

```
camera/
├── README.md          # 本文档
└── camera.py          # 摄像头接口实现
```

## 接口说明
流程:
1. 前端先 POST 到树莓派 /api/camera/start（让树莓派开始推流）
2. 等待 5 秒延迟（cameraStartDelay）
3. 再 POST 到后端 /api/camera_yzt/start（后端开始从 MediaMTX 拉流）
4. 停止时：先停后端拉流，再通知树莓派停止推流

正常流程应该是:
1. 树莓派摄像头先向 MediaMTX 推流 (rtmp://192.168.31.254:1935/fish_camera)
2. MediaMTX 转换为 RTSP (rtsp://127.0.0.1:8554/fish_camera)
3. 后端才能拉到流
你的树莓派摄像头现在正在推流吗？
可以先手动验证 MediaMTX 是否有流：
ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:8554/fish_camera -f null -
如果上面命令报错说明流还没推到 MediaMTX。后端服务会持续重连，一旦树莓派开始推流就能正常显示。
### 启动摄像头捕获

```python
from camera import CameraCapture

# 初始化摄像头
camera = CameraCapture(
    camera_index=0,      # 摄像头索引，默认0
    fps=25,              # 采样帧率，默认25
    api_url="http://localhost:8000"  # 后端API地址
)

# 启动捕获
camera.start()

# 停止捕获
camera.stop()
```

### 回调函数

每次捕获到帧时，会自动将图像上传到后端进行检测。

```python
def on_frame_detected(result: dict):
    """检测结果回调"""
    print(f"检测到鱼: {result}")

camera = CameraCapture(on_detected=on_frame_detected)
```

## API 端点

- `POST /api/camera/start` - 开始摄像头捕获
- `POST /api/camera/stop` - 停止摄像头捕获
- `GET /api/camera/status` - 获取摄像头状态

## 依赖

- opencv-python
- numpy
- requests

```markdown
## 六、代码提交规范

1. **开发流程**
```bash
git pull origin dev        # 开发前同步代码
cd camera                  # 进入摄像头目录
git add .                  # 添加修改
git commit -m "feat(camera): fix fps stability"  # 按规范提交
git push                   # 推送到远程