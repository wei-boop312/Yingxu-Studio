import { api } from "../../../scripts/api.js";

// Explicit static preparation/export only. No model downloads or /prompt calls.
export function openIdentityPanel() {
  document.querySelector(".wa-identity-overlay")?.waDispose?.();
  const element = (tag, content = "") => { const item = document.createElement(tag); item.textContent = content; return item; };
  const overlay = element("div"); overlay.className = "wa-overlay wa-identity-overlay open";
  const panel = element("section"); panel.className = "wa-panel"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "PhotoMaker身份参考");
  const header = element("header"); const close = element("button", "关闭");
  header.append(element("h2", "PhotoMaker v1身份参考 · 实验分支"), close);
  const main = element("main");
  const warning = element("p", "仅使用获授权的单人参考图。人物质量尚未验收，结果可能明显失真，不用于生产。此分支不保证同一身份、服装/商标保真或显存足够。编译和导出不会运行任务，也不自动下载模型。建议英文画面描述。触发词由模板插入，不要自行添加img或photomaker。");
  const refresh = element("button", "检查当前依赖");
  const prompt = element("textarea"); prompt.maxLength = 2000; prompt.setAttribute("aria-label", "身份参考画面描述");
  prompt.placeholder = "wearing a dark blue jacket, studio portrait, neutral background";
  const options = element("div");
  const consent = element("input"); consent.type = "checkbox"; consent.setAttribute("aria-label", "确认参考肖像已获授权");
  const consentLabel = element("label", "我确认对所选参考肖像有权使用，且理解结果不保证身份完全一致。"); consentLabel.prepend(consent);
  const build = element("button", "编译身份参考草稿（不运行）"); build.disabled = true;
  const download = element("button", "导出身份参考JSON（不运行）"); download.disabled = true;
  const status = element("p", "先检查依赖，然后明确选择模型与参考图。"); status.setAttribute("role", "status");
  const report = element("pre"); report.style.whiteSpace = "pre-wrap"; report.style.overflowWrap = "anywhere";
  let epoch = 0, active = null, preview = null, compiled = null, fields = {};
  overlay.waDispose = () => { epoch++; active?.abort(); overlay.remove(); };
  close.addEventListener("click", overlay.waDispose);
  const clearDraft = () => { epoch++; active?.abort(); compiled = null; download.disabled = true; report.textContent = ""; build.disabled = !preview; refresh.disabled = false; };
  prompt.addEventListener("input", clearDraft); consent.addEventListener("change", clearDraft);
  async function request(path, body, signal) {
    const response = await api.fetchApi("/workflow-architect/" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store", redirect: "error", signal });
    if (!response.body) throw new Error("接口缺少正文");
    const reader = response.body.getReader(); const chunks = []; let total = 0;
    let envelope;
    try {
      while (true) { const { value, done } = await reader.read(); if (done) break; total += value.length; if (total > 1024 * 1024 || chunks.length >= 4096) throw new Error("响应超限"); chunks.push(value); }
      const bytes = new Uint8Array(total); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      envelope = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    if (!response.ok || !envelope.ok) throw new Error(envelope.error?.message || "接口未加载，请确认已更新并重启后端");
    return envelope.data;
  }
  function select(label, choices) {
    const wrapper = element("label", label); const input = element("select"); input.setAttribute("aria-label", label);
    const empty = element("option", "请明确选择，不默认取第一项"); empty.value = ""; input.append(empty);
    for (const choice of choices) { const option = element("option", choice); option.value = choice; input.append(option); }
    input.addEventListener("change", clearDraft); wrapper.append(input); options.append(wrapper); return input;
  }
  refresh.addEventListener("click", async () => {
    clearDraft(); preview = null; consent.checked = false; build.disabled = true; options.replaceChildren(); fields = {};
    const runEpoch = epoch, controller = new AbortController(); active = controller;
    const timer = setTimeout(() => controller.abort(), 30000); refresh.disabled = true; status.textContent = "读取当前节点与模型选项，不加载模型…";
    try {
      await request("refresh", {}, controller.signal);
      if (controller.signal.aborted || epoch !== runEpoch || !overlay.isConnected) return;
      const value = await request("people/identity/prepare", {}, controller.signal);
      if (controller.signal.aborted || epoch !== runEpoch || !overlay.isConnected) return;
      if (value.route !== "photomaker-v1-native" || value.execution_authorized !== false || value.runtime_verified !== false) throw new Error("身份准备合同不匹配");
      report.textContent = JSON.stringify({ missing_nodes: value.missing_nodes, missing_models: value.missing_models, notice: value.notice }, null, 2);
      if (value.missing_nodes.length || value.missing_models.length) { status.textContent = "缺少依赖，请先补齐；不会自动下载或改用普通文生图。"; return; }
      preview = value;
      fields.checkpoint = select("SDXL基础模型", value.selection.checkpoints);
      fields.photomaker = select("PhotoMaker v1权重", value.selection.photomaker);
      fields.lora = select("PhotoMaker身份LoRA（本地提取）", value.selection.lora);
      fields.reference = select("经授权的身份参考图", value.selection.reference_images);
      fields.subject = select("主体描述词", ["person", "man", "woman"]);
      fields.reference.addEventListener("change", () => { consent.checked = false; });
      build.disabled = false; status.textContent = "明确选择模型、参考图与主体词，再确认授权。默认512×512、24步、LoRA0.6、单张；不是资源或效果保证。";
    } catch (error) { if (epoch === runEpoch && overlay.isConnected) { preview = null; build.disabled = true; status.textContent = `检查失败：${error.message}`; } }
    finally { clearTimeout(timer); if (active === controller) active = null; if (epoch === runEpoch) refresh.disabled = false; }
  });
  build.addEventListener("click", async () => {
    if (!preview) return;
    if (!consent.checked || !prompt.value.trim() || Object.values(fields).some(input => !input.value)) { status.textContent = "请填写画面描述、明确选择全部模型和参考图，并确认肖像授权。"; return; }
    const runEpoch = epoch, controller = new AbortController(); active = controller;
    const timer = setTimeout(() => controller.abort(), 30000); build.disabled = true; refresh.disabled = true; download.disabled = true; compiled = null;
    const inputs = [prompt, consent, ...Object.values(fields)]; inputs.forEach(input => { input.disabled = true; });
    try {
      const value = await request("people/identity/prepare", { build_draft: true, expected: preview.expected, portrait_authorized: true,
        prompt: prompt.value, reference_image: fields.reference.value, subject: fields.subject.value, width: 512, height: 512, model_overrides: { checkpoint: fields.checkpoint.value, photomaker: fields.photomaker.value, identity_lora: fields.lora.value } }, controller.signal);
      if (controller.signal.aborted || epoch !== runEpoch || !overlay.isConnected) return;
      if (value.execution_authorized !== false || value.runtime_verified !== false) throw new Error("身份准备合同不匹配");
      compiled = value.compiled; download.disabled = !compiled?.ui_workflow;
      report.textContent = JSON.stringify({ status: value.status, validation: value.validation, execution_authorized: false, runtime_verified: false }, null, 2);
      status.textContent = compiled ? "专用草稿已编译。导出后仍需人工检查与运行；没有自动提交任务。" : "未生成草稿，请重新检查依赖。";
    } catch (error) { if (epoch === runEpoch && overlay.isConnected) { preview = null; status.textContent = `编译失败：${error.message}。请重新检查依赖。`; } }
    finally { clearTimeout(timer); if (active === controller) active = null; if (epoch === runEpoch) { build.disabled = !preview; refresh.disabled = false; inputs.forEach(input => { input.disabled = false; }); } }
  });
  download.addEventListener("click", () => {
    if (!compiled?.ui_workflow || !preview) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(compiled.ui_workflow, null, 2)], { type: "application/json" }));
    const link = element("a"); link.href = url; link.download = "workflow-architect-photomaker-reference.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  main.append(warning, refresh, prompt, options, consentLabel, build, download, status, report);
  panel.append(header, main); overlay.append(panel); document.body.append(overlay);
}
