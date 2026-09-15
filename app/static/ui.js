document.addEventListener("DOMContentLoaded", () => {
  const forms = document.querySelectorAll("form[method='post'], form[method='POST']");
  forms.forEach((form) => {
    if (form.dataset.noLoading === "true") return;
    form.addEventListener("submit", () => {
      const submit = form.querySelector("button[type='submit'], input[type='submit']");
      if (submit) {
        submit.disabled = true;
        submit.dataset.originalText = submit.textContent || submit.value || "";
        if ("value" in submit) submit.value = "Выполняется…";
        else submit.textContent = "Выполняется…";
      }
      if (!form.querySelector(".loading-strip")) {
        const strip = document.createElement("div");
        strip.className = "loading-strip";
        strip.setAttribute("aria-label", "Выполняется");
        form.classList.add("is-loading");
        form.appendChild(strip);
      }
    });
  });

  const wrap = (nodes, summary) => {
    if (!nodes.length) return;
    const parent = nodes[0].parentElement;
    if (!parent || nodes.some((node) => node.parentElement !== parent)) return;
    if (parent.dataset.compacted === "true") return;
    const details = document.createElement("details");
    details.className = "compact-details";
    const title = document.createElement("summary");
    title.textContent = summary;
    const content = document.createElement("div");
    content.className = "compact-content";
    parent.insertBefore(details, nodes[0]);
    nodes.forEach((node) => content.appendChild(node));
    details.appendChild(title);
    details.appendChild(content);
    parent.dataset.compacted = "true";
  };

  document.querySelectorAll(".facts").forEach((list) => {
    const items = Array.from(list.children).filter((node) => node.matches("li"));
    if (items.length > 8) {
      const details = document.createElement("details");
      details.className = "compact-details";
      const summary = document.createElement("summary");
      summary.textContent = `Показать список (${items.length})`;
      const content = document.createElement("div");
      content.className = "compact-content";
      list.replaceWith(details);
      details.append(summary, content);
      items.forEach((item) => content.appendChild(item));
      content.appendChild(list);
      list.style.display = "none";
    }
  });

  document.querySelectorAll("section.card").forEach((section) => {
    const children = Array.from(section.children).filter((node) => node.matches("article.card, div.card"));
    if (children.length > 5) wrap(children, `Показать записи (${children.length})`);
  });

  document.querySelectorAll("table").forEach((table) => {
    const rows = table.querySelectorAll("tbody tr");
    if (rows.length > 15 && table.parentElement) {
      const parent = table.parentElement;
      const details = document.createElement("details");
      details.className = "compact-details";
      const summary = document.createElement("summary");
      summary.textContent = `Показать таблицу (${rows.length} строк)`;
      parent.insertBefore(details, table);
      details.append(summary, table);
    }
  });
});
