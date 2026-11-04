// UI identity and selection only. Scientific identity is computed by Python.
export function fingerprint(value: unknown): string {
  const sorted = (item: unknown): unknown => Array.isArray(item) ? item.map(sorted) : item && typeof item === 'object'
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => [key, sorted(value)])) : item;
  return JSON.stringify(sorted(value));
}

export function completionMatches(selectedId: string | null, completedId: string, submitted: string, draft: unknown): boolean {
  return selectedId === completedId && submitted === fingerprint(draft);
}

export function sample(md: number[], values: number[], depth: number): number | undefined {
  if (!Number.isFinite(depth) || depth < md[0] || depth > md.at(-1)!) return undefined;
  const i = md.findIndex(value => value >= depth);
  if (i < 0) return undefined;
  if (i === 0 || md[i] === depth) return values[i];
  const fraction = (depth - md[i-1]) / (md[i] - md[i-1]);
  return values[i-1] + fraction * (values[i] - values[i-1]);
}

export function adjacentComponents(lengths: {id:string; length_m:number}[], depth: number): string[] {
  let top = 0;
  return lengths.filter(component => {
    const bottom = top + component.length_m;
    const hit = depth >= top - 1e-7 && depth <= bottom + 1e-7;
    top = bottom;
    return hit;
  }).map(component => component.id);
}

export function inputChanges(baseline:unknown,variant:unknown,path=''): {path:string;baseline:unknown;variant:unknown}[] {
  if(fingerprint(baseline)===fingerprint(variant))return [];
  if(baseline&&variant&&typeof baseline==='object'&&typeof variant==='object'&&!Array.isArray(baseline)&&!Array.isArray(variant)){
    const a=baseline as Record<string,unknown>,b=variant as Record<string,unknown>;
    return [...new Set([...Object.keys(a),...Object.keys(b)])].sort().flatMap(key=>inputChanges(a[key],b[key],path+'/'+key));
  }
  return [{path,baseline,variant}];
}
