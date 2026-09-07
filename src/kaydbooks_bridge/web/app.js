"use strict";
const $ = (id) => document.getElementById(id);
let key = "",
  company = "",
  catalog = null,
  view = "overview",
  evidence = null,
  requestKey = "",
  currentJob = null,
  busy = false,
  accessRevision = null;
const names = {
  customer_id: "Customer",
  vendor_id: "Supplier",
  txn_date: "Transaction date",
  due_date: "Due date",
  ref_number: "Reference",
  currency: "Currency",
  deposit_id: "Bank / deposit account",
  method_id: "Payment method",
  bank_id: "Bank account",
  total_amount: "Total amount",
  invoice_txn_id: "Original invoice",
  bill_txn_id: "Original bill",
  credit_txn_id: "Credit transaction",
  terms_id: "Payment terms",
  item_id: "Item",
  expense_id: "Expense account",
  quantity: "Quantity",
  unit_price: "Unit price",
  cost: "Unit cost",
  amount: "Amount",
  txn_id: "Transaction",
  tax_amount: "Tax",
  discount_amount: "Settlement discount",
  discount_account: "Discount account",
  entity_list_id: "Customer or supplier filter",
  item_list_id: "Item filter",
};
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "checked" || k === "disabled") node[k] = v;
    else node.setAttribute(k, v);
  }
  for (const child of children.flat())
    if (child !== null && child !== undefined)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  if (tag === "label") {
    const control = node.querySelector("input,select,textarea");
    const label = [...node.childNodes]
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent)
      .join(" ")
      .trim();
    if (control && label) control.setAttribute("aria-label", label);
  }
  return node;
}
function title(value) {
  return value
    .replaceAll("-", " ")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
function notice(message, error = false) {
  $("notice").className = "notice" + (error ? " error" : "");
  $("notice").textContent = message;
}
async function api(action, parameters = {}, selected = company) {
  const response = await fetch("/api/ui", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + key,
    },
    body: JSON.stringify({
      action,
      parameters,
      ...(selected ? { company: selected } : {}),
    }),
  });
  const data = await response.json();
  if (!response.ok)
    throw Error(data.error || "The request could not be completed.");
  return data;
}
async function task(callback) {
  if (busy) return;
  busy = true;
  document.body.setAttribute("aria-busy", "true");
  $("workspace").inert = true;
  try {
    await callback();
  } catch (error) {
    notice(error.message, true);
  } finally {
    busy = false;
    document.body.removeAttribute("aria-busy");
    $("workspace").inert = false;
  }
}
function button(text, callback, cls = "") {
  return el(
    "button",
    { type: "button", class: cls, onclick: () => task(callback) },
    text,
  );
}
function options(select, values, empty = "Choose…", selected = "") {
  select.replaceChildren(el("option", { value: "" }, empty));
  for (const value of values) {
    const id = typeof value === "string" ? value : value.id;
    const label = typeof value === "string" ? title(value) : value.label;
    select.append(el("option", { value: id }, label));
  }
  select.value = selected;
}
function field(name, choices = null, value = "", optional = false, label = null) {
  const input = choices
    ? el("select", { "data-field": name })
    : el("input", {
        "data-field": name,
        type:
          name.endsWith("_date") || ["date_from", "date_to"].includes(name)
            ? "date"
            : "text",
        inputmode: [
          "amount",
          "total_amount",
          "quantity",
          "unit_price",
          "cost",
          "discount_amount",
        ].includes(name)
          ? "decimal"
          : "text",
      });
  if (choices) options(input, choices, optional ? "None" : "Choose…", value);
  else input.value = value;
  if (!optional) input.required = true;
  return el("label", {}, label || names[name] || title(name), input);
}
function value(root, name) {
  return root.querySelector('[data-field="' + name + '"]').value;
}
function setValue(root, name, v) {
  const x = root.querySelector('[data-field="' + name + '"]');
  if (x) x.value = v ?? "";
}
function permissions(p) {
  return catalog.permissions.includes(p);
}
function empty(message) {
  $("content").replaceChildren(
    el(
      "div",
      { class: "empty" },
      el("h2", {}, message),
      el("p", {}, "Choose a company to continue."),
    ),
  );
}
function table(headers, rows) {
  const body = el("tbody");
  for (const row of rows)
    body.append(
      el(
        "tr",
        {},
        row.map((v) => el("td", {}, v)),
      ),
    );
  return el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      {},
      el(
        "thead",
        {},
        el(
          "tr",
          {},
          headers.map((h) => el("th", {}, h)),
        ),
      ),
      body,
    ),
  );
}
function panel(heading, description, ...children) {
  return el(
    "section",
    { class: "panel" },
    el(
      "div",
      { class: "panel-title" },
      el(
        "div",
        {},
        el("h2", {}, heading),
        description ? el("p", {}, description) : null,
      ),
    ),
    ...children,
  );
}
function badge(state) {
  return el("span", { class: "badge " + state }, title(state));
}
function decimal(raw, scale = 6) {
  if (!/^\d{1,12}(?:\.\d{1,6})?$/.test(raw))
    throw Error("Enter a plain positive decimal.");
  const [a, b = ""] = raw.split(".");
  if (b.length > scale) throw Error("Amount must use exact cents.");
  return BigInt(a) * 10n ** BigInt(scale) + BigInt(b.padEnd(scale, "0") || "0");
}
function money(raw) {
  const n = decimal(raw, 2);
  return (n / 100n).toString() + "." + (n % 100n).toString().padStart(2, "0");
}
function lineAmount(q, p) {
  const v = (decimal(q) * decimal(p) + 5000000000n) / 10000000000n;
  return (v / 100n).toString() + "." + (v % 100n).toString().padStart(2, "0");
}
function keyFor() {
  return "manual-" + crypto.randomUUID().replaceAll("-", "");
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  key = $("access-key").value;
  $("login-error").textContent = "";
  try {
    const session = await api("catalog", {}, "");
    $("principal").textContent = "Signed in as " + session.principal;
    options($("company"), session.companies, "Choose a company");
    $("access-key").value = "";
    $("login").classList.add("hidden");
    $("workspace").classList.remove("hidden");
  } catch (error) {
    key = "";
    $("login-error").textContent = error.message;
  }
});
$("logout").onclick = () => {
  key = "";
  location.reload();
};
$("company").onchange = () =>
  task(async () => {
    company = $("company").value;
    catalog = null;
    currentJob = null;
    evidence = null;
    if (!company) {
      empty("Choose your company");
      return;
    }
    catalog = await api("catalog");
    await show(view);
  });
for (const nav of document.querySelectorAll("nav button"))
  nav.onclick = () => task(() => show(nav.dataset.view));
