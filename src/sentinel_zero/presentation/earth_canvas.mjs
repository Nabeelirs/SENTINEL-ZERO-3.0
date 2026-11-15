// Owned offline viewer. Pixels are verified PNG display derivatives, never a new analysis.
export function fitTransform(width, height, columns, rows, padding = 44) {
  if (![width,height,columns,rows,padding].every(Number.isFinite) || columns<=0 || rows<=0 || width<=0 || height<=0) throw Error('Invalid viewport geometry');
  const scale=Math.max(0.01,Math.min(Math.max(1,width-2*padding)/columns,Math.max(1,height-2*padding)/rows));
  return {scale,x:(width-columns*scale)/2,y:(height-rows*scale)/2};
}
export function pixelAt(x,y,view,columns,rows) {
  if (!view || ![x,y,view.scale,view.x,view.y].every(Number.isFinite) || view.scale<=0) return null;
  if (x<view.x || y<view.y || x>=view.x+columns*view.scale || y>=view.y+rows*view.scale) return null;
  const column=Math.floor((x-view.x)/view.scale),row=Math.floor((y-view.y)/view.scale);
  return row>=0&&column>=0&&row<rows&&column<columns?{row,column}:null;
}
export function pixelCentre(row,column,t) {
  return {easting:t[2]+(column+.5)*t[0]+(row+.5)*t[1],northing:t[5]+(column+.5)*t[3]+(row+.5)*t[4]};
}
export function zoomTransform(view,x,y,factor,minScale,maxScale) {
  const scale=Math.max(minScale,Math.min(maxScale,view.scale*factor));
  return {scale,x:x-(x-view.x)*scale/view.scale,y:y-(y-view.y)*scale/view.scale};
}

