export type Allowance={tool_class:string;band_in:number;dp_ref_psi:number;flow_ref_gpm:number;exponent:number;sources:string[]};
export function allowanceFor(rows:Allowance[],toolClass:string,collar:number):Allowance|undefined {
  return rows.filter(row=>row.tool_class===toolClass).sort((a,b)=>Math.abs(a.band_in-collar)-Math.abs(b.band_in-collar)||a.band_in-b.band_in)[0];
}
