// 模拟数据

export interface FishRecord {
  fish_id: string
  video_id: string
  created_at: string
  original_image_url: string
  corrected_image_url: string
  body_length?: number
  body_height?: number
  body_weight?: number
  condition_factor?: number
  height_length_ratio?: number
  surface_damage: string
  confidence?: number
  status: 'normal' | 'abnormal'
}

export interface VideoRecord {
  id: string
  uploadTime: string
  duration: string
  fishCount: number
  status: 'completed' | 'processing' | 'pending'
}

export interface ImageRecord {
  id: string
  url: string
  name: string
  time: string
  fish_id: string
  video_id: string
}

export function generateFishData(count: number = 50): FishRecord[] {
  return Array.from({ length: count }, (_, i) => ({
    fish_id: `F20260309${String(i + 1).padStart(4, '0')}`,
    video_id: `V20260309${String(Math.floor(i / 10) + 1).padStart(3, '0')}`,
    created_at: `2026-03-09 ${String(Math.floor(i / 2) + 9).padStart(2, '0')}:${String((i * 3) % 60).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}`,
    original_image_url: '/src/assets/images/test-thumb.jpg',
    corrected_image_url: '/src/assets/images/test-thumb.jpg',
    body_length: 20 + Math.random() * 15,
    body_height: 5 + Math.random() * 3,
    body_weight: 100 + Math.random() * 200,
    condition_factor: 1.0 + Math.random() * 0.3,
    height_length_ratio: 0.2 + Math.random() * 0.1,
    surface_damage: Math.random() > 0.7 ? (Math.random() > 0.5 ? '轻微' : '严重') : '无',
    confidence: 0.85 + Math.random() * 0.15,
    status: Math.random() > 0.2 ? 'normal' : 'abnormal'
  }))
}

export function generateVideoData(count: number = 20): VideoRecord[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `V202603${String(i + 1).padStart(3, '0')}`,
    uploadTime: `2026-03-0${(i % 9) + 1} ${String((i % 12) + 8).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}`,
    duration: `${Math.floor(Math.random() * 5)}:${String(Math.floor(Math.random() * 60)).padStart(2, '0')}`,
    fishCount: Math.floor(Math.random() * 50) + 10,
    status: Math.random() > 0.3 ? 'completed' : Math.random() > 0.5 ? 'processing' : 'pending'
  }))
}

export function generateImageData(count: number = 50): ImageRecord[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `img_${i}`,
    url: '/src/assets/images/test-thumb.jpg',
    name: `fish_${String(i + 1).padStart(4, '0')}.jpg`,
    time: `2026-03-0${(i % 9) + 1} ${String((i % 12) + 8).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}`,
    fish_id: `F20260309${String(i + 1).padStart(4, '0')}`,
    video_id: `V20260309${String(Math.floor(i / 10) + 1).padStart(3, '0')}`
  }))
}

export const fishData = generateFishData(50)
export const videoData = generateVideoData(20)
export const originalImagesData = generateImageData(50)
export const correctedImagesData = generateImageData(50)
