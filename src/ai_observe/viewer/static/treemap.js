"use strict";
(function(root){
  function metricValue(n, metric){
    const v = metric === "events" ? n.events : metric === "recent" ? n.recent : n.bytes;
    return (typeof v === "number" && v > 0) ? v : 0;
  }
  function colorFor(n){
    if(n.isDir) return "#d8dee9";
    const m = /\.([^.\/]+)$/.exec(n.name || n.path || "");
    const ext = m ? m[1].toLowerCase() : "";
    const pal = {js:"#f6d365",py:"#8ec5fc",md:"#c3aed6",json:"#fbc2eb",txt:"#a1c4fd",html:"#ffb199",css:"#b8e994"};
    return pal[ext] || "#cfd8dc";
  }
  function worst(row, side){
    if(!row.length) return Infinity;
    let sum=0, min=Infinity, max=0;
    for(const r of row){ sum+=r.area; min=Math.min(min,r.area); max=Math.max(max,r.area); }
    if(sum<=0 || side<=0) return Infinity;
    const s2 = side*side;
    return Math.max((s2*max)/(sum*sum), (sum*sum)/(s2*min));
  }
  function layoutRow(row, rect, out){
    let sum=0; for(const r of row) sum += r.area;
    if(sum<=0) return;
    if(rect.w >= rect.h){
      const h = sum / rect.w; let x = rect.x;
      for(const r of row){ const w = r.area / h; out.push({node:r.node,x:x,y:rect.y,w:w,h:h}); x += w; }
      rect.y += h; rect.h -= h;
    }else{
      const w = sum / rect.h; let y = rect.y;
      for(const r of row){ const h = r.area / w; out.push({node:r.node,x:rect.x,y:y,w:w,h:h}); y += h; }
      rect.x += w; rect.w -= w;
    }
  }
  function squarify(items, rect){
    const total = items.reduce((s,it)=>s+it.value,0);
    if(total<=0 || rect.w<=0 || rect.h<=0) return [];
    const area = rect.w*rect.h;
    const scaled = items.map(it=>({node:it.node, area:it.value*area/total}));
    const pending = scaled.slice().sort((a,b)=>b.area-a.area || String(a.node.path).localeCompare(String(b.node.path)));
    const out = []; let row = []; const r = {x:rect.x,y:rect.y,w:rect.w,h:rect.h};
    while(pending.length){
      const item = pending[0]; const side = Math.min(r.w,r.h);
      if(!row.length || worst(row.concat([item]), side) <= worst(row, side)) { row.push(item); pending.shift(); }
      else { layoutRow(row, r, out); row = []; }
    }
    layoutRow(row, r, out);
    return out;
  }
  function layoutTreemap(node, width, height, metric){
    const rects=[];
    function rec(n, x, y, w, h, depth){
      if(!n || !n.children || w<=0 || h<=0) return;
      const inset = depth===0 ? 0 : 3;
      const ix=x+inset, iy=y+inset, iw=Math.max(0,w-2*inset), ih=Math.max(0,h-2*inset);
      const items = n.children.map(c=>({node:c, value:metricValue(c, metric)})).filter(i=>i.value>0);
      for(const cell of squarify(items, {x:ix,y:iy,w:iw,h:ih})){
        const c = cell.node;
        rects.push({path:c.path, name:c.name, isDir:!!c.isDir, x:cell.x, y:cell.y, w:cell.w, h:cell.h, color:colorFor(c), bytes:c.bytes, events:c.events, recent:c.recent, last_touched_ms:c.last_touched_ms});
        if(c.isDir) rec(c, cell.x, cell.y, cell.w, cell.h, depth+1);
      }
    }
    rec(node,0,0,width,height,0);
    return rects;
  }
  function findNode(node, path){
    if(!node) return null; if(node.path===path) return node;
    for(const c of (node.children||[])){ const f=findNode(c,path); if(f) return f; }
    return null;
  }
  function renderTreemap(el, opts){
    const rootNode = opts.rootNode, state = opts.state, metric = state.metric;
    const w = Math.max(1, el.clientWidth || 800), h = Math.max(1, el.clientHeight || 500);
    while(el.firstChild) el.removeChild(el.firstChild);
    const rects = layoutTreemap(rootNode, w, h, metric);
    if(!rects.length){ const msg=document.createElement("div"); msg.className="empty"; msg.textContent="No paths under "+state.currentRoot; el.appendChild(msg); return; }
    for(const r of rects){
      const d=document.createElement("button"); d.type="button"; d.className="tile"+(r.isDir?" dir":" file");
      if(r.path===state.selectedPath) d.className += " selected"; if(r.path===state.hoveredPath) d.className += " hovered";
      d.style.left=r.x+"px"; d.style.top=r.y+"px"; d.style.width=Math.max(0,r.w)+"px"; d.style.height=Math.max(0,r.h)+"px"; d.style.backgroundColor=r.color;
      d.title = r.path+"\nBytes: "+r.bytes+"\nEvents: "+r.events+"\nLast touched: "+(r.last_touched_ms||0);
      const label=document.createElement("span"); label.textContent=r.name||r.path; d.appendChild(label);
      d.addEventListener("mouseenter",()=>opts.onHover(r.path)); d.addEventListener("mouseleave",()=>opts.onHover(null));
      d.addEventListener("click",()=>{ if(r.isDir) opts.onDrill(r.path); else opts.onSelect(r.path); });
      el.appendChild(d);
    }
  }
  const api={layoutTreemap:layoutTreemap, findNode:findNode, renderTreemap:renderTreemap};
  if(typeof module!=="undefined"&&module.exports) module.exports=api; else root.AiObserveTreemap=api;
})(typeof self!=="undefined"?self:this);
