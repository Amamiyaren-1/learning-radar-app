export type SoundPreferences={enabled:boolean;musicVolume:number;effectsVolume:number};
export const soundDefaults:SoundPreferences={enabled:true,musicVolume:18,effectsVolume:28};
export const clampVolume=(n:unknown,fallback:number)=>typeof n==='number'&&Number.isFinite(n)?Math.max(0,Math.min(100,n)):fallback;
export function soundPreferences(saved:any,legacy:any={}):SoundPreferences{
  return {enabled:typeof saved?.enabled==='boolean'?saved.enabled:typeof legacy?.music==='boolean'?legacy.music:true,
    musicVolume:clampVolume(saved?.musicVolume,clampVolume(legacy?.volume,soundDefaults.musicVolume)),effectsVolume:clampVolume(saved?.effectsVolume,soundDefaults.effectsVolume)};
}