async function show(next) {
  view = next;
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("page-title").textContent = {
    overview: "Overview",
    entry: "New document",
    reports: "Reports",
    imports: "Import spreadsheet",
    access: "User access",
    upload: "Upload document",
    dispatch: "Posting schedules",
    masters: "Customers, suppliers & items",
  }[view];
  if (!catalog) {
    empty("Choose your company");
    return;
  }
  $("notice").classList.add("hidden");
  if (view === "overview") await overview();
  if (view === "entry") entry();
  if (view === "reports") reportForm();
  if (view === "imports") imports();
  if (view === "upload") uploadDocument();
  if (view === "access") await access();
  if (view === "dispatch") await postingSchedules();
  if (view === "masters") masterForm();
  window.scrollTo(0, 0);
}
async function overview() {
  const state = await api("status");
  const counts = { draft: 0, queued: 0, verified: 0, attention: 0 };
  for (const j of state.jobs) {
    if (j.state in counts) counts[j.state]++;
    if (["unknown", "posted-unverified", "in-flight"].includes(j.state))
      counts.attention++;
  }
  const cards = el(
    "div",
    { class: "cards" },
    [
      ["Drafts", counts.draft, "Ready for review"],
      ["Queued", counts.queued, "Awaiting deliberate posting"],
      ["Verified", counts.verified, "Saved result checked"],
      ["Needs attention", counts.attention, "Held for reconciliation"],
    ].map(([l, v, h], i) =>
      el(
        "div",
        { class: "metric" + (i === 2 ? " accent" : "") },
        el("div", { class: "label" }, l),
        el("div", { class: "value" }, v),
        el("div", { class: "hint" }, h),
      ),
    ),
  );
  const actions = el(
    "div",
    { class: "toolbar" },
    button("New document", () => show("entry"), "primary"),
    button("Refresh", overview),
  );
  if (permissions("pause"))
    actions.append(
      button(
        state.paused ? "Resume sample workspace" : "Pause sample workspace",
        async () => {
          await api("pause", { paused: !state.paused });
          await overview();
        },
      ),
    );
  const rows = state.jobs.map((j) => [
    j.ref_number || j.id.slice(0, 10),
    catalog.operations[j.operation] || j.operation,
    j.txn_date || "—",
    badge(j.state),
    button("Open", () => openJob(j.id)),
  ]);
  const box = panel(
    "Documents",
    state.paused
      ? "Sample posting is paused. Review and read-only checks remain available."
      : "Sample posting requires a deliberate action and valid company limits.",
    actions,
    table(["Reference", "Document", "Date", "Status", ""], rows),
  );
  if (!rows.length)
    box.append(
      el(
        "p",
        { class: "small" },
        "No documents yet. Create a draft to get started.",
      ),
    );
  $("content").replaceChildren(
    cards,
    box,
    el(
      "p",
      { class: "small" },
      "Showing the latest " +
        state.jobs.length +
        " of " +
        state.total_jobs +
        " documents. Production posting is disabled.",
    ),
  );
}
function entry(existing = null, onTemplate = null, observed = null) {
  evidence = null;
  requestKey = keyFor();
  currentJob = existing;
  const op = el("select", { id: "operation" });
  options(
    op,
    Object.entries(catalog.operations)
      .filter(([id]) => id !== "master.change")
      .map(([id, label]) => ({ id, label })),
    "Choose a document",
    existing?.operation || "invoice.create",
  );
  op.disabled = !!existing;
  const form = el("form", { id: "document-form" });
  const basics = el("div", { class: "grid" }),
    lines = el("div", { id: "lines" }),
    lineSection = el("section", { class: "form-section" }),
    extras = el("div", { class: "grid" });
  const connector = el("select", { id: "connector" });
  options(
    connector,
    catalog.connectors,
    "Choose a connection",
    catalog.connectors.length === 1 ? catalog.connectors[0] : "",
  );
  const namespace = el("select", { id: "namespace" });
  options(
    namespace,
    catalog.sources,
    "Choose a source",
    catalog.sources.length === 1 ? catalog.sources[0] : "",
  );
  const settings = el(
    "div",
    { class: "grid" },
    el("label", {}, "Document type", op),
    el("label", {}, "QuickBooks connection", connector),
    el("label", {}, "Source", namespace),
  );
  const check = button("Check details", async () => {
    const payload = collect();
    evidence = await api("check", {
      operation: op.value,
      connector_id: connector.value,
      payload,
    });
    notice("Details checked against QuickBooks. Save this draft for review.");
    save.disabled = false;
  });
  const save = button(
    existing ? "Save correction and review" : "Save and review",
    async () => {
      if (!evidence) throw Error("Check the current details first.");
      const params = {
        request_key: requestKey,
        namespace: namespace.value,
        operation: op.value,
        payload: collect(),
        master_evidence: evidence.evidence,
      };
      if (existing)
        params.revision = {
          parent_id: existing.id,
          parent_fingerprint: existing.fingerprint,
          reason: $("correction-reason").value,
        };
      const job = observed
        ? await api("prepare_extraction_v1", {
            extraction_id: observed.extraction_id,
            extraction_sha256: observed.sha256,
            idempotency_key: requestKey,
            operation: op.value,
            payload: params.payload,
            master_evidence: evidence.evidence,
          })
        : await api("prepare", params);
      if (!observed) await api("validate", { job_id: job.id });
      await openJob(job.id);
      notice(
        observed
          ? "Source draft saved. Confirm every uncertain value against the original before validation."
          : existing
            ? "Correction saved. The original and its history are retained."
            : "Draft saved and validated. Review it before approval or posting.",
      );
    },
    "primary",
  );
  save.disabled = true;
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("input", () => {
    evidence = null;
    save.disabled = true;
  });
  form.addEventListener("change", () => {
    evidence = null;
    save.disabled = true;
  });
  let collection = "lines";
  function addLine(data = {}, kind = null) {
    const operation = op.value,
      isBill = ["bill.create", "supplier-credit.create"].includes(operation),
      isAllocation = collection === "allocations";
    const row = el("div", { class: "line" + (isBill ? " bill" : "") });
    let type = null;
    if (isBill) {
      type = el("select", { "data-kind": "kind" });
      options(
        type,
        [
          { id: "expense", label: "Expense" },
          { id: "item", label: "Item" },
        ],
        "Choose",
        kind || (data.item_id ? "item" : "expense"),
      );
      row.append(el("label", {}, "Line type", type));
    }
    function fields() {
      [...row.children]
        .filter((x) => !type || !x.contains(type))
        .forEach((x) => x.remove());
      if (isAllocation) {
        row.append(
          field("txn_id", null, data.txn_id || ""),
          field("amount", null, data.amount || ""),
        );
        if (
          ["customer-payment.create", "supplier-payment.create"].includes(
            operation,
          )
        ) {
          const role =
            operation === "customer-payment.create"
              ? "customer_discount"
              : "supplier_discount";
          row.append(
            field("discount_amount", null, data.discount_amount || "", true),
            field(
              "discount_account",
              catalog.master_account_roles.filter((r) => r === role),
              data.discount_account || "",
              true,
            ),
          );
        }
      } else if (isBill && type.value === "expense")
        row.append(
          field("expense_id", catalog.choices.expenses, data.expense_id || ""),
          field("amount", null, data.amount || ""),
        );
      else {
        const rate = isBill ? "cost" : "unit_price";
        row.append(
          field(
            "item_id",
            isBill ? catalog.choices.bill_items : catalog.choices.items,
            data.item_id || "",
          ),
          field("quantity", null, data.quantity || "1"),
          field(rate, null, data[rate] || ""),
          field("amount", null, data.amount || ""),
        );
        row.querySelector('[data-field="amount"]').readOnly = true;
        for (const f of ["quantity", rate])
          row
            .querySelector('[data-field="' + f + '"]')
            .addEventListener("input", () => {
              try {
                setValue(
                  row,
                  "amount",
                  lineAmount(value(row, "quantity"), value(row, rate)),
                );
              } catch {
                setValue(row, "amount", "");
              }
            });
      }
      row.append(
        button("Remove", () => {
          row.remove();
          evidence = null;
          save.disabled = true;
        }),
      );
    }
    fields();
    if (type)
      type.onchange = () => {
        data = {};
        fields();
      };
    lines.append(row);
  }
  function build() {
    basics.replaceChildren();
    extras.replaceChildren();
    lines.replaceChildren();
    lineSection.replaceChildren();
    const operation = op.value,
      isCustomer =
        operation.startsWith("customer") || operation === "invoice.create",
      isCreditApply = operation.endsWith(".apply");
    const accountChoices = isCustomer
      ? operation === "invoice.create" ||
        operation === "customer-credit.create" ||
        isCreditApply
        ? catalog.choices.customers
        : catalog.choices.payment_customers
      : operation === "bill.create" || operation === "supplier-credit.create"
        ? catalog.choices.bill_vendors
        : catalog.choices.payment_vendors;
    basics.append(
      field(isCustomer ? "customer_id" : "vendor_id", accountChoices),
      field("ref_number"),
      field("currency", [catalog.currency], catalog.currency),
    );
    if (["supplier-payment.create", "supplier-credit.apply"].includes(operation))
      basics.querySelector('[data-field="ref_number"]').maxLength = 11;
    if (!isCreditApply) basics.append(field("txn_date"));
    if (operation === "bill.create")
      basics.append(
        field("due_date"),
        field("terms_id", catalog.choices.terms, "", true),
      );
    if (
      operation === "customer-credit.create" ||
      operation === "customer-credit.apply"
    )
      basics.append(field("invoice_txn_id"));
    if (
      operation === "supplier-credit.create" ||
      operation === "supplier-credit.apply"
    )
      basics.append(field("bill_txn_id"));
    if (isCreditApply)
      basics.append(field("credit_txn_id"), field("total_amount"));
    if (
      operation === "supplier-payment.create" ||
      operation === "supplier-credit.apply"
    )
      basics.append(field("bank_id", catalog.choices.banks));
    if (
      operation === "customer-payment.create" ||
      operation === "customer-refund.create"
    )
      basics.append(
        field("deposit_id", catalog.choices.deposits),
        field("method_id", catalog.choices.methods),
      );
    collection =
      operation.includes("payment") || operation === "customer-refund.create"
        ? "allocations"
        : "lines";
    if (!isCreditApply) {
      if (collection === "allocations") basics.append(field("total_amount"));
      lineSection.append(
        el(
          "h3",
          {},
          collection === "allocations"
            ? "Apply to transactions"
            : "Document lines",
        ),
        lines,
        button(
          collection === "allocations" ? "Add allocation" : "Add line",
          () => addLine(),
        ),
      );
      addLine();
    }
    if (existing) {
      for (const [k, v] of Object.entries(existing.payload))
        if (!Array.isArray(v)) setValue(basics, k, v);
      lines.replaceChildren();
      for (const data of existing.payload[collection] || []) addLine(data);
    }
  }
  function collect() {
    if (!form.reportValidity()) throw Error("Complete the required fields.");
    const payload = {};
    for (const input of basics.querySelectorAll("[data-field]"))
      if (input.value)
        payload[input.dataset.field] =
          input.dataset.field === "total_amount"
            ? money(input.value)
            : input.value;
    if (!op.value.endsWith(".apply"))
      payload[collection] = [...lines.children].map((row) => {
        const out = {};
        for (const input of row.querySelectorAll("[data-field]")) {
          if (
            ["discount_amount", "discount_account"].includes(
              input.dataset.field,
            ) &&
            !input.value
          )
            continue;
          out[input.dataset.field] = ["amount", "discount_amount"].includes(
            input.dataset.field,
          )
            ? money(input.value)
            : input.value;
        }
        return out;
      });
    if (["invoice.create", "customer-credit.create"].includes(op.value))
      payload.tax_amount = "0.00";
    return payload;
  }
  op.onchange = () => {
    evidence = null;
    save.disabled = true;
    build();
  };
  build();
  form.append(
    settings,
    el("section", { class: "form-section" }, el("h3", {}, "Details"), basics),
    lineSection,
  );
  if (existing)
    form.append(
      el(
        "label",
        { class: "form-section" },
        "Reason for correction",
        el("input", {
          id: "correction-reason",
          required: "required",
          maxlength: "500",
        }),
      ),
    );
  form.append(
    el(
      "section",
      { class: "form-section toolbar" },
      ...(onTemplate
        ? [
            button(
              "Use these defaults for the spreadsheet",
              () =>
                onTemplate({
                  payload: collect(),
                  operation: op.value,
                  namespace: namespace.value,
                  connector: connector.value,
                }),
              "primary",
            ),
          ]
        : [check, save]),
    ),
  );
  if (observed) {
    namespace.value = observed.namespace;
    namespace.disabled = true;
    const sourcePanel = el(
      "details",
      { class: "form-section", open: "open" },
      el(
        "summary",
        {},
        "Retained source observations - review against the original",
      ),
    );
    for (const page of observed.pages)
      sourcePanel.append(
        el("h3", {}, "Page " + page.page),
        el("pre", { class: "observed-text" }, page.text),
      );
    sourcePanel.append(
      table(
        ["Observed field", "Alternatives"],
        Object.entries(observed.candidates)
          .filter(([, v]) => v.length)
          .map(([k, v]) => [title(k), v.map((c) => c.text).join(" | ")]),
      ),
    );
    form.prepend(sourcePanel);
  }
  $("content").replaceChildren(
    panel(
      onTemplate
        ? "Set spreadsheet defaults"
        : existing
          ? "Correct document"
          : "Prepare a document",
      onTemplate
        ? "Enter one valid example. Next, choose which values each spreadsheet column replaces. Unmapped fields keep these defaults."
        : "Enter the accounting details, check them against QuickBooks, then save for review.",
      form,
    ),
  );
}

