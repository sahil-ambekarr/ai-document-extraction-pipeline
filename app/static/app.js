"use strict";

const $ = (id) => document.getElementById(id);
let activeId = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function money(value, currency) {
  if (value === null || value === undefined) return "";
  const text = Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${text} ${currency}` : text;
}

async function loadHealth() {
  try {
    const health = await (await fetch("/health")).json();
    const models = health.provider === "mock" ? "recorded responses"
      : `text: ${health.models.text}, vision: ${health.models.vision}`;
    $("health").textContent = `${health.provider} · ${models} · max ${health.max_attempts} attempts`;
    if (health.status !== "ok") $("health").textContent += " · provider unreachable";
  } catch {
    $("health").textContent = "API unreachable";
  }
}

function showResult(doc) {
  activeId = doc.id;
  $("result").classList.remove("hidden");
  $("result-title").textContent = `#${doc.id} ${doc.filename}`;
  const status = $("status");
  status.textContent = doc.valid ? "schema valid" : "invalid after retries";
  status.className = `pill ${doc.valid ? "ok" : "bad"}`;
  const retries = doc.retry_count === 1 ? "1 retry" : `${doc.retry_count} retries`;
  $("meta").textContent =
    `${doc.provider} / ${doc.model} · ${doc.mode} mode · ${retries} · ${(doc.latency_ms / 1000).toFixed(1)} s`;
  $("download-one").href = `/export.xlsx?document_id=${doc.id}`;

  $("pages").innerHTML = doc.page_urls.length
    ? doc.page_urls.map((url, i) => `<img src="${url}" alt="Page ${i + 1}">`).join("")
    : '<p class="muted">No page preview stored.</p>';

  $("attempts").innerHTML = doc.attempts.map((a) =>
    `<span>attempt ${a.attempt}: ${a.valid ? "valid" : `${a.errors.length} error(s)`} · ${(a.latency_ms / 1000).toFixed(1)} s</span>`
  ).join("");

  const errorBox = $("errors");
  if (doc.errors.length) {
    errorBox.innerHTML = `<strong>Validation errors (final attempt)</strong><ul>${
      doc.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>`;
    errorBox.classList.remove("hidden");
  } else {
    errorBox.classList.add("hidden");
  }
  $("json").textContent = JSON.stringify(doc.data, null, 2);
  document.querySelectorAll("#doc-rows tr").forEach((row) => {
    row.classList.toggle("active", Number(row.dataset.id) === doc.id);
  });
}

async function loadDocument(id) {
  const response = await fetch(`/documents/${id}`);
  if (response.ok) showResult(await response.json());
}

async function loadDocuments() {
  const docs = await (await fetch("/documents?limit=100")).json();
  const body = $("doc-rows");
  if (!docs.length) {
    body.innerHTML = '<tr><td colspan="10" class="muted">No documents yet. Upload one above.</td></tr>';
    return;
  }
  body.innerHTML = docs.map((d) => `
    <tr data-id="${d.id}" class="${d.id === activeId ? "active" : ""}">
      <td>${d.id}</td>
      <td>${escapeHtml(d.filename)}</td>
      <td>${escapeHtml(d.document_type ?? "")}</td>
      <td>${escapeHtml(d.number ?? "")}</td>
      <td>${escapeHtml(d.vendor_name ?? "")}</td>
      <td class="num">${money(d.total, d.currency)}</td>
      <td><span class="pill ${d.valid ? "ok" : "bad"}">${d.valid ? "valid" : "invalid"}</span></td>
      <td>${d.retry_count}</td>
      <td>${escapeHtml(d.mode)} / ${escapeHtml(d.model)}</td>
      <td class="num">${(d.latency_ms / 1000).toFixed(1)} s</td>
    </tr>`).join("");
  body.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => loadDocument(row.dataset.id));
  });
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData();
  form.append("file", $("file").files[0]);
  if ($("mode").value) form.append("mode", $("mode").value);
  if ($("doc_type").value) form.append("doc_type", $("doc_type").value);

  $("submit").disabled = true;
  const started = Date.now();
  const timer = setInterval(() => {
    $("progress").textContent = `Extracting… ${Math.round((Date.now() - started) / 1000)} s (CPU models can take minutes)`;
  }, 1000);
  try {
    const response = await fetch("/extract", { method: "POST", body: form });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? response.statusText);
    $("progress").textContent = "";
    showResult(payload);
    await loadDocuments();
  } catch (error) {
    $("progress").textContent = `Failed: ${typeof error.message === "string" ? error.message : JSON.stringify(error.message)}`;
  } finally {
    clearInterval(timer);
    $("submit").disabled = false;
  }
});

loadHealth();
loadDocuments();
