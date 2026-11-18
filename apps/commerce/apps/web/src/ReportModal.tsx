import {useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,Form,Modal,Select,Space,Spin,Tag} from 'antd';
import {CheckCircleOutlined} from '@ant-design/icons';
import {api,errorText} from './api';
import type {Page} from './api';

type Version={id:string;is_original:boolean;created_at:string;auto_overall:string};
const status:Record<string,string>={compliant:'通过',non_compliant:'未通过',manual_review:'人工复核'};
const checkLabels:Record<string,string>={pixel_protection:'像素保护',local_decode:'最终图解码',min_width:'最小宽度',min_height:'最小高度',max_width:'最大宽度',max_height:'最大高度',max_bytes:'文件大小',aspect_ratio:'宽高比',white_background:'白底要求',added_text_or_logo:'文字与标记',added_watermark:'水印要求',product_long_edge_ratio:'商品占图比例'};
const reasons:Record<string,string>={rule_evidence_unverified:'平台规则证据尚未核验',rule_set_incomplete:'当前规则集合不完整',analysis_incomplete:'视觉事实识别不完整，需人工检查',visual_uncertainties:'检测存在不确定因素',facts_dimension_mismatch:'检测尺寸与成品不一致',background_uncertain:'背景判断置信度不足',mark_origin_uncertain:'文字或标记来源尚未确定',product_extent_uncertain:'商品范围判断置信度不足',product_identity_requires_review:'需要人工确认商品身份和外观',deterministic_sampling_review:'本任务被选中进行人工抽检',rule_config_invalid:'规则配置需要运营检查',min_width:'图片宽度不足',min_height:'图片高度不足',max_bytes:'文件超过大小限制',max_width:'图片宽度超过规则上限',max_height:'图片高度超过规则上限',aspect_ratio:'图片宽高比不符合规则',white_background:'背景未满足白底要求',added_text_or_logo:'发现规则禁止的文字或标记',added_watermark:'发现规则禁止的水印',product_long_edge_ratio:'商品占图片长边比例不足'};
export function ReportModal({taskId,onClose}:{taskId:string;onClose:()=>void}){
 const [report,setReport]=useState<any>();
 const [versions,setVersions]=useState<Version[]>([]);
 const [cursor,setCursor]=useState<string|null>(null);
 const [selected,setSelected]=useState<string>();
 const [error,setError]=useState<string>();
 const [loading,setLoading]=useState(false);
 const live=useRef(true);
 useEffect(()=>{live.current=true;let active=true;Promise.all([api<any>('/tasks/'+taskId+'/compliance'),api<Page<Version>>('/tasks/'+taskId+'/compliance/versions')]).then(([r,v])=>{if(!active)return;setReport(r);setSelected(r.id);setVersions(v.items);setCursor(v.next_cursor)}).catch(e=>{if(active)setError(errorText(e))});return()=>{active=false;live.current=false}},[taskId]);
 const choice=useRef(0);
 async function choose(id:string){const sequence=++choice.current;setSelected(id);setLoading(true);setError(undefined);try{const r=await api('/tasks/'+taskId+'/compliance?check_id='+id);if(live.current&&sequence===choice.current)setReport(r)}catch(e){if(live.current&&sequence===choice.current){setSelected(report?.id);setError(errorText(e))}}finally{if(live.current&&sequence===choice.current)setLoading(false)}}
 async function more(){try{const v=await api<Page<Version>>('/tasks/'+taskId+'/compliance/versions?cursor='+encodeURIComponent(cursor!));if(!live.current)return;setVersions(old=>[...old,...v.items.filter(x=>!old.some(y=>x.id===y.id))]);setCursor(v.next_cursor)}catch(e){if(live.current)setError(errorText(e))}}
 const options=versions.map(v=>({value:v.id,label:(v.is_original?'原始报告':'重算报告')+' · '+new Date(v.created_at).toLocaleString('zh-CN')+' · '+v.id.slice(0,8)}));
 if(report&&!options.some(o=>o.value===report.id))options.push({value:report.id,label:'原始报告 · '+new Date(report.created_at).toLocaleString('zh-CN')});
 return <Modal title="最终成品检查报告" open onCancel={onClose} width={720} footer={<Space>{report&&!loading&&<Button href={'/api/v1/tasks/'+taskId+'/report/download?check_id='+report.id}>下载当前报告</Button>}<Button onClick={onClose}>关闭</Button></Space>}>
  {error&&<Alert type="error" showIcon message={error}/>}
  {!report?<Spin aria-label="加载报告"/>:<>
   <Form layout="vertical"><Form.Item label="报告版本"><Select aria-label="报告版本" value={selected} options={options} onChange={choose}/></Form.Item></Form>
   {cursor&&<Button onClick={more}>加载更早报告</Button>}
   {loading?<Spin aria-label="切换报告"/>:<>
    <Alert showIcon type={report.auto_overall==='compliant'?'success':report.auto_overall==='non_compliant'?'error':'warning'} message={report.auto_overall==='manual_review'?'需要人工复核':report.auto_overall==='non_compliant'?'发现未通过的规则':'满足当前已验证规则'}/>
    {report.detection_degraded&&<Alert style={{marginTop:12}} type="warning" showIcon message="视觉检测未取得可用结果" description="本次按运营审核交付已保存的成品。请人工核对商品细节、文字、标识和平台要求；此交付不代表检测成功。"/>}
    {report.latest_review&&<Alert style={{marginTop:12}} type={report.latest_review.decision==='approved'?'success':report.latest_review.decision==='rejected'?'error':'warning'} showIcon message={'人工复核：'+({approved:'通过',rejected:'未通过',needs_review:'继续复核'} as Record<string,string>)[report.latest_review.decision]} description={<span>{report.latest_review.reason} · {new Date(report.latest_review.created_at).toLocaleString('zh-CN')}（原自动报告保留）</span>}/>}
    <p className="hash-text">最终图 SHA-256：{report.final_sha256}</p>
    {report.per_platform_results.map((p:any,i:number)=><Card key={i} role="region" aria-label={p.platform.toUpperCase()+' '+p.marketplace+' 检查结果'} size="small" title={<Space wrap>{p.platform.toUpperCase()}<Tag>{p.marketplace}</Tag><Tag color={p.status==='compliant'?'green':p.status==='non_compliant'?'red':'gold'}>{status[p.status]}</Tag></Space>} style={{marginTop:12}}><p className="report-scope">类目：{p.category||'历史记录未标注'}</p><p className="report-scope">图片用途：{p.image_type==='MAIN'?'主图':p.image_type||'历史记录未标注'} · {p.evidence_status==='verified'?'规则资料已核验':p.evidence_status==='unverified'?'规则资料待核验':'资料状态未记录'}</p><p className="report-scope">规则版本：{p.rule_version||'未验证'}</p><ul>{p.reasons?.map((r:any,j:number)=><li key={j}>{reasons[typeof r==='string'?r:r.code]||'需要进一步人工检查'}</li>)}</ul>{p.checks?.map((c:any,j:number)=><Tag key={j} color={c.status==='failed'?'red':undefined} icon={c.status==='passed'?<CheckCircleOutlined/>:undefined}>{checkLabels[c.id]||'其他检查'} · {({passed:'已通过',failed:'未通过',not_applicable:'不适用',unverified:'未验证',error:'检查错误'} as Record<string,string>)[c.status]||'待检查'}</Tag>)}</Card>)}
   </>}
  </>}
 </Modal>
}
