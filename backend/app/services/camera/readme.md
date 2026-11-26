## 0515

### 路径修改
1. 将camera部分移动到了app/文件夹下，才能在backend目录下正常启动服务。python特性。
此外 将router，service和schema做了拆分。
2. raspberrypi/ 是数据采集侧部分代码，

### 软件需求
需要安装mediamtx 以接收视频流

### 后续更新

杨紫婷：\
app/schemas/camera_schema.py : 规定请求和返回的信息 \
app/routers/camera_router.py ：需要实现的功能

王润正：\
app/services/camera/camera_service.py \
现在有：启动、停止、读取流。
需要服务端ip

### cmd
在后端主机命令行执行下列命令可以实树莓派现摄像头的启动和停止
B端后端 FastAPI：http://127.0.0.1:8000
MediaMTX 的 RTMP 推流地址：rtmp://192.168.31.254:1935/fish_camera
A端树莓派摄像头控制接口：http://raspberrypi.local:9000/camera/start
### ip配置
树莓派 FastAPI：
http://raspberrypi.local:9000
树莓派推流到主机 MediaMTX：
rtmp://192.168.31.254:1935/fish_camera
主机后端从 MediaMTX 拉流：
rtmp://192.168.31.254:1935/fish_camera
# 后台接口查询摄像头视频历史
curl "http://127.0.0.1:8000/api/camera_yzt/history"
# 启动树莓派摄像头录制
curl -X POST "http://192.168.31.254:1935/api/camera_yzt/start" -H "Content-Type: application/json" -d '{"stream_url": "rtmp://127.0.0.1:8000/fish_camera"}'
# 等待一段时间后停止
curl -X POST "http://127.0.0.1:8000/api/camera_yzt/stop"

# camera_service_yzt.py :
1.  确定start 命令成功传递给树莓派执行之后再修改self.xxx（包括摄像头索引，帧率等参数）
    同样的,确定stop 命令成功传递给树莓派执行之后再修改self.xxx
    主要是start_stream 和 stop_stream 函数中，return的内容。
2. 保留start 和 stop 函数来给树莓派命令，并返回结果。
3. 数据库部分单独写函数，不要在启动和关闭流的地方添加数据库操作。
4. 帧读取部分单独写函数，不要在启动和关闭流里面读取帧。
手动测试树莓派推流
curl -X POST "http://raspberrypi.local:9000/camera/start" \
  -H "Content-Type: application/json" \
  -d '{
    "stream_url": "rtmp://192.168.31.254:1935/fish_camera",
    "width": 1920,
    "height": 1080,
    "fps": 25
  }'
启动摄像头：

curl -X POST "http://raspberrypi.local:9000/camera/start" \
  -H "Content-Type: application/json" \
  -d '{
    "stream_url": "rtmp://192.168.31.254:1935/fish_camera",
    "width": 1920,
    "height": 1080,
    "fps": 25
  }'

关闭：

curl -X POST "http://192.168.1.110:9000/camera/stop"
curl -X POST "http://raspberrypi.local:9000/camera/stop"
读取视频流：

ffmpeg -rtsp_transport tcp   -i rtsp://127.0.0.1:8554/fish_camera   -f null -

启动摄像头：
curl -X POST "http://ip_of_raspberry_pi:9000/camera/start" \
  -H "Content-Type: application/json" \
  -d '{
    "stream_url": "rtmp://ip_of_server:1935/fish_camera",
    "width": 1920,
    "height": 1080,
    "fps": 25
  }'

关闭：
curl -X POST "http://ip_of_raspberry_pi:9000/camera/stop"

读取视频流测试命令：
ffmpeg -rtsp_transport tcp   -i rtsp://127.0.0.1:8554/fish_camera   -f null -



杨紫婷
已完成内容
Task 1: 服务层
- 新建 backend/app/services/camera_yzt/camera_yzt_service.py
- CameraServiceYzt 类使用后台线程拉取 RTSP 流，包含:
- start_stream() 启动守护线程
- _capture_loop() 循环读取帧并编码为 JPEG
- stop_stream() 停止线程
- get_latest_frame() 返回缓存帧
- 断连重连逻辑（延迟递增，最多30秒）
Task 2: 路由层
- 新建 backend/app/routers/camera_yzt_router.py
- 端点:
- POST /api/camera_yzt/start - 启动拉流
- POST /api/camera_yzt/stop - 停止拉流
- GET /api/camera_yzt/status - 获取状态
- GET /api/camera_yzt/frame - 获取最新帧（JPEG）
Task 3: 前端大屏
- Dashboard.vue 修改:
- 点击"摄像头输入"按钮开始拉流，再次点击"关闭摄像头"
- 流启动后视频区切换为 <img> 显示实时画面
- 使用 /api/camera_yzt/frame 每100ms刷新帧
Task 4: 主应用
- main.py 已注册 camera_yzt_router
