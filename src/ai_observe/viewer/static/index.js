"use strict";
(function(root){
  const nodeAggregator=(typeof module!=="undefined"&&module.exports&&(!root||!root.document))?require("./aggregator.js"):null;
  const FILTER_STORAGE_KEY="ai_observe.viewer.filters.v1";const STABLE_FILTER_ORIGIN="http://127.0.0.1:7878";

  function parentPath(p){if(!p||p==="/")return null;const parts=p.split("/").filter(Boolean);parts.pop();return parts.length?"/"+parts.join("/"):"/";}
  function breadcrumbSegments(path){ const segs=[{label:"/",path:"/"}]; const parts=(path||"/").split("/").filter(Boolean); let cur=""; for(const part of parts){ cur += "/"+part; segs.push({label:part,path:cur}); } return segs; }
  function liveBadgeState(lastAppendAtMs,status,nowMs){if(status==="shutdown")return {text:"shutdown",className:"badge red"};const live=typeof lastAppendAtMs==="number"&&nowMs-lastAppendAtMs<2000;return live?{text:"live",className:"badge green"}:{text:"idle",className:"badge gray"};}
  function isInScope(currentRoot,path){return currentRoot==="/"||path===currentRoot||path.startsWith(currentRoot+"/");}

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
  function uniquePatterns(patterns){
    const seen=new Set(), out=[];
    for(const pattern of patterns){
      if(seen.has(pattern)) continue;
      seen.add(pattern);
      out.push(pattern);
    }
    return out;
  }
  function normalizeUniqueFilterPatterns(patterns, api){
    const normalized=normalizeFilterPatterns(patterns, api);
    if(!normalized.ok) return normalized;
    return {ok:true, patterns:uniquePatterns(normalized.patterns), errors:[]};
  }
  function filterEditorSummary(patterns){ return "Filters ("+(Array.isArray(patterns)?patterns.length:0)+")"; }
  function addFilterPattern(patterns, pattern, api){ return normalizeUniqueFilterPatterns((patterns||[]).concat([pattern]), api); }
  function updateFilterPatternAt(patterns, index, pattern, api){
    if(!Array.isArray(patterns) || index<0 || index>=patterns.length) return {ok:false, patterns:(patterns||[]).slice(), errors:["filter pattern index is invalid"]};
    const next=patterns.slice();
    next[index]=pattern;
    return normalizeUniqueFilterPatterns(next, api);
  }
  function removeFilterPatternAt(patterns, index){
    if(!Array.isArray(patterns) || index<0 || index>=patterns.length) return {ok:false, patterns:(patterns||[]).slice(), errors:["filter pattern index is invalid"]};
    const next=patterns.slice();
    next.splice(index,1);
    return {ok:true, patterns:next, errors:[]};
  }
  function resetFilterPatterns(api){ return {ok:true, patterns:factoryFilterPatterns(api), errors:[]}; }
  function subtreePatternFor(path){ return path==="/" ? "/**" : String(path||"").replace(/\/+$/,"")+"/**"; }
  function filterPatternProposals(item){
    if(!item || !item.path) return [];
    const proposals=[{label:"Exact path", pattern:item.path, kind:"exact"}];
    if(item.isDir) proposals.push({label:"Subtree", pattern:subtreePatternFor(item.path), kind:"subtree"});
    return proposals;
  }
  function exactPatternsForSelection(paths){ return uniquePatterns((paths||[]).filter(Boolean)); }
  function collectTreePaths(node, out){
    out=out||[];
    if(!node) return out;
    if(node.path) out.push(node.path);
    for(const child of (node.children||[])) collectTreePaths(child,out);
    return out;
  }
  function pruneSelectedPaths(paths,tree){if(!paths||paths.length===0)return [];const allowed=new Set(collectTreePaths(tree,[]));return paths.filter(path=>allowed.has(path));}
  function togglePathSelection(paths, path){
    const next=new Set(paths||[]);
    if(next.has(path)) next.delete(path); else next.add(path);
    return Array.from(next);
  }
  function selectVisibleRange(paths, anchorPath, targetPath, visiblePaths){
    const start=(visiblePaths||[]).indexOf(anchorPath), end=(visiblePaths||[]).indexOf(targetPath);
    if(start<0 || end<0) return togglePathSelection(paths,targetPath);
    const next=new Set(paths||[]);
    const lo=Math.min(start,end), hi=Math.max(start,end);
    for(let i=lo;i<=hi;i++) next.add(visiblePaths[i]);
    return Array.from(next);
  }
  function updateMultiSelectionState(current, path, info){
    current=current||{};
    const before=current.selectedPaths||[];
    let next, anchor=current.selectionAnchorPath||null;
    if(info && info.shiftKey && anchor){
      next=selectVisibleRange(before,anchor,path,info.visiblePaths||[]);
    }else{
      next=togglePathSelection(before,path);
      anchor=path;
    }
    const selectedPath=next.indexOf(path)>=0 ? path : (current.selectedPath===path ? null : (current.selectedPath||null));
    return {selectedPaths:next, selectedPath:selectedPath, selectionAnchorPath:anchor};
  }
  function readStoredFilterPatterns(storage, locationLike, api){
    const defaults=factoryFilterPatterns(api);
    if(!isStableFilterOrigin(locationLike) || !storage) return defaults;
    try{
      const raw=storage.getItem(FILTER_STORAGE_KEY);
      if(!raw) return defaults;
      const parsed=JSON.parse(raw);
      const normalized=normalizeUniqueFilterPatterns(parsed, api);
      return normalized.ok ? normalized.patterns : defaults;
    }catch(_err){
      return defaults;
    }
  }
  function writeStoredFilterPatterns(storage, locationLike, patterns, api){
    if(!isStableFilterOrigin(locationLike) || !storage) return false;
    const normalized=normalizeUniqueFilterPatterns(patterns, api);
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
  function ing1(buf,agg,event){buf.push(event);agg.ingest(event);return 1;}
  function ingN(buf,agg,events){if(!Array.isArray(events))return 0;for(const event of events)ing1(buf,agg,event);return events.length;}
  function ingestAppendData(data,buf,agg){try{return ing1(buf,agg,JSON.parse(data));}catch(_err){return 0;}}
  function ingestAppendBatchData(data,buf,agg){try{return ingN(buf,agg,JSON.parse(data));}catch(_err){return 0;}}

  const helpers={parentPath,breadcrumbSegments,liveBadgeState,isInScope,FILTER_STORAGE_KEY,STABLE_FILTER_ORIGIN,isStableFilterOrigin,normalizeFilterPatterns,normalizeUniqueFilterPatterns,uniquePatterns,filterEditorSummary,addFilterPattern,updateFilterPatternAt,removeFilterPatternAt,resetFilterPatterns,subtreePatternFor,filterPatternProposals,exactPatternsForSelection,collectTreePaths,pruneSelectedPaths,togglePathSelection,selectVisibleRange,updateMultiSelectionState,readStoredFilterPatterns,writeStoredFilterPatterns,createAggregatorFromEvents,snapshotFromEvents,ingestAppendData,ingestAppendBatchData};
  if(typeof module!=="undefined"&&module.exports&&(!root||!root.document)){ module.exports=helpers; return; }

  const eventBuffer=[];
  const initialFilterPatterns=readStoredFilterPatterns(root.localStorage, root.location, AiObserveAggregator);
  let agg=AiObserveAggregator.createAggregator({filter_patterns:initialFilterPatterns});
  const state={metric:"bytes",includeFiltered:false,includeNoise:false,filterPatterns:initialFilterPatterns.slice(),currentRoot:"/",selectedPath:null,selectedPaths:new Set(),selectionAnchorPath:null,hoveredPath:null,expanded:new Set(["/"]),sort:{column:"bytes",dir:"desc"},lastAppendAtMs:null,liveStatus:"idle",scrollSelected:false,filtersOpen:false,filterPreview:null};
  root.viewer={agg:agg,state:state,eventBuffer:eventBuffer,breadcrumbSegments:breadcrumbSegments,parentPath:parentPath,liveBadgeState:liveBadgeState,setFilterPatterns:setFilterPatterns,rebuildAggregator:rebuildAggregator};
  const el={treemap:document.getElementById("treemap"),table:document.getElementById("table"),badge:document.getElementById("live-badge"),counts:document.getElementById("counts"),metric:document.getElementById("metric-controls"),filtered:document.getElementById("show-filtered")||document.getElementById("show-noise"),filtersButton:document.getElementById("filters-button"),addSelectedFilters:document.getElementById("add-selected-filters"),filtersEditor:document.getElementById("filters-editor"),filtersClose:document.getElementById("filters-close"),filtersList:document.getElementById("filters-list"),filtersAddForm:document.getElementById("filters-add-form"),filtersNewPattern:document.getElementById("filters-new-pattern"),filtersError:document.getElementById("filters-error"),filtersReset:document.getElementById("filters-reset"),filterPreview:document.getElementById("filter-preview"),filterPreviewTitle:document.getElementById("filter-preview-title"),filterPreviewForm:document.getElementById("filter-preview-form"),filterPreviewList:document.getElementById("filter-preview-list"),filterPreviewError:document.getElementById("filter-preview-error"),filterPreviewCancel:document.getElementById("filter-preview-cancel"),up:document.getElementById("up-button"),crumb:document.getElementById("breadcrumb")};
  let pending=false, lastSnap=null;
  function preserveSelection(){ if(state.selectedPath && !isInScope(state.currentRoot,state.selectedPath)) state.selectedPath=null; }
  function rebuildAggregator(){ agg=createAggregatorFromEvents(eventBuffer,state.filterPatterns,AiObserveAggregator); root.viewer.agg=agg; return agg; }
  function setFilterPatterns(patterns){ const normalized=normalizeUniqueFilterPatterns(patterns,AiObserveAggregator); if(!normalized.ok) return normalized; state.filterPatterns=normalized.patterns.slice(); writeStoredFilterPatterns(root.localStorage,root.location,state.filterPatterns,AiObserveAggregator); rebuildAggregator(); preserveSelection(); renderFilterEditor(); scheduleRender(); return {ok:true,patterns:state.filterPatterns.slice(),errors:[]}; }
  function setFilterError(message){ if(!el.filtersError) return; el.filtersError.textContent=message||""; el.filtersError.hidden=!message; }
  function commitFilterResult(result){ if(!result.ok){ setFilterError(result.errors.join("; ")); return false; } setFilterError(""); setFilterPatterns(result.patterns); return true; }
  function makePatternInput(value,label){ const input=document.createElement("input"); input.type="text"; input.value=value; input.setAttribute("aria-label",label); input.autocomplete="off"; return input; }
  function renderFilterRow(pattern,index){
    const li=document.createElement("li");
    const input=makePatternInput(pattern,"Filter pattern "+(index+1));
    const save=document.createElement("button");
    save.type="button";
    save.textContent="Save";
    const remove=document.createElement("button");
    remove.type="button";
    remove.textContent="Remove";
    function saveEdit(){ commitFilterResult(updateFilterPatternAt(state.filterPatterns,index,input.value,AiObserveAggregator)); }
    save.addEventListener("click",saveEdit);
    input.addEventListener("keydown",ev=>{ if(ev.key==="Enter"){ ev.preventDefault(); saveEdit(); } });
    remove.addEventListener("click",()=>{ commitFilterResult(removeFilterPatternAt(state.filterPatterns,index)); });
    li.appendChild(input);
    li.appendChild(save);
    li.appendChild(remove);
    return li;
  }
  function renderFilterEditor(){
    if(el.filtersButton){
      el.filtersButton.textContent=filterEditorSummary(state.filterPatterns);
      el.filtersButton.setAttribute("aria-expanded",state.filtersOpen?"true":"false");
    }
    if(el.filtersEditor) el.filtersEditor.hidden=!state.filtersOpen;
    if(!el.filtersList) return;
    while(el.filtersList.firstChild) el.filtersList.removeChild(el.filtersList.firstChild);
    if(state.filterPatterns.length===0){
      const li=document.createElement("li");
      li.className="empty";
      li.textContent="No filters configured.";
      el.filtersList.appendChild(li);
    }else{
      state.filterPatterns.forEach((pattern,index)=>{ el.filtersList.appendChild(renderFilterRow(pattern,index)); });
    }
  }
  function toggleFilterEditor(open){
    state.filtersOpen=open == null ? !state.filtersOpen : !!open;
    renderFilterEditor();
    if(state.filtersOpen && el.filtersNewPattern) el.filtersNewPattern.focus();
  }
  function selectedPathList(){ return Array.from(state.selectedPaths); }
  function renderSelectionControls(){
    if(!el.addSelectedFilters) return;
    const count=state.selectedPaths.size;
    el.addSelectedFilters.hidden=count<2;
    el.addSelectedFilters.textContent="Add "+count+" selected to Filters";
  }
  function setPreviewError(message){ if(!el.filterPreviewError) return; el.filterPreviewError.textContent=message||""; el.filterPreviewError.hidden=!message; }
  function closeFilterPreview(){ state.filterPreview=null; setPreviewError(""); renderFilterPreview(); }
  function showFilterPreview(title, proposals, mode){
    state.filterPreview={title:title, proposals:proposals.slice(), mode:mode||"multi", selectedIndex:0};
    state.filtersOpen=false;
    renderFilterEditor();
    renderFilterPreview();
  }
  function renderFilterPreview(){
    const preview=state.filterPreview;
    if(!el.filterPreview || !el.filterPreviewList) return;
    el.filterPreview.hidden=!preview;
    if(!preview) return;
    el.filterPreviewTitle.textContent=preview.title;
    while(el.filterPreviewList.firstChild) el.filterPreviewList.removeChild(el.filterPreviewList.firstChild);
    preview.proposals.forEach((proposal,index)=>{
      const li=document.createElement("li");
      const label=document.createElement("span");
      label.textContent=proposal.label||"Pattern";
      if(preview.mode==="single"){
        const radio=document.createElement("input");
        radio.type="radio";
        radio.name="filter-preview-choice";
        radio.checked=index===preview.selectedIndex;
        radio.addEventListener("change",()=>{ state.filterPreview.selectedIndex=index; });
        li.appendChild(radio);
      }else{
        li.appendChild(label);
      }
      const input=makePatternInput(proposal.pattern,proposal.label||("Filter pattern "+(index+1)));
      input.dataset.index=String(index);
      li.appendChild(input);
      if(preview.mode==="single") li.appendChild(label);
      el.filterPreviewList.appendChild(li);
    });
  }
  function previewPatternsFromDom(){
    const preview=state.filterPreview;
    if(!preview || !el.filterPreviewList) return [];
    const inputs=Array.from(el.filterPreviewList.querySelectorAll("input[type=\"text\"]"));
    if(preview.mode==="single"){
      const input=inputs[preview.selectedIndex];
      return input ? [input.value] : [];
    }
    return inputs.map(input=>input.value);
  }
  function commitFilterPreview(){
    const values=previewPatternsFromDom();
    const normalized=normalizeUniqueFilterPatterns(state.filterPatterns.concat(values),AiObserveAggregator);
    if(!normalized.ok){ setPreviewError(normalized.errors.join("; ")); return false; }
    setFilterPatterns(normalized.patterns);
    closeFilterPreview();
    return true;
  }
  function pruneSelections(tree){if(state.selectedPaths.size===0){state.selectionAnchorPath=null;return;}const pruned=pruneSelectedPaths(selectedPathList(),tree);state.selectedPaths=new Set(pruned);if(state.selectionAnchorPath&&!state.selectedPaths.has(state.selectionAnchorPath))state.selectionAnchorPath=pruned.length?pruned[pruned.length-1]:null;}
  function onMultiSelect(path, info){
    const next=updateMultiSelectionState({selectedPaths:selectedPathList(),selectedPath:state.selectedPath,selectionAnchorPath:state.selectionAnchorPath},path,info||{});
    state.selectedPaths=new Set(next.selectedPaths);
    state.selectedPath=next.selectedPath;
    state.selectionAnchorPath=next.selectionAnchorPath;
    state.scrollSelected=true;
    scheduleRender();
  }
  function onContext(item, ev){
    const proposals=filterPatternProposals(item);
    if(!proposals.length) return;
    showFilterPreview("Add path to Filters",proposals,proposals.length>1?"single":"multi");
  }
  function onAddSelectedFilters(){
    const patterns=exactPatternsForSelection(selectedPathList());
    if(patterns.length<2) return;
    showFilterPreview("Add selected paths to Filters",patterns.map(path=>({label:"Exact path",pattern:path,kind:"exact"})),"multi");
  }
  function renderBreadcrumb(){ while(el.crumb.firstChild) el.crumb.removeChild(el.crumb.firstChild); for(const seg of breadcrumbSegments(state.currentRoot)){ const b=document.createElement("button"); b.type="button"; b.textContent=seg.label; b.addEventListener("click",()=>{state.currentRoot=seg.path; preserveSelection(); scheduleRender();}); el.crumb.appendChild(b); } el.up.disabled=state.currentRoot==="/"; }
  function render(){ pending=false; lastSnap=agg.snapshot({metric:state.metric,include_filtered:state.includeFiltered}); const rootNode=AiObserveTreemap.findNode(lastSnap.tree,state.currentRoot)||{path:state.currentRoot,name:state.currentRoot,isDir:true,children:[],bytes:0,events:0,recent:0,last_touched_ms:0}; preserveSelection(); pruneSelections(lastSnap.tree); renderBreadcrumb(); renderSelectionControls(); el.counts.textContent=lastSnap.total_event_count+" events, "+lastSnap.filtered_event_count+" filtered"; for(const b of el.metric.querySelectorAll("button")) b.classList.toggle("active", b.dataset.metric===state.metric); AiObserveTreemap.renderTreemap(el.treemap,{rootNode:rootNode,state:state,onSelect:onSelect,onHover:onHover,onDrill:onDrill,onContext:onContext,onMultiSelect:onMultiSelect}); AiObserveTable.renderTable(el.table,{rootNode:rootNode,state:state,latestTsMs:lastSnap.latest_ts_ms,onSelect:onSelect,onHover:onHover,onToggle:onToggle,onSort:onSort,onContext:onContext,onMultiSelect:onMultiSelect}); state.scrollSelected=false; }
  function scheduleRender(){ if(pending) return; pending=true; setTimeout(render,250); }
  function onSelect(p){ state.selectedPath=p; state.selectedPaths=new Set(); state.selectionAnchorPath=null; state.scrollSelected=true; scheduleRender(); }
  function onHover(p){ state.hoveredPath=p; scheduleRender(); }
  function onDrill(p){ state.currentRoot=p; state.expanded.add(p); preserveSelection(); scheduleRender(); }
  function onToggle(p){ if(state.expanded.has(p)) state.expanded.delete(p); else state.expanded.add(p); scheduleRender(); }
  function onSort(col){ if(state.sort.column===col) state.sort.dir=state.sort.dir==="asc"?"desc":"asc"; else state.sort={column:col,dir:col==="path"?"asc":"desc"}; state.scrollSelected=true; scheduleRender(); }
  el.metric.addEventListener("click",ev=>{ const b=ev.target.closest("button[data-metric]"); if(!b) return; state.metric=b.dataset.metric; state.scrollSelected=true; scheduleRender(); });
  el.filtered.addEventListener("change",()=>{ state.includeFiltered=el.filtered.checked; state.includeNoise=state.includeFiltered; preserveSelection(); scheduleRender(); });
  el.filtersButton.addEventListener("click",()=>{ toggleFilterEditor(); });
  el.filtersClose.addEventListener("click",()=>{ toggleFilterEditor(false); });
  el.filtersAddForm.addEventListener("submit",ev=>{ ev.preventDefault(); if(commitFilterResult(addFilterPattern(state.filterPatterns,el.filtersNewPattern.value,AiObserveAggregator))){ el.filtersNewPattern.value=""; el.filtersNewPattern.focus(); } });
  el.filtersReset.addEventListener("click",()=>{ commitFilterResult(resetFilterPatterns(AiObserveAggregator)); });
  el.addSelectedFilters.addEventListener("click",onAddSelectedFilters);
  el.filterPreviewForm.addEventListener("submit",ev=>{ ev.preventDefault(); commitFilterPreview(); });
  el.filterPreviewCancel.addEventListener("click",closeFilterPreview);
  el.up.addEventListener("click",()=>{ const p=parentPath(state.currentRoot); if(p){ state.currentRoot=p; preserveSelection(); scheduleRender(); }});
  const es=new EventSource("/events");function onIngested(n){if(n>0){state.lastAppendAtMs=performance.now();scheduleRender();}}
  es.addEventListener("append",ev=>{ onIngested(ingestAppendData(ev.data,eventBuffer,agg)); });
  es.addEventListener("append_batch",ev=>{ onIngested(ingestAppendBatchData(ev.data,eventBuffer,agg)); });
  es.addEventListener("shutdown",()=>{ state.liveStatus="shutdown"; updateBadge(); es.close(); });
  es.onerror=()=>{ if(state.liveStatus!=="shutdown") state.liveStatus="idle"; updateBadge(); };
  function updateBadge(){ const b=liveBadgeState(state.lastAppendAtMs,state.liveStatus,performance.now()); el.badge.textContent=b.text; el.badge.className=b.className; }
  setInterval(updateBadge,500); updateBadge(); renderFilterEditor(); renderFilterPreview(); render();
})(typeof self!=="undefined"?self:(typeof globalThis!=="undefined"?globalThis:this));
