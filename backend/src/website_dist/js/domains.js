/** Keep the chosen primary domain first in the existing comma-separated contract. */
export function primaryFirst(domains, primary) {
  const unique = [...new Set(domains.map((value) => String(value).trim()).filter(Boolean))];
  return unique.includes(primary) ? [primary, ...unique.filter((value) => value !== primary)] : unique;
}

/** Explicit primary selection prevents checkbox DOM order from determining task placement. */
export function mountPrimaryDomain(container, preferred = "") {
  container.parentElement.querySelector("[data-primary-domain]")?.remove();
  const label = document.createElement("label");
  label.dataset.primaryDomain = "";
  label.textContent = "Primary domain (stored first)";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Primary domain");
  label.append(select);
  container.after(label);
  const checked = () => [...container.querySelectorAll('input[name="domain"]:checked')].map((input) => input.value);
  function refresh(primary = select.value) {
    const domains = checked();
    select.replaceChildren(...domains.map((domain) => {
      const option = document.createElement("option");
      option.value = option.textContent = domain;
      return option;
    }));
    if (domains.includes(primary)) select.value = primary;
    select.disabled = !domains.length;
  }
  container.addEventListener("change", () => refresh());
  refresh(preferred);
  return {
    read: () => primaryFirst(checked(), select.value),
    setPrimary: (primary) => refresh(primary),
  };
}
