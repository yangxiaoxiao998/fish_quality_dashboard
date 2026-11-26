摄像头视频
  ↓
stream_router 创建 video_task
  ↓
Redis/RQ fish 队列
  ↓
algorithm_video_pipeline 调用整视频 CLI
  ↓
抽帧、矫正、检测/分割/跟踪、测量
  ↓
结果导入 PostgreSQL 并生成报表


摄像头输入：
树莓派向 MediaMTX 推送 RTMP
  ↓
camera_yzt_service 从本机 MediaMTX 读取并录制 MP4
  ↓
停止录制后沿用同一个 video_id 入队整视频算法
  ↓
结果导入 PostgreSQL、生成视频报告和日报
