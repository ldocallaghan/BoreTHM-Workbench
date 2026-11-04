// Display selection only: these functions never invoke or emulate a solver.
export function savedIndex(times:number[], requested:number):number {
  if(!times.length)return 0;
  let lo=0,hi=times.length;
  while(lo<hi){const mid=(lo+hi)>>>1;if(times[mid]<=requested)lo=mid+1;else hi=mid;}
  return Math.max(0,lo-1); // At duplicate event times choose the explicit right side.
}

export function eventIndex(events:{left_index?:number|null;right_index?:number|null}[],index:number,direction:-1|1):number {
  const indices=[...new Set(events.flatMap(e=>[e.left_index,e.right_index]).filter((i):i is number=>i!=null))].sort((a,b)=>a-b);
  return direction===1 ? indices.find(i=>i>index)??index : indices.filter(i=>i<index).at(-1)??index;
}

export function stateSegments<T>(times:number[],values:T[],states:string[]):{x:number[];y:T[]}[] {
  const result:{x:number[];y:T[]}[]=[];
  for(let i=0;i<times.length;i++){
    if(!i||states[i]!==states[i-1])result.push({x:[],y:[]});
    result.at(-1)!.x.push(times[i]);result.at(-1)!.y.push(values[i]);
  }
  return result;
}
