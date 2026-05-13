"use strict";
(function(root){
  function parentPath(p){ if(!p||p==="/") return null; const parts=p.split("/").filter(Boolean); parts.pop(); return parts.length?"/"+parts.join("/"):"/"; }
  function breadcrumbSegments(path){ const segs=[{label:"/",path:"/"}]; const parts=(path||"/").split("/").filter(Boolean); let cur=""; for(const part of parts){ cur += "/"+part; segs.push({label:part,path:cur}); } return segs; }
  function liveBadgeState(lastAppendAtMs, status, nowMs){
    if(status==="shutdown") return {text:"shutdown", className:"badge red"};
    const live = typeof lastAppendAtMs === "number" && nowMs - lastAppendAtMs < 2000;
    return live ? {text:"live", className:"badge green"} : {text:"idle", className:"badge gray"};
  }
  function isInScope(currentRoot, path){ return currentRoot==="/" || path===currentRoot || path.startsWith(currentRoot+"/"); }
  const helpers={parentPath:parentPath,breadcrumbSegments:breadcrumbSegments,liveBadgeState:liveBadgeState,isInScope:isInScope};
  if(typeof module!=="undefined"&&module.exports&&(!root||!root.document)){ module.exports=helpers; return; }

  const agg=AiObserveAggregator.createAggregator();
  const state={metric:"bytes",includeNoise:false,currentRoot:"/",selectedPath:null,hoveredPath:null,expanded:new Set(["/"]),sort:{column:"bytes",dir:"desc"},lastAppendAtMs:null,liveStatus:"idle",scrollSelected:false};
  root.viewer={agg:agg,state:state,breadcrumbSegments:breadcrumbSegments,parentPath:parentPath,liveBadgeState:liveBadgeState};
  const el={treemap:document.getElementById("treemap"),table:document.getElementById("table"),badge:document.getElementById("live-badge"),counts:document.getElementById("counts"),metric:document.getElementById("metric-controls"),noise:document.getElementById("show-noise"),up:document.getElementById("up-button"),crumb:document.getElementById("breadcrumb")};
  let pending=false, lastSnap=null;
  function preserveSelection(){ if(state.selectedPath && !isInScope(state.currentRoot,state.selectedPath)) state.selectedPath=null; }
  function renderBreadcrumb(){ while(el.crumb.firstChild) el.crumb.removeChild(el.crumb.firstChild); for(const seg of breadcrumbSegments(state.currentRoot)){ const b=document.createElement("button"); b.type="button"; b.textContent=seg.label; b.addEventListener("click",()=>{state.currentRoot=seg.path; preserveSelection(); scheduleRender();}); el.crumb.appendChild(b); } el.up.disabled=state.currentRoot==="/"; }
  function render(){ pending=false; lastSnap=agg.snapshot({metric:state.metric,include_noise:state.includeNoise}); const rootNode=AiObserveTreemap.findNode(lastSnap.tree,state.currentRoot)||{path:state.currentRoot,name:state.currentRoot,isDir:true,children:[],bytes:0,events:0,recent:0,last_touched_ms:0}; preserveSelection(); renderBreadcrumb(); el.counts.textContent=lastSnap.total_event_count+" events, "+lastSnap.filtered_event_count+" filtered"; for(const b of el.metric.querySelectorAll("button")) b.classList.toggle("active", b.dataset.metric===state.metric); AiObserveTreemap.renderTreemap(el.treemap,{rootNode:rootNode,state:state,onSelect:onSelect,onHover:onHover,onDrill:onDrill}); AiObserveTable.renderTable(el.table,{rootNode:rootNode,state:state,latestTsMs:lastSnap.latest_ts_ms,onSelect:onSelect,onHover:onHover,onToggle:onToggle,onSort:onSort}); state.scrollSelected=false; }
  function scheduleRender(){ if(pending) return; pending=true; setTimeout(render,250); }
  function onSelect(p){ state.selectedPath=p; state.scrollSelected=true; scheduleRender(); }
  function onHover(p){ state.hoveredPath=p; scheduleRender(); }
  function onDrill(p){ state.currentRoot=p; state.expanded.add(p); preserveSelection(); scheduleRender(); }
  function onToggle(p){ if(state.expanded.has(p)) state.expanded.delete(p); else state.expanded.add(p); scheduleRender(); }
  function onSort(col){ if(state.sort.column===col) state.sort.dir=state.sort.dir==="asc"?"desc":"asc"; else state.sort={column:col,dir:col==="path"?"asc":"desc"}; state.scrollSelected=true; scheduleRender(); }
  el.metric.addEventListener("click",ev=>{ const b=ev.target.closest("button[data-metric]"); if(!b) return; state.metric=b.dataset.metric; state.scrollSelected=true; scheduleRender(); });
  el.noise.addEventListener("change",()=>{ state.includeNoise=el.noise.checked; preserveSelection(); scheduleRender(); });
  el.up.addEventListener("click",()=>{ const p=parentPath(state.currentRoot); if(p){ state.currentRoot=p; preserveSelection(); scheduleRender(); }});
  const es=new EventSource("/events");
  es.addEventListener("append",ev=>{ try{ agg.ingest(JSON.parse(ev.data)); state.lastAppendAtMs=performance.now(); scheduleRender(); }catch(_err){} });
  es.addEventListener("shutdown",()=>{ state.liveStatus="shutdown"; updateBadge(); es.close(); });
  es.onerror=()=>{ if(state.liveStatus!=="shutdown") state.liveStatus="idle"; updateBadge(); };
  function updateBadge(){ const b=liveBadgeState(state.lastAppendAtMs,state.liveStatus,performance.now()); el.badge.textContent=b.text; el.badge.className=b.className; }
  setInterval(updateBadge,500); updateBadge(); render();
})(typeof self!=="undefined"?self:(typeof globalThis!=="undefined"?globalThis:this));
