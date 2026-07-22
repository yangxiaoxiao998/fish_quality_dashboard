上传视频
  ↓
  <!-- 获取视频流 -->
stream_ingest.py
  ↓
  <!-- 处理视频帧 -->
algo_facade.process_video_frames()
  ↓
  <!-- 抽帧 -->
frame_extract_service.extract_video_frames()
  ↓
抽帧保存 jpg
  ↓
  <!-- 矫正 -->
apply_frame_perspective_if_enabled()
  ↓
后续检测 / 分割 / 裁剪读取矫正后的帧


摄像头输入：
树莓派推流
  ↓
B端 camera_yzt_service 读取实时帧
  ↓
apply_perspective_to_frame_if_enabled(frame)
  ↓
保存 frame_fish_detection，包括 bbox 和 center_x/center_y
  ↓
前端点击画面
  ↓
/api/dashboard/frame/select-fish
  ↓
根据点击坐标找最近鱼
  ↓
前端显示矫正后的 Camera Stream
  ↓
保存矫正后的逐帧图像
  ↓
检测算法使用矫正后的帧