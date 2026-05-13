"use strict";
(function(root){
  function metricValue(n, metric){ return metric==="events"?n.events:metric==="recent"?n.recent:n.bytes; }
  function relativeTime(ms, latest){
    if(!ms) return ""; const d=Math.max(0, Math.round(((latest||ms)-ms)/1000));
    if(d<60) return d+"s ago"; const m=Math.round(d/60); if(m<60) return m+"m ago"; return Math.round(m/60)+"h ago";
  }
  function sortedChildren(node, sort){
    const arr=(node.children||[]).slice(); const key=sort.column, dir=sort.dir==="asc"?1:-1;
    arr.sort((a,b)=>{
      let av = key==="path" ? (a.name||a.path) : key==="last" ? a.last_touched_ms : metricValue(a,key);
      let bv = key==="path" ? (b.name||b.path) : key==="last" ? b.last_touched_ms : metricValue(b,key);
      if(typeof av==="string") return dir*av.localeCompare(bv);
      return dir*((av||0)-(bv||0)) || (a.name||"").localeCompare(b.name||"");
    });
    return arr;
  }
  function renderTable(el, opts){
    const rootNode=opts.rootNode, state=opts.state, latest=opts.latestTsMs||0;
    while(el.firstChild) el.removeChild(el.firstChild);
    const table=document.createElement("table");
    const thead=document.createElement("thead"); const hr=document.createElement("tr");
    const heads=[["path","Path"],["bytes","Bytes written"],["events","Events"],["last","Last touched"]];
    for(const h of heads){ const th=document.createElement("th"); const b=document.createElement("button"); b.type="button"; b.textContent=h[1]+(state.sort.column===h[0]?(state.sort.dir==="asc"?" ▲":" ▼"):""); b.addEventListener("click",()=>opts.onSort(h[0])); th.appendChild(b); hr.appendChild(th); }
    thead.appendChild(hr); table.appendChild(thead);
    const tbody=document.createElement("tbody"); table.appendChild(tbody); el.appendChild(table);
    function row(n, depth){
      const tr=document.createElement("tr"); tr.dataset.path=n.path; tr.className=(n.isDir?"dir":"file")+(n.path===state.selectedPath?" selected":"")+(n.path===state.hoveredPath?" hovered":"");
      const pathTd=document.createElement("td"); pathTd.style.paddingLeft=(depth*18+6)+"px";
      const btn=document.createElement("button"); btn.type="button"; btn.className="row-path"; btn.textContent=(n.isDir?(state.expanded.has(n.path)?"▾ ":"▸ "):"  ")+(n.name||n.path);
      btn.addEventListener("click",()=>{ opts.onSelect(n.path); if(n.isDir) opts.onToggle(n.path); }); pathTd.appendChild(btn); tr.appendChild(pathTd);
      for(const text of [String(n.bytes||0), String(n.events||0), relativeTime(n.last_touched_ms, latest)]){ const td=document.createElement("td"); td.textContent=text; tr.appendChild(td); }
      tr.addEventListener("mouseenter",()=>opts.onHover(n.path)); tr.addEventListener("mouseleave",()=>opts.onHover(null)); tbody.appendChild(tr);
      if(n.isDir && state.expanded.has(n.path)){ for(const c of sortedChildren(n,state.sort)) row(c, depth+1); }
    }
    if(rootNode){ for(const c of sortedChildren(rootNode,state.sort)) row(c,0); }
    const sel = state.selectedPath && tbody.querySelector('[data-path="'+(window.CSS&&CSS.escape?CSS.escape(state.selectedPath):state.selectedPath.replace(/"/g,'\\"'))+'"]');
    if(sel && state.scrollSelected) sel.scrollIntoView({block:"nearest"});
  }
  const api={renderTable:renderTable, sortedChildren:sortedChildren, relativeTime:relativeTime};
  if(typeof module!=="undefined"&&module.exports) module.exports=api; else root.AiObserveTable=api;
})(typeof self!=="undefined"?self:this);