export default function(component) {
  const {parentElement,data,setStateValue}=component;
  const root=parentElement.querySelector('.earth-viewer');
  if (!root) return;
  if (root.__atlasController) {root.__atlasController.update(data);return;}
  const canvas=root.querySelector('canvas'),ctx=canvas.getContext('2d');
  const text=role=>root.querySelector(`[data-role="${role}"]`);
  const stage=root.querySelector('.canvas-stage'),message=root.querySelector('.scene-message');
  const model=root.__atlasModel || {mode:'AFTER',view:null,split:.5,request:null,cursor:null,identity:null};
  root.__atlasModel=model;
  let payload=data,images={},width=1,height=1,base=null,drag=null,destroyed=false,ready=false;
  const listeners=[];
  const listen=(target,name,handler,options)=>{target.addEventListener(name,handler,options);listeners.push(()=>target.removeEventListener(name,handler,options));};
  function bounds(){return canvas.getBoundingClientRect();}
  function location(event){const r=bounds();return{x:event.clientX-r.left,y:event.clientY-r.top};}
  function fail(){message.hidden=false;message.textContent='The local observation preview could not load. Use the static scientific comparison below; no substitute measurements are shown.';}
  function coordinate(point){
    if(!point){root.querySelector('.coordinate-readout').textContent='Outside the recorded raster footprint; no measurement at this pointer location.';return;}
    const c=pixelCentre(point.row,point.column,payload.grid.transform);
    root.querySelector('.coordinate-readout').textContent=`R ${point.row} · C ${point.column}   E ${c.easting.toFixed(1)} m · N ${c.northing.toFixed(1)} m`;
  }
  function legend(){
    const change=model.mode==='CHANGE',image=payload.images[change?'CHANGE':'BEFORE'];
    const [lo,hi]=image.limits,mid=(lo+hi)/2;
    text('legend-title').textContent=change?'NISAR HHHH Backscatter Change (dB)':'Terrain-corrected gamma-zero power · dB display';
    root.querySelector('.ramp').style.background=`linear-gradient(to right,${image.colours.join(',')})`;
    text('legend-min').textContent=`${lo} dB`;text('legend-mid').textContent=`${mid} dB`;text('legend-max').textContent=`+${hi} dB`;
    const count=model.mode==='COMPARE'?`${payload.images.BEFORE.saturated.toLocaleString('en-US')} before / ${payload.images.AFTER.saturated.toLocaleString('en-US')} after`:payload.images[model.mode].saturated.toLocaleString('en-US');
    text('display-note').textContent=`Display saturation only: ${count} eligible cells. Saved scientific extremes remain unchanged. ${change?'Purple = lower, teal = greater power; physical cause unverified.':'Both dates use the same −25 to +15 dB scale.'}`;
  }
  function setMode(mode){
    if(!['BEFORE','AFTER','CHANGE','COMPARE'].includes(mode))return;
    model.mode=mode;root.dataset.mode=mode;
    root.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===mode)));
    text('scene-title').textContent=mode==='CHANGE'?'October 18 − October 6 · Backscatter change':mode==='COMPARE'?'October 6 ↔ October 18 · Aligned comparison':`${mode==='BEFORE'?payload.before_date:payload.after_date} · HHHH backscatter`;
    root.querySelector('.comparison-control').hidden=mode!=='COMPARE';
    root.querySelector('.compare-badges').hidden=mode!=='COMPARE';
    legend();draw();
  }
  function reset(){
    base=fitTransform(width,height,payload.grid.width,payload.grid.height,width<500?36:48);
    model.view={...base};draw();
  }
  function resize(){
    const r=stage.getBoundingClientRect();if(r.width<1||r.height<1)return;
    const changed=Math.abs((model.viewportWidth||0)-r.width)>1||Math.abs((model.viewportHeight||0)-r.height)>1;
    width=r.width;height=r.height;
    model.viewportWidth=width;model.viewportHeight=height;
    const ratio=Math.min(3,globalThis.devicePixelRatio||1);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    ctx.setTransform(ratio,0,0,ratio,0,0);
    if(changed||!model.view)reset();else draw();
  }
  function draw(){
    if(!ready||!model.view||destroyed)return;
    const v=model.view,g=payload.grid,w=g.width*v.scale,h=g.height*v.scale;
    ctx.clearRect(0,0,width,height);ctx.fillStyle='#070e18';ctx.fillRect(0,0,width,height);
    ctx.imageSmoothingEnabled=false;
    if(model.mode==='COMPARE'){
      const split=v.x+w*model.split;
      ctx.save();ctx.beginPath();ctx.rect(0,0,Math.max(0,split),height);ctx.clip();ctx.drawImage(images.BEFORE,v.x,v.y,w,h);ctx.restore();
      ctx.save();ctx.beginPath();ctx.rect(Math.max(0,split),0,width,height);ctx.clip();ctx.drawImage(images.AFTER,v.x,v.y,w,h);ctx.restore();
      ctx.strokeStyle='#06101a';ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(split,v.y);ctx.lineTo(split,v.y+h);ctx.stroke();
      ctx.strokeStyle='#eefbf8';ctx.lineWidth=1.5;ctx.stroke();
      ctx.fillStyle='#e9fff8';ctx.beginPath();ctx.arc(split,Math.max(25,Math.min(height-25,v.y+h/2)),5,0,Math.PI*2);ctx.fill();
    }else ctx.drawImage(images[model.mode],v.x,v.y,w,h);
    ctx.lineWidth=1;ctx.strokeStyle='#6e98a8';ctx.strokeRect(v.x-.5,v.y-.5,w+1,h+1);
    ctx.font='9px ui-monospace,monospace';ctx.fillStyle='#a9c2d2';ctx.strokeStyle='#496477';
    const step=Math.max(100,Math.ceil(65/v.scale/100)*100);
    for(let c=0;c<=g.width;c+=step){const x=v.x+c*v.scale;if(x<32||x>width-32)continue;const y=Math.min(height-21,v.y+h+10);ctx.textAlign='center';ctx.fillText(Math.round(g.transform[2]+c*g.transform[0]).toLocaleString('en-US'),x,y);}
    for(let r=0;r<=g.height;r+=step){const y=v.y+r*v.scale;if(y<22||y>height-18)continue;ctx.textAlign='right';ctx.fillText(Math.round(g.transform[5]+r*g.transform[4]).toLocaleString('en-US'),Math.max(43,v.x-8),y+3);}
    if(model.cursor){
      const x=v.x+(model.cursor.column+.5)*v.scale,y=v.y+(model.cursor.row+.5)*v.scale;
      ctx.save();ctx.strokeStyle='#07111b';ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(x-11,y);ctx.lineTo(x+11,y);ctx.moveTo(x,y-11);ctx.lineTo(x,y+11);ctx.stroke();ctx.strokeStyle='#fcfff8';ctx.lineWidth=1.4;ctx.stroke();ctx.strokeRect(x-v.scale/2,y-v.scale/2,Math.max(v.scale,2),Math.max(v.scale,2));ctx.restore();
    }
    let distance=1000,length=distance/g.transform[0]*v.scale;
    while(length>width*.22){distance/=2;length/=2;}
    if(length>=20){const x=width-24-length,y=height-19;ctx.strokeStyle='#b6d4dd';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(x,y-3);ctx.lineTo(x,y);ctx.lineTo(x+length,y);ctx.lineTo(x+length,y-3);ctx.stroke();ctx.textAlign='center';ctx.font='9px ui-monospace,monospace';ctx.fillStyle='#b4cbd8';ctx.fillText(`${distance>=1000?distance/1000+' km':distance+' m'} projected`,x+length/2,y-7);}
    root.dataset.imageReady='true';root.dataset.scale=String(v.scale);root.dataset.offsetX=String(v.x);root.dataset.offsetY=String(v.y);root.dataset.split=String(model.split);
  }
  function zoom(factor,x=width/2,y=height/2){if(!base||!model.view)return;model.view=zoomTransform(model.view,x,y,factor,base.scale*.8,base.scale*16);draw();}
  function inspect(point){
    if(!point)return;model.cursor=point;coordinate(point);draw();
    setStateValue('selection',{row:point.row,column:point.column,identity:payload.identity,mode:model.mode,sequence:Date.now()});
  }
  function update(next){
    payload=next;
    const shortDate=value=>{const [year,month,day]=value.split('-');return day+' '+['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][Number(month)-1];};
    // The viewer itself has data-mode. Scope to buttons so an ancestor match
    // cannot overwrite the first date when selection triggers a server rerun.
    root.querySelector('button[data-mode="BEFORE"] small').textContent=shortDate(payload.before_date);
    root.querySelector('button[data-mode="AFTER"] small').textContent=shortDate(payload.after_date);
    text('before-badge').textContent=payload.before_date+' UTC';text('after-badge').textContent=payload.after_date+' UTC';
    const changed=model.identity!==payload.identity;
    if(changed){model.identity=payload.identity;model.view=null;model.cursor=null;images={};ready=false;}
    if(payload.selected&&Number.isInteger(payload.selected.row)&&Number.isInteger(payload.selected.column))model.cursor={row:payload.selected.row,column:payload.selected.column};
    if(payload.request&&payload.request.id!==model.request){model.request=payload.request.id;model.mode=payload.request.mode;}
    if(!Object.keys(images).length){
      message.hidden=false;
      Promise.all(['BEFORE','AFTER','CHANGE'].map(mode=>new Promise((resolve,reject)=>{
        const item=payload.images[mode];if(!item.png.startsWith('data:image/png;base64,')){reject(Error('Only verified embedded PNG supported'));return;}
        const image=new Image();image.onload=()=>{if(image.width!==payload.grid.width||image.height!==payload.grid.height){reject(Error('Grid/image mismatch'));return;}images[mode]=image;resolve();};image.onerror=reject;image.src=item.png;
      }))).then(()=>{if(destroyed)return;ready=true;message.hidden=true;setMode(model.mode);resize();if(model.cursor)coordinate(model.cursor);}).catch(fail);
    }else{setMode(model.mode);if(model.cursor)coordinate(model.cursor);}
  }
  root.querySelectorAll('[data-mode]').forEach(button=>listen(button,'click',()=>setMode(button.dataset.mode)));
  root.querySelectorAll('[data-action]').forEach(button=>listen(button,'click',()=>button.dataset.action==='reset'?reset():zoom(button.dataset.action==='zoom-in'?1.4:1/1.4)));
  const slider=root.querySelector('input[type="range"]');slider.value=String(model.split*100);
  listen(slider,'input',()=>{model.split=Number(slider.value)/100;draw();});
  listen(canvas,'pointerdown',event=>{if(event.button!==0||!model.view)return;const p=location(event);const splitX=model.view.x+payload.grid.width*model.view.scale*model.split;drag={x:p.x,y:p.y,v:{...model.view},moved:false,kind:model.mode==='COMPARE'&&Math.abs(p.x-splitX)<=12?'split':'pan'};if(event.pointerType!=='touch')canvas.setPointerCapture(event.pointerId);});
  listen(canvas,'pointermove',event=>{
    const p=location(event);
    if(drag){const dx=p.x-drag.x,dy=p.y-drag.y;if(Math.hypot(dx,dy)>4){if(event.pointerType==='touch'&&Math.abs(dy)>Math.abs(dx))return;drag.moved=true;if(drag.kind==='split'){model.split=Math.max(0,Math.min(1,(p.x-model.view.x)/(payload.grid.width*model.view.scale)));slider.value=String(Math.round(model.split*100));}else model.view={...drag.v,x:drag.v.x+dx,y:drag.v.y+dy};draw();}}
    coordinate(pixelAt(p.x,p.y,model.view,payload.grid.width,payload.grid.height));
    const line=model.view?model.view.x+payload.grid.width*model.view.scale*model.split:0;
    canvas.style.cursor=model.mode==='COMPARE'&&Math.abs(p.x-line)<=12?'ew-resize':'crosshair';
  });
  listen(canvas,'pointerup',event=>{if(!drag)return;const p=location(event),moved=drag.moved,kind=drag.kind;drag=null;if(!moved&&kind!=='split')inspect(pixelAt(p.x,p.y,model.view,payload.grid.width,payload.grid.height));});
  listen(canvas,'pointercancel',()=>{drag=null;});
  listen(canvas,'wheel',event=>{if(event.ctrlKey){event.preventDefault();const p=location(event);zoom(Math.exp(-event.deltaY*.002),p.x,p.y);}},{passive:false});
  listen(canvas,'keydown',event=>{
    const g=payload.grid,point=model.cursor||{row:Math.floor(g.height/2),column:Math.floor(g.width/2)},step=event.shiftKey?10:1;
    if(event.key.startsWith('Arrow')){event.preventDefault();model.cursor={row:Math.max(0,Math.min(g.height-1,point.row+(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0))),column:Math.max(0,Math.min(g.width-1,point.column+(event.key==='ArrowRight'?step:event.key==='ArrowLeft'?-step:0)))};coordinate(model.cursor);draw();}
    else if(event.key==='Enter'){event.preventDefault();inspect(point);}
    else if(event.key==='+'||event.key==='='){event.preventDefault();zoom(1.4);}
    else if(event.key==='-'){event.preventDefault();zoom(1/1.4);}
    else if(event.key==='0'){event.preventDefault();reset();}
  });
  const observer=new ResizeObserver(resize);observer.observe(stage);
  root.__atlasController={update};
  update(data);resize();
  return()=>{destroyed=true;observer.disconnect();listeners.forEach(remove=>remove());delete root.__atlasController;};
}
