// src/services/api.ts
//
// 第一阶段：旧 /api/videos 上传后，会由 VideoWall 组件内部调用
// /api/algorithm/videos 触发真实算法 CLI。
//
// 这里只保留兼容层，让旧 admin 页面继续能读 video_task 与 fish 列表。
// 新算法结果走 /api/algorithm/videos/{video_id}/result。
import axios from 'axios';
import type { FishDetail, StatsBucket, FishInVideo, VideoTaskStatus, BatchFishDetailRequest } from '@/types/fish';
import {
  getAlgorithmVideoResult,
  getAlgorithmErrorConclusion,
  runAlgorithmVideo,
} from '@/api/algorithm';
import { getFishImageUrl, getFishLengthCm, getFishNoTailLengthCm, getFishHeightCm, getFishWeightG, getFishConditionFactor, getFishErrorMessage, getFishDisplayId } from '@/utils/fishFields';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

const apiClient = axios.create({
  baseURL: API_BASE_URL,
});

// 兼容旧业务：上传视频。
// 注意：第一阶段后端 /api/videos 仍是 upload + enqueue 旧 mock 路径。
// VideoWall 组件会在 upload 成功后调用 runAlgorithmVideo 触发真实算法 CLI。
export const uploadVideo = async (file: File): Promise<{
  video_id: string;
  status: string;
  task_id: string;
  created_at: string;
  playback_url?: string;
  source_path?: string;
  video_path?: string;
}> => {
  const formData = new FormData();
  formData.append('file', file);
  const response = await apiClient.post('/api/videos', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

// 直接以 input_video 调用真实算法 CLI。第一阶段主推入口。
export async function runAlgorithmVideoByPath(input_video: string, video_fps = 25) {
  const resp = await runAlgorithmVideo({
    input_video,
    camera_id: 'default',
    device: 'cuda',
    video_fps,
    overwrite: true,
    import_to_db: true,
  });
  return resp.data;
}

// 读取新算法结果。返回的 fish 列表字段已是 cm / no_tail_cm / pred_weight_g 等。
export async function getAlgorithmResult(videoId: string) {
  const resp = await getAlgorithmVideoResult(videoId)
  const data = resp.data?.data
  const fishes = (data?.fish || []).map((f) => ({
    fish_id: f.fish_uid ?? `track_${f.track_id}`,
    fish_uid: f.fish_uid,
    track_id: f.track_id,
    video_id: f.video_id,
    crop_image_url: f.crop_image_url ?? '',
    crop_image_path: f.crop_image_path,
    body_length_cm: f.body_length_cm,
    body_length_no_tail_cm: f.body_length_no_tail_cm,
    body_height_cm: f.body_height_cm,
    pred_weight_g: f.pred_weight_g,
    condition_factor: f.condition_factor,
    surface_damage_ratio: f.surface_damage_ratio,
    error_message: f.error_message,
    status: f.status,
    // 兼容：补一份旧字段，方便旧页面
    body_length: getFishLengthCm(f) as any,
    body_length_without_tail: getFishNoTailLengthCm(f) as any,
    body_height: getFishHeightCm(f) as any,
    body_weight: getFishWeightG(f) as any,
  }))
  return {
    video_id: data?.video_id ?? videoId,
    status: data?.status,
    summary: data?.summary,
    grounded_video_url: data?.grounded_video_url,
    measurement_csv_url: data?.measurement_csv_url,
    fish: fishes,
  }
}

export async function getAlgorithmErrorText(videoId: string): Promise<string> {
  const resp = await getAlgorithmErrorConclusion(videoId)
  return resp.data?.text || ''
}

// 兼容旧 video_task / fishes 接口（旧路由保留）
export const getVideoStatus = async (videoId: string): Promise<VideoTaskStatus> => {
  const response = await apiClient.get(`/api/videos/${videoId}`);
  return response.data;
};

export const stopVideoTask = async (videoId: string): Promise<{ video_id: string; status: string; }> => {
  const response = await apiClient.post(`/api/videos/${videoId}/stop`);
  return response.data;
};

export const getFishesInVideo = async (videoId: string): Promise<FishInVideo[]> => {
  const response = await apiClient.get(`/api/videos/${videoId}/fishes`);
  return response.data;
};

export const getFishDetail = async (fishId: string): Promise<FishDetail> => {
  const response = await apiClient.get(`/api/fishes/${fishId}`);
  return response.data;
};

export const batchGetFishDetails = async (request: BatchFishDetailRequest): Promise<Record<string, FishDetail>> => {
  const response = await apiClient.post('/api/fishes/batch', request);
  return response.data;
};

export const getStats = async (params: { start_time?: string; end_time?: string; interval?: string; video_id?: string }): Promise<StatsBucket[]> => {
  const response = await apiClient.get('/api/stats', { params });
  return response.data;
};

export const getWorkerStatus = async (): Promise<any> => {
  const response = await apiClient.get('/api/worker/status');
  return response.data;
};

export const healthCheck = async (): Promise<any> => {
  const response = await apiClient.get('/health');
  return response.data;
};
