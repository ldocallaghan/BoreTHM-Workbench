import test from 'node:test';
import assert from 'node:assert/strict';
import {rendererPoint,frames,dot,cross,pickedMD,meshDepths,meshCost,fieldValue,pointAt,holeAtMD,ringPath,DISPLAY_TRANSFORM} from '../src/sceneState.ts';
test('engineering transform preserves distance and separates north from TVD',()=>{
  assert.deepEqual(rendererPoint(110,220,300,[100,200,0]),[10,-300,-20]);
  const p=rendererPoint(3,4,12,[0,0,0]);assert.equal(Math.hypot(...p),13);
});
test('right-handed ENU plan view has north up and east right on an asymmetric turn',()=>{
  assert.equal(DISPLAY_TRANSFORM,'enu-y-up-v2');
  const east=rendererPoint(1,0,0,[0,0,0]),north=rendererPoint(0,1,0,[0,0,0]);
  assert.ok(dot(cross(east,north),[0,1,0])>0);
  const turn=[[0,0,0],[0,100,10],[40,160,20],[120,160,40]].map(p=>rendererPoint(p[0],p[1],p[2],[0,0,0]));
  const plan=turn.map(p=>[p[0],-p[2]||0]);
  assert.deepEqual(plan,[[0,0],[0,100],[40,160],[120,160]]);
});
test('hole lookup uses half-open intervals and only final lower endpoint',()=>{
  const holes=[{id:'surface',md_top_m:0,md_bottom_m:100,radius:.3,layers:['steel','cement']},
    {id:'middle',md_top_m:100,md_bottom_m:1000,radius:.2,layers:['steel']},
    {id:'bottom',md_top_m:1000,md_bottom_m:3000,radius:.1,layers:['formation']}];
  for(const [md,id,layers] of [[0,'surface',['steel','cement']],[99.999,'surface',['steel','cement']],
    [100,'middle',['steel']],[100.001,'middle',['steel']],[999.999,'middle',['steel']],
    [1000,'bottom',['formation']],[1000.001,'bottom',['formation']],[3000,'bottom',['formation']]] as const){
    assert.equal(holeAtMD(holes,md)?.id,id);assert.deepEqual(holeAtMD(holes,md)?.layers,layers);
  }
  assert.equal(holeAtMD(holes,3000)?.radius,.1);assert.equal(holeAtMD(holes,3000.001),undefined);
  assert.equal(holeAtMD(holes,-1),undefined);
});
test('cross-section rings have both outer and inner subpaths for an open fluid space',()=>{
  const path=ringPath(20,30);assert.equal((path.match(/M /g)||[]).length,2);
  assert.ok(path.includes('M 120 90'));assert.ok(path.includes('M 110 90'));
});
test('frames remain orthonormal without flips on straight and curved sections',()=>{
  const fs=frames([[0,0,0],[0,-10,0],[0,-20,0],[5,-30,0],[15,-35,1],[25,-35,2]]);
  fs.forEach((f,i)=>{assert.ok(Math.abs(dot(f.normal,f.tangent))<1.e-12);assert.ok(Math.abs(dot(f.normal,f.binormal))<1.e-12);assert.ok(Math.abs(Math.hypot(...f.normal)-1)<1.e-12);if(i)assert.ok(dot(f.normal,fs[i-1].normal)>0);});
});
test('mesh sampling retains every station and material boundary; picking uses MD',()=>{
  const depths=meshDepths(3000,[1111,2222],[100,1000,2700]);
  for(const z of [0,100,1000,1111,2222,2700,3000])assert.ok(depths.includes(z));
  assert.equal(pickedMD(3000,.5),1500);assert.equal(pickedMD(3000,2),3000);
});
test('scene values share chart sampling and never invent bore pressure',()=>{
  const p={md_m:[0,1000],bore_temperature_C:[20,80],annulus_temperature_C:[30,90],annulus_wall_pressure_gauge_Pa:[0,1.e7]};
  assert.equal(fieldValue(p,'temperature','bore',250),35);
  assert.equal(fieldValue(p,'temperature','annulus',250),45);
  assert.equal(fieldValue(p,'pressure','annulus',250),2.5e6);
  assert.equal(fieldValue(p,'pressure','bore',250),undefined);
  assert.equal(fieldValue(p,'temperature','bore',1001),undefined);
});
test('MD remains authoritative for chordal positions, without extrapolation',()=>{
  const t={fully_positioned:true,md_m:[0,1000],easting_m:[100,600],northing_m:[200,200],tvd_m:[0,800]} as Parameters<typeof pointAt>[0];
  assert.deepEqual(pointAt(t,500),[250,-400,0]);assert.equal(pointAt(t,1001),undefined);
});
test('render preflight bounds complex material meshes before GPU allocation',()=>{
  const simple={string:[{length_m:3000,pipe:{layers:[{}]}}],hole:[{md_top_m:0,md_bottom_m:3000,layers:[]}]};
  assert.ok(meshCost(meshDepths(3000,[],[]),simple).withinBudget);
  const complex={string:Array.from({length:500},()=>({length_m:6,pipe:{layers:[{},{}]}})),hole:simple.hole};
  const cost=meshCost(meshDepths(3000,[],Array.from({length:500},(_,i)=>(i+1)*6)),complex);
  assert.ok(cost.meshes>512);assert.equal(cost.withinBudget,false);
});
test('display-only returning paths retain finite frames at a symmetric reversal',()=>{
  for(const f of frames([[0,0,0],[0,-1,0],[0,0,0],[1,0,0]])){
    assert.ok(Math.abs(Math.hypot(...f.tangent)-1)<1.e-12);
    assert.ok(Math.abs(Math.hypot(...f.normal)-1)<1.e-12);
    assert.ok(Math.abs(Math.hypot(...f.binormal)-1)<1.e-12);
  }
});
