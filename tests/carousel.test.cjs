const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
function element(dataset = {}) {
  return {dataset, hidden:false, textContent:'', attrs:{}, events:{}, active:false,
    classList:{toggle(){}}, setAttribute(name,value){this.attrs[name]=value;},
    addEventListener(name,fn){this.events[name]=fn;}};
}
function fixture(reduced = false) {
  const slides=[element(),element(),element()];
  const dots=[0,1,2].map(i=>element({go:String(i)}));
  const controls={prev:element(),next:element(),pause:element(),count:element(),status:element()};
  const root=element({autoplay:'5000'});
  root.contains=()=>false;
  root.querySelectorAll=q=>q==='[data-slide]'?slides:dots;
  root.querySelector=q=>({'[data-prev]':controls.prev,'[data-next]':controls.next,'[data-pause]':controls.pause,'[data-carousel-count]':controls.count,'[data-carousel-status]':controls.status})[q];
  const listeners={};
  const document={hidden:false,querySelectorAll:q=>q==='[data-carousel]'?[root]:[],addEventListener:(name,fn)=>listeners[name]=fn};
  const timers=new Map();let id=0;
  const window={matchMedia:()=>({matches:reduced,addEventListener(){}}),setInterval:fn=>{timers.set(++id,fn);return id;},clearInterval:key=>timers.delete(key)};
  vm.runInNewContext(code,{window,document});
  return {slides,dots,root,controls,timers,document,listeners};
}
const f=fixture();
assert.equal(f.slides[0].hidden,false);
assert.equal(f.slides[1].hidden,true);
assert.equal(f.timers.size,1);
[...f.timers.values()][0]();
assert.equal(f.slides[1].hidden,false);
f.controls.next.events.click();
assert.equal(f.slides[2].hidden,false);
assert.equal(f.controls.count.textContent,'3 / 3');
assert.equal(f.timers.size,0);
f.controls.pause.events.click();assert.equal(f.timers.size,1);
f.root.events.mouseenter();assert.equal(f.timers.size,0);
f.root.events.mouseleave();assert.equal(f.timers.size,1);
f.document.hidden=true;f.listeners.visibilitychange();assert.equal(f.timers.size,0);
f.document.hidden=false;f.listeners.visibilitychange();assert.equal(f.timers.size,1);
f.dots[0].events.click();assert.equal(f.slides[0].hidden,false);assert.equal(f.timers.size,0);
f.root.events.keydown({key:'ArrowLeft',preventDefault(){}});assert.equal(f.slides[2].hidden,false);
const quiet=fixture(true);assert.equal(quiet.timers.size,0);
quiet.controls.next.events.click();assert.equal(quiet.slides[1].hidden,false);
console.log('Carrusel: avance automático, controles, pausa, pestaña oculta y movimiento reducido verificados.');
