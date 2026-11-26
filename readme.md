# 鱼品质智能检测系统

基于树莓派摄像头 + MediaMTX + FastAPI + RQ + PyTorch 的鱼体检测、测量与报表系统。
本文档面向**从 GitHub 克隆代码后自行部署**的用户，重点说明如何补齐不在仓库中的大模型权重、准备运行环境并启动开发版。

> 运行期产生的 `data/`、算法模型 `algorithms/pth/`（除少量小模型外）、`node_modules/`、构建产物都**不入库**，需要按本文手动下载或生成。模型文件较大，仓库不保存，避免超出 GitHub 的文件大小限制。

---

## 1. 系统组成

| 组件 | 说明 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite 7，默认端口 `5173` |
| 后端 | FastAPI + Uvicorn，默认端口 `8000`，接口文档 `/docs` |
| 任务队列 | Redis + RQ Worker |
| 数据库 | PostgreSQL，默认库 `fishdb` |
| 视频流 | MediaMTX（已随仓库提供二进制），RTMP `1935` / RTSP `8554` / HLS `8888` |
| 算法 | PyTorch、GroundingDINO、SAM/SAM-HQ、RF-DETR、Mask R-CNN、CatBoost、ViT |

目录结构：

```text
B_fish_quality_dashboard/
├── backend/               FastAPI 后端、RQ worker、PyInstaller 打包
├── algorithms/            算法源码与模型
│   ├── core/              标定、检测、跟踪、测量、质量算法
│   ├── tools/             整视频 pipeline 入口
│   ├── config/            路径与配置
│   └── pth/               ★ 模型权重（需自行下载/生成，不入库）
├── frontend/              Vue3 前端
├── desktop/               Electron 桌面壳
├── mediamtx/              MediaMTX 二进制与配置
├── scripts/               发布、备份、运维脚本
├── start_dev.sh           开发环境一键启动
└── stop_dev.sh            开发环境一键停止
```

---

## 2. 环境要求

推荐 **Ubuntu 22.04 x86_64**，并安装以下依赖：

- NVIDIA 驱动 + CUDA 12.8（算法默认使用 GPU，`torch 2.7.0+cu128`）
- Python **3.10**（使用 Conda 环境，本文以环境名 `full` 为例）
- Node.js **≥ 20.19.0**（与 npm 放在同一个 Conda 环境中）
- PostgreSQL、Redis
- ffmpeg / ffprobe（必须可被 `PATH` 找到）
- `curl`、`git`、`iproute2`（`ip` 命令）

> MediaMTX、模型算法源码、前端源码已包含在仓库中，无需额外下载。

---

## 3. 克隆代码

```bash
git clone https://github.com/yangxiaoxiao998/fish_quality_dashboard.git
cd fish_quality_dashboard
```

---

## 4. 下载模型权重（必做）

`algorithms/pth/` 中的模型分两处获取：

- **GitHub Release 附件**（单文件均 < 2 GB）：`groundingdino`、`best_com`、`best_coco`、`maskrcnn`、`swin`、`catboost`、小树模型，以及 `bert`/`vit` 大权重和 RF-DETR engine。
- **公开上游 / HuggingFace 镜像**（单文件 > 2 GB，超 GitHub Release 上限）：两个 SAM 权重 `sam_vit_h_4b8939.pth`、`sam_hq_vit_h.pth`。它们本来就是公开模型，直接从官方源下载即可，**不需要任何人上传**。

> 国内网络直连 `huggingface.co` 常被阻断，请使用镜像 `https://hf-mirror.com`（脚本默认已用）。SAM 官方源 `dl.fbaipublicfiles.com` 一般可直连。

### 4.1 模型清单

