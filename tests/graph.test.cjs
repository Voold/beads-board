// Run with node --test tests/graph.test.cjs. No npm packages are needed.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(source); // Check the whole application, including startup code.
const context=vm.createContext({document:{getElementById:()=>({value:''})}});
vm.runInContext(source.split("\n    $('refresh').addEventListener")[0],context);
const {buildGraph,graphEdgePoints,graphRoundedPath,graphSubtreeIds,graphSignature}=context;
const issue=(id,parent=null,blockers=[])=>({id,parent,title:id,issue_type:'task',status:'open',dependencies:blockers.map(from=>({issue_id:id,depends_on_id:from,type:'blocks'}))});

function checkLayout(issues) {
  const layout=buildGraph(issues);
  assert.equal(layout.positions.size,issues.length);
  const entries=[...layout.positions];
  for (let i=0;i<entries.length;i++) {
    const [id,a]=entries[i];
    assert.ok(Number.isFinite(a.x)&&Number.isFinite(a.y),id);
    for (let j=i+1;j<entries.length;j++) {
      const [other,b]=entries[j];
      assert.ok(Math.abs(a.x-b.x)>=240 || Math.abs(a.y-b.y)>=112,`${id} overlaps ${other}`);
    }
  }
  layout.edges.forEach((edge,index)=>{
    const a=layout.positions.get(edge.from),b=layout.positions.get(edge.to);
    if (!(layout.cyclic.has(edge.from)&&layout.cyclic.has(edge.to)&&layout.ranks.get(edge.from)===layout.ranks.get(edge.to))) assert.ok(a.x>b.x,`${edge.from} must be right of ${edge.to}`);
    const points=graphEdgePoints(layout,edge,index);
    assert.ok(!/NaN|Infinity/.test(graphRoundedPath(points)));
    assert.deepEqual(Array.from(points[0]),[a.x,a.y+56]);
    assert.deepEqual(Array.from(points.at(-1)),[b.x+240,b.y+56]);
    for (let i=1;i<points.length;i++) {
      const [x1,y1]=points[i-1],[x2,y2]=points[i];
      assert.ok(x1===x2||y1===y2,'links must follow orthogonal corridors');
      for (const [id,p] of entries) {
        if (id===edge.from||id===edge.to) continue;
        const hits=x1===x2 ? x1>p.x&&x1<p.x+240&&Math.max(y1,y2)>p.y&&Math.min(y1,y2)<p.y+112 : y1>p.y&&y1<p.y+112&&Math.max(x1,x2)>p.x&&Math.min(x1,x2)<p.x+240;
        assert.ok(!hits,`${edge.from} → ${edge.to} crosses card ${id}`);
      }
    }
  });
  return layout;
}

