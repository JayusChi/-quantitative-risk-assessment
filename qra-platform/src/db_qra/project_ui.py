# ruff: noqa: E501
from __future__ import annotations

import json


def project_workspace_html(project_id: str | None = None) -> bytes:
    initial_project = json.dumps(project_id, ensure_ascii=False)
    template = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QRA 项目工作台</title>
<style>
:root{--navy:#0c2434;--navy-2:#16384b;--blue:#0879c9;--blue-2:#dff2ff;--teal:#087f83;--green:#16724b;--green-bg:#e4f5ec;--amber:#9a5b00;--amber-bg:#fff1cb;--red:#a73535;--red-bg:#fde9e7;--ink:#132631;--muted:#607683;--line:#d8e3e8;--surface:#fff;--soft:#f4f8fa;--shadow:0 16px 45px rgba(19,45,61,.11);--radius:18px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--soft);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.5}
button,input,select{font:inherit}
button,a{-webkit-tap-highlight-color:transparent}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #76bff0;outline-offset:3px}
.skip{position:fixed;left:16px;top:-60px;z-index:99;background:#fff;padding:10px 14px;border-radius:8px;color:var(--navy)}
.skip:focus{top:12px}
header{height:72px;background:var(--navy);color:#fff;display:flex;align-items:center;padding:0 max(24px,calc((100vw - 1280px)/2));gap:20px;position:sticky;top:0;z-index:20;box-shadow:0 6px 24px rgba(2,20,31,.18)}
.brand{display:flex;align-items:center;gap:12px;color:#fff;text-decoration:none;font-weight:800;letter-spacing:.02em}
.brand-mark{width:38px;height:38px;border-radius:12px;background:linear-gradient(145deg,#31b8c1,#1381d1);display:grid;place-items:center;font-size:13px}
.brand small{display:block;color:#a9c4d2;font-size:10px;font-weight:500;letter-spacing:.08em}
header nav{margin-left:auto;display:flex;gap:8px}
header nav a{color:#d6e6ee;text-decoration:none;padding:8px 11px;border-radius:9px;font-size:13px}
header nav a:hover{background:rgba(255,255,255,.1);color:#fff}
main{width:min(1280px,calc(100% - 36px));margin:0 auto;padding:36px 0 72px}
.hero{background:linear-gradient(125deg,var(--navy),#18536c);color:#fff;border-radius:24px;padding:34px 38px;display:flex;justify-content:space-between;gap:24px;align-items:center;box-shadow:var(--shadow)}
.eyebrow{font-size:11px;font-weight:800;letter-spacing:.13em;text-transform:uppercase;color:#65d0d3}
h1{font-size:32px;line-height:1.2;margin:7px 0 9px}
.hero p{margin:0;color:#c7dce6;max-width:690px}
.actions{display:flex;gap:10px;flex-wrap:wrap}
.btn{border:1px solid var(--line);background:#fff;color:var(--ink);padding:10px 14px;border-radius:10px;font-weight:700;font-size:13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:6px}
.btn:hover{border-color:#9bb7c5;background:#f8fbfc}
.btn.primary{background:var(--blue);border-color:var(--blue);color:#fff}
.btn.primary:hover{background:#0568ad}
.btn.demo{background:#22a4a7;border-color:#22a4a7;color:#fff}
.btn.danger{color:var(--red);border-color:#efc2bd}
.btn:disabled{opacity:.5;cursor:not-allowed}
.hero .btn{border-color:rgba(255,255,255,.34)}
.hero .btn:not(.primary):not(.demo){background:rgba(255,255,255,.08);color:#fff}
.section-head{display:flex;justify-content:space-between;align-items:end;margin:30px 0 14px;gap:16px}
.section-head h2{margin:0;font-size:21px}
.section-head p{margin:3px 0 0;color:var(--muted);font-size:13px}
.project-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.project-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:0 5px 18px rgba(23,50,66,.05);display:flex;flex-direction:column;gap:16px}
.project-card:hover{border-color:#a9c8d7;box-shadow:var(--shadow)}
.card-top{display:flex;justify-content:space-between;gap:12px}
.card-top h3{margin:0;font-size:18px}
.card-top p{margin:4px 0 0;color:var(--muted);font-size:12px}
.badge{display:inline-flex;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:800;white-space:nowrap;background:var(--blue-2);color:#096aab}
.badge.demo{background:var(--amber-bg);color:var(--amber)}
.badge.ok{background:var(--green-bg);color:var(--green)}
.badge.block{background:var(--red-bg);color:var(--red)}
.card-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.card-stat{background:var(--soft);border-radius:11px;padding:9px}
.card-stat span{display:block;color:var(--muted);font-size:10px}
.card-stat b{display:block;margin-top:3px;font-size:14px}
.progress{height:7px;background:#dfe9ed;border-radius:99px;overflow:hidden;margin-top:6px}
.progress i{height:100%;display:block;background:linear-gradient(90deg,var(--blue),#27aca8);border-radius:inherit}
.card-foot{display:flex;justify-content:space-between;gap:10px;align-items:center}
.empty{background:#fff;border:1px dashed #b9cbd4;border-radius:18px;text-align:center;padding:54px 22px;color:var(--muted)}
.empty b{display:block;color:var(--ink);font-size:17px;margin-bottom:5px}
.crumb{border:0;background:transparent;color:var(--blue);font-weight:700;padding:0;cursor:pointer;margin-bottom:18px}
.synthetic-banner{background:var(--amber-bg);border:1px solid #e8c56d;color:#704300;border-radius:15px;padding:14px 17px;margin-bottom:18px;display:flex;gap:12px;align-items:flex-start}
.synthetic-banner b{display:block}
.project-head{background:#fff;border:1px solid var(--line);border-radius:22px;padding:24px;display:flex;justify-content:space-between;gap:20px;box-shadow:0 7px 22px rgba(23,50,66,.05)}
.project-head h1{color:var(--ink);font-size:27px;margin:4px 0}
.project-head p{margin:0;color:var(--muted);font-size:12px}
.overview-metrics{display:grid;grid-template-columns:repeat(3,minmax(105px,1fr));gap:9px;min-width:400px}
.overview-metrics div{background:var(--soft);border-radius:13px;padding:11px}
.overview-metrics span{font-size:10px;color:var(--muted);display:block}
.overview-metrics b{font-size:17px}
.stepper{background:#fff;border:1px solid var(--line);border-radius:18px;margin:18px 0;padding:18px;display:grid;grid-template-columns:repeat(6,1fr);gap:4px}
.step{position:relative;text-align:center;color:var(--muted);font-size:11px;font-weight:700}
.step:before{content:"";height:3px;background:#dce7eb;position:absolute;left:-50%;right:50%;top:14px}
.step:first-child:before{display:none}
.step i{width:29px;height:29px;border-radius:50%;background:#e6eef1;display:grid;place-items:center;margin:0 auto 7px;font-style:normal;position:relative;z-index:1}
.step.COMPLETED{color:var(--green)}
.step.COMPLETED:before,.step.COMPLETED i{background:var(--green)}
.step.COMPLETED i{color:#fff}
.step.ACTIVE{color:var(--blue)}
.step.ACTIVE i{background:var(--blue);color:#fff;box-shadow:0 0 0 5px var(--blue-2)}
.step.BLOCKED{color:var(--red)}
.step.BLOCKED i{background:var(--red);color:#fff;box-shadow:0 0 0 5px var(--red-bg)}
.workspace{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:17px;align-items:start}
.panel{background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;margin-bottom:17px}
.panel-head{padding:17px 19px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}
.panel-head h2{font-size:16px;margin:0}
.panel-head small{color:var(--muted)}
.panel-body{padding:18px 19px}
.next{background:linear-gradient(140deg,#ecf8ff,#effaf7)}
.next h2{font-size:22px;margin:0 0 5px}
.next p{color:var(--muted);margin:0 0 15px}
.blocked{background:var(--red-bg);color:#7e2929;border-left:4px solid var(--red);padding:11px 13px;border-radius:8px;margin:12px 0}
.file-list,.node-list{display:grid;gap:8px}
.file,.node{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;padding:10px 11px;background:var(--soft);border-radius:10px}
.file b,.node b{font-size:12px;overflow-wrap:anywhere}
.file small,.node small{display:block;color:var(--muted);font-size:10px}
.node{grid-template-columns:28px minmax(0,1fr) auto}
.node-num{width:25px;height:25px;border-radius:8px;background:#dce9ef;display:grid;place-items:center;font-size:9px;font-weight:800}
.node.COMPLETED .node-num{background:var(--green-bg);color:var(--green)}
.node.FAILED_ISOLATED .node-num,.node[class*="SKIPPED"] .node-num{background:var(--red-bg);color:var(--red)}
.report-box{background:var(--navy);color:#fff;border-radius:14px;padding:18px}
.report-box p{color:#bdd2dc;font-size:12px}
.report-frame{width:100%;height:670px;border:1px solid var(--line);border-radius:14px;margin-top:14px;background:#fff}
details.audit{background:#fff;border:1px solid var(--line);border-radius:14px;margin-top:17px}
details.audit summary{cursor:pointer;padding:14px 17px;font-weight:700}
.audit-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;padding:0 17px 17px}
.audit-grid div{background:var(--soft);border-radius:9px;padding:9px;overflow-wrap:anywhere}
.audit-grid span{font-size:9px;color:var(--muted);display:block}
.audit-grid b{font:10px ui-monospace,Consolas,monospace}
dialog{border:0;border-radius:18px;width:min(650px,calc(100% - 28px));padding:0;box-shadow:0 26px 80px rgba(4,25,38,.3)}
dialog::backdrop{background:rgba(6,28,41,.62)}
.dialog-head{padding:20px 22px 14px;border-bottom:1px solid var(--line)}
.dialog-head h2{margin:0;font-size:19px}
.dialog-body{padding:19px 22px}
.dialog-foot{padding:14px 22px;border-top:1px solid var(--line);display:flex;justify-content:flex-end;gap:9px}
.field{margin-bottom:14px}
.field label{display:block;font-size:11px;font-weight:800;margin-bottom:5px}
.field input,.field select,.field textarea{width:100%;border:1px solid #b7cbd5;border-radius:9px;padding:10px;background:#fff;font:inherit}
.field textarea{min-height:100px;resize:vertical}
.field small{color:var(--muted)}
.drop{border:2px dashed #aec6d2;border-radius:14px;padding:24px;text-align:center;background:var(--soft)}
.drop input{max-width:100%}
.error-box{display:none;background:var(--red-bg);color:var(--red);padding:10px;border-radius:8px;margin-top:10px}
.error-box.show{display:block}
.toast{position:fixed;right:20px;bottom:20px;z-index:50;background:var(--navy);color:#fff;border-radius:12px;padding:12px 15px;box-shadow:var(--shadow);display:none;max-width:360px}
.toast.show{display:block}
[hidden]{display:none!important}
.badges{display:flex;gap:7px;flex-wrap:wrap}.notice{background:var(--blue-2);color:#225d7e;border-radius:10px;padding:10px 11px;margin-bottom:10px;font-size:10px;overflow-wrap:anywhere}.report-box .badge{white-space:normal}
@media(max-width:900px){header{padding:0 18px}header nav a:first-child{display:none}main{width:min(100% - 24px,760px);padding-top:22px}.hero,.project-head{align-items:flex-start;flex-direction:column}.overview-metrics{min-width:0;width:100%}.project-grid,.workspace{grid-template-columns:1fr}.stepper{overflow-x:auto;grid-template-columns:repeat(6,minmax(94px,1fr))}.stepper::-webkit-scrollbar{height:5px}}
@media(max-width:560px){header{height:62px}.brand small,header nav a:first-child{display:none}main{width:calc(100% - 18px)}.hero{padding:24px 20px;border-radius:18px}h1{font-size:25px}.hero .actions,.project-head .actions{width:100%}.hero .btn,.project-head .btn{flex:1}.project-grid{grid-template-columns:1fr}.card-stats,.overview-metrics{grid-template-columns:1fr 1fr}.overview-metrics div:last-child{grid-column:1/-1}.project-head{padding:18px}.panel-body{padding:15px}.audit-grid{grid-template-columns:1fr}.report-frame{height:520px}}
</style></head><body>
<a class="skip" href="#main">跳到主要内容</a>
<header><a class="brand" href="/projects/"><span class="brand-mark">QRA</span><span>项目工作台<small>风险评估统一旅程</small></span></a><nav aria-label="全局导航"><a href="/projects/">所有项目</a><a href="/admin/">高级管理与审计</a></nav></header>
<main id="main" tabindex="-1">
<section id="listView"><div class="hero"><div><div class="eyebrow">Project workspace</div><h1>从项目资料到风险结果，一处完成</h1><p>上传资料后，系统会自动整理、标出需要您确认的内容并完成计算。您无需准备结构化代码，也不需要理解底层存储。</p></div><div class="actions"><button class="btn" id="newProjectBtn">新建项目</button><button class="btn demo" id="loadDemoBtn">加载全合成演示项目</button></div></div><div class="section-head"><div><h2>项目列表</h2><p>查看数据完整度、待办问题、计算进度和最新报告。</p></div><label class="field" style="margin:0"><span class="skip">项目筛选</span><select id="projectFilter" aria-label="筛选项目"><option value="active">进行中的项目</option><option value="archived">已归档项目</option></select></label></div><div id="projectGrid" class="project-grid" aria-live="polite"></div></section>
<section id="detailView" hidden><button class="crumb" id="backProjects">← 返回项目列表</button><div id="syntheticBanner" class="synthetic-banner" hidden><span aria-hidden="true">⚠</span><div><b>全合成演示数据</b><span id="syntheticText"></span></div></div><div class="project-head"><div><div class="eyebrow">当前项目</div><h1 id="projectName"></h1><p id="projectMeta"></p></div><div class="overview-metrics" id="overviewMetrics"></div></div><div class="stepper" id="stepper" aria-label="项目进度"></div><div class="workspace"><div><section class="panel next" id="nextPanel"><div class="panel-body"><div class="eyebrow">下一步</div><h2 id="nextTitle"></h2><p id="nextDescription"></p><div id="blockedReason" class="blocked" role="status" hidden></div><div class="actions"><button class="btn primary" id="nextActionBtn"></button><button class="btn" id="refreshProjectBtn">刷新状态</button></div></div></section><section class="panel" id="sourcesPanel"><div class="panel-head"><div><h2>项目资料</h2><small>安全状态、版本和隔离情况</small></div><span class="badge" id="sourceCount"></span></div><div class="panel-body"><div class="file-list" id="sourceList"></div></div></section><section class="panel" id="reportPanel"><div class="panel-head"><div><h2>报告中心</h2><small>草稿、完整性、一致性、引用和人工状态</small></div><span id="reportStatus" class="badge"></span></div><div class="panel-body" id="reportBody"></div></section></div><div><section class="panel"><div class="panel-head"><div><h2>数据复核</h2><small>只处理系统标出的项目</small></div><span class="badge" id="reviewBadge"></span></div><div class="panel-body" id="reviewBody"></div></section><section class="panel"><div class="panel-head"><div><h2>计算进度</h2><small>11 个风险计算环节</small></div><span class="badge" id="nodeBadge"></span></div><div class="panel-body"><div class="notice" id="calculationVersions"></div><div class="node-list" id="nodeList"></div></div></section></div></div><details class="audit"><summary>高级审计与技术追溯</summary><div class="audit-grid" id="auditGrid"></div><div style="padding:0 17px 17px"><a class="btn" href="/admin/">打开高级管理页</a></div></details></section>
</main>
<dialog id="projectDialog" aria-labelledby="projectDialogTitle"><form method="dialog"><div class="dialog-head"><h2 id="projectDialogTitle">新建风险评估项目</h2></div><div class="dialog-body"><div class="field"><label for="newProjectName">项目名称</label><input id="newProjectName" required maxlength="160" autocomplete="off" placeholder="例如：东线输气管道年度评估"></div><div class="field"><label for="newCaseId">项目编号（可选）</label><input id="newCaseId" maxlength="120" autocomplete="off" placeholder="例如：QRA-2026-001"></div><div class="error-box" id="projectError" role="alert" tabindex="-1"></div></div><div class="dialog-foot"><button class="btn" value="cancel">取消</button><button class="btn primary" id="createProjectBtn" value="default">创建并上传资料</button></div></form></dialog>
<dialog id="uploadDialog" aria-labelledby="uploadDialogTitle"><form method="dialog"><div class="dialog-head"><h2 id="uploadDialogTitle">上传项目资料</h2></div><div class="dialog-body"><div class="drop"><label for="sourceFiles"><b>选择或拖入项目文件</b><br><small>支持 CSV、Excel、Word、PDF、PNG、JPG/JPEG 和 ZIP；系统会先做安全检查。</small></label><input id="sourceFiles" type="file" multiple accept=".csv,.xls,.xlsx,.docx,.pdf,.png,.jpg,.jpeg,.zip"></div><div class="field" style="margin-top:14px"><label for="failurePolicy">异常文件处理</label><select id="failurePolicy"><option value="QUARANTINE_AND_CONTINUE">隔离异常文件，继续处理可用资料</option><option value="ALL_OR_NOTHING">发现异常时暂停整个项目</option></select></div><div id="uploadSummary" aria-live="polite"></div><div class="error-box" id="uploadError" role="alert" tabindex="-1"></div></div><div class="dialog-foot"><button class="btn" value="cancel">取消</button><button class="btn primary" id="submitFilesBtn" value="default">上传并开始处理</button></div></form></dialog>
<dialog id="reportConfirmDialog" aria-labelledby="reportConfirmTitle"><form method="dialog"><div class="dialog-head"><h2 id="reportConfirmTitle">人工确认测试报告</h2></div><div class="dialog-body"><div class="notice"><b>确认边界</b><br>确认只表示受控测试报告内容已复核；不会解除合成数据声明或正式发布阻断。</div><div class="field"><label for="reportReviewer">确认人</label><input id="reportReviewer" required maxlength="120" autocomplete="name" value="local-user"></div><div class="field"><label for="reportReason">确认说明</label><textarea id="reportReason" required maxlength="1000" placeholder="例如：已核对数字、引用、水印和合成数据使用边界"></textarea></div><div class="error-box" id="reportConfirmError" role="alert" tabindex="-1"></div></div><div class="dialog-foot"><button class="btn" value="cancel">取消</button><button class="btn primary" id="submitReportConfirmBtn" value="default">确认测试报告</button></div></form></dialog>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>
const initialProject=__INITIAL_PROJECT__;
const state={projects:[],project:null,profiles:[],poll:null,adminToken:null,tokenPrompt:null};
const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const authHeaders=()=>{const h={'Content-Type':'application/json'},t=state.adminToken;if(t)h['X-QRA-Admin-Token']=t;return h};
async function api(path,options={}){const attempted=options._tokenAttempted,requestOptions={...options};delete requestOptions._tokenAttempted;const r=await fetch(path,{...requestOptions,headers:{...authHeaders(),...(options.headers||{})}});let d={};try{d=await r.json()}catch(e){}if(r.status===403&&!attempted){if(!state.tokenPrompt)state.tokenPrompt=Promise.resolve(prompt('请输入管理访问令牌；令牌仅保留在当前页面内存中：',''));const supplied=await state.tokenPrompt;state.tokenPrompt=null;if(supplied){state.adminToken=supplied;return api(path,{...options,_tokenAttempted:true})}}if(!r.ok)throw new Error(d.message||d.error||`请求失败 (${r.status})`);return d}
function toast(message,error=false){const e=$('#toast');e.textContent=message;e.style.background=error?'#8f3030':'';e.classList.add('show');clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove('show'),4200)}
function fmtBytes(n){n=Number(n||0);if(n<1024)return `${n} B`;if(n<1048576)return `${(n/1024).toFixed(1)} KB`;return `${(n/1048576).toFixed(1)} MB`}
function badgeClass(p){return p.is_demo?'demo':p.status==='REPORT_READY'?'ok':String(p.status).includes('FAILED')||p.status==='NEEDS_REVIEW'?'block':''}
function renderProjects(){const archived=$('#projectFilter').value==='archived',rows=state.projects.filter(p=>Boolean(p.archived)===archived),grid=$('#projectGrid');if(!rows.length){grid.innerHTML=`<div class="empty" style="grid-column:1/-1"><b>${archived?'没有已归档项目':'还没有项目'}</b>${archived?'归档的项目会显示在这里。':'新建项目上传资料，或加载全合成演示项目快速体验完整流程。'}</div>`;return}grid.innerHTML=rows.map(p=>`<article class="project-card"><div class="card-top"><div><h3>${esc(p.name)}</h3><p>${esc(p.case_id||p.id)} · ${p.is_demo?'全合成演示数据':'项目资料'}</p></div><span class="badge ${badgeClass(p)}">${esc(p.status_label)}</span></div>${p.synthetic_warning?'<span class="badge demo" style="align-self:flex-start">仅供演示，不可用于真实评价</span>':''}<div class="card-stats"><div class="card-stat"><span>数据完整度</span><b>${p.data_completeness_percent}%</b><div class="progress"><i style="width:${p.data_completeness_percent}%"></i></div></div><div class="card-stat"><span>待处理问题</span><b>${p.pending_issue_count} 项</b></div><div class="card-stat"><span>计算进度</span><b>${p.calculation_progress.completed} / ${p.calculation_progress.total}</b></div></div><div class="card-foot"><span style="font-size:11px;color:var(--muted)">${p.latest_report?'最新报告已就绪':'下一步：'+esc(p.next_action.label)}</span><div class="actions"><button class="btn danger" data-archive="${esc(p.id)}">${p.archived?'恢复':'归档'}</button><button class="btn primary" data-open="${esc(p.id)}">进入项目</button></div></div></article>`).join('');grid.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>openProject(b.dataset.open));grid.querySelectorAll('[data-archive]').forEach(b=>b.onclick=()=>archiveProject(b.dataset.archive,!archived))}
async function loadProjects(){state.projects=await api('/admin/api/projects?include_archived=true');renderProjects()}
async function openProject(id,push=true){state.project=await api(`/admin/api/projects/${encodeURIComponent(id)}`);if(push)history.pushState({project:id},'',`/projects/${encodeURIComponent(id)}/`);$('#listView').hidden=true;$('#detailView').hidden=false;renderProject();window.scrollTo(0,0);schedulePoll()}
function showList(push=true){clearTimeout(state.poll);state.project=null;$('#detailView').hidden=true;$('#listView').hidden=false;if(push)history.pushState({},'', '/projects/');loadProjects().catch(e=>toast(e.message,true));window.scrollTo(0,0)}
function renderProject(){const p=state.project;$('#projectName').textContent=p.name;$('#projectMeta').textContent=`${p.case_id||'未设置项目编号'} · ${p.status_label}`;$('#syntheticBanner').hidden=!p.synthetic_warning;$('#syntheticText').textContent=p.synthetic_warning||'';$('#overviewMetrics').innerHTML=`<div><span>数据完整度</span><b>${p.data_completeness_percent}%</b><div class="progress"><i style="width:${p.data_completeness_percent}%"></i></div></div><div><span>待处理问题</span><b>${p.pending_issue_count} 项</b></div><div><span>计算进度</span><b>${p.calculation_progress.completed} / ${p.calculation_progress.total}</b></div>`;$('#stepper').innerHTML=p.journey_steps.map((s,i)=>`<div class="step ${s.state}" aria-current="${s.state==='ACTIVE'||s.state==='BLOCKED'?'step':'false'}"><i>${s.state==='COMPLETED'?'✓':i+1}</i><span>${esc(s.label)}</span></div>`).join('');$('#nextTitle').textContent=p.next_action.label;$('#nextDescription').textContent=nextDescription(p);$('#blockedReason').hidden=!p.blocked_reason;$('#blockedReason').textContent=p.blocked_reason||'';const next=$('#nextActionBtn');next.textContent=p.next_action.label;next.disabled=p.next_action.id==='WAIT';next.onclick=handleNext;renderSources();renderReview();renderNodes();renderReport();renderAudit()}
function nextDescription(p){return {NEEDS_UPLOAD:'上传现有项目文件，系统会自动进行安全检查、解析和字段整理。',PROCESSING_SOURCES:'资料正在后台整理，完成后只会把需要您判断的项目列出来。',SOURCE_PROCESSING_FAILED:'资料处理没有完成。问题原因已显示，修正后可从这里重试。',NEEDS_REVIEW:'系统已完成自动整理，请只处理被标出的冲突、缺失或低置信字段。',READY_TO_CALCULATE:'数据已经确认，可以使用受控参数开始风险计算。',CALCULATING:'11 个计算环节正在依次运行，本页面会自动刷新进度。',CALCULATION_FAILED:'已确认数据仍然保留，可安全地重新发起计算。',REPORT_READY:'计算结果和检查项已经就绪，可在本页查看或导出。'}[p.status]||''}
function renderSources(){const rows=state.project.sources||[];$('#sourceCount').textContent=`${rows.length} 份`;$('#sourceList').innerHTML=rows.length?rows.map(f=>`<div class="file"><div><b>${esc(f.name)}</b><small>${esc(f.media_type)} · ${fmtBytes(f.byte_count)}${f.duplicate?' · 已识别重复版本':''}</small>${f.issue?`<small style="color:var(--red)">${esc(f.issue)}</small>`:''}</div><span class="badge ${f.status==='PARSED'?'ok':f.status==='QUARANTINED'||f.status==='PARSE_FAILED'?'block':''}">${esc(f.status_label)}</span></div>`).join(''):'<div class="empty"><b>尚未上传资料</b>支持表格、文档、PDF、图片和资料包。</div>'}
function renderReview(){const p=state.project,r=p.review,unresolved=Number(r?.progress?.unresolved||0);$('#reviewBadge').textContent=p.confirmed_data?'已确认':r?`${unresolved} 项待处理`:'未开始';let html;if(p.confirmed_data)html='<p>数据已完成确认，后续计算只读取这个已确认版本。</p>';else if(r)html=`<p>已处理 <b>${r.progress.resolved}</b> / ${r.progress.total} 项。${unresolved?'只需继续处理系统标出的项目。':'当前没有未处理项目，请运行检查并确认数据。'}</p><a class="btn primary" href="/admin/reviews/${encodeURIComponent(p.advanced_audit.conversion_job_id)}/?project_id=${encodeURIComponent(p.id)}">打开数据复核</a>`;else if(p.status==='NEEDS_REVIEW')html=`<p>自动整理已完成。进入复核页后只处理标出的字段。</p><button class="btn primary" data-review>开始数据复核</button>`;else html='<p>资料处理完成后，这里会显示需要人工确认的内容。</p>';$('#reviewBody').innerHTML=html;$('#reviewBody [data-review]')?.addEventListener('click',handleNext)}
const nodeNames=['数据清单','指标覆盖','管段几何','失效频率','泄漏点离散','泄漏源项','喷射火阈值','标准附录校核','证据自适应风险','人员风险计算','风险矩阵'];
function renderNodes(){const p=state.project,rows=p.nodes||[],v=p.calculation_versions||{},packs=v.parameter_pack_ids||[];$('#nodeBadge').textContent=`${p.calculation_progress.completed} / 11`;$('#calculationVersions').innerHTML=`<b>受控版本</b><br>数据：${esc(v.data_version||'等待确认')}${v.data_sha256?' · '+esc(String(v.data_sha256).slice(0,12))+'…':''}<br>参数：${esc(packs.length?packs.join('、'):v.parameter_binding||'等待绑定')}${v.engine_version?'<br>引擎：'+esc(v.engine_version):''}`;$('#nodeList').innerHTML=(rows.length?rows:Array.from({length:11},(_,i)=>({sequence_no:i+1,label_zh:nodeNames[i],status:'NOT_STARTED',missing_inputs:[]}))).map((n,i)=>`<div class="node ${esc(n.status)}"><span class="node-num">${n.status==='COMPLETED'?'✓':i+1}</span><div><b>${esc(n.label_zh||nodeNames[i])}</b><small>${nodeStatus(n.status)}${n.status==='COMPLETED'?' · 结果摘要已生成':''}${n.error_message?' · '+esc(n.error_message):''}${(n.missing_inputs||[]).length?' · 需要补充 '+esc(n.missing_inputs.map(x=>x.path).join('、')):''}</small></div><span class="badge ${n.status==='COMPLETED'?'ok':String(n.status).includes('FAILED')||String(n.status).includes('SKIPPED')?'block':''}">${nodeStatus(n.status)}</span></div>`).join('')}
function nodeStatus(s){return {NOT_STARTED:'等待开始',QUEUED:'排队中',RUNNING:'计算中',COMPLETED:'已完成',FAILED_ISOLATED:'失败',SKIPPED_MISSING_INPUT:'缺资料未运行',SKIPPED_DEPENDENCY_FAILED:'上游失败未运行'}[s]||s}
function renderReport(){const p=state.project,r=p.latest_report,c=p.report_center||{};$('#reportStatus').textContent=!r?'等待计算':c.controlled_report_id?(c.draft?'等待人工确认':'已人工确认'):'计算结果已就绪';if(!r){$('#reportBody').innerHTML='<p>计算完成后，这里会显示草稿状态、完整性、数值一致性、引用和人工复核状态。</p>';return}const s=p.calculation.summary||{},controlled=Boolean(c.controlled_report_id),downloads=controlled?`<a class="btn" href="${esc(r.pdf_url)}">导出 PDF</a><a class="btn" href="${esc(r.docx_url)}">导出 DOCX</a>`:'<button class="btn" disabled title="生成受控报告后开放">PDF</button><button class="btn" disabled title="生成受控报告后开放">DOCX</button>',primary=controlled?'<button class="btn primary" id="showReportBtn">在本页查看受控 HTML</button>':'<button class="btn primary" id="generateReportBtn">生成受控报告</button>',confirm=controlled&&c.draft?'<button class="btn demo" id="confirmReportBtn">人工确认报告</button>':'';$('#reportBody').innerHTML=`<div class="report-box"><b>${controlled?esc(r.label):'风险计算结果已生成'}${c.draft?' · 草稿':''}</b><p>完成 ${s.completed_node_count||0} 个计算环节，失败 ${s.failed_node_count||0} 个，跳过 ${s.skipped_node_count||0} 个。正式报告许可：${s.formal_acceptance_judgement_allowed?'已开放':'未开放（当前为测试/草稿结果）'}。${controlled?'<br>上下文哈希 '+esc(String(c.context_sha256||'').slice(0,16))+'… · 草稿哈希 '+esc(String(c.draft_sha256||'').slice(0,16))+'…':''}</p><div class="badges"><span class="badge ${c.completeness==='PASS'?'ok':'block'}">完整性 ${esc(c.completeness)}</span><span class="badge ${c.numerical_consistency==='PASS'?'ok':'block'}">数值一致性 ${esc(c.numerical_consistency)}</span><span class="badge ${c.citations==='BOUND'?'ok':''}">引用 ${esc(c.citations)}</span><span class="badge">人工状态 ${esc(c.manual_status)}</span></div><div class="actions" style="margin-top:13px">${primary}${controlled?`<a class="btn" href="${esc(r.html_url)}" target="_blank" rel="noopener">新窗口打开</a>`:''}<a class="btn" href="${esc(r.zip_url)}">导出 ZIP</a>${downloads}${confirm}</div></div><iframe id="reportFrame" class="report-frame" title="QRA受控测试报告" hidden></iframe>`;$('#generateReportBtn')?.addEventListener('click',generateReport);$('#showReportBtn')?.addEventListener('click',()=>{const f=$('#reportFrame');f.src=r.html_url;f.hidden=false;f.scrollIntoView({behavior:'smooth'})});$('#confirmReportBtn')?.addEventListener('click',()=>{showError('#reportConfirmError','');$('#reportReason').value='';$('#reportConfirmDialog').showModal();setTimeout(()=>$('#reportReviewer').focus(),50)})}
async function generateReport(){const b=$('#generateReportBtn');b.disabled=true;b.textContent='正在生成受控报告…';try{const result=await api(`/admin/api/projects/${encodeURIComponent(state.project.id)}/reports`,{method:'POST',body:JSON.stringify({actor:'local-user'})});toast(result.message||'受控报告已生成');await openProject(state.project.id,false)}catch(e){toast(e.message,true);b.disabled=false;b.textContent='生成受控报告'}}
async function confirmReport(event){event.preventDefault();const reviewer=$('#reportReviewer').value.trim(),reason=$('#reportReason').value.trim();if(!reviewer){showError('#reportConfirmError','请填写确认人');return}if(reason.length<4){showError('#reportConfirmError','确认说明至少需要4个字符');return}const b=$('#submitReportConfirmBtn');b.disabled=true;try{const id=state.project.report_center.controlled_report_id,result=await api(`/admin/api/reports/${encodeURIComponent(id)}/confirm`,{method:'POST',body:JSON.stringify({reviewer,reason,actor:reviewer})});$('#reportConfirmDialog').close();toast(result.message||'测试报告已确认');await openProject(state.project.id,false)}catch(e){showError('#reportConfirmError',e.message)}finally{b.disabled=false}}
function renderAudit(){const a=state.project.advanced_audit;$('#auditGrid').innerHTML=Object.entries(a).map(([k,v])=>`<div><span>${esc(k)}</span><b>${esc(v||'—')}</b></div>`).join('')}
async function handleNext(){const p=state.project,a=p.next_action.id;if(a==='UPLOAD_FILES'){$('#sourceFiles').value='';$('#uploadSummary').textContent='';showError('#uploadError','');$('#uploadDialog').showModal();return}if(a==='OPEN_REVIEW'){location.href=`/admin/reviews/${encodeURIComponent(p.advanced_audit.conversion_job_id)}/?project_id=${encodeURIComponent(p.id)}`;return}if(a==='OPEN_REPORT'){$('#reportPanel').scrollIntoView({behavior:'smooth'});$('#showReportBtn')?.focus();return}try{const result=await api(`/admin/api/projects/${encodeURIComponent(p.id)}/continue`,{method:'POST',body:'{}'});if(result.redirect_url)location.href=result.redirect_url;else{toast(result.message||'已继续处理');await openProject(p.id,false)}}catch(e){toast(e.message,true)}}
function showError(selector,message){const e=$(selector);e.textContent=message;e.classList.toggle('show',Boolean(message));if(message)e.focus()}
function fileBase64(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',',2)[1]);r.onerror=reject;r.readAsDataURL(file)})}
async function submitFiles(event){event.preventDefault();const files=[...$('#sourceFiles').files];if(!files.length){showError('#uploadError','请至少选择一份项目资料');return}const b=$('#submitFilesBtn');b.disabled=true;$('#uploadSummary').textContent=`正在准备 ${files.length} 份文件…`;try{const encoded=[];for(const file of files)encoded.push({file_name:file.name,media_type:file.type,content_base64:await fileBase64(file)});const profile=state.profiles[0]?.mapping_version;if(!profile)throw new Error('当前没有可用的资料映射配置');await api(`/admin/api/projects/${encodeURIComponent(state.project.id)}/files`,{method:'POST',body:JSON.stringify({profile,failure_policy:$('#failurePolicy').value,files:encoded,actor:'local-user'})});$('#uploadDialog').close();toast('资料已上传，系统开始自动整理');await openProject(state.project.id,false)}catch(e){showError('#uploadError',e.message)}finally{b.disabled=false}}
async function createProject(event){event.preventDefault();const name=$('#newProjectName').value.trim();if(!name){showError('#projectError','请填写项目名称');return}try{const p=await api('/admin/api/projects',{method:'POST',body:JSON.stringify({name,case_id:$('#newCaseId').value.trim()||null,actor:'local-user'})});$('#projectDialog').close();await loadProjects();await openProject(p.id);setTimeout(()=>handleNext(),120)}catch(e){showError('#projectError',e.message)}}
async function loadDemo(){const b=$('#loadDemoBtn');b.disabled=true;b.textContent='正在加载演示项目…';try{const r=await api('/admin/api/projects/demo',{method:'POST',body:JSON.stringify({actor:'local-user'})});toast(r.created?'演示项目已加载，计算已启动':'已打开现有演示项目');await loadProjects();await openProject(r.project.id)}catch(e){toast(e.message,true)}finally{b.disabled=false;b.textContent='加载全合成演示项目'}}
async function archiveProject(id,archived){try{await api(`/admin/api/projects/${encodeURIComponent(id)}/archive`,{method:'POST',body:JSON.stringify({archived,actor:'local-user'})});toast(archived?'项目已归档':'项目已恢复');await loadProjects()}catch(e){toast(e.message,true)}}
function schedulePoll(){clearTimeout(state.poll);if(state.project&&['PROCESSING_SOURCES','CALCULATING'].includes(state.project.status))state.poll=setTimeout(()=>openProject(state.project.id,false).catch(e=>toast(e.message,true)),1500)}
$('#newProjectBtn').onclick=()=>{$('#newProjectName').value='';$('#newCaseId').value='';showError('#projectError','');$('#projectDialog').showModal();setTimeout(()=>$('#newProjectName').focus(),50)};$('#loadDemoBtn').onclick=loadDemo;$('#createProjectBtn').onclick=createProject;$('#submitFilesBtn').onclick=submitFiles;$('#submitReportConfirmBtn').onclick=confirmReport;$('#backProjects').onclick=()=>showList();$('#refreshProjectBtn').onclick=()=>openProject(state.project.id,false).catch(e=>toast(e.message,true));$('#projectFilter').onchange=renderProjects;window.onpopstate=e=>e.state?.project?openProject(e.state.project,false):showList(false);
async function boot(){try{state.profiles=await api('/admin/api/conversion-profiles');await loadProjects();if(initialProject)await openProject(initialProject,false)}catch(e){toast(e.message,true)}}boot();
</script></body></html>"""
    return template.replace("__INITIAL_PROJECT__", initial_project).encode("utf-8")


__all__ = ["project_workspace_html"]