| 文件（放入 `algorithms/pth/`） | 大小 | SHA256 | 来源 |
| --- | --- | --- | --- |
| `groundingdino_swint_ogc.pth` | 661.8 MB | `3b3ca2563c77c69f651d7bd133e97139c186df06231157a64c507099c52bc799` | GitHub Release |
| `best_coco_bbox_mAP_epoch_12.pth` | 668.7 MB | `599c348a3100969c6e2732bca235c23109181a5e76bc88d984f8c85ef1f79868` | GitHub Release |
| `best_com_base.pth` | 984.4 MB | `61ef6ef34abbd158263415cfc200f03191ad4b8d9a815c7495acb9aee75ea7c5` | GitHub Release |
| `maskrcnn_resnet50_fpn_v2-tail.pth` | 175.4 MB | `98d186614d5da7818841fcddb14c815b8dcb6bab07bf256f8c06841bef3aa1b4` | GitHub Release |
| `maskrcnn_resnet50_fpn_v2-wound.pth` | 175.4 MB | `2d1eb14582396b333c69ff15bef09ffa4e8b7181da8f816ba54dad58fb43910a` | GitHub Release |
| `swin_tiny_patch4_window7_224.pth` | 109.0 MB | `9f71c168d837d1b99dd1dc29e14990a7a9e8bdc5f673d46b04fe36fe15590ad3` | GitHub Release |
| `catboost_full_model.pkl` | 1.0 MB | `91205df496057b6099d04bdc1ea8a0503494e606162ce5ef5154c1c2e8bacccb` | GitHub Release |
| `tree_model/belly_train_tree.pkl` | 3.4 KB | `e36bf4c0ff67c2e96a7306d4ce12d11c8832bfec5bba204d1c26b33b588f6288` | GitHub Release |
| `bert-base-uncased/pytorch_model.bin` | 420.1 MB | `097417381d6c7230bd9e3557456d726de6e83245ec8b24f529f60198a67b203a` | GitHub Release / HF |
| `cache/vit-base/model.safetensors` | 329.6 MB | `fd4e1169c7aa6c2dbfa8a6448be13b35abc0ee256190857c90009d12c094619b` | GitHub Release / HF |
| `output_onnx_dynamic/rfdetr-seg-medium-b64-fp16.engine` | 73.4 MB | `325a721364d4c2e49424132990e969286cedbe4464cc4ec7ebbb581110a695a8` | GitHub Release / 自行重建 |
| `sam_vit_h_4b8939.pth` | 2445.7 MB | `a7bf3b02f3ebf1267aba913ff637d9a2d5c33d3173bb679e46d9f338c26f262e` | SAM 官方源 |
| `sam_hq_vit_h.pth` | 2451.8 MB | `a7ac14a085326d9fa6199c8c698c4f0e7280afdbb974d2c4660ec60877b45e35` | HF 镜像（lkeab/hq-sam） |

`bert-base-uncased/` 目录还需 `config.json`、`tokenizer_config.json`、`tokenizer.json`、`vocab.txt`；
`cache/vit-base/` 目录还需 `config.json`、`preprocessor_config.json`。这些是标准模型的小文件，脚本会从 HuggingFace 镜像获取，不放进 Release（Release 附件名不能带目录，`config.json` 还会与其它模型撞名）。

> 注意：`best_com_base_new.pth`、`output_onnx_dynamic.tar.xz`、`cache/vit-base/pytorch_model.bin` 属于重复/历史文件，**部署不需要下载**。
>
> 仓库若为 **Private**，匿名 `curl` 无法下载 Release 附件、也无法 `git clone`。对外发布前请将仓库改为 Public；否则下载脚本需改用带 token 的 `gh release download`。

### 4.2 一键下载脚本（复制到项目根目录执行）

脚本会创建目录、下载并逐一做 SHA256 校验。运行前只需确认 **GitHub Release 标签 `GH_TAG`（本项目用 `models-v1.0.0`）及其附件已上传**——两个 SAM 走公开源，无需额外准备。如果维护者用了不同的标签或仓库名，只需改脚本顶部对应变量。

