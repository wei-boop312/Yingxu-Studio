export type Sku={id:string,product_id:string,code:string,name:string};
export type Reference={id:string,asset_version_id:string,role:string,physical_face:string,confirmed:boolean,note:string,sha256:string};
export type Fact={id:string,fact_key:string,revision:number,label:string,value:string,status:string,conditions:string,reference_ids:string[]};
export type Plan={goal:string,copy:string,allowed_faces:string[],route:string,facts:{id:string}[],references:{id:string}[]};
export type Slot={id:string,display_number:number,title:string,purpose:string,required_fact_keys:string[],plan_id:string|null,plan_stale:boolean,plan:Plan|null,current_output_id:string|null,readiness:{status:string,reasons:string[]}};
export type Output={id:string,slot_id:string,plan_id:string,sha256:string,route:string,review_state:string,eligible:boolean,review_id:string|null,provenance:{source_note:string},width:number,height:number};
export type Delivery={id:string,status:string,mode:string,manifest:{scope:string,delivered_count:number,total_slots:number,pending_slots:{slot_number:number,reason:string}[]},created_at:string};
export type Project={id:string,product_id:string,sku_id:string,title:string,marketplace:string,platform:string,language:string,category:string,image_use:string,revision:number,
 output_spec:{width:number,height:number,format:string,max_bytes:number},facts:Fact[],references:Reference[],slots:Slot[],archived_slots:Slot[],outputs:Output[],deliveries:Delivery[]};
export type ProjectRow={id:string,title:string,sku_code:string,marketplace:string,language:string};