async function openJob(id) {
  view = "overview";
  $("page-title").textContent = "Review document";
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  currentJob = await api("job", { job_id: id });
  const job = currentJob;
  let preview = null;
  if (["validated", "queued"].includes(job.state)) {
    try {
      preview = await api("preview", { job_id: id });
    } catch (error) {
      notice(error.message, true);
    }
  }
  const detail = el("dl", { class: "detail" });
  for (const [k, v] of Object.entries(job.payload)) {
    if (Array.isArray(v)) continue;
    if (job.operation === "master.change" && typeof v === "object") continue;
    detail.append(
      el("div", {}, el("dt", {}, names[k] || title(k)), el("dd", {}, v)),
    );
  }
  const actions = el("div", { class: "toolbar" });
  actions.append(
    button("Back to documents", () => show("overview")),
    button("Refresh", () => openJob(id)),
  );
  if (
    ["draft", "validated", "queued"].includes(job.state) &&
    !job.attempt &&
    permissions("prepare") &&
    job.submitter === catalog.principal
  )
    actions.append(
      button("Correct draft", () =>
        job.operation === "master.change" ? masterForm(job) : entry(job),
      ),
    );
  if (job.state === "draft" && permissions("validate"))
    actions.append(
      button(
        "Validate draft",
        async () => {
          await api("validate", { job_id: id });
          await openJob(id);
        },
        "primary",
      ),
    );
  if (["validated", "queued"].includes(job.state) && permissions("approve"))
    actions.append(
      button("Approve", async () => {
        await api("approve", { job_id: id });
        await openJob(id);
        notice("Approval recorded for these exact details.");
      }),
    );
  if (job.state === "validated" && permissions("submit"))
    actions.append(
      button(
        "Queue for posting",
        async () => {
          await api("submit", { job_id: id });
          await openJob(id);
        },
        "primary",
      ),
    );
  if (job.state === "queued" && permissions("post-sample"))
    actions.append(
      button(
        "Post to sample company",
        async () => {
          await api("post-sample", { job_id: id });
          await openJob(id);
        },
        "primary",
      ),
    );
  if (
    ["unknown", "posted-unverified"].includes(job.state) &&
    permissions("recover")
  )
    actions.append(
      button("Reconcile saved result", async () => {
        await api("reconcile-sample", { job_id: id });
        await openJob(id);
      }),
    );
  const p = panel(
    job.payload.ref_number,
    catalog.operations[job.operation],
    badge(job.state),
    detail,
  );
  const rows = job.payload.lines || job.payload.allocations || [];
  if (job.operation === "master.change") {
    p.append(
      table(
        ["Changed field", "New value"],
        Object.entries(job.payload.fields).map(([k, v]) => [
          title(k),
          String(v),
        ]),
      ),
    );
    if (job.master_evidence?.original)
      p.append(
        el(
          "details",
          {},
          el("summary", {}, "Reviewed original record"),
          el("pre", {}, JSON.stringify(job.master_evidence.original, null, 2)),
        ),
      );
  }
  if (rows.length) {
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    p.append(
      table(
        cols.map((k) => names[k] || title(k)),
        rows.map((r) => cols.map((k) => r[k] || "—")),
      ),
    );
  }
  if (preview?.total)
    p.append(
      el(
        "div",
        { class: "totals" },
        "Total",
        el("strong", {}, catalog.currency + " " + preview.total),
      ),
    );
  if (preview?.discount_total)
    p.append(
      el(
        "p",
        {},
        "Cash: " +
          catalog.currency +
          " " +
          preview.cash_total +
          " · Settlement discount: " +
          catalog.currency +
          " " +
          preview.discount_total +
          " · Total settled: " +
          catalog.currency +
          " " +
          preview.settlement_total,
      ),
    );
  if (job.approval_by)
    p.append(el("p", { class: "small" }, "Approved by " + job.approval_by));
  if (job.txn_id)
    p.append(
      el(
        "p",
        { class: "small" },
        (job.operation === "master.change"
          ? "QuickBooks record: "
          : "QuickBooks transaction: ") + job.txn_id,
      ),
    );
  if (job.source_observations) {
    const observed = el(
      "details",
      { class: "form-section" },
      el("summary", {}, "Read retained OCR observations"),
    );
    for (const page of job.source_observations.pages)
      observed.append(
        el("h3", {}, "Page " + page.page),
        el("pre", { class: "observed-text" }, page.text),
      );
    p.append(observed);
  }
  const source = job.source;
  p.append(
    el(
      "div",
      { class: "source" },
      "Original source retained · " +
        source.namespace +
        " / " +
        source.reference,
      el("br"),
      "Document fingerprint: " + job.fingerprint,
    ),
  );
  if (source.original_values?.document_id)
    p.append(
      button("Download original source", async () => {
        const retained = await api("source", { job_id: id });
        const bytes = Uint8Array.from(atob(retained.content_base64), (c) =>
          c.charCodeAt(0),
        );
        const url = URL.createObjectURL(
          new Blob([bytes], { type: "application/octet-stream" }),
        );
        const suffix =
          {
            "application/json": ".json",
            "text/csv": ".csv",
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
              ".xlsx",
          }[retained.media_type] || ".txt";
        const a = el("a", { href: url, download: retained.reference + suffix });
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      }),
    );
  if (job.state === "superseded")
    p.append(button("Open current revision", () => openJob(job.superseded_by)));
  if (
    source.uncertain_fields?.length &&
    job.state === "draft" &&
    permissions("review-source")
  ) {
    const confirmations = el("div", { class: "checks" }),
      confirmed = {};
    for (const path of source.uncertain_fields) {
      const v = path
        .split(".")
        .reduce((a, k) => a[k], source.original_values.extraction);
      confirmed[path] = v;
      confirmations.append(
        el(
          "label",
          {},
          el("input", { type: "checkbox" }),
          path + " = " + String(v),
        ),
      );
    }
    p.append(
      el(
        "section",
        { class: "form-section" },
        el("h3", {}, "Confirm uncertain source values"),
        el(
          "p",
          {},
          "Compare every value below with the original source before confirming. Use Correct draft if a value is wrong.",
        ),
        confirmations,
        button("Confirm reviewed values", async () => {
          if (
            [...confirmations.querySelectorAll("input")].some((c) => !c.checked)
          )
            throw Error("Review and select every uncertain field.");
          await api("review-source", {
            job_id: id,
            fingerprint: job.fingerprint,
            confirmed_values: confirmed,
          });
          await openJob(id);
          notice("Source review recorded. The draft can now be validated.");
        }),
      ),
    );
  }
  p.append(el("section", { class: "form-section" }, actions));
  $("content").replaceChildren(p);
  window.scrollTo(0, 0);
}