```bash
set -euo pipefail

# ==== 按实际托管位置修改 ====
GH_OWNER="yangxiaoxiao998"            # GitHub 用户名/组织
GH_REPO="fish_quality_dashboard"      # 仓库名
GH_TAG="models-v1.0.0"                # Release 标签（需先在 GitHub 上创建并上传附件）

GH_BASE="https://github.com/${GH_OWNER}/${GH_REPO}/releases/download/${GH_TAG}"
# HuggingFace 国内加速镜像；不能直连 huggingface.co 时用它（可下载，无需登录）
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
# ============================

PTH="algorithms/pth"
mkdir -p "$PTH/tree_model" "$PTH/bert-base-uncased" \
         "$PTH/cache/vit-base" "$PTH/output_onnx_dynamic"

verify() {
    local file="$1" want="$2"
    local got
    got=$(sha256sum "$file" | awk '{print $1}')
    if [ "$got" != "$want" ]; then
        echo "❌ SHA256 校验失败: $file"
        echo "   期望: $want"
        echo "   实际: $got"
        return 1
    fi
    echo "✅ $file"
}

gh_get()  { curl -fL --retry 3 -o "$2" "$GH_BASE/$1"; }   # $1 = Release 附件名（仅文件名）
hf_up()   { curl -fL --retry 3 -o "$3" "$HF_ENDPOINT/$1/resolve/main/$2"; }  # 上游标准模型（走镜像）

# ---- GitHub Release 附件（< 2GB，附件名只能是纯文件名）----
gh_get groundingdino_swint_ogc.pth            "$PTH/groundingdino_swint_ogc.pth"
gh_get best_coco_bbox_mAP_epoch_12.pth        "$PTH/best_coco_bbox_mAP_epoch_12.pth"
gh_get best_com_base.pth                      "$PTH/best_com_base.pth"
gh_get maskrcnn_resnet50_fpn_v2-tail.pth      "$PTH/maskrcnn_resnet50_fpn_v2-tail.pth"
gh_get maskrcnn_resnet50_fpn_v2-wound.pth     "$PTH/maskrcnn_resnet50_fpn_v2-wound.pth"
gh_get swin_tiny_patch4_window7_224.pth       "$PTH/swin_tiny_patch4_window7_224.pth"
gh_get catboost_full_model.pkl                "$PTH/catboost_full_model.pkl"
gh_get belly_train_tree.pkl                   "$PTH/tree_model/belly_train_tree.pkl"

# 大权重（用纯文件名作为 Release 附件名）
gh_get pytorch_model.bin                      "$PTH/bert-base-uncased/pytorch_model.bin"
gh_get model.safetensors                      "$PTH/cache/vit-base/model.safetensors"
gh_get rfdetr-seg-medium-b64-fp16.engine      "$PTH/output_onnx_dynamic/rfdetr-seg-medium-b64-fp16.engine"
gh_get rfdetr-seg-medium.onnx                 "$PTH/output_onnx_dynamic/rfdetr-seg-medium.onnx"

# BERT / ViT 的小配置文件从 HuggingFace 镜像获取（避免 Release 里 config.json 撞名）
hf_up google-bert/bert-base-uncased config.json           "$PTH/bert-base-uncased/config.json"
hf_up google-bert/bert-base-uncased tokenizer_config.json "$PTH/bert-base-uncased/tokenizer_config.json"
hf_up google-bert/bert-base-uncased tokenizer.json        "$PTH/bert-base-uncased/tokenizer.json"
hf_up google-bert/bert-base-uncased vocab.txt             "$PTH/bert-base-uncased/vocab.txt"
hf_up google/vit-base-patch16-224-in21k config.json             "$PTH/cache/vit-base/config.json"
hf_up google/vit-base-patch16-224-in21k preprocessor_config.json "$PTH/cache/vit-base/preprocessor_config.json"

# ---- 两个 SAM 权重（> 2GB，来自公开上游，无需上传）----
# SAM ViT-H：Meta 官方源
curl -fL --retry 3 -o "$PTH/sam_vit_h_4b8939.pth" \
  https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth
# SAM-HQ ViT-H：经 HuggingFace 镜像
hf_up lkeab/hq-sam sam_hq_vit_h.pth "$PTH/sam_hq_vit_h.pth"

# ---- 校验 ----
verify "$PTH/groundingdino_swint_ogc.pth"       3b3ca2563c77c69f651d7bd133e97139c186df06231157a64c507099c52bc799
verify "$PTH/best_coco_bbox_mAP_epoch_12.pth"   599c348a3100969c6e2732bca235c23109181a5e76bc88d984f8c85ef1f79868
verify "$PTH/best_com_base.pth"                 61ef6ef34abbd158263415cfc200f03191ad4b8d9a815c7495acb9aee75ea7c5
verify "$PTH/maskrcnn_resnet50_fpn_v2-tail.pth" 98d186614d5da7818841fcddb14c815b8dcb6bab07bf256f8c06841bef3aa1b4
verify "$PTH/maskrcnn_resnet50_fpn_v2-wound.pth" 2d1eb14582396b333c69ff15bef09ffa4e8b7181da8f816ba54dad58fb43910a
verify "$PTH/swin_tiny_patch4_window7_224.pth"  9f71c168d837d1b99dd1dc29e14990a7a9e8bdc5f673d46b04fe36fe15590ad3
verify "$PTH/catboost_full_model.pkl"           91205df496057b6099d04bdc1ea8a0503494e606162ce5ef5154c1c2e8bacccb
verify "$PTH/tree_model/belly_train_tree.pkl"   e36bf4c0ff67c2e96a7306d4ce12d11c8832bfec5bba204d1c26b33b588f6288
verify "$PTH/bert-base-uncased/pytorch_model.bin" 097417381d6c7230bd9e3557456d726de6e83245ec8b24f529f60198a67b203a
verify "$PTH/cache/vit-base/model.safetensors"  fd4e1169c7aa6c2dbfa8a6448be13b35abc0ee256190857c90009d12c094619b
verify "$PTH/output_onnx_dynamic/rfdetr-seg-medium-b64-fp16.engine" 325a721364d4c2e49424132990e969286cedbe4464cc4ec7ebbb581110a695a8
verify "$PTH/sam_vit_h_4b8939.pth"              a7bf3b02f3ebf1267aba913ff637d9a2d5c33d3173bb679e46d9f338c26f262e
verify "$PTH/sam_hq_vit_h.pth"                  a7ac14a085326d9fa6199c8c698c4f0e7280afdbb974d2c4660ec60877b45e35

echo "全部模型下载并校验完成。"
```

