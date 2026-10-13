/** Display identity and physical positions are independent. UI contract v1 only. */
export type ArchiveRecord = {id:string; title:string; url:string; description:string; tags:string[]};
export let records: ArchiveRecord[] = [];
export let archiveColumns: string[] = [];
let groups: number[][] = [];
let rowOrigin = 0;
export function configureRecords(items: ArchiveRecord[], columns?: string[]) {
  records=items; rowOrigin=0;
  archiveColumns=columns || [...new Set(['全部档案','未分类',...items.flatMap(r=>r.tags)])];
  groups=archiveColumns.map((tag,i)=>items.flatMap((r,index)=>i===0||r.tags.includes(tag)||(tag==='未分类'&&!r.tags.length)?[index]:[]));
}
export function columnFiles(lane:number) { return groups[lane] || []; }
export function rebaseRows(shift:number) { rowOrigin+=shift; }
export function logicalRow(row:number) { return row+rowOrigin; }
export function fileLocation(index:number,lane?:number) {
  const group=lane!==undefined && columnFiles(lane).includes(index)?lane:groups.findIndex(g=>g.includes(index));
  if(group<0) throw new RangeError('No archive for this display index');
  const row=12+columnFiles(group).indexOf(index)-rowOrigin;
  // Slot is only a physical pool address; never a business identity.
  return {lane:group,row,slot:((group%9+9)%9)*64+((row%64+64)%64)};
}