function reportForm() {
  const report = el("select", { id: "report-type" });
  options(
    report,
    Object.keys(catalog.reports),
    "Choose a report",
    "profit-loss",
  );
  const connection = el("select", { id: "report-connection" });
  options(
    connection,
    catalog.connectors,
    "Choose a connection",
    catalog.connectors.length === 1 ? catalog.connectors[0] : "",
  );
  const from = field("date_from"),
    to = field("date_to"),
    basis = el("select", { id: "report-basis" }),
    entity = field("entity_list_id", catalog.report_entities, "", true),
    item = field("item_list_id", catalog.report_items, "", true);
  const dates = el(
    "div",
    { class: "grid" },
    from,
    to,
    el("label", {}, "Accounting basis", basis),
    entity,
    item,
  );
  const form = el(
    "form",
    {},
    el(
      "div",
      { class: "grid two" },
      el("label", {}, "Report", report),
      el("label", {}, "Connection", connection),
    ),
    el("section", { class: "form-section" }, dates),
  );
  function change() {
    const cap = catalog.reports[report.value] || {
      date_mode: "period",
      fixed_accrual: false,
    };
    from.classList.toggle("hidden", cap.date_mode !== "period");
    from.querySelector("input").required = cap.date_mode === "period";
    options(
      basis,
      cap.fixed_accrual ? ["Accrual"] : ["Accrual", "Cash"],
      "Choose basis",
      "Accrual",
    );
  }
  report.onchange = change;
  change();
  form.onsubmit = (e) => e.preventDefault();
  form.append(
    el(
      "section",
      { class: "form-section" },
      button(
        "Run report",
        async () => {
          if (!report.value || !form.reportValidity())
            throw Error("Choose a report and its dates.");
          const specification = {
            report: report.value,
            date_to: value(to, "date_to"),
            basis: basis.value,
          };
          if (catalog.reports[report.value].date_mode === "period")
            specification.date_from = value(from, "date_from");
          for (const [node, k] of [
            [entity, "entity_list_id"],
            [item, "item_list_id"],
          ])
            if (value(node, k)) specification[k] = value(node, k);
          notice("Reading the report from QuickBooks…");
          const result = await api("native_report_v1", {
            connector_id: connection.value,
            run_id: String(Date.now()) + "1",
            specification,
          });
          drawReport(result.report);
        },
        "primary",
      ),
    ),
  );
  $("content").replaceChildren(
    panel(
      "Company reports",
      "Choose an explicit period and accounting basis. Reports are read-only.",
      form,
    ),
    el("div", { id: "report-result" }),
  );
}
function drawReport(report) {
  const wrap = table(
    report.columns.map((c) =>
      c.titles
        .map((t) => t.value)
        .filter(Boolean)
        .join(" · "),
    ),
    report.rows.map((r) =>
      report.columns.map(
        (c) =>
          r.cells[String(c.id)]?.value ??
          (c.id === 1 ? r.label?.value || r.text || "" : ""),
      ),
    ),
  );
  wrap.querySelector("table").className = "report-table";
  [...wrap.querySelectorAll("tbody tr")].forEach(
    (tr, i) => (tr.className = report.rows[i].kind.toLowerCase()),
  );
  $("report-result").replaceChildren(
    panel(
      report.title,
      report.subtitle,
      el(
        "p",
        { class: "small" },
        catalog.currency +
          " · " +
          report.basis +
          " basis · " +
          report.row_count +
          " rows · Complete native response",
      ),
      wrap,
      el(
        "p",
        { class: "source" },
        "Read " +
          new Date(report.read_started_at * 1000).toLocaleString() +
          " · Evidence " +
          report.response_sha256,
      ),
    ),
  );
  notice("Report received. Native totals and source evidence are retained.");
}

