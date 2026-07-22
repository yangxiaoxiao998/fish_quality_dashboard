// src/types/fish.ts
// 第一阶段：算法最新字段加入；旧字段保留兼容。

export interface FishInVideo {
  fish_id: string;
  track_id: string | number;
  // 新字段
  fish_uid?: string;
  // 旧 bbox 保留
  bbox?: [number, number, number, number];
  status?: 'pending' | 'processing' | 'done' | 'incomplete' | 'failed';
}

export interface FishDetail {
  fish_id: string;
  // 新字段
  fish_uid?: string;
  track_id?: string | number;
  video_id: string;
  status: 'done' | 'incomplete' | 'failed' | 'processing';
  // 新图片字段（推荐使用）
  crop_image_path?: string;
  crop_image_url?: string;
  // 旧图片字段（保留兼容）
  corrected_image_url?: string;
  original_image_url?: string;
  bbox?: [number, number, number, number];
  // 新数值字段（推荐使用）
  body_length_cm?: number | null;
  body_length_no_tail_cm?: number | null;
  body_height_cm?: number | null;
  pred_weight_g?: number | null;
  surface_damage_ratio?: number | null;
  error_message?: string;
  // 旧数值字段（保留兼容）
  body_length?: number;
  body_length_without_tail?: number;
  body_height?: number;
  body_weight?: number;
  condition_factor?: number;
  surface_damage?: string;
  created_at?: string;
  detection_id?: string;
}

export interface VideoTaskStatus {
  video_id: string;
  status: 'queued' | 'processing' | 'done' | 'failed' | 'stopped' | 'recording' | 'interrupted';
  total_fish?: number;
  processed_fish?: number;
  progress?: number;
  fps?: number;
  queue_status?: string;
}

export interface StatsBucket {
  bucket_start: string;
  bucket_end: string;
  count: number;
  mean_body_length: number;
  mean_body_weight: number;
  mean_condition_factor: number;
  abnormal_rate: number;
}

export interface BatchFishDetailRequest {
  fish_ids: string[];
}
