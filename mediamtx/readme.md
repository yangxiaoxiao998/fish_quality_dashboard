### 必须带配置文件启动 MediaMTX

nohup ./mediamtx ./mediamtx.yml \

> mediamtx.log 2>&1 &

### 关闭mediamtx

pkill -f mediamtx
sleep 1
sudo ss -lunp | grep ':10000' || echo "udp 10000 free"

### 重启mediamtx

cd /home/dsl/Code/yangzt/test3/mediamtx

pkill -f mediamtx || true
sleep 1

./mediamtx ./mediamtx.yml > mediamtx.log 2>&1 &

sleep 2
tail -80 mediamtx.log

### 开启树莓派

curl -X POST "http://raspberrypi.local:9000/camera/start"
  -H "Content-Type: application/json"
  -d '{
    "stream_url": "rtmp://192.168.0.100:1935/fish_camera",
    "width": 1920,
    "height": 1080,
    "fps": 25
  }'
输出结果：{"status":"started","stream_url":"rtmp://192.168.31.254:1935/fish_camera","width":1920,"height":1080,"fps":25}
