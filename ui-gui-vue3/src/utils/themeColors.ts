export interface ThemeColors {
  primary: string
  accent: string
  muted: string
  borderGold: string
  glowStart: string
  glowEnd: string
}

function hslf(rr: number, gg: number, bb: number): [number, number, number] {
  const rf = rr / 255, gf = gg / 255, bf = bb / 255
  const mx = Math.max(rf, gf, bf), mn = Math.min(rf, gf, bf)
  let h = 0, s = 0
  const l = (mx + mn) / 2
  if (mx !== mn) {
    const d = mx - mn
    s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn)
    if (mx === rf) h = ((gf - bf) / d + (gf < bf ? 6 : 0)) * 60
    else if (mx === gf) h = ((bf - rf) / d + 2) * 60
    else h = ((rf - gf) / d + 4) * 60
  }
  return [Math.round(h), Math.round(s * 100), Math.round(l * 100)]
}

function hth(h: number, s: number, l: number): string {
  const sf = s / 100, lf = l / 100
  const c = (1 - Math.abs(2 * lf - 1)) * sf
  const x = c * (1 - Math.abs((h / 60) % 2 - 1))
  const m = lf - c / 2
  let rf = 0, gf = 0, bf = 0
  if (h < 60) { rf = c; gf = x }
  else if (h < 120) { rf = x; gf = c }
  else if (h < 180) { gf = c; bf = x }
  else if (h < 240) { gf = x; bf = c }
  else if (h < 300) { rf = x; bf = c }
  else { rf = c; bf = x }
  const th = (v: number) => Math.max(0, Math.min(255, Math.round((v + m) * 255))).toString(16).padStart(2, '0')
  return `#${th(rf)}${th(gf)}${th(bf)}`.toUpperCase()
}

export function buildThemeColors(hex: string): ThemeColors {
  const rr = parseInt(hex.slice(1, 3), 16)
  const gg = parseInt(hex.slice(3, 5), 16)
  const bb = parseInt(hex.slice(5, 7), 16)
  const [h, s, l] = hslf(rr, gg, bb)
  const accent = hth(h, s, Math.max(0, l - 12))
  const muted = hth(h, Math.max(0, s - 25), Math.max(0, l - 8))
  const borderGold = hth(h, Math.max(0, s - 15), Math.max(0, l - 30))
  return {
    primary: hex,
    accent,
    muted,
    borderGold,
    glowStart: `rgba(${rr},${gg},${bb},0.10)`,
    glowEnd: `rgba(${parseInt(borderGold.slice(1, 3), 16)},${parseInt(borderGold.slice(3, 5), 16)},${parseInt(borderGold.slice(5, 7), 16)},0.04)`,
  }
}
