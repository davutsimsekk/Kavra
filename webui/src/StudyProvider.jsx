import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { Pause, Play, Timer, X } from 'lucide-react'
import { clockText, timerElapsed } from './studyState.mjs'
import './StudyWorkspace.css'
const StudyContext=createContext(null)
export const useStudy=()=>useContext(StudyContext)
export const openStudy=()=>window.dispatchEvent(new Event('open-study'))
async function request(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Çalışma takip servisine bağlanılamadı.');return data}
export function StudyProvider({children}){
 const [state,setState]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[tick,setTick]=useState(Date.now()),[inWorkspace,setInWorkspace]=useState(false)
 const current=useRef(null),locked=useRef(false),audio=useRef(null),lastTimer=useRef(null),offset=useRef(0)
 function accept(next){if(current.current&&next.revision<current.current.revision)return;offset.current=next.serverNow-Date.now()/1000;current.current=next;setState(next)}
 async function refresh(){try{accept(await request('/api/study'))}catch(e){setError(e.message)}}
 useEffect(()=>{refresh();const interval=setInterval(()=>{if(current.current?.timer?.status==='running'||document.querySelector('.study-shell')||Date.now()-(current.current?.serverNow||0)*1000>30000)refresh()},5000);const clock=setInterval(()=>setTick(Date.now()),1000);const visible=()=>{if(!document.hidden)refresh()};document.addEventListener('visibilitychange',visible);window.addEventListener('focus',visible);return()=>{clearInterval(interval);clearInterval(clock);document.removeEventListener('visibilitychange',visible);window.removeEventListener('focus',visible)}},[])
 useEffect(()=>{const timer=state?.timer;const previous=lastTimer.current;if(timer?.status==='finished'&&previous?.id===timer.id&&previous.status!=='finished'){
   if(state.settings.sound&&audio.current){try{const osc=audio.current.createOscillator(),gain=audio.current.createGain();osc.connect(gain);gain.connect(audio.current.destination);gain.gain.setValueAtTime(.1,audio.current.currentTime);gain.gain.exponentialRampToValueAtTime(.001,audio.current.currentTime+.6);osc.frequency.value=660;osc.start();osc.stop(audio.current.currentTime+.6)}catch{}}
   if('Notification' in window&&Notification.permission==='granted'&&document.hidden){try{new Notification(timer.phase==='focus'?'Odak süresi tamamlandı':'Mola tamamlandı',{body:'Sonraki aralığı başlatmak için çalışma alanına dön.',tag:'study-timer'})}catch{}}
 }lastTimer.current=timer?{...timer}:null},[state])
 async function command(action,data={}){if(locked.current||!current.current)return null;locked.current=true;setBusy(true);setError('');
   if(action==='timer.start'||action==='timer.resume'||action==='timer.next'){try{audio.current ||= new (window.AudioContext||window.webkitAudioContext)();audio.current.resume()}catch{}}
   try{const next=await request('/api/study/commands',{action,data,revision:current.current.revision});accept(next);return next}catch(e){try{accept(await request('/api/study'))}catch{}setError(e.message);return null}finally{locked.current=false;setBusy(false)}}
 async function restore(backup){if(locked.current||!current.current)return false;locked.current=true;setBusy(true);try{accept(await request('/api/study/restore',{backup,revision:current.current.revision}));setError('');return true}catch(e){setError(e.message);return false}finally{locked.current=false;setBusy(false)}}
 const now=tick/1000+offset.current,timer=state?.timer,task=state?.tasks.find(t=>t.id===timer?.taskId)
 return <StudyContext.Provider value={{state,error,setError,busy,command,refresh,restore,now,setInWorkspace}}>{children}
 {timer&&<aside className="study-dock" aria-label="Aktif çalışma sayacı"><button onClick={openStudy}><Timer size={19}/><span><strong>{task?.title}</strong><small>{timer.phase==='focus'?'Odak':'Mola'} · {timer.status==='finished'?'Tamamlandı':clockText(timer.targetSeconds?timer.targetSeconds-timerElapsed(timer,now):timerElapsed(timer,now))}</small></span></button>{timer.status!=='finished'&&<button disabled={busy} className="icon-button" aria-label={timer.status==='running'?'Sayacı duraklat':'Sayacı sürdür'} onClick={()=>command(timer.status==='running'?'timer.pause':'timer.resume',{timerId:timer.id})}>{timer.status==='running'?<Pause size={17}/>:<Play size={17}/>}</button>}<button className="icon-button" disabled={busy} aria-label="Süreyi kaydet ve sayacı kapat" onClick={()=>command('timer.stop',{timerId:timer.id})}><X size={17}/></button></aside>}
 </StudyContext.Provider>
}
