export const localDay = (value=Date.now()) => { const d=new Date(value);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-') }
export function addDays(value,count){const d=new Date(value+'T12:00:00');d.setDate(d.getDate()+count);return localDay(d)}
export function clockText(seconds){const n=Math.max(0,Math.floor(seconds||0));return (n>=3600?String(Math.floor(n/3600)).padStart(2,'0')+':':'')+String(Math.floor(n/60)%60).padStart(2,'0')+':'+String(n%60).padStart(2,'0')}
export function durationText(seconds){const minutes=Math.floor((seconds||0)/60);return minutes>=60?Math.floor(minutes/60)+' sa '+minutes%60+' dk':minutes+' dk'}
export function timerElapsed(timer,now){if(!timer)return 0;const elapsed=timer.elapsedSeconds+(timer.status==='running'?Math.max(0,now-timer.startedAt):0);return timer.targetSeconds?Math.min(elapsed,timer.targetSeconds):elapsed}
export function allEntries(state,now){const entries=[...state.entries];const t=state.timer;if(t?.status==='running'&&t.phase==='focus'){entries.push({id:'live',taskId:t.taskId,projectId:state.tasks.find(x=>x.id===t.taskId)?.projectId,startedAt:t.startedAt,seconds:Math.max(0,timerElapsed(t,now)-t.elapsedSeconds),kind:t.mode})}return entries}
export function secondsInDay(entry,key){const start=new Date(key+'T00:00:00').getTime()/1000;const end=new Date(addDays(key,1)+'T00:00:00').getTime()/1000;return Math.max(0,Math.min(entry.startedAt+entry.seconds,end)-Math.max(entry.startedAt,start))}
export function dayStats(state,key,now){const entries=allEntries(state,now);return {seconds:entries.reduce((n,e)=>n+secondsInDay(e,key),0),completed:state.tasks.filter(t=>t.completedAt&&localDay(t.completedAt*1000)===key).length}}
export function folderContains(folders,id,selected){let current=id;const seen=new Set();while(current&&!seen.has(current)){if(current===selected)return true;seen.add(current);current=folders.find(f=>f.id===current)?.parentId}return false}
export function taskDepth(tasks,task){let current=task,depth=0;const seen=new Set();while(current?.parentId&&!seen.has(current.id)){seen.add(current.id);current=tasks.find(t=>t.id===current.parentId);depth++}return Math.min(depth,8)}
export function filterTasks(state,{view='today',projectId='',folderId='',query='',tag='',showDone=false,date=localDay()}={}){
 const needle=query.trim().toLocaleLowerCase('tr-TR');return state.tasks.filter(t=>{
 const project=state.projects.find(p=>p.id===t.projectId);if(view==='archive'){if(!t.archived&&!project?.archived)return false}else if(t.archived||project?.archived)return false;
 if(projectId&&t.projectId!==projectId)return false;if(folderId&&!folderContains(state.folders,t.folderId,folderId))return false;
 if(needle&&![t.title,t.notes,...t.tags].join(' ').toLocaleLowerCase('tr-TR').includes(needle))return false;
 if(tag&&!t.tags.includes(tag))return false;
 if(!showDone&&view!=='archive'&&view!=='done'&&t.status==='done')return false;
 if(view==='done')return t.status==='done';
 if(view==='today')return (t.plannedDate&&t.plannedDate<=date)||(t.dueDate&&t.dueDate<=date)||t.status==='doing';
 if(view==='inbox')return !t.plannedDate;
 return true;
 }).sort((a,b)=>a.order-b.order||a.createdAt-b.createdAt)
}