### 4.3 直接用官方上游地址（备选）

BERT、ViT 是标准 HuggingFace 模型，也可用官方地址代替 Release 镜像（校验和可能与上表不同，`vit_completeness` 按目录加载，可用即可）。国内把 `huggingface.co` 换成 `hf-mirror.com` 即可：

```bash
HF=https://hf-mirror.com   # 能直连官方时可改为 https://huggingface.co

# BERT 权重 + 配置
for f in pytorch_model.bin config.json tokenizer_config.json tokenizer.json vocab.txt; do
  curl -fL -o "algorithms/pth/bert-base-uncased/$f" \
    "$HF/google-bert/bert-base-uncased/resolve/main/$f"
done

# ViT-base 离线缓存
mkdir -p algorithms/pth/cache/vit-base
for f in config.json preprocessor_config.json model.safetensors; do
  curl -fL -o "algorithms/pth/cache/vit-base/$f" \
    "$HF/google/vit-base-patch16-224-in21k/resolve/main/$f"
done
```

SAM / SAM-HQ 官方来源：

- SAM：`https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth`（一般可直连）
- SAM-HQ：`https://hf-mirror.com/lkeab/hq-sam/resolve/main/sam_hq_vit_h.pth`（国内镜像）

---

## 5. 准备运行环境

### 5.1 创建 Conda 环境（Python 3.10）

```bash
conda create -n full python=3.10 -y
conda activate full
```

安装后端依赖：

```bash
cd backend
pip install -e .              # 读取 backend/pyproject.toml
pip install psycopg2-binary rq uvicorn[standard]   # 如未包含
cd ..
```

安装算法依赖（PyTorch 等，按 CUDA 版本选择）：

```bash
# 以 CUDA 12.8 为例
pip install torch==2.7.0 torchvision --index-url https://download.pytorch.org/whl/cu128
pip install transformers==4.38.2 ultralytics opencv-python \
            scikit-learn pandas matplotlib scipy statsmodels \
            catboost xgboost lightgbm joblib supervision python-docx reportlab numpy
```

如需使用 RF-DETR 的 TensorRT 推理，另装 `tensorrt==10.8.*`（见第 6 节）。

### 5.2 安装前端依赖（需要同环境下的 Node ≥ 20.19）

```bash
cd frontend
npm install
cd ..
```

### 5.3 启动 PostgreSQL 与 Redis，并建库

```bash
sudo systemctl start postgresql redis-server

# 创建数据库用户与库（与默认 DATABASE_URL 一致）
sudo -u postgres psql -c "CREATE USER fish WITH PASSWORD 'fish';"
sudo -u postgres psql -c "CREATE DATABASE fishdb OWNER fish;"

# 建表（后端启动时也会 create_all，此步可手动确认）
cd backend
FISH_PROJECT_ROOT="$(cd .. && pwd)" \
FISH_DATA_ROOT="$(cd .. && pwd)/data" \
PYTHONPATH=. python tools/create_tables.py
cd ..
```

默认连接串：

```text
DATABASE_URL=postgresql://fish:fish@127.0.0.1:5432/fishdb
REDIS_URL=redis://127.0.0.1:6379/0
```

如使用其他地址，启动前 `export DATABASE_URL=... REDIS_URL=...` 覆盖即可。