function masterForm(parent = null) {
  let original = parent?.master_evidence?.original || null,
    target = parent?.payload?.target || null,
    proof = null;
  const request = "master-" + crypto.randomUUID();
  const form = el(
    "form",
    {},
    el(
      "div",
      { class: "grid" },
      field(
        "kind",
        ["customer", "supplier", "service", "inventory", "discount", "other-charge"],
        parent?.payload.kind || "customer",
      ),
      field(
        "change_action",
        ["create", "update"],
        parent?.payload.action || "create",
      ),
      field("ref_number", null, parent?.payload.ref_number || ""),
      field(
        "source",
        catalog.sources,
        catalog.sources.length === 1 ? catalog.sources[0] : "",
      ),
      field(
        "connector",
        catalog.connectors,
        catalog.connectors.length === 1 ? catalog.connectors[0] : "",
      ),
      field(
        "service_mode",
        ["sales", "sales-purchase"],
        parent?.payload.service_mode || "sales",
      ),
      field("list_id", null, target?.list_id || "", true),
    ),
  );
  const fields = el("div", { class: "grid form-section" }),
    observed = el("div"),
    toolbar = el("div", { class: "toolbar" });
  const reason = field("reason_for_correction", null, "", !parent);
  const save = button(
    "Save master draft",
    async () => {
      if (!proof) throw Error("Check the current master details first.");
      const parameters = {
        request_key: request,
        namespace: value(form, "source"),
        operation: "master.change",
        payload: payload(),
        master_evidence: proof,
      };
      if (parent)
        parameters.revision = {
          parent_id: parent.id,
          parent_fingerprint: parent.fingerprint,
          reason: value(form, "reason_for_correction"),
        };
      const job = await api("prepare", parameters);
      await api("validate", { job_id: job.id });
      await openJob(job.id);
      notice(
        "Master draft validated. Approval and sample posting remain separate actions.",
      );
    },
    "primary",
  );
  save.disabled = true;
  function changed() {
    if (proof)
      notice("Details changed. Check the master details again before saving.");
    proof = null;
    save.disabled = true;
  }
  function renderValues(prefill = null) {
    fields.replaceChildren();
    const kind = value(form, "kind"),
      update = value(form, "change_action") === "update",
      purchased = value(form, "service_mode") === "sales-purchase";
    const mapped = {
      name: original?.Name || "",
      active: original?.IsActive !== "false",
      company_name: original?.CompanyName || "",
      phone: original?.Phone || "",
      email: original?.Email || "",
    };
    const item =
      original?.SalesOrPurchase || original?.SalesAndPurchase || original || {};
    Object.assign(
      mapped,
      {
        sales_description: item.Desc || item.SalesDesc || "",
        sales_price: item.Price || item.SalesPrice || "0.00",
        purchase_description: item.PurchaseDesc || "",
        purchase_cost: item.PurchaseCost || "0.00",
        discount_description: item.ItemDesc || "",
        discount_amount: item.DiscountRate || "0.00",
      },
      prefill || {},
    );
    fields.append(
      field("name", null, mapped.name),
      el(
        "label",
        {},
        "Active",
        el("input", {
          type: "checkbox",
          "data-field": "active",
          checked: mapped.active,
        }),
      ),
    );
    if (["customer", "supplier"].includes(kind))
      for (const n of ["company_name", "phone", "email"])
        fields.append(field(n, null, mapped[n], true));
    else if (kind === "discount") {
      fields.append(
        field("discount_description", null, mapped.discount_description, true),
        field("discount_amount", null, mapped.discount_amount, false, "Fixed discount amount"),
      );
      if (!update)
        fields.append(field("discount_account", catalog.master_account_roles, mapped.discount_account || ""));
    }
    else {
      fields.append(
        field("sales_description", null, mapped.sales_description, true),
        field("sales_price", null, mapped.sales_price),
      );
      if (!update)
        fields.append(
          field(
            "income_account",
            catalog.master_account_roles,
            mapped.income_account || "",
          ),
        );
      if (kind === "inventory" || purchased) {
        fields.append(
          field(
            "purchase_description",
            null,
            mapped.purchase_description,
            true,
          ),
          field("purchase_cost", null, mapped.purchase_cost),
        );
        if (!update)
          for (const n of kind === "inventory"
            ? ["cogs_account", "asset_account"]
            : ["expense_account"])
            fields.append(
              field(n, catalog.master_account_roles, mapped[n] || ""),
            );
      }
    }
    form.querySelector('[data-field="list_id"]').disabled = !update;
    form.querySelector('[data-field="list_id"]').closest("label").hidden =
      !update;
    form.querySelector('[data-field="service_mode"]').disabled =
      !["service", "other-charge"].includes(kind) || (update && !!original);
    form.querySelector('[data-field="service_mode"]').closest("label").hidden =
      !["service", "other-charge"].includes(kind);
    observed.replaceChildren(
      original
        ? el(
            "details",
            {},
            el("summary", {}, "Original record and edit sequence"),
            el("pre", {}, JSON.stringify(original, null, 2)),
          )
        : el(
            "p",
            { class: "small" },
            update
              ? "Read the exact record before editing it."
              : "New records start without balances or stock.",
          ),
    );
    changed();
  }
  function payload() {
    if (!form.reportValidity())
      throw Error("Complete the required master fields.");
    const kind = value(form, "kind"),
      action = value(form, "change_action"),
      values = {};
    for (const input of fields.querySelectorAll("[data-field]"))
      values[input.dataset.field] =
        input.type === "checkbox" ? input.checked : input.value;
    const result = {
      ref_number: value(form, "ref_number"),
      kind,
      action,
      fields: values,
    };
    if (["service", "other-charge"].includes(kind)) result.service_mode = value(form, "service_mode");
    if (action === "update") {
      if (!target || target.list_id !== value(form, "list_id"))
        throw Error("Read the selected master record first.");
      result.target = target;
    }
    return result;
  }
  toolbar.append(
    button("Read existing record", async () => {
      if (value(form, "change_action") !== "update")
        throw Error("Choose update to read an existing record.");
      const result = await api("master-lookup", {
        connector_id: value(form, "connector"),
        kind: value(form, "kind"),
        list_id: value(form, "list_id"),
      });
      original = result.record;
      target = result.target;
      if (["service", "other-charge"].includes(value(form, "kind")))
        setValue(
          form,
          "service_mode",
          original.SalesAndPurchase ? "sales-purchase" : "sales",
        );
      renderValues();
      notice("Original record read. Review and edit the intended fields.");
    }),
    button("Check master details", async () => {
      const result = await api("check", {
        operation: "master.change",
        connector_id: value(form, "connector"),
        payload: payload(),
      });
      proof = result.evidence;
      save.disabled = false;
      notice(
        "Names, original edit sequence and accounts checked. Review the fields before saving.",
      );
    }),
    save,
  );
  form.append(toolbar, observed, fields);
  if (parent) form.append(reason);
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("input", changed);
  for (const name of ["kind", "change_action", "service_mode"])
    form
      .querySelector('[data-field="' + name + '"]')
      .addEventListener("change", () => {
        original = null;
        target = null;
        renderValues();
      });
  form.querySelector('[data-field="list_id"]').addEventListener("input", () => {
    original = null;
    target = null;
  });
  renderValues(parent?.payload.fields);
  $("content").replaceChildren(
    panel(
      parent ? "Correct master draft" : "Customers, suppliers & items",
      "Changes retain the original source and require current company permissions and review. Sample updates are limited to Bridge-created test records.",
      form,
    ),
  );
  window.scrollTo(0, 0);
}

