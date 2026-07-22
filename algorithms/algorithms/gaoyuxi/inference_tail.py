import os
import sys
from pathlib import Path

import torch
import numpy as np
import cv2
from PIL import Image
import torchvision.transforms as T
from torchvision.utils import draw_bounding_boxes, draw_segmentation_masks
from torchvision.models.detection import maskrcnn_resnet50_fpn_v2
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parents[2]
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.paths import TAIL_DETECTOR_CHECKPOINT  # noqa: E402


DEFAULT_CHECKPOINT = TAIL_DETECTOR_CHECKPOINT


class TailDetector:
    def __init__(self, checkpoint_path=None, device=None, num_classes=3):
        """
        初始化模型：只运行一次，加载权重
        """
        self.device = device if device else torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        print(f"Using device: {self.device}")

        ckpt_path = Path(checkpoint_path) if checkpoint_path else Path(DEFAULT_CHECKPOINT)

        # --- 模型构建 ---
        self.model = maskrcnn_resnet50_fpn_v2(weights=None)
        in_features_box = self.model.roi_heads.box_predictor.cls_score.in_features
        in_features_mask = self.model.roi_heads.mask_predictor.conv5_mask.in_channels
        dim_reduced = self.model.roi_heads.mask_predictor.conv5_mask.out_channels

        # 替换 Head
        self.model.roi_heads.box_predictor = FastRCNNPredictor(in_features_box, num_classes)
        self.model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, dim_reduced, num_classes)

        # --- 加载权重 ---
        if ckpt_path.exists():
            state_dict = torch.load(str(ckpt_path), map_location=self.device)
            self.model.load_state_dict(state_dict)
            print(f"模型权重加载成功: {ckpt_path}")
        else:
            raise FileNotFoundError(f"找不到权重文件: {ckpt_path}")

        self.model.to(self.device)
        self.model.eval()
        self.transform = T.ToTensor()

    def predict(self, image_np, out_dir, file_stem="result", threshold=0.5):
        """
        image_np: OpenCV 读取或处理后的图像 (H, W, 3) BGR 格式 (uint8)
        out_dir: 结果保存目录
        file_stem: 保存文件的文件名前缀
        """
        out_dir = Path(out_dir)
        os.makedirs(out_dir, exist_ok=True)

        # --- 关键点：OpenCV(BGR) 转 RGB ---
        # PyTorch 模型通常是用 RGB 训练的，而 OpenCV 默认是 BGR
        img_rgb = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
        img_tensor = self.transform(img_rgb).to(self.device) # [C,H,W], float32

        # --- 推理 ---
        with torch.no_grad():
            outputs = self.model([img_tensor])
        
        output = outputs[0]

        # --- 筛选结果 (Tail = Label 2) ---
        target_label_id = 2 
        scores_mask = output['scores'] > threshold
        label_mask = output['labels'] == target_label_id
        keep_mask = scores_mask & label_mask

        pred_boxes = output['boxes'][keep_mask].cpu()
        pred_scores = output['scores'][keep_mask].cpu()
        raw_masks = output['masks'][keep_mask] # [N, 1, H, W]

        # --- 可视化 ---
        img_uint8 = (img_tensor * 255).to(torch.uint8).cpu() # 回到 uint8 用于绘图

        if len(raw_masks) > 0:
            pred_masks_bool = (raw_masks[:, 0] > threshold).cpu()
            pred_masks_np = pred_masks_bool.numpy().astype(np.uint8)

            # 1. 绘制 Mask
            img_with_masks = draw_segmentation_masks(img_uint8, masks=pred_masks_bool, alpha=0.5, colors=["red"] * len(pred_masks_bool))
            
            # 2. 绘制 Box
            labels_text = [f"tail {s:.2f}" for s in pred_scores]
            img_final = draw_bounding_boxes(img_with_masks, boxes=pred_boxes, labels=labels_text, colors=["red"] * len(pred_boxes), width=2)

            if len(pred_masks_np) > 0 and len(pred_scores) > 0:
                # 找到置信度最高的目标索引
                best_idx = torch.argmax(pred_scores).item()
                best_mask = pred_masks_np[best_idx]
                best_score = pred_scores[best_idx]
                mask_img = Image.fromarray(best_mask * 255)
                save_path = out_dir / f"{file_stem}_mask_best.png"
                mask_img.save(save_path)
                
                print(f"已保存置信度最高的 Mask (Score: {best_score:.4f}) -> {save_path}")
        else:
            print(f"[{file_stem}] 未检测到目标。")
            img_final = img_uint8

        # 保存最终合成图
        out_image_path = out_dir / f"{file_stem}_pred.png"
        Image.fromarray(img_final.permute(1, 2, 0).numpy()).save(out_image_path)
        return out_image_path