---

## 6. TensorRT engine 说明（GPU 更换时必读）

`algorithms/pth/output_onnx_dynamic/rfdetr-seg-medium-b64-fp16.engine` 是针对
**RTX 5060 Ti + TensorRT 10.8 + CUDA 12.8** 构建的固定 batch=64 FP16 引擎，
**TensorRT engine 与 GPU 架构、TensorRT 版本强绑定，不能跨机器通用**。

如果你的 GPU 或 TensorRT 版本不同，请先下载 `rfdetr-seg-medium.onnx`，再用仓库自带工具重建：

```bash
conda activate full
python algorithms/tools/build_rfdetr_trt108.py \
  --onnx algorithms/pth/output_onnx_dynamic/rfdetr-seg-medium.onnx \
  --engine algorithms/pth/output_onnx_dynamic/rfdetr-seg-medium-b64-fp16.engine \
  --workspace-gib 8
```

---

## 7. 启动

### 7.1 一键启动（开发版）

```bash
# 默认 Conda 环境名 full；如不同请指定
CONDA_ENV_DIR=/home/<user>/miniconda3/envs/full ./start_dev.sh
# 或
CONDA_ENV_NAME=full ./start_dev.sh
```

脚本会依次启动 PostgreSQL、Redis、MediaMTX、RQ Worker、Upgrade Manager、FastAPI、Vite。

启动成功后访问：

- 前端：`http://127.0.0.1:5173`
- 后端接口文档：`http://127.0.0.1:8000/docs`
- MediaMTX：RTMP `rtmp://127.0.0.1:1935/fish_camera`、HLS `http://127.0.0.1:8888/fish_camera/index.m3u8`

停止：

```bash
./stop_dev.sh
```

后端首次启动会执行建表、启动迁移、队列恢复和摄像头恢复，可能超过 30 秒，脚本会等待 `/docs` 就绪。

### 7.2 摄像头相关环境变量

```text
# 树莓派服务地址（树莓派侧 API）
FISH_PI_BASE_URL=http://raspberrypi.local:9000

# 给树莓派的推流目标（必须是树莓派能访问到的主机局域网 IP）
# 自动模式会跟随主机网卡地址生成
FISH_CAMERA_AUTO_PUSH_URL=1
FISH_CAMERA_NETWORK_INTERFACE=enp3s0

# 后端本机从 MediaMTX 读取流
FISH_CAMERA_READ_URL=rtmp://127.0.0.1:1935/fish_camera

# 断流重连宽限（秒）
FISH_CAMERA_RECONNECT_GRACE_SECONDS=180
```

无树莓派/摄像头时，前端与服务仍可启动，但正式录制需要真实的 MediaMTX 推流。

---

## 8. 为什么模型不放进 Git（维护者参考）

大模型权重和构建产物不应提交到仓库，否则 GitHub 会因单文件 >100MB 拒推。仓库根目录应维护如下 `.gitignore`：

```gitignore
# 运行数据与发布产物
data/
update/dist/
build/
dist/
desktop/dist/
*.tar.xz

# 依赖
*/node_modules/

# 平台垃圾
$RECYCLE.BIN/

# 模型权重（改由 Release / HuggingFace 下载，见第 4 节）
algorithms/pth/*.pth
algorithms/pth/*.pkl
algorithms/pth/bert-base-uncased/
algorithms/pth/cache/
algorithms/pth/output_onnx_dynamic/
```

### 8.1 创建 GitHub Release 并上传模型（< 2GB 文件）

1. 打开仓库 `https://github.com/yangxiaoxiao998/fish_quality_dashboard`，右侧点 **Releases** → **Create a new release**（或访问 `/releases/new`）。
2. **Choose a tag** 输入 `models-v1.0.0`，选 “Create new tag on publish”，目标分支 `main`。
3. **Release title** 填 `Model weights v1.0.0`，描述随意。
4. 在 **Attach binaries** 区域拖入第 4.1 节表中 “GitHub Release” 来源的文件（**单个文件上限 2 GiB**）。附件名就是文件名本身（不含目录），因此下载脚本按纯文件名获取。
5. 点 **Publish release**。

命令行等价操作（`gh` 2.x，已 `gh auth login`）。`gh release create` 默认要求当前目录是 git 仓库，**若目录未初始化 git，用 `-R` 指定仓库即可**：

