import type {Generation,Region} from './api';
export type Draft={asset_id:string|null,version_id:string|null,generationType:Generation['type'],prompt:string,protection:Generation['protection_mode'],output:string,platforms:string[],appearanceId?:string,regions:Region[]};
export const defaultPrompt='简洁自然的产品场景，柔和光线，保留产品真实细节';
export const draftKey=(user:string,product:string)=>'visual:draft:v1:'+user+':'+product;
export function readDraft(user:string,product:string):Draft|null{
 try{const raw=sessionStorage.getItem(draftKey(user,product));if(!raw||raw.length>100000)return null;const d=JSON.parse(raw);
 if(!['white_background','scene','lifestyle'].includes(d.generationType)||typeof d.prompt!=='string'||d.prompt.length>4000||!['pixel_exact','none'].includes(d.protection)||!['1024x1024','1536x1024','1024x1536'].includes(d.output)||!Array.isArray(d.platforms)||d.platforms.length>3||!d.platforms.every((p:unknown)=>typeof p==='string'&&['amazon','temu','ozon','ebay','shopify'].includes(p))||!Array.isArray(d.regions)||d.regions.length>20)return null;
 if(d.asset_id!==null&&typeof d.asset_id!=='string'||d.version_id!==null&&typeof d.version_id!=='string'||d.appearanceId!==undefined&&typeof d.appearanceId!=='string')return null;
 for(const r of d.regions){if(!['source_x','source_y','width','height','target_x','target_y'].every(k=>Number.isSafeInteger(r[k])&&r[k]>=0)||r.width===0||r.height===0)return null}
 // Keep editing work from earlier versions, but require a fresh platform choice.
 // A legacy selection must never silently become authorization for another platform.
 const platforms=d.platforms.every((p:string)=>['amazon','temu','ozon'].includes(p))?d.platforms:[];
 return {...d,platforms} as Draft;
 }catch{return null}
}
export function regionsFit(regions:Region[],width:number,height:number){
 return regions.every((r,i)=>r.source_x+r.width<=width&&r.source_y+r.height<=height&&r.target_x+r.width<=width&&r.target_y+r.height<=height&&!regions.some((b,j)=>j!==i&&Math.max(r.target_x,b.target_x)<Math.min(r.target_x+r.width,b.target_x+b.width)&&Math.max(r.target_y,b.target_y)<Math.min(r.target_y+r.height,b.target_y+b.height)));
}
export function writeDraft(user:string,product:string,draft:Draft){try{sessionStorage.setItem(draftKey(user,product),JSON.stringify(draft))}catch{/* Storage can be disabled; server tasks remain authoritative. */}}
export function readSelected(user:string){try{return sessionStorage.getItem('visual:selected-product:'+user)}catch{return null}}
export function writeSelected(user:string,product:string){try{sessionStorage.setItem('visual:selected-product:'+user,product)}catch{}}
