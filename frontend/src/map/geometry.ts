import type { Pt } from './types'

export function dist(a: Pt, b: Pt): number { return Math.hypot(a.x - b.x, a.y - b.y) }

/** Point at fraction t (0..1, clamped) of the polyline's length. */
export function pointAt(points: Pt[], t: number): Pt {
  if (points.length === 0) return { x: 0, y: 0 }
  if (points.length === 1) return points[0]
  const tt = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0))
  const total = points.slice(1).reduce((n, p, i) => n + dist(points[i], p), 0)
  let left = tt * total
  for (let i = 1; i < points.length; i++) {
    const d = dist(points[i - 1], points[i])
    if (left <= d && d > 0) {
      const f = left / d
      return { x: points[i - 1].x + (points[i].x - points[i - 1].x) * f, y: points[i - 1].y + (points[i].y - points[i - 1].y) * f }
    }
    left -= d
  }
  return points[points.length - 1]
}

export function inPolygon(p: Pt, poly: Pt[]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i], b = poly[j]
    if ((a.y > p.y) !== (b.y > p.y) && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inside = !inside
  }
  return inside
}

export const pts = (ps: Pt[]) => ps.map((p) => `${p.x},${p.y}`).join(' ')
const r1 = (n: number) => Math.round(n * 10) / 10
export const fmt = (p: Pt) => `(${r1(p.x)}, ${r1(p.y)})`