```bash
gh release create models-v1.0.0 -R yangxiaoxiao998/fish_quality_dashboard \
  algorithms/pth/groundingdino_swint_ogc.pth \
  algorithms/pth/best_coco_bbox_mAP_epoch_12.pth \
  algorithms/pth/best_com_base.pth \
  algorithms/pth/maskrcnn_resnet50_fpn_v2-tail.pth \
  algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth \
  algorithms/pth/swin_tiny_patch4_window7_224.pth \
  algorithms/pth/catboost_full_model.pkl \
  algorithms/pth/tree_model/belly_train_tree.pkl \
  algorithms/pth/bert-base-uncased/pytorch_model.bin \
  algorithms/pth/cache/vit-base/model.safetensors \
  algorithms/pth/output_onnx_dynamic/rfdetr-seg-medium-b64-fp16.engine \
  algorithms/pth/output_onnx_dynamic/rfdetr-seg-medium.onnx \
  --title "Model weights v1.0.0" --notes "release model assets"
```

> 这些文件的 basename（`pytorch_model.bin`、`model.safetensors`、`belly_train_tree.pkl`、`rfdetr-*.engine/.onnx`）互不重复，`gh` 会用 basename 作为附件名，与第 4.2 节脚本一致。
>
> **私有仓库注意**：仓库为 Private 时，匿名用户既不能 `git clone`，也不能用 `curl` 下载 Release 附件。对外部署请先把仓库改为 Public；仅在内部使用时，下载需改用 `gh release download models-v1.0.0 -R <owner>/<repo> -D algorithms/pth`（需登录）。

### 8.2 两个 SAM 权重无需上传

`sam_vit_h_4b8939.pth`（SAM）和 `sam_hq_vit_h.pth`（SAM-HQ）都是**公开模型**，超过 GitHub Release 的 2 GiB 单文件上限，因此**不放 Release**，也不需要在 HuggingFace 建仓上传。部署时直接按第 4 节脚本从公开源获取：

- SAM：Meta 官方 `https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth`
- SAM-HQ：HuggingFace 镜像 `https://hf-mirror.com/lkeab/hq-sam/resolve/main/sam_hq_vit_h.pth`

> 若你的网络环境已能直连 `huggingface.co`，把镜像域名替换为 `huggingface.co` 即可。
>
> 说明：曾尝试把 SAM 上传到个人 HuggingFace 仓库，但这需要能访问 huggingface.co 且无实际收益——公开源已提供同一份权重（SHA256 与本地一致）。如上表所列，下载后仍会做 SHA256 校验。

发布完成后，第 4.2 节脚本即可下载全部模型（GitHub Release + 公开上游）；若标签或仓库名有变，只改脚本顶部变量。

---

## 9. 常见问题

- **`git push` 报文件过大**：确认没有把 `algorithms/pth/*.pth`、`data/`、`node_modules/` 提交进去，参照第 8 节 `.gitignore`。
- **算法报模型缺失**：检查 `algorithms/pth/` 是否按第 4 节完整下载并通过 SHA256 校验。
- **ViT 完整性模型加载失败**：`algorithms/pth/cache/vit-base/` 需包含 `config.json`、`preprocessor_config.json` 和权重文件（`model.safetensors` 或 `pytorch_model.bin`）。
- **RF-DETR engine 反序列化失败**：GPU/TRT 版本与构建环境不一致，按第 6 节用 onnx 重建。
- **`start_dev.sh` 报找不到 Node/npm**：确保 Node 与 Python 在同一 Conda 环境，或显式设置 `CONDA_ENV_DIR`。
- **摄像头 `no stream`**：确认树莓派推送目标是主机局域网 IP（不是 `127.0.0.1`），并检查 MediaMTX `1935` 端口与防火墙。
- **后端起不来**：查看 `data/logs/dev/backend.log`，确认 PostgreSQL/Redis 已启动、`DATABASE_URL` 正确。
- **HuggingFace 连不上 / `hf auth login` 超时或 SSL 报错**：国内直连 `huggingface.co` 常被阻断或 DNS 污染。下载模型时用镜像 `HF_ENDPOINT=https://hf-mirror.com`（第 4.2 节脚本已默认）。**不需要登录 HuggingFace**——第 4 节所有模型都从公开源或镜像下载，不涉及个人账号。

---

开发阶段的详细业务说明、模块边界、算法流程和稳定性验收清单见 `PROJECT_CONTEXT.md`，修复记录见 `requireLog.md`。
