import {useState} from 'react';
import {Alert,Button,Input,Modal,Select,Space} from 'antd';
import type {Task} from './api';

type Props={task?:Task,live?:boolean};

export function TrialHelp({task,live=false}:Props){
 const [panel,setPanel]=useState<'guide'|'feedback'>();
 const [area,setArea]=useState('操作流程');
 const [detail,setDetail]=useState('');
 const [expected,setExpected]=useState('');
 const [includeTask,setIncludeTask]=useState(false);
 const close=()=>{setPanel(undefined);setDetail('');setExpected('');setArea('操作流程');setIncludeTask(false)};
 const download=()=>{
  if(!detail.trim())return;
  const feedback={schema_version:1,created_at:new Date().toISOString(),mode:live?'live_bailian':'offline_trial',area,description:detail.trim(),expected:expected.trim(),...(includeTask&&task?{task:{id:task.id,status:task.status,phase:task.phase}}:{})};
  const url=URL.createObjectURL(new Blob([JSON.stringify(feedback,null,2)],{type:'application/json;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download='cross-studio-feedback-'+Date.now()+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 return <>
  <Space className="trial-help-actions" wrap><Button onClick={()=>setPanel('guide')}>试用指引</Button><Button onClick={()=>setPanel('feedback')}>记录反馈</Button></Space>
  <Modal title="初版试用指引" open={panel==='guide'} onCancel={close} footer={<Button type="primary" onClick={close}>开始试用</Button>}>
   <Alert showIcon type={live?'warning':'info'} message={live?'当前连接百炼真实供应商；确认报价并提交后会产生实际费用。':'当前工作台使用离线供应商，可体验完整操作流程；不会调用真实付费模型。'}/>
   <ol className="trial-guide-steps">
    <li><b>建立产品资料</b><p>新建或选择产品，填写名称和类目；外观资料可稍后补充。</p></li>
    <li><b>上传商品原图</b><p>支持 PNG、JPEG、WebP。等待校验完成，再在左侧原图上拖动框选品牌、文字或接口。</p></li>
    <li><b>确认保护与生成设置</b><p>精确保护保持工作图尺寸，最多4MP；可调整右侧目标框位置。暂不需要保护时，请明确选择“无保护”。</p></li>
    <li><b>获取报价并确认</b><p>设置图片类型、描述和目标平台，获取报价后再确认创建任务。提交前不会冻结积分。</p></li>
    <li><b>查看成品与报告</b><p>任务完成后对照原图检查，并下载 PNG 和报告。像素保护通过仍需检查外观、接缝和平台规则。</p></li>
   </ol>
   <Alert showIcon type="warning" message="已知限制：真实测试中的保护区域回贴存在色块和错位，已留档待试用后改进。检查报告不能代替上架审核。"/>
   <p>结果或费用未知时请等待管理员对账，不要重复提交。工作台草稿保存在当前浏览器会话中，关闭会话后可能丢失；已提交任务可在“任务记录”查看。</p>
  </Modal>
  <Modal title="记录试用反馈" open={panel==='feedback'} onCancel={close} footer={<Space><Button onClick={close}>关闭</Button><Button type="primary" disabled={!detail.trim()} onClick={download}>下载反馈文件</Button></Space>}>
   <p>反馈只下载到本机，不会自动发送。请描述操作步骤和实际结果，勿填写密码、API密钥或其他敏感信息。</p>
   <label className="trial-feedback-field">问题环节<Select aria-label="问题环节" value={area} onChange={setArea} options={['操作流程','产品资料','上传与保护','报价与任务','成品与报告','其他'].map(value=>({value,label:value}))}/></label>
   <label className="trial-feedback-field">操作步骤和实际结果（必填）<Input.TextArea aria-label="操作步骤和实际结果" value={detail} onChange={e=>setDetail(e.target.value)} rows={4} maxLength={4000} showCount/></label>
   <label className="trial-feedback-field">期望结果<Input.TextArea aria-label="期望结果" value={expected} onChange={e=>setExpected(e.target.value)} rows={2} maxLength={2000}/></label>
   {task&&<label className="trial-feedback-field">关联当前任务<Select aria-label="关联当前任务" value={includeTask?'yes':'no'} onChange={value=>setIncludeTask(value==='yes')} options={[{value:'no',label:'不附带任务信息'},{value:'yes',label:'仅附带任务编号、状态和阶段'}]}/></label>}
   <p className="subtle">文件不自动包含账户、图片、画面描述、原始响应或密钥。关闭窗口会清空未导出的反馈。</p>
  </Modal>
 </>;
}
