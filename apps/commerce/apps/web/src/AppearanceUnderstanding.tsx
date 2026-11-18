import {useEffect,useRef,useState} from 'react';
import {Alert,App,Button,Checkbox,Form,Input,Modal,Select,Space,Typography} from 'antd';
import {api,post,errors} from './api';

type Summary={description:string,colors:string[],materials:string[],uncertainties:string[]};
type Understanding={id:string,status:'quoted'|'submitting'|'succeeded'|'failed'|'uncertain',error_code?:string,result:Summary|null,confirmed_profile_id:string|null,source_asset_version_id:string,reference_rates_per_million_tokens?:Record<string,string>};
export type UnderstoodAppearance={id:string,name:string,config:{description:string,colors:string[],materials:string[],source_asset_version_id?:string,understanding_id?:string},revision:number};

export function AppearanceUnderstanding({product,source,live,disabled,onConfirmed}:{product:string,source:string,live:boolean,disabled:boolean,onConfirmed:(profile:UnderstoodAppearance)=>void}){
 const {message}=App.useApp();const [open,setOpen]=useState(false);const [busy,setBusy]=useState(false);const [record,setRecord]=useState<Understanding>();const [quote,setQuote]=useState<Understanding>();const [accepted,setAccepted]=useState(false);const [reviewed,setReviewed]=useState(false);const [form]=Form.useForm();const alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const failure=(e:unknown)=>message.error(errors[(e as {code?:string})?.code||'']||'操作未完成，请刷新识别记录核对，勿重复提交');
 const update=(value:Understanding)=>{if(!alive.current)return;setRecord(value);setReviewed(false);if(value.result)form.setFieldsValue({name:'AI外观 · 待校订',...value.result})};
 const load=async()=>{try{const value=await api<{items:Understanding[]}>('/appearance-understandings?product_id='+product+'&source_asset_version_id='+source);if(alive.current&&value.items[0]){const latest=value.items[0];update(latest);if(latest.status==='submitting'||latest.status==='uncertain')update(await post<Understanding>('/appearance-understandings/'+latest.id+'/recover'))}}catch(e){if(alive.current)failure(e)}};
 const pending=record?.status==='submitting'||record?.status==='uncertain';
 const start=async()=>{setBusy(true);try{const value=await post<Understanding>('/appearance-understandings/quotes',{product_id:product,source_asset_version_id:source});if(alive.current){setQuote(value);setAccepted(false)}}catch(e){if(alive.current)failure(e)}finally{if(alive.current)setBusy(false)}};
 const submit=async()=>{if(!quote||!accepted||busy)return;const intent=quote;setQuote(undefined);update({...intent,status:'submitting'});setBusy(true);try{update(await post<Understanding>('/appearance-understandings/'+intent.id+'/submit',undefined,{'X-Provider-Confirmation':'bailian_live'}))}catch(e){if(alive.current){failure(e);try{update(await api<Understanding>('/appearance-understandings/'+intent.id))}catch{/* Preserve unresolved state if readback is unavailable. */}}}finally{if(alive.current)setBusy(false)}};
 const save=async(values:Summary&{name:string})=>{if(!record||busy||!reviewed)return;setBusy(true);try{const value=await post<UnderstoodAppearance>('/appearance-understandings/'+record.id+'/confirm',{confirmed:true,name:values.name,config:{description:values.description,colors:values.colors||[],materials:values.materials||[]}});if(alive.current){onConfirmed(value);setOpen(false);message.success('外观描述已确认，已选为重绘参考')}}catch(e){if(alive.current)failure(e)}finally{if(alive.current)setBusy(false)}};
 return <><Button type="primary" size="large" disabled={disabled||!live} onClick={()=>{setOpen(true);load()}}>AI 理解产品外观</Button>{!live&&<small>AI 自动理解需连接真实视觉供应商；可手工填写外观资料。</small>}
 <Modal title="AI 理解产品外观" open={open} onCancel={()=>setOpen(false)} footer={null} width={650} destroyOnHidden={false}>
 <Typography.Paragraph>先理解当前商品原图，生成可编辑的外观文字；确认后将原图和文字共同用于重绘。无需划区或贴片，文字约束不保证像素级一致。</Typography.Paragraph>
 <Space><Button onClick={start} loading={busy} disabled={!!pending}>获取外观理解费用说明</Button><Button onClick={load} disabled={busy}>刷新识别记录</Button></Space>
 {record&&<Typography.Paragraph copyable={{text:record.id}}>识别编号：{record.id}</Typography.Paragraph>}
 {pending&&<Alert showIcon type="warning" message="请求已提交，结果尚未确认" description="不要重复识别。若页面曾中断，可刷新识别记录读取已有结果；长期未完成需管理员核对原始回执。此操作不会生成商品图片。"/>}
 {record?.status==='failed'&&<Alert type="error" showIcon message={record.error_code==='manual_delivery_closed'?'本次识别交付已人工关闭，供应商费用仍待核实。':'供应商明确拒绝本次识别，请检查模型权限或配置。'}/>}
 {record?.status==='succeeded'&&record.result&&<>
 <Alert type="info" showIcon message="AI 外观草稿 · 请核对原图并校订后确认" description="重点检查形状比例、配色位置、材质、部件关系和标识；场景描述请放在工作台的画面描述中。"/>
 {record.result.uncertainties.length>0&&<Alert type="warning" message="AI 无法确定的细节" description={record.result.uncertainties.join('；')}/>}
 {record.confirmed_profile_id&&<Alert type="info" message="此识别已确认。如需修改已保存的文字，请使用工作台的编辑资料。"/>}<Form disabled={!!record.confirmed_profile_id} form={form} layout="vertical" onFinish={save} onValuesChange={()=>setReviewed(false)}>
 <Form.Item name="name" label="外观资料名称" rules={[{required:true,whitespace:true}]}><Input maxLength={255}/></Form.Item>
 <Form.Item name="description" label="外观文字约束" rules={[{required:true,whitespace:true}]}><Input.TextArea rows={8} maxLength={4000}/></Form.Item>
 <Form.Item name="colors" label="颜色与分布"><Select mode="tags"/></Form.Item><Form.Item name="materials" label="材质与纹理"><Select mode="tags"/></Form.Item>
 <Checkbox checked={reviewed} onChange={e=>setReviewed(e.target.checked)}>我已对照原图校订，确认将这份文字用于重绘（不启用像素回贴）</Checkbox>
 <Button htmlType="submit" type="primary" loading={busy} disabled={!reviewed}>确认外观描述并使用</Button>
 </Form></>}
 </Modal>
 <Modal title="确认外观理解费用" open={!!quote} onCancel={()=>setQuote(undefined)} onOk={submit} okText="确认费用并识别一次" okButtonProps={{disabled:!accepted}}>
 <p>百炼 qwen3.8-flash 将接收当前商品原图，执行一次外观理解；不会重新生成图片。平台积分扣除：0。供应商按实际 Token 收费，实际金额待账单核实。</p>
 {quote?.reference_rates_per_million_tokens&&<p>参考费率（元 / 百万 Token）：输入 {quote.reference_rates_per_million_tokens.input_token}，输出 {quote.reference_rates_per_million_tokens.output_token}，缓存输入 {quote.reference_rates_per_million_tokens.input_token_cache}。</p>}
 <Checkbox checked={accepted} onChange={e=>setAccepted(e.target.checked)}>确认本次独立付费识别及原图传给百炼</Checkbox>
 </Modal></>;
}
