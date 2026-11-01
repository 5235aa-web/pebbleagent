import { reactive, watch } from 'vue'

/* Notification sounds, synthesised rather than shipped as files.

   Five voices, all built from oscillators so there is nothing to download and no
   binary asset to keep in sync. Each one is deliberately long enough to be
   recognisable across a room (1.9-2.8 s) and shaped so it does not click at
   either edge: a short linear attack, an exponential tail. */

export type SoundId =
  | 'piano-dawn'
  | 'guitar-pluck'
  | 'violin-swell'
  | 'marimba-glass'
  | 'harp-fall'
  | 'silent'

export type NotifyKind = 'finished' | 'question' | 'permission'

export interface NotifySettings {
  /* Two independent picks: a finished reply and a question waiting on you are
     different events, so they get different voices. */
  soundDone: SoundId
  soundAsk: SoundId
  volume: number
  alerts: { desktop: boolean; permission: boolean; finished: boolean }
  when: 'always' | 'unfocused'
}

const KEY = 'pebble.notify'

const DEFAULTS: NotifySettings = {
  soundDone: 'piano-dawn',
  soundAsk: 'violin-swell',
  volume: 62,
  alerts: { desktop: false, permission: true, finished: true },
  when: 'unfocused'
}

function load(): NotifySettings {
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return { ...DEFAULTS, alerts: { ...DEFAULTS.alerts } }
    const parsed = JSON.parse(raw) as Partial<NotifySettings> & { sound?: SoundId }
    // Older builds stored a single `sound`; seed both picks from it once.
    const legacy = parsed.sound
    return {
      soundDone: (parsed.soundDone as SoundId) || legacy || DEFAULTS.soundDone,
      soundAsk: (parsed.soundAsk as SoundId) || legacy || DEFAULTS.soundAsk,
      volume:
        typeof parsed.volume === 'number'
          ? Math.max(0, Math.min(100, parsed.volume))
          : DEFAULTS.volume,
      alerts: { ...DEFAULTS.alerts, ...(parsed.alerts ?? {}) },
      when: parsed.when === 'always' ? 'always' : 'unfocused'
    }
  } catch {
    return { ...DEFAULTS, alerts: { ...DEFAULTS.alerts } }
  }
}

export const notifySettings = reactive<NotifySettings>(load())

watch(
  notifySettings,
  () => {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(notifySettings))
    } catch {
      /* quota or private mode: the setting simply does not persist */
    }
  },
  { deep: true }
)

/* ---------------- synthesis ---------------- */

let ctx: AudioContext | null = null

function audio(): AudioContext | null {
  if (typeof window === 'undefined') return null
  if (!ctx) {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return null
    ctx = new Ctor()
  }
  // Browsers start the context suspended until a gesture; resume is a no-op once running.
  if (ctx.state === 'suspended') void ctx.resume()
  return ctx
}

interface Note {
  freq: number
  at: number
  dur: number
  gain: number
  type: OscillatorType
  /* A second oscillator a few cents away turns a plain tone into a bowed or
     plucked one; zero keeps it pure. */
  detune?: number
  filter?: { freq: number; q: number }
  /* Slow pitch movement, used by the bowed voice. */
  vibrato?: { rate: number; depth: number }
}

function voice(ac: AudioContext, out: GainNode, n: Note): void {
  const t0 = ac.currentTime + n.at
  const env = ac.createGain()
  const attack = Math.min(0.06, n.dur * 0.12)
  env.gain.setValueAtTime(0.0001, t0)
  env.gain.linearRampToValueAtTime(n.gain, t0 + attack)
  env.gain.exponentialRampToValueAtTime(0.0001, t0 + n.dur)

  let node: AudioNode = env
  if (n.filter) {
    const filter = ac.createBiquadFilter()
    filter.type = 'lowpass'
    filter.frequency.value = n.filter.freq
    filter.Q.value = n.filter.q
    env.connect(filter)
    node = filter
  }
  node.connect(out)

  const oscillators: OscillatorNode[] = []
  const build = (detune: number) => {
    const osc = ac.createOscillator()
    osc.type = n.type
    osc.frequency.value = n.freq
    osc.detune.value = detune
    osc.connect(env)
    oscillators.push(osc)
  }
  build(0)
  if (n.detune) build(n.detune)

  if (n.vibrato) {
    const lfo = ac.createOscillator()
    const amount = ac.createGain()
    lfo.frequency.value = n.vibrato.rate
    amount.gain.value = n.vibrato.depth
    lfo.connect(amount)
    oscillators.forEach(o => amount.connect(o.detune))
    lfo.start(t0)
    lfo.stop(t0 + n.dur + 0.1)
  }

  oscillators.forEach(o => o.start(t0))
  oscillators.forEach(o => o.stop(t0 + n.dur + 0.05))
}

const C4 = 261.63
const E4 = 329.63
const G4 = 392.0
const A4 = 440.0
const B4 = 493.88
const C5 = 523.25
const E5 = 659.25
const G5 = 783.99
const C6 = 1046.5
const E6 = 1318.51
const G6 = 1567.98

interface SoundSpec {
  id: SoundId
  name: string
  blurb: string
  build: (ac: AudioContext, out: GainNode) => void
}

/* Warm struck string: the fundamental plus a quiet octave, long tail. */
const piano = (ac: AudioContext, out: GainNode) => {
  const notes = [C4, E4, G4, C5]
  notes.forEach((freq, i) => {
    voice(ac, out, {
      freq,
      at: i * 0.13,
      dur: 2.4 - i * 0.15,
      gain: 0.5 - i * 0.06,
      type: 'triangle',
      detune: 6,
      filter: { freq: 2600, q: 0.7 }
    })
    voice(ac, out, { freq: freq * 2, at: i * 0.13, dur: 1.4 - i * 0.1, gain: 0.1, type: 'sine' })
  })
}

