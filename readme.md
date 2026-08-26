运行软件
./stop_dev.sh

CONDA_ENV_DIR=/home/dsl/miniconda3/envs/full ./start_dev.sh

* 检查摄像头
  PI_BASE_OVERRIDE="http://192.168.31.79:9000"
  bash tools/collect_camera_desync_debug.sh
* 打开 [http://127.0.0.1:5173/](http://127.0.0.1:1124/)
  * 开屏背景正常。
  * 标题“鱼品质智能检测系统”正常。
  * 录入、报表、设置按钮正常。
  * 页面没有 stylelint 红色遮罩。
* 打开 [http://127.0.0.1:5173/data-input](http://127.0.0.1:1124/data-input)
  * 占位页正常。
  * 返回首页按钮正常。
  * 进入原完整首页按钮正常。
  * 页面没有 stylelint 红色遮罩。
* 打开 [http://127.0.0.1:5173/dashboard](http://127.0.0.1:1124/dashboard)
  * 原 Dashboard 正常。
  * 左中右布局还在。
  * 摄像头输入区域还在。
* 打开后台页面：
  /admin/data-report
  /admin/video-history
  /admin/system-settings