test('both fictional demo projects: directed edges, no overlapping cards or links through cards',()=>{
  const snapshot=JSON.parse(html.match(/<script id="snapshot-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
  for (const project of snapshot.projects) checkLayout(project.issues);
});
test('empty graph, single closed task, missing parent and disconnected groups',()=>{
  assert.equal(checkLayout([]).components,0);
  const layout=checkLayout([{...issue('solo'),status:'closed'},issue('orphan','missing'),issue('root'),issue('child','root')]);
  assert.equal(layout.components,3);
});
test('uneven hierarchy branches stay together and parents align to their children',()=>{
  const tasks=[issue('root'),issue('a','root'),issue('b','root'),issue('a1','a'),issue('a2','a'),issue('a3','a'),issue('b1','b')];
  const layout=checkLayout(tasks);
  assert.ok(layout.positions.get('a3').y<layout.positions.get('b1').y);
  assert.ok(Math.abs(layout.positions.get('a').y-(layout.positions.get('a1').y+layout.positions.get('a3').y)/2)<1);
});
test('duplicate relations, parent-child dependencies, supersedes and cycles',()=>{
  const tasks=[issue('root'),{...issue('child'),dependencies:[{issue_id:'child',depends_on_id:'root',type:'parent-child'},{issue_id:'child',depends_on_id:'root',type:'blocks'},{issue_id:'child',depends_on_id:'root',type:'blocks'}]},issue('a',null,['b']),issue('b',null,['a']),issue('self',null,['self']),{...issue('old'),dependencies:[{issue_id:'old',depends_on_id:'root',type:'supersedes'}]}];
  const layout=checkLayout(tasks);
  assert.deepEqual([...layout.cyclic].sort(),['a','b','self']);
  assert.equal(layout.edges.filter(edge=>edge.from==='root'&&edge.to==='child').length,2);
});
test('shuffled input has identical placement; status changes preserve layout signature',()=>{
  const tasks=[issue('root'),issue('root.2','root'),issue('root.10','root'),issue('other',null,['root.2'])];
  const a=checkLayout(tasks),b=checkLayout(tasks.slice().reverse());
  assert.deepEqual([...a.positions],[...b.positions]);
  assert.equal(graphSignature(tasks),graphSignature(tasks.map(task=>({...task,status:'closed'}))));
  assert.notEqual(graphSignature(tasks),graphSignature([...tasks,issue('new')]));
});
test('subtree movement includes nested children and excludes parents, siblings and blockers',()=>{
  const tasks=[issue('root'),issue('a','root'),issue('b','root'),issue('a1','a'),issue('blocker'),issue('a2','a',['blocker'])];
  const layout=checkLayout(tasks),children=new Map();
  for (const edge of layout.edges) if (edge.type==='parent-child') children.set(edge.from,[...(children.get(edge.from)||[]),edge.to]);
  assert.deepEqual(Array.from(graphSubtreeIds('a',children)).sort(),['a','a1','a2']);
  assert.deepEqual(Array.from(graphSubtreeIds('root',children)).sort(),['a','a1','a2','b','root']);
});
test('random directed graphs retain all tasks, with clear levels and unobstructed routes',()=>{
  let seed=73;
  const random=()=>((seed=(1664525*seed+1013904223)>>>0)/2**32);
  for (let n=0;n<60;n++) {
    const tasks=Array.from({length:24},(_,i)=>issue(`task-${i}`,i&&random()<.65?`task-${Math.floor(random()*i)}`:null));
    for (let i=1;i<tasks.length;i++) for (let j=0;j<i;j++) if (random()<.07) tasks[i].dependencies.push({issue_id:tasks[i].id,depends_on_id:tasks[j].id,type:'blocks'});
    checkLayout(tasks);
  }
});
test('deep hierarchy uses iterative walks without overflowing the stack',()=>{
  const tasks=Array.from({length:3000},(_,i)=>issue(`deep-${i}`,i?`deep-${i-1}`:null));
  const layout=buildGraph(tasks);
  assert.equal(layout.positions.size,tasks.length);
  assert.equal(layout.ranks.get('deep-2999'),2999);
});

test('graph rendering and handlers: arrows, hover, subtree drag, search, zoom and reset',()=>{
  // A small DOM adapter exercises application handlers; this is not a browser layout test.
  function element(dataset={}) {
    const classes=new Set();
    return {dataset,style:{},value:'',clientWidth:1200,clientHeight:700,handlers:{},attributes:{},
      classList:{add:key=>classes.add(key),remove:key=>classes.delete(key),contains:key=>classes.has(key),toggle:(key,on)=>on?classes.add(key):classes.delete(key)},
      addEventListener(name,callback){this.handlers[name]=callback;},
      setAttribute(name,value){this.attributes[name]=value;},
      setPointerCapture(){},getBoundingClientRect(){return {left:0,top:0,width:1200,height:700};},
      querySelectorAll(selector){
        const attr=selector.match(/^\[(data-[\w-]+)\]$/)?.[1];
        if (!attr) return [];
        this.cache ||= new Map();
        if (this.cache.has(attr)) return this.cache.get(attr);
        const key=attr.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase());
        const nodes=[...(this.innerHTML||'').matchAll(new RegExp(`<[^>]+\\s${attr}="([^"]*)"[^>]*>`,'g'))].map(match=>element({[key]:match[1]}));
        this.cache.set(attr,nodes);return nodes;
      }
    };
  }
  const elements=new Map(),board=element(),get=id=>{if (!elements.has(id)) elements.set(id,element());return elements.get(id);};
  const ctx=vm.createContext({document:{getElementById:get},requestAnimationFrame:callback=>callback()});
  vm.runInContext(source.split("\n    $('refresh').addEventListener")[0],ctx);
  const state=vm.runInContext('state',ctx);
  const tasks=[issue('root'),issue('a','root'),issue('b','root'),issue('a1','a'),issue('blocker',null),issue('a2','a',['blocker'])];
  state.projects=[{slug:'test',issues:tasks}];state.view='graph';
  ctx.render=()=>{board.cache=new Map();ctx.renderGraph(board);};
  ctx.render();
  let runtime=vm.runInContext('graphRuntime',ctx);
  assert.equal(runtime.lines.length,5);
  assert.ok(runtime.lines.every(([line])=>line.attributes.d.startsWith('M ')));
  assert.ok(board.innerHTML.includes('marker-end="url(#graph-arrow-parent-child)"'));
  const nodes=board.querySelectorAll('[data-graph-node]'),a=nodes.find(node=>node.dataset.graphNode==='a');
  a.handlers.pointerenter();
  assert.equal(nodes.find(node=>node.dataset.graphNode==='blocker').classList.contains('context-dim'),true);
  a.handlers.pointerleave();
  assert.ok(nodes.every(node=>!node.classList.contains('context-dim')));
  const old=new Map([...runtime.layout.positions].map(([id,p])=>[id,{...p}])),scale=runtime.view.scale;
  a.handlers.pointerdown({button:0,clientX:0,clientY:0,pointerId:1,stopPropagation(){},preventDefault(){}});
  a.handlers.pointermove({clientX:50,clientY:30});a.handlers.pointerup();
  for (const id of ['a','a1','a2']) assert.ok(Math.abs(runtime.layout.positions.get(id).x-old.get(id).x-50/scale)<1e-8);
  for (const id of ['root','b','blocker']) assert.deepEqual({...runtime.layout.positions.get(id)},old.get(id));
  get('search').value='a1';ctx.graphFocusFirstMatch();
  assert.ok(runtime.view.scale>=.8);
  const point=runtime.layout.positions.get('a1');
  assert.ok(Math.abs(runtime.view.x+(point.x+120)*runtime.view.scale-600)<1e-8);
  const before=runtime.view.scale;ctx.graphZoom(1.25,600,350);
  assert.equal(runtime.view.scale,before*1.25);
  get('search').value='';
  board.querySelectorAll('[data-graph-action]').find(node=>node.dataset.graphAction==='layout').handlers.click();
  runtime=vm.runInContext('graphRuntime',ctx);
  for (const [id,p] of old) assert.deepEqual({...runtime.layout.positions.get(id)},p);
  let opened;
  ctx.openDrawer=key=>{opened=key;};
  board.querySelectorAll('[data-graph-node]').find(node=>node.dataset.graphNode==='a1').handlers.keydown({key:'Enter',preventDefault(){}});
  assert.equal(opened,'test:a1');
});
