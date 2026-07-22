/**
 * frontend/src/utils/fishFields.ts
 *
 * 算法新字段兼容读取 helper。
 *
 * 后端新字段：
 *   body_length_cm / body_length_no_tail_cm / body_height_cm /
 *   pred_weight_g / surface_damage_ratio / error_message /
 *   crop_image_path / fish_uid
 *
 * 旧字段兼容：body_length / body_height / body_weight / surface_damage_percent /
 *   error_msg / original_image_url / corrected_image_url。
 *
 * 后续阶段清理旧数据后，可以只保留新字段。
 */
export function valueOrFallback<T>(
  primary: T | null | undefined,
  fallback: T | null | undefined
): T | null | undefined {
  return primary ?? fallback
}

export function getFishLengthCm(fish: any): number | null | undefined {
  if (!fish) return null
  return fish.body_length_cm ?? fish.body_length ?? null
}

export function getFishNoTailLengthCm(fish: any): number | null | undefined {
  if (!fish) return null
  return (
    fish.body_length_no_tail_cm ?? fish.body_length_without_tail ?? null
  )
}

export function getFishHeightCm(fish: any): number | null | undefined {
  if (!fish) return null
  return fish.body_height_cm ?? fish.body_height ?? null
}

export function getFishWeightG(fish: any): number | null | undefined {
  if (!fish) return null
  return fish.pred_weight_g ?? fish.body_weight ?? null
}

export function getFishConditionFactor(fish: any): number | null | undefined {
  if (!fish) return null
  return fish.condition_factor ?? null
}

export function getFishErrorMessage(fish: any): string | null {
  if (!fish) return null
  return fish.error_message ?? fish.error_msg ?? null
}

// 引入 normalizeImageUrl 用于把 data/results/... 之类路径转 /static/...
import { normalizeImageUrl } from '@/utils/reportPath'

export function getFishDisplayId(fish: any): string | number | null {
  if (!fish) return null
  if (fish.display_fish_id !== undefined && fish.display_fish_id !== null) {
    return fish.display_fish_id
  }
  if (fish.track_id !== undefined && fish.track_id !== null) {
    return fish.track_id
  }
  if (fish.fish_uid) return fish.fish_uid
  return fish.fish_id ?? null
}

export function getFishImageUrl(fish: any): string {
  if (!fish) return ''
  // 优先级：
  // 1. crop_image_url (浏览器路径，e.g. /static/results/...)
  // 2. crop_image_path (算法端原路径，e.g. data/results/... /home/dsl/...)
  // 3. corrected_image_url / original_image_url (兼容旧字段)
  const raw =
    fish.crop_image_url ??
    fish.crop_image_path ??
    fish.corrected_image_url ??
    fish.original_image_url ??
    ''
  return normalizeImageUrl(raw)
}

export function getFishDamageRatio(fish: any): number | null {
  if (!fish) return null
  if (fish.surface_damage_ratio !== undefined && fish.surface_damage_ratio !== null) {
    const v = Number(fish.surface_damage_ratio)
    return Number.isFinite(v) ? v : null
  }
  if (fish.surface_damage_percent !== undefined && fish.surface_damage_percent !== null) {
    const v = Number(fish.surface_damage_percent) / 100
    return Number.isFinite(v) ? v : null
  }
  return null
}

export function getFishDamagePercent(fish: any, digits = 2): string {
  const ratio = getFishDamageRatio(fish)
  if (ratio === null) return '--'
  return `${(ratio * 100).toFixed(digits)}%`
}

export function getFishSurfaceDamageText(fish: any): string {
  if (!fish) return '无'
  const text = fish.surface_damage
  if (text && String(text).length > 0 && String(text) !== '无') {
    return String(text)
  }
  const ratio = getFishDamageRatio(fish)
  if (ratio === null) return '无'
  if (ratio <= 0) return '无'
  if (ratio < 0.03) return '轻微'
  if (ratio < 0.1) return '中等'
  return '严重'
}

export function fmtFishNumber(
  v: number | null | undefined,
  digits = 2
): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--'
  return Number(v).toFixed(digits)
}
