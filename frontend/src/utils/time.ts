import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'

dayjs.extend(utc)
dayjs.extend(timezone)

export const CHINA_TZ = 'Asia/Shanghai'

function isDateOnly(value: unknown): boolean {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
}

export function formatChinaDateTime(value?: string | number | Date | null): string {
  if (!value) return '--'
  if (isDateOnly(value)) return `${value} 00:00`
  const d = dayjs(value)
  if (!d.isValid()) return String(value)
  return d.tz(CHINA_TZ).format('YYYY-MM-DD HH:mm')
}

export function formatChinaDate(value?: string | number | Date | null): string {
  if (!value) return '--'
  if (isDateOnly(value)) return String(value)
  const d = dayjs(value)
  if (!d.isValid()) return String(value)
  return d.tz(CHINA_TZ).format('YYYY-MM-DD')
}