async function postingSchedules() {
  const data = await api("dispatch-status");
  const body = panel(
    "Posting schedules",
    "Manual is the default. Enabled profiles dispatch only queued, approved work under the selected company's sample limits. Exceptions stay in review.",
  );
  for (const profile of data.profiles) {
    const details = el(
      "details",
      {},
      el(
        "summary",
        {},
        profile.id +
          " · " +
          profile.definition.mode +
          " · " +
          (profile.enabled ? "Enabled" : "Cancelled"),
      ),
      el(
        "pre",
        {},
        JSON.stringify(
          { rules: profile.definition, runs: profile.occurrences },
          null,
          2,
        ),
      ),
    );
    if (profile.enabled && permissions("manage-workflows"))
      details.append(
        button("Cancel " + profile.id, async () => {
          await api("dispatch-cancel", { profile_id: profile.id });
          await postingSchedules();
          notice("Profile cancelled. Future write authorizations are stopped.");
        }),
      );
    body.append(details);
  }
  if (
    !["manage-workflows", "post-sample", "submit", "validate"].every(
      permissions,
    )
  ) {
    body.append(
      el("p", {}, "Your permissions allow viewing these profiles only."),
    );
    $("content").replaceChildren(body);
    return;
  }
  const jobs = (await api("status")).jobs.filter((j) => j.state === "queued");
  const form = el(
    "form",
    { class: "form-grid" },
    field("profile_id", null, "profile-" + Date.now()),
    field("mode", ["scheduled", "automatic"], "scheduled"),
    field(
      "operation",
      Object.entries(catalog.operations)
        .filter(([id]) => id !== "master.change")
        .map(([id, label]) => ({ id, label })),
      "invoice.create",
    ),
    field("source", catalog.sources),
    field(
      "scheduled_job",
      jobs.map((j) => ({ id: j.id, label: j.ref_number || j.id })),
      "",
      true,
    ),
    field(
      "first_run_utc",
      null,
      new Date(Date.now() + 60000).toISOString().slice(0, 16),
    ),
    field("interval_minutes", null, "15"),
    field("max_runs", null, "1"),
    field("expires_hours", null, "1"),
    field("missed_run", ["skip", "coalesce"], "skip"),
    field("grace_seconds", null, "30"),
    field("max_jobs_per_run", null, "1"),
    field("max_jobs_total", null, "1"),
    field("max_amount_per_job", null, "5.00"),
    field("max_amount_total", null, "5.00"),
  );
  form.querySelector('[data-field="first_run_utc"]').type = "datetime-local";
  form.append(
    el(
      "p",
      { class: "small" },
      "First run is UTC. Scheduled mode binds the selected job; automatic mode selects owned queued jobs matching the operation and source. Budgets include held attempts. A separately started dispatch worker is needed for unattended runs.",
    ),
  );
  const review = el("pre", { class: "hidden" });
  let pending = null;
  const enable = button(
    "Enable reviewed profile",
    async () => {
      if (!pending) throw Error("Review these rules first.");
      await api("dispatch-create", pending);
      await postingSchedules();
      notice(
        "Profile enabled. Required approvals and sample limits still apply.",
      );
    },
    "primary",
  );
  enable.disabled = true;
  form.addEventListener("input", () => {
    pending = null;
    enable.disabled = true;
    review.classList.add("hidden");
  });
  form.append(
    button("Review dispatch rules", async () => {
      if (!form.reportValidity()) return;
      const first = new Date(value(form, "first_run_utc") + ":00Z");
      const number = (name) => {
        const v = Number(value(form, name));
        if (!Number.isInteger(v)) throw Error("Enter whole-number limits.");
        return v;
      };
      const mode = value(form, "mode");
      if (mode === "scheduled" && !value(form, "scheduled_job"))
        throw Error("Choose a queued job for scheduled mode.");
      pending = {
        profile_id: value(form, "profile_id"),
        specification: {
          mode,
          timezone: "UTC",
          first_run: first.toISOString(),
          interval_seconds: number("interval_minutes") * 60,
          max_runs: number("max_runs"),
          expires_at: first.getTime() / 1000 + number("expires_hours") * 3600,
          missed_run: value(form, "missed_run"),
          grace_seconds: number("grace_seconds"),
          operations: [value(form, "operation")],
          sources: [value(form, "source")],
          job_ids: mode === "scheduled" ? [value(form, "scheduled_job")] : [],
          max_jobs_per_run: number("max_jobs_per_run"),
          max_jobs_total: number("max_jobs_total"),
          max_amount_per_job: value(form, "max_amount_per_job"),
          max_amount_total: value(form, "max_amount_total"),
        },
      };
      review.textContent = JSON.stringify(pending, null, 2);
      review.classList.remove("hidden");
      enable.disabled = false;
    }),
    review,
    enable,
  );
  form.addEventListener("submit", (e) => e.preventDefault());
  body.append(
    panel(
      "New dispatch profile",
      "Review the exact rules before enabling them.",
      form,
    ),
    button("Run due sample work", async () => {
      const result = await api("dispatch-tick");
      await postingSchedules();
      notice(
        result.results.length +
          " due job(s) processed; inspect each retained result.",
      );
    }),
  );
  $("content").replaceChildren(body);
}

