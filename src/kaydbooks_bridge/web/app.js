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
function field(name, choices = null, value = "", optional = false) {
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
        ].includes(name)
          ? "decimal"
          : "text",
      });
  if (choices) options(input, choices, optional ? "None" : "Choose…", value);
  else input.value = value;
  if (!optional) input.required = true;
  return el("label", {}, names[name] || title(name), input);
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
  if (view === "access") await access();
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
function entry(existing = null, onTemplate = null) {
  evidence = null;
  requestKey = keyFor();
  currentJob = existing;
  const op = el("select", { id: "operation" });
  options(
    op,
    Object.entries(catalog.operations).map(([id, label]) => ({ id, label })),
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
      const job = await api("prepare", params);
      await api("validate", { job_id: job.id });
      await openJob(job.id);
      notice(
        existing
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
        for (const input of row.querySelectorAll("[data-field]"))
          out[input.dataset.field] =
            input.dataset.field === "amount" ? money(input.value) : input.value;
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
    actions.append(button("Correct draft", () => entry(job)));
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
  if (job.approval_by)
    p.append(el("p", { class: "small" }, "Approved by " + job.approval_by));
  if (job.txn_id)
    p.append(
      el("p", { class: "small" }, "QuickBooks transaction: " + job.txn_id),
    );
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