/* Plucked nylon: sawtooth under a low pass, staggered like a rolled chord. */
const guitar = (ac: AudioContext, out: GainNode) => {
  const notes = [E4, G4, B4, E5, B4]
  notes.forEach((freq, i) => {
    voice(ac, out, {
      freq,
      at: i * 0.115,
      dur: 1.9 - i * 0.12,
      gain: 0.42 - i * 0.04,
      type: 'sawtooth',
      detune: 9,
      filter: { freq: 1500, q: 1.4 }
    })
  })
}

/* Bowed: slow swell, two detuned saws and a gentle vibrato instead of an attack. */
const violin = (ac: AudioContext, out: GainNode) => {
  voice(ac, out, {
    freq: A4,
    at: 0,
    dur: 2.8,
    gain: 0.34,
    type: 'sawtooth',
    detune: 14,
    filter: { freq: 2100, q: 0.9 },
    vibrato: { rate: 5.2, depth: 7 }
  })
  voice(ac, out, {
    freq: E5,
    at: 0.32,
    dur: 2.4,
    gain: 0.24,
    type: 'sawtooth',
    detune: -12,
    filter: { freq: 2400, q: 0.9 },
    vibrato: { rate: 5.6, depth: 6 }
  })
  voice(ac, out, {
    freq: C5,
    at: 0.64,
    dur: 2.1,
    gain: 0.18,
    type: 'sawtooth',
    detune: 8,
    filter: { freq: 1900, q: 0.9 },
    vibrato: { rate: 5.0, depth: 5 }
  })
}

/* Mallet on glass: pure sines in a widening stack, each shorter than the last. */
const marimba = (ac: AudioContext, out: GainNode) => {
  const notes = [C5, G5, C6, E6, G6]
  notes.forEach((freq, i) => {
    voice(ac, out, { freq, at: i * 0.14, dur: 2.2 - i * 0.22, gain: 0.4 - i * 0.05, type: 'sine' })
    voice(ac, out, { freq: freq * 3, at: i * 0.14, dur: 0.7, gain: 0.05, type: 'sine' })
  })
}

/* Harp cascade: a falling run, every note left ringing. */
const harp = (ac: AudioContext, out: GainNode) => {
  const notes = [G5, E5, C5, G4, E4, C4]
  notes.forEach((freq, i) => {
    voice(ac, out, {
      freq,
      at: i * 0.1,
      dur: 2.5 - i * 0.16,
      gain: 0.36,
      type: 'triangle',
      detune: 5,
      filter: { freq: 3200, q: 0.6 }
    })
  })
}

export const SOUNDS: SoundSpec[] = [
  { id: 'piano-dawn', name: 'piano dawn', blurb: 'rising major triad · 2.4 s', build: piano },
  { id: 'guitar-pluck', name: 'guitar pluck', blurb: 'nylon arpeggio · 1.9 s', build: guitar },
  { id: 'violin-swell', name: 'violin swell', blurb: 'bowed fifth, slow · 2.8 s', build: violin },
  { id: 'marimba-glass', name: 'marimba glass', blurb: 'mallet stack · 2.2 s', build: marimba },
  { id: 'harp-fall', name: 'harp fall', blurb: 'falling run · 2.5 s', build: harp },
  { id: 'silent', name: 'silent', blurb: 'no alert', build: () => {} }
]

export function playSound(id?: SoundId): void {
  const spec = SOUNDS.find(s => s.id === (id ?? notifySettings.soundDone))
  if (!spec || spec.id === 'silent') return
  const ac = audio()
  if (!ac) return
  const master = ac.createGain()
  // Cap the ceiling: these are alerts, not media playback.
  master.gain.value = (Math.max(0, Math.min(100, notifySettings.volume)) / 100) * 0.5
  master.connect(ac.destination)
  spec.build(ac, master)
}

/* ---------------- delivery ---------------- */

function desktopNotice(kind: NotifyKind): void {
  if (typeof Notification === 'undefined') return
  if (Notification.permission === 'denied') return
  if (Notification.permission !== 'granted') {
    // Asking here is the only moment it is justified: the user just enabled it.
    void Notification.requestPermission()
    return
  }
  const copy: Record<NotifyKind, { title: string; body: string }> = {
    finished: { title: 'Reply finished', body: 'The agent stopped working on your last prompt.' },
    question: { title: 'The agent asked you something', body: 'Open Pebble to answer.' },
    permission: { title: 'Permission needed', body: 'A tool call is waiting for your decision.' }
  }
  try {
    new Notification(copy[kind].title, { body: copy[kind].body, silent: true })
  } catch {
    /* some platforms throw when the constructor is unavailable */
  }
}

/* One entry point for every trigger, so `when` and the per-kind switches are
   applied in one place instead of at each call site. */
export function notify(kind: NotifyKind): void {
  const s = notifySettings
  if (s.when === 'unfocused' && typeof document !== 'undefined' && document.hasFocus()) return
  const wantsSound =
    kind === 'finished' ? s.alerts.finished : kind === 'permission' ? s.alerts.permission : true
  if (wantsSound) playSound(kind === 'finished' ? s.soundDone : s.soundAsk)
  if (s.alerts.desktop) desktopNotice(kind)
}

export function requestDesktopPermission(): void {
  if (typeof Notification === 'undefined') return
  if (Notification.permission === 'default') void Notification.requestPermission()
}