async function access() {
  if (!permissions("manage-users")) {
    $("content").replaceChildren(
      panel(
        "User access",
        "Your account does not have company user-management permission.",
      ),
    );
    return;
  }
  const data = await api("company_access_v1", {
    action: "inspect",
    parameters: {},
  });
  accessRevision = data.config_revision;
  const body = panel(
    "Company access",
    "Permissions apply only to the selected company. Changes are recorded in the audit.",
  );
  const users = data.users || [];
  const records = Array.isArray(users)
    ? users
    : Object.entries(users).map(([principal, permissions]) => ({
        principal,
        permissions,
      }));
  body.append(
    table(
      ["User", "Permissions"],
      records.map((u) => [
        u.principal || u.id,
        Array.isArray(u.permissions)
          ? u.permissions.map(title).join(", ")
          : "See company configuration",
      ]),
    ),
  );
  body.append(
    el(
      "p",
      { class: "small" },
      "Role editing is available after reviewing the current configuration revision. New sign-in keys are provisioned in private company setup.",
    ),
  );
  const principal = el("input", {
      id: "access-user",
      placeholder: "Existing user ID",
    }),
    roleChoices = el("div", { class: "checks" });
  for (const role of ["preparer", "approver", "administrator"])
    roleChoices.append(
      el(
        "label",
        {},
        el("input", { type: "checkbox", value: role }),
        title(role),
      ),
    );
  body.append(
    el(
      "section",
      { class: "form-section" },
      el("label", {}, "User", principal),
      roleChoices,
      button(
        "Apply selected roles",
        async () => {
          const roles = [...roleChoices.querySelectorAll("input:checked")].map(
            (n) => n.value,
          );
          if (!roles.length)
            throw Error("Choose at least one role, or use revoke access.");
          await api("company_access_v1", {
            action: "set_user",
            parameters: {
              principal: principal.value,
              expected_revision: accessRevision,
              roles,
            },
          });
          await access();
          notice("Company roles updated.");
        },
        "primary",
      ),
      button(
        "Revoke company access",
        async () => {
          await api("company_access_v1", {
            action: "set_user",
            parameters: {
              principal: principal.value,
              expected_revision: accessRevision,
              permissions: [],
            },
          });
          await access();
          notice("Company access revoked.");
        },
        "danger",
      ),
    ),
  );
  const exact = el("div", { class: "checks" });
  for (const permission of data.role_presets.administrator)
    exact.append(
      el(
        "label",
        {},
        el("input", { type: "checkbox", value: permission }),
        title(permission),
      ),
    );
  body.append(
    el(
      "section",
      { class: "form-section" },
      el("h3", {}, "Individual permissions"),
      el(
        "p",
        {},
        "Select exactly the permissions this user should have. An empty selection revokes access.",
      ),
      exact,
      button("Apply exact permissions", async () => {
        await api("company_access_v1", {
          action: "set_user",
          parameters: {
            principal: principal.value,
            expected_revision: accessRevision,
            permissions: [...exact.querySelectorAll("input:checked")].map(
              (i) => i.value,
            ),
          },
        });
        await access();
        notice("Exact permissions saved.");
      }),
    ),
  );
  body.append(
    el(
      "section",
      { class: "form-section" },
      el("h3", {}, "Approval policy"),
      el(
        "p",
        {},
        data.allow_self_approval
          ? "Users with approval permission may approve their own documents."
          : "Documents need approval from a different authorized user.",
      ),
      button(
        data.allow_self_approval
          ? "Require a separate approver"
          : "Allow users to approve their own documents",
        async () => {
          await api("company_access_v1", {
            action: "set_self_approval",
            parameters: {
              expected_revision: accessRevision,
              allow: !data.allow_self_approval,
            },
          });
          catalog = await api("catalog");
          await access();
          notice("Approval policy updated.");
        },
      ),
    ),
  );
  $("content").replaceChildren(body);
}

