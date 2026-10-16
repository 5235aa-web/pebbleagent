export type CompletionSoundId = 'off'|'chime'|'pop'|'glass'|'bell'|'pulse'|'crystal'|'wood'|'soft'
export interface CompletionSoundOption { id: CompletionSoundId; label: string; description: string }
export const COMPLETION_SOUNDS: CompletionSoundOption[] = [
  { id: 'off', label: 'Off', description: 'No reminder' },
  { id: 'chime', label: 'Chime', description: 'Soft two-note chime' },
  { id: 'pop', label: 'Pop', description: 'Tiny bright pop' },
  { id: 'glass', label: 'Glass', description: 'Light glass ping' },
  { id: 'bell', label: 'Bell', description: 'Warm desk bell' },
  { id: 'pulse', label: 'Pulse', description: 'Clean digital pulse' },
  { id: 'crystal', label: 'Crystal', description: 'Clear shimmering tone' },
  { id: 'wood', label: 'Wood', description: 'Warm wooden knock' },
  { id: 'soft', label: 'Soft', description: 'Gentle fade-in pad' },
]
const STORAGE_KEY='pebble:completion-sound'
let audioContext:AudioContext|null=null
function getAudioContext():AudioContext|null{if(typeof window==='undefined')return null;const C=window.AudioContext||(window as any).webkitAudioContext;if(!C)return null;if(!audioContext)audioContext=new C();return audioContext}
function playTone(ctx:AudioContext,start:number,freq:number,dur:number,gain:number,type:OscillatorType='sine'){const o=ctx.createOscillator();const g=ctx.createGain();o.type=type;o.frequency.setValueAtTime(freq,start);g.gain.setValueAtTime(0.0001,start);g.gain.exponentialRampToValueAtTime(gain,start+0.015);g.gain.exponentialRampToValueAtTime(0.0001,start+dur);o.connect(g);g.connect(ctx.destination);o.start(start);o.stop(start+dur+0.03)}
async function ensureAudioReady(ctx:AudioContext){if(ctx.state==='suspended')await ctx.resume()}
export function getCompletionSound():CompletionSoundId{try{const s=localStorage.getItem(STORAGE_KEY) as CompletionSoundId|null;return s||'chime'}catch{return'chime'}}
export function setCompletionSound(sid:CompletionSoundId){try{localStorage.setItem(STORAGE_KEY,sid)}catch{}}
export async function playCompletionSound(soundId:CompletionSoundId=getCompletionSound()){if(soundId==='off')return;const ctx=getAudioContext();if(!ctx)return;try{await ensureAudioReady(ctx);const n=ctx.currentTime;switch(soundId){case'pop':playTone(ctx,n,880,0.09,0.08,'triangle');playTone(ctx,n+0.035,1320,0.07,0.045,'sine');break;case'glass':playTone(ctx,n,1568,0.18,0.055,'sine');playTone(ctx,n+0.025,2093,0.15,0.035,'triangle');break;case'bell':playTone(ctx,n,784,0.24,0.07,'sine');playTone(ctx,n+0.02,1175,0.22,0.035,'triangle');playTone(ctx,n+0.04,1568,0.18,0.025,'sine');break;case'pulse':playTone(ctx,n,523,0.08,0.055,'square');playTone(ctx,n+0.11,659,0.08,0.045,'square');break;case'crystal':playTone(ctx,n,1760,0.35,0.04,'sine');playTone(ctx,n+0.02,2637,0.3,0.025,'sine');playTone(ctx,n+0.04,3520,0.25,0.015,'sine');playTone(ctx,n+0.08,2093,0.15,0.018,'triangle');break;case'wood':playTone(ctx,n,440,0.12,0.065,'triangle');playTone(ctx,n+0.01,880,0.08,0.03,'sine');playTone(ctx,n+0.015,1320,0.06,0.015,'sine');break;case'soft':{const so=ctx.createOscillator();const sg=ctx.createGain();so.type='sine';so.frequency.setValueAtTime(440,n);sg.gain.setValueAtTime(0.0001,n);sg.gain.exponentialRampToValueAtTime(0.05,n+0.15);sg.gain.exponentialRampToValueAtTime(0.0001,n+0.4);so.connect(sg);sg.connect(ctx.destination);so.start(n);so.stop(n+0.45);playTone(ctx,n+0.05,554,0.3,0.025,'sine');playTone(ctx,n+0.08,659,0.25,0.018,'sine');break}case'chime':default:playTone(ctx,n,659,0.13,0.055,'sine');playTone(ctx,n+0.1,988,0.2,0.05,'sine');break}}catch{}}
export function primeCompletionSound(){const ctx=getAudioContext();if(!ctx||ctx.state!=='suspended')return;ctx.resume().catch(()=>{})}
