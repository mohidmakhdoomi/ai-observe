"use strict";
(function(root){
  const nodeAggregator = (typeof module !== "undefined" && module.exports && (!root || !root.document)) ? require("./aggregator.js") : null;
  const FILTER_STORAGE_KEY = "ai_observe.viewer.filters.v1";
  const STABLE_FILTER_ORIGIN = "http://127.0.0.1:7878";

  function parentPath(p){ if(!p||p==="/") return null; const parts=p.split("/").filter(Boolean); parts.pop(); return parts.length?"/"+parts.join("/"):"/"; }
  function breadcrumbSegments(path){ const segs=[{label:"/",path:"/"}]; const parts=(path||"/").split("/").filter(Boolean); let cur=""; for(const part of parts){ cur += "/"+part; segs.push({label:part,path:cur}); } return segs; }
  function liveBadgeState(lastAppendAtMs, status, nowMs){
    if(status==="shutdown") return {text:"shutdown", className:"badge red"};
    const live = typeof lastAppendAtMs === "number" && nowMs - lastAppendAtMs < 2000;
    return live ? {text:"live", className:"badge green"} : {text:"idle", className:"badge gray"};
  }
  function isInScope(currentRoot, path){ return currentRoot==="/" || path===currentRoot || path.startsWith(currentRoot+"/"); }

  function apiOrDefault(api){ return api || nodeAggregator || (root && root.AiObserveAggregator); }
  function factoryFilterPatterns(api){ const aggApi=apiOrDefault(api); return (aggApi&&aggApi.factoryFilterPatterns?aggApi.factoryFilterPatterns:[]).slice(); }
  function isStableFilterOrigin(locationLike){
    if(!locationLike) return false;
    if(locationLike.origin) return locationLike.origin === STABLE_FILTER_ORIGIN;
    return locationLike.protocol === "http:" && locationLike.hostname === "127.0.0.1" && String(locationLike.port) === "7878";
  }
  function normalizeFilterPatterns(patterns, api){
    const aggApi=apiOrDefault(api);
    if(!Array.isArray(patterns)) return {ok:false, patterns:[], errors:["filter pattern list must be an array"]};
    const out=[], errors=[];
    for(const pattern of patterns){
      const result=aggApi.validateFilterPattern(pattern);
      if(result.ok) out.push(result.pattern); else errors.push(result.error);
    }
    return errors.length ? {ok:false, patterns:out, errors:errors} : {ok:true, patterns:out, errors:[]};
  }
  function readStoredFilterPatterns(storage, locationLike, api){
    const defaults=factoryFilterPatterns(api);
    if(!isStableFilterOrigin(locationLike) || !storage) return defaults;
    try{
      const raw=storage.getItem(FILTER_STORAGE_KEY);
      if(!raw) return defaults;
      const parsed=JSON.parse(raw);
      const normalized=normalizeFilterPatterns(parsed, api);
      return normalized.ok ? normalized.patterns : defaults;
    }catch(_err){
      return defaults;
    }
  }
  function writeStoredFilterPatterns(storage, locationLike, patterns, api){
    if(!isStableFilterOrigin(locationLike) || !storage) return false;
    const normalized=normalizeFilterPatterns(patterns, api);
    if(!normalized.ok) return false;
    try{
      storage.setItem(FILTER_STORAGE_KEY, JSON.stringify(normalized.patterns));
      return true;
    }catch(_err){
      return false;
    }
  }
  function createAggregatorFromEvents(events, filterPatterns, api){
    const aggApi=apiOrDefault(api);
    const agg=aggApi.createAggregator({filter_patterns:filterPatterns});
    for(const event of events) agg.ingest(event);
    return agg;
  }
  function snapshotFromEvents(events, filterPatterns, snapshotOpts, api){
    return createAggregatorFromEvents(events, filterPatterns, api).snapshot(snapshotOpts||{});
  }

  const helpers={parentPath:parentPath,breadcrumbSegments:breadcrumbSegments,liveBadgeState:liveBadgeState,isInScope:isInScope,FILTER_STORAGE_KEY:FILTER_STORAGE_KEY,STABLE_FILTER_ORIGIN:STABLE_FILTER_ORIGIN,isStableFilterOrigin:isStableFilterOrigin,normalizeFilterPatterns:normalizeFilterPatterns,readStoredFilterPatterns:readStoredFilterPatterns,writeStoredFilterPatterns:writeStoredFilterPatterns,createAggregatorFromEvents:createAggregatorFromEvents,snapshotFromEvents:snapshotFromEvents};
  if(typeof module!=="undefined"&&module.exports&&(!root||!root.document)){ module.exports=helpers; return; }

  const eventBuffer=[];
  const initialFilterPatterns=readStoredFilterPatterns(root.localStorage, root.location, AiObserveAggregator);
  let agg=AiObserveAggregator.createAggregator({filter_patterns:initialFilterPatterns});
  const state={metric:"bytes",includeFiltered:false,includeNoise:false,filterPatterns:initialFilterPatterns.slice(),currentRoot:"/",selectedPath:null,hoveredPath:null,expanded:new Set(["/"]),sort:{column:"bytes",dir:"desc"},lastAppendAtMs:null,liveStatus:"idle",scrollSelected:false};
  root.viewer={agg:agg,state:state,eventBuffer:eventBuffer,breadcrumbSegments:breadcrumbSegments,parentPath:parentPath,liveBadgeState:liveBadgeState,setFilterPatterns:setFilterPatterns,rebuildAggregator:rebuildAggregator};
  const el={treemap:document.getElementById("treemap"),table:document.getElementById("table"),badge:document.getElementById("live-badge"),counts:document.getElementById("counts"),metric:document.getElementById("metric-controls"),noise:document.getElementById("show-noise"),up:document.getElementById("up-button"),crumb:document.getElementById("breadcrumb")};
  let pending=false, lastSnap=null;
  function preserveSelection(){ if(state.selectedPath && !isInScope(state.currentRoot,state.selectedPath)) state.selectedPath=null; }
  function rebuildAggregator(){ agg=createAggregatorFromEvents(eventBuffer,state.filterPatterns,AiObserveAggregator); root.viewer.agg=agg; return agg; }
  function setFilterPatterns(patterns){ const normalized=normalizeFilterPatterns(patterns,AiObserveAggregator); if(!normalized.ok) return normalized; state.filterPatterns=normalized.patterns.slice(); writeStoredFilterPatterns(root.localStorage,root.location,state.filterPatterns,AiObserveAggregator); rebuildAggregator(); preserveSelection(); scheduleRender(); return {ok:true,patterns:state.filterPatterns.slice(),errors:[]}; }
  function renderBreadcrumb(){ while(el.crumb.firstChild) el.crumb.removeChild(el.crumb.firstChild); for(const seg of breadcrumbSegments(state.currentRoot)){ const b=document.createElement("button"); b.type="button"; b.textContent=seg.label; b.addEventListener("click",()=>{state.currentRoot=seg.path; preserveSelection(); scheduleRender();}); el.crumb.appendChild(b); } el.up.disabled=state.currentRoot==="/"; }
  function render(){ pending=false; lastSnap=agg.snapshot({metric:state.metric,include_filtered:state.includeFiltered}); const rootNode=AiObserveTreemap.findNode(lastSnap.tree,state.currentRoot)||{path:state.currentRoot,name:state.currentRoot,isDir:true,children:[],bytes:0,events:0,recent:0,last_touched_ms:0}; preserveSelection(); renderBreadcrumb(); el.counts.textContent=lastSnap.total_event_count+" events, "+lastSnap.filtered_event_count+" filtered"; for(const b of el.metric.querySelectorAll("button")) b.classList.toggle("active", b.dataset.metric===state.metric); AiObserveTreemap.renderTreemap(el.treemap,{rootNode:rootNode,state:state,onSelect:onSelect,onHover:onHover,onDrill:onDrill}); AiObserveTable.renderTable(el.table,{rootNode:rootNode,state:state,latestTsMs:lastSnap.latest_ts_ms,onSelect:onSelect,onHover:onHover,onToggle:onToggle,onSort:onSort}); state.scrollSelected=false; }
  function scheduleRender(){ if(pending) return; pending=true; setTimeout(render,250); }
  function onSelect(p){ state.selectedPath=p; state.scrollSelected=true; scheduleRender(); }
  function onHover(p){ state.hoveredPath=p; scheduleRender(); }
  function onDrill(p){ state.currentRoot=p; state.expanded.add(p); preserveSelection(); scheduleRender(); }
  function onToggle(p){ if(state.expanded.has(p)) state.expanded.delete(p); else state.expanded.add(p); scheduleRender(); }
  function onSort(col){ if(state.sort.column===col) state.sort.dir=state.sort.dir==="asc"?"desc":"asc"; else state.sort={column:col,dir:col==="path"?"asc":"desc"}; state.scrollSelected=true; scheduleRender(); }
  el.metric.addEventListener("click",ev=>{ const b=ev.target.closest("button[data-metric]"); if(!b) return; state.metric=b.dataset.metric; state.scrollSelected=true; scheduleRender(); });
  el.noise.addEventListener("change",()=>{ state.includeFiltered=el.noise.checked; state.includeNoise=state.includeFiltered; preserveSelection(); scheduleRender(); });
  el.up.addEventListener("click",()=>{ const p=parentPath(state.currentRoot); if(p){ state.currentRoot=p; preserveSelection(); scheduleRender(); }});
  const es=new EventSource("/events");
  es.addEventListener("append",ev=>{ try{ const parsed=JSON.parse(ev.data); eventBuffer.push(parsed); agg.ingest(parsed); state.lastAppendAtMs=performance.now(); scheduleRender(); }catch(_err){} });
  es.addEventListener("shutdown",()=>{ state.liveStatus="shutdown"; updateBadge(); es.close(); });
  es.onerror=()=>{ if(state.liveStatus!=="shutdown") state.liveStatus="idle"; updateBadge(); };
  function updateBadge(){ const b=liveBadgeState(state.lastAppendAtMs,state.liveStatus,performance.now()); el.badge.textContent=b.text; el.badge.className=b.className; }
  setInterval(updateBadge,500); updateBadge(); render();
})(typeof self!=="undefined"?self:(typeof globalThis!=="undefined"?globalThis:this));