function imports() {
  const file = el("input", { type: "file", accept: ".csv,.xlsx" }),
    dataset = el("input", { placeholder: "For example: weekly-sales" }),
    source = el("select");
  options(
    source,
    catalog.sources,
    "Choose a source",
    catalog.sources.length === 1 ? catalog.sources[0] : "",
  );
  const delimiter = el("select");
  options(
    delimiter,
    [
      { id: ",", label: "Comma" },
      { id: ";", label: "Semicolon" },
      { id: "\t", label: "Tab" },
      { id: "|", label: "Pipe" },
    ],
    "Choose delimiter",
    ",",
  );
  const sheet = el("input", { placeholder: "Exact worksheet name for XLSX" });
  $("content").replaceChildren(
    panel(
      "Import spreadsheet",
      "One transaction per row. Stable row keys prevent duplicates across corrected files and reordered rows.",
      el(
        "div",
        { class: "grid" },
        el("label", {}, "CSV or XLSX file", file),
        el("label", {}, "Dataset name (keep the same for re-imports)", dataset),
        el("label", {}, "Source", source),
        el("label", {}, "CSV separator", delimiter),
        el("label", {}, "XLSX worksheet", sheet),
      ),
      el(
        "section",
        { class: "form-section" },
        button(
          "Read columns",
          async () => {
            const upload = file.files[0];
            if (!upload || upload.size > 4 * 1024 * 1024)
              throw Error("Choose a CSV or XLSX file up to 4 MB.");
            if (!/^[a-z][a-z0-9_-]{0,63}$/.test(dataset.value))
              throw Error(
                "Use a dataset name starting with a letter, followed by lowercase letters, numbers, hyphens or underscores.",
              );
            if (!source.value) throw Error("Choose the source.");
            const xlsx = upload.name.toLowerCase().endsWith(".xlsx"),
              format = xlsx
                ? { sheet: sheet.value }
                : { delimiter: delimiter.value };
            if (!xlsx && !upload.name.toLowerCase().endsWith(".csv"))
              throw Error("Only CSV and XLSX are supported.");
            const bytes = new Uint8Array(await upload.arrayBuffer());
            let raw = "";
            for (let i = 0; i < bytes.length; i += 16384)
              raw += String.fromCharCode(...bytes.subarray(i, i + 16384));
            const content_base64 = btoa(raw),
              media_type = xlsx
                ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                : "text/csv";
            const retained = await api("capture_document_v1", {
              namespace: source.value,
              reference: "upload-" + crypto.randomUUID().replaceAll("-", ""),
              media_type,
              content_base64,
            });
            const columns = await api("table-columns", {
              document_id: retained.document_id,
              format,
            });
            entry(null, (defaults) =>
              mapping({
                document: retained.document_id,
                format,
                dataset: dataset.value,
                columns,
                ...defaults,
              }),
            );
            notice(
              "Read " +
                columns.row_count +
                " rows. Set defaults, then choose the column mapping.",
            );
          },
          "primary",
        ),
      ),
    ),
  );
}
function leaves(object, prefix = "") {
  return Object.entries(object).flatMap(([k, v]) => {
    const path = prefix ? prefix + "." + k : k;
    return v !== null && typeof v === "object" ? leaves(v, path) : [[path, v]];
  });
}
function mapping(batch) {
  const keyColumn = el("select");
  options(keyColumn, batch.columns.headers, "Choose the stable row key");
  const fields = el("div"),
    selected = [];
  for (const [path, defaultValue] of leaves(batch.payload)) {
    const select = el("select");
    options(select, batch.columns.headers, "Keep default: " + defaultValue);
    const kind = el("select");
    const fieldName = path.split(".").at(-1);
    options(
      kind,
      ["text", "date", "money", "decimal"],
      "Choose conversion",
      fieldName.endsWith("_date")
        ? "date"
        : ["amount", "total_amount", "tax_amount"].includes(fieldName)
          ? "money"
          : ["quantity", "cost", "unit_price"].includes(fieldName)
            ? "decimal"
            : "text",
    );
    fields.append(
      el(
        "div",
        { class: "mapping-row" },
        el(
          "label",
          {},
          path
            .split(".")
            .map(
              (p) =>
                names[p] ||
                (/^\d+$/.test(p) ? "Line " + (Number(p) + 1) : title(p)),
            )
            .join(" / "),
          select,
        ),
        el("label", {}, "Value type", kind),
      ),
    );
    selected.push({ path, select, kind });
  }
  const sample = table(
    batch.columns.headers,
    batch.columns.sample_rows.map((r) =>
      batch.columns.headers.map((h) => r[h] ?? ""),
    ),
  );
  $("content").replaceChildren(
    panel(
      "Map spreadsheet columns",
      "Keep this mapping and dataset name consistent when re-importing. Original files and row previews are retained.",
      sample,
      el(
        "section",
        { class: "form-section" },
        el("label", {}, "Stable row identity", keyColumn),
      ),
      fields,
      el(
        "section",
        { class: "form-section" },
        button(
          "Preview rows",
          async () => {
            const columns = {};
            for (const f of selected)
              if (f.select.value)
                columns[f.path] = {
                  column: f.select.value,
                  type: f.kind.value,
                };
            if (!keyColumn.value || !Object.keys(columns).length)
              throw Error(
                "Choose a row identity and at least one mapped field.",
              );
            const specification = {
              dataset: batch.dataset,
              operation: batch.operation,
              key_column: keyColumn.value,
              template: batch.payload,
              columns,
              ...batch.format,
            };
            const plan = await api("table_intake_v1", {
              action: "preview",
              parameters: { document_id: batch.document, specification },
            });
            intakePreview(batch, plan);
          },
          "primary",
        ),
      ),
    ),
  );
}
function intakePreview(batch, plan) {
  const choices = [];
  const rows = plan.rows.map((r) => {
    const box = el("input", {
      type: "checkbox",
      disabled: !!r.errors.length,
      "aria-label": "Select row " + r.row,
    });
    choices.push({ box, row: r });
    return [
      box,
      r.row_key || "Missing key",
      r.payload?.ref_number || "—",
      r.errors.join("; ") || "Ready to check",
      button("Details", async () => {
        const body = el("dl", { class: "detail" });
        for (const [p, v] of leaves(r.payload || r.values))
          body.append(el("div", {}, el("dt", {}, p), el("dd", {}, String(v))));
        $("row-detail").replaceChildren(
          panel("Row " + r.row, "Review the mapped values.", body),
        );
      }),
    ];
  });
  const outcomes = el("div", { id: "intake-outcomes" });
  $("content").replaceChildren(
    panel(
      "Review import",
      plan.rows.length +
        " rows. Selected rows are checked individually before draft preparation. This never approves or posts accounting.",
      table(["Select", "Row key", "Reference", "Result", ""], rows),
      el(
        "section",
        { class: "form-section toolbar" },
        button("Select valid rows", () =>
          choices.forEach((c) => (c.box.checked = !c.box.disabled)),
        ),
        button(
          "Prepare selected drafts",
          async () => {
            const chosen = choices.filter((c) => c.box.checked);
            if (!chosen.length) throw Error("Select at least one valid row.");
            const results = [];
            for (const { row } of chosen) {
              try {
                notice("Checking row " + row.row_key + "…");
                const verified = await api("check", {
                  operation: batch.operation,
                  connector_id: batch.connector,
                  payload: row.payload,
                });
                const prepared = await api("table_intake_v1", {
                  action: "prepare_rows",
                  parameters: {
                    preview_id: plan.preview_id,
                    row_keys: [row.row_key],
                    master_evidence: { [row.row_key]: verified.evidence },
                  },
                });
                const item = prepared.rows[0];
                results.push([
                  row.row_key,
                  item.errors.length
                    ? item.errors.join("; ")
                    : "Draft prepared",
                  item.job_id
                    ? button("Review", () => openJob(item.job_id))
                    : "",
                ]);
              } catch (error) {
                results.push([row.row_key, error.message, ""]);
              }
              outcomes.replaceChildren(table(["Row", "Outcome", ""], results));
            }
            notice(
              "Selected rows processed. Review the results below; successful drafts are preserved.",
            );
          },
          "primary",
        ),
      ),
      outcomes,
    ),
    el("div", { id: "row-detail" }),
  );
}

function uploadDocument() {
  const file = el("input", { type: "file", accept: ".pdf,.png,.jpg,.jpeg" }),
    source = el("select");
  options(
    source,
    catalog.sources,
    "Choose a source",
    catalog.sources.length === 1 ? catalog.sources[0] : "",
  );
  $("content").replaceChildren(
    panel(
      "Read a source document",
      "Printed English PDF, PNG or JPEG, up to 4 MB and four pages. Extraction retains alternatives for review; it never approves or posts.",
      el(
        "div",
        { class: "grid two" },
        el("label", {}, "PDF, photo or scan", file),
        el("label", {}, "Source", source),
      ),
      el(
        "section",
        { class: "form-section" },
        button(
          "Extract for review",
          async () => {
            const upload = file.files[0];
            if (!upload || upload.size > 4 * 1024 * 1024 || !source.value)
              throw Error("Choose a source and a supported file up to 4 MB.");
            const suffix = upload.name.toLowerCase().split(".").at(-1),
              media_type = {
                pdf: "application/pdf",
                png: "image/png",
                jpg: "image/jpeg",
                jpeg: "image/jpeg",
              }[suffix];
            if (!media_type) throw Error("Choose PDF, PNG or JPEG.");
            const bytes = new Uint8Array(await upload.arrayBuffer());
            let raw = "";
            for (let i = 0; i < bytes.length; i += 16384)
              raw += String.fromCharCode(...bytes.subarray(i, i + 16384));
            const retained = await api("capture_document_v1", {
              namespace: source.value,
              reference: "scan-" + crypto.randomUUID().replaceAll("-", ""),
              media_type,
              content_base64: btoa(raw),
            });
            notice(
              "Reading the document with local OCR. No values are approved automatically...",
            );
            const result = await api("extract_document_v1", {
              document_id: retained.document_id,
            });
            entry(null, null, result);
            notice(
              "Source retained. Enter the intended transaction from these observations, then check details and review every field.",
            );
          },
          "primary",
        ),
      ),
    ),
  );
}
