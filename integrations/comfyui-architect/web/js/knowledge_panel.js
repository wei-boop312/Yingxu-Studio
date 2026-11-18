import { api } from "../../../scripts/api.js";

// Local retrieval, explicit selections and draft export. Never calls /prompt.
export function openKnowledgePanel(initialText = "") {
  document.querySelector(".wa-knowledge-overlay")?.waDispose?.();
  const overlay = document.createElement("div");
  overlay.className = "wa-overlay wa-knowledge-overlay open";
  const panel = document.createElement("section");
  panel.className = "wa-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "本地知识辅助");
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = "本地知识辅助 · 检索与草稿，不运行";
  const close = document.createElement("button");
  close.textContent = "关闭";
  const main = document.createElement("main");
  const note = document.createElement("p");
  note.textContent = "关键词／中文双字检索，不是本地语义模型；API关闭。资料命中不代表模型已安装或生成质量已验收。";
  const query = document.createElement("textarea");
  query.maxLength = 4000;
  query.value = initialText.slice(0, 4000);
  query.setAttribute("aria-label", "知识辅助需求");
  const search = document.createElement("button");
  search.textContent = "检索并检查依赖（不编译）";
  const profile = document.createElement("select"); profile.setAttribute("aria-label", "知识辅助运行配置");
  for (const value of ["stable", "low_vram"]) { const option = document.createElement("option"); option.value = value; option.textContent = value; profile.append(option); }
  const choices = document.createElement("div");
  const build = document.createElement("button"); build.textContent = "编译草稿（不运行）"; build.disabled = true;
  const download = document.createElement("button"); download.textContent = "导出工作流JSON（不运行）"; download.disabled = true;
  const report = document.createElement("pre"); report.style.whiteSpace = "pre-wrap"; report.style.overflowWrap = "anywhere";
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  const results = document.createElement("div");
  let active = null, epoch = 0, preview = null, compiled = null, modelSelects = [], imageSelect = null;
  overlay.waDispose = () => { epoch++; active?.abort(); overlay.remove(); };
  close.addEventListener("click", overlay.waDispose);
  function invalidate() {
    epoch++; active?.abort(); preview = null; compiled = null;
    choices.replaceChildren(); report.textContent = ""; modelSelects = []; imageSelect = null;
    build.disabled = true; download.disabled = true; search.disabled = false;
    status.textContent = "需求或配置变化，请重新检索；旧草稿已关闭。";
  }
  query.addEventListener("input", invalidate); profile.addEventListener("change", invalidate);
  function makeSelect(label, values) {
    const wrapper = document.createElement("label"); wrapper.textContent = label;
    const select = document.createElement("select"); select.setAttribute("aria-label", label);
    const empty = document.createElement("option"); empty.value = ""; empty.textContent = "请明确选择，不自动取第一项"; select.append(empty);
    for (const value of values) { const option = document.createElement("option"); option.value = value; option.textContent = value; select.append(option); }
    select.addEventListener("change", () => { compiled = null; download.disabled = true; report.textContent = ""; });
    wrapper.append(select); choices.append(wrapper); return select;
  }
  async function request(body, controller) {
    const response = await api.fetchApi("/workflow-architect/knowledge/assist", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      cache: "no-store", redirect: "error", signal: controller.signal,
    });
    if (!response.body) throw new Error("接口缺少响应正文");
    const reader = response.body.getReader(); const chunks = []; let size = 0;
    let envelope;
    try {
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        size += value.byteLength;
        if (size > 1024 * 1024 || chunks.length >= 4096) throw new Error("知识辅助响应超限");
        chunks.push(value);
      }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      envelope = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    if (!response.ok || !envelope.ok) throw new Error(envelope.error?.message || "插件接口未就绪");
    const value = envelope.data;
    if (value?.execution_authorized !== false || value.api_enabled !== false || value.embedding_enabled !== false || !Array.isArray(value.retrieval?.results) || value.retrieval.results.length > 20) throw new Error("知识辅助合同不匹配");
    return value;
  }
  search.addEventListener("click", async () => {
    invalidate(); const runEpoch = epoch;
    const controller = new AbortController();
    active = controller;
    const timer = setTimeout(() => controller.abort(), 30000);
    search.disabled = true;
    results.replaceChildren();
    status.textContent = "读取本机知识库与当前节点登记…";
    try {
      const body = { text: query.value, options: { profile: profile.value }, build_draft: false };
      const value = await request(body, controller);
      if (controller.signal.aborted || !overlay.isConnected || active !== controller || epoch !== runEpoch) return;
      preview = { value, body };
      const retrieval = value.retrieval;
      if (value.person_request) {
        const person = value.person_request;
        const summary = document.createElement("section");
        const heading = document.createElement("h3"); heading.textContent = `人物需求：${person.title}`;
        const inputs = document.createElement("p"); inputs.textContent = `需要的输入：${person.required_inputs.map(item => item.label).join("、") || "无需参考图；仍需明确选择基础模型"}`;
        summary.append(heading, inputs);
        for (const message of person.limitations) { const warning = document.createElement("p"); warning.textContent = message; summary.append(warning); }
        if (person.dependency_report) {
          const note = document.createElement("p"); note.textContent = person.dependency_report.notice; summary.append(note);
          for (const route of person.dependency_report.routes) {
            const heading = document.createElement("h4"); heading.textContent = route.title;
            const missing = document.createElement("p"); missing.textContent = `缺少节点：${route.missing_nodes.join("、") || "未列出"}；缺少权重：${route.missing_weights.join("、") || "未列出（不代表已验证）"}`;
            const unchecked = document.createElement("p"); unchecked.textContent = `仍需核验：${route.unchecked.join("；")}。${route.experimental_template_available ? "可关闭本面板，使用主面板的独立“PhotoMaker身份参考（实验）”入口；不自动授权执行。" : "没有可执行模板。"}`;
            summary.append(heading, missing, unchecked);
          }
        }
        results.append(summary);
      }
      for (const model of value.selection.models) modelSelects.push({ binding: model.binding, select: makeSelect(model.label, model.choices) });
      if (value.selection.source_image_required) imageSelect = makeSelect("原始输入图片", value.selection.source_images);
      build.disabled = !value.selected_template;
      status.textContent = `${retrieval.document_count}条记录；版本 ${retrieval.corpus_revision}；节点登记 ${retrieval.registry_generation}。${value.selected_template ? "请明确选择输入与模型后编译草稿。" : "没有兼容执行模板，仅供参考。"}`;
      for (const item of retrieval.results) {
        const card = document.createElement("section");
        const heading = document.createElement("h3");
        heading.textContent = item.title;
        const detail = document.createElement("p");
        detail.textContent = `${item.id} · ${item.compatibility.status} · 分数${item.score}（不是成功概率）`;
        const body = document.createElement("p");
        body.textContent = item.text;
        const missing = document.createElement("p");
        missing.textContent = `缺少节点：${item.compatibility.missing_nodes.join("、") || "未列出"}；缺少模型选项：${(item.compatibility.missing_models || []).join("、") || "未列出"}；仍需明确选择模型：${item.compatibility.unselected_models.join("、") || "无"}`;
        const evidence = document.createElement("p"); evidence.textContent = `依据：${item.source_ids.join("、") || "插件内置模板"}；文档SHA ${item.document_sha256}`;
        card.append(heading, detail, body, missing, evidence);
        if (item.url) {
          try { const url = new URL(item.url); if (url.protocol === "https:") { const link = document.createElement("a"); link.textContent = "原始来源（外部网页）"; link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link); } } catch { /* Invalid links are not navigated. */ }
        }
        results.append(card);
      }
    } catch (error) {
      if (overlay.isConnected && active === controller && epoch === runEpoch) status.textContent = controller.signal.aborted ? "检索已取消或超时，未执行任何工作流。" : String(error.message);
    } finally {
      clearTimeout(timer);
      if (active === controller) { active = null; search.disabled = false; }
    }
  });
  build.addEventListener("click", async () => {
    if (!preview) return;
    if (modelSelects.some(item => !item.select.value) || (imageSelect && !imageSelect.value)) { status.textContent = "必须明确选择全部模型和原始图片，不使用默认第一项。"; return; }
    const runEpoch = epoch; const controller = new AbortController(); active = controller;
    const timer = setTimeout(() => controller.abort(), 30000);
    const selects = [...modelSelects.map(item => item.select), ...(imageSelect ? [imageSelect] : [])]; selects.forEach(item => { item.disabled = true; });
    build.disabled = true; search.disabled = true; download.disabled = true; compiled = null;
    try {
      const value = await request({ ...preview.body, build_draft: true, expected: preview.value.expected,
        model_overrides: Object.fromEntries(modelSelects.map(item => [item.binding, item.select.value])),
        input_overrides: imageSelect ? { source_image: imageSelect.value } : {},
      }, controller);
      if (controller.signal.aborted || !overlay.isConnected || active !== controller || epoch !== runEpoch) return;
      compiled = value.compiled;
      report.textContent = JSON.stringify({ status: value.status, validation: value.validation, draft: compiled?.draft, execution_ready: compiled?.execution_ready, execution_authorized: false }, null, 2);
      download.disabled = !compiled?.ui_workflow;
      status.textContent = compiled ? "草稿已编译；请核对验证结果。导出不运行，也不代表图像质量验收。" : "编译没有产出草稿，请检查依赖。";
    } catch (error) { if (epoch === runEpoch && overlay.isConnected) { preview = null; status.textContent = `编译失败：${error.message}。请重新检索。`; } }
    finally {
      clearTimeout(timer);
      if (active === controller) active = null;
      if (epoch === runEpoch) { build.disabled = !preview; search.disabled = false; selects.forEach(item => { item.disabled = false; }); }
    }
  });
  download.addEventListener("click", () => {
    if (!compiled?.ui_workflow || !preview) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(compiled.ui_workflow, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "workflow-architect-knowledge-draft.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  header.append(title, close);
  main.append(note, query, profile, search, build, download, status, choices, report, results);
  panel.append(header, main); overlay.append(panel); document.body.append(overlay);
}
