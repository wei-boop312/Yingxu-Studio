import { api } from "../../../scripts/api.js";

// Read-only metadata view. Never touches app.graph, installs nodes or submits prompts.
export async function openResearchPanel() {
  const existing = document.querySelector(".wa-research-overlay");
  if (existing) { existing.classList.add("open"); return; }
  const overlay = document.createElement("div");
  overlay.className = "wa-overlay wa-research-overlay open";
  const panel = document.createElement("section");
  panel.className = "wa-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "电商生产资料库");
  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = "电商生产资料库 · 研究配方，不是执行许可";
  const close = document.createElement("button");
  close.textContent = "关闭";
  close.addEventListener("click", () => overlay.remove());
  header.append(title, close);
  const main = document.createElement("main");
  const status = document.createElement("p");
  status.setAttribute("role", "status");
  status.textContent = "读取本机插件资料…";
  const search = document.createElement("input");
  search.placeholder = "搜索白底、精修、换装、风格或模型";
  search.setAttribute("aria-label", "搜索生产资料");
  search.maxLength = 200;
  const category = document.createElement("select");
  category.setAttribute("aria-label", "生产资料类别");
  for (const [value, label] of [["", "全部"], ["image", "图片"], ["portrait", "人物与换装"], ["video", "视频"]]) {
    const option = document.createElement("option"); option.value = value; option.textContent = label; category.append(option);
  }
  const results = document.createElement("div");
  main.append(status, search, category, results);
  panel.append(header, main); overlay.append(panel); document.body.append(overlay);
  let recipes = [];
  const render = () => {
    const query = search.value.trim().toLowerCase();
    const selected = recipes.filter(recipe => (!category.value || recipe.category === category.value) &&
      `${recipe.title} ${recipe.id} ${recipe.model_profiles.join(" ")}`.toLowerCase().includes(query));
    results.replaceChildren();
    for (const recipe of selected) {
      const card = document.createElement("section");
      const heading = document.createElement("h3"); heading.textContent = recipe.title;
      const info = document.createElement("p"); info.textContent = `候选模型：${recipe.model_profiles.join(" / ")}；授权审查：${recipe.commercial_review}`;
      const warning = document.createElement("p"); warning.textContent = recipe.warning;
      const boundary = document.createElement("p"); boundary.textContent = "未做目标环境推理验收；此配方不能安装、提交或证明商品保真。";
      card.append(heading, info, warning, boundary);
      for (const source of recipe.sources) {
        let url; try { url = new URL(source.url); } catch { continue; }
        if (url.protocol !== "https:") continue;
        const link = document.createElement("a"); link.textContent = `${source.id} ${source.title}`;
        link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
        card.append(link, document.createElement("br"));
      }
      results.append(card);
    }
    status.textContent = `${selected.length}项研究配方。无执行、下载或费用操作。`;
  };
  search.addEventListener("input", render); category.addEventListener("change", render);
  try {
    const response = await api.fetchApi("/workflow-architect/research", { cache: "no-store", redirect: "error" });
    if (!response.ok) throw new Error("本机尚未加载资料接口；请保存工作流并在更新插件后重启ComfyUI。");
    const envelope = await response.json();
    const data = envelope?.data;
    if (!envelope.ok || data?.execution_authorized !== false || !Array.isArray(data.recipes) || data.recipes.length > 128) throw new Error("资料合同不匹配，关闭读取。");
    if (data.recipes.some(recipe => recipe.executable !== false || recipe.runtime_verified !== false ||
      typeof recipe.title !== "string" || typeof recipe.id !== "string" || !Array.isArray(recipe.model_profiles) ||
      !recipe.model_profiles.every(value => typeof value === "string") || !Array.isArray(recipe.sources))) throw new Error("资料包含不允许的执行标记。");
    if (!overlay.isConnected) return;
    recipes = data.recipes; render();
  } catch (error) {
    if (overlay.isConnected) status.textContent = error.message;
  }
}
