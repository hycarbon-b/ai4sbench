const api = async (path, options = {}) => {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
};

const authStatus = document.querySelector("#auth-status");
const loginButtons = [document.querySelector("#login-button"), document.querySelector("#hero-login")];
const proposalForm = document.querySelector("#proposal-form");
const proposalResult = document.querySelector("#proposal-result");
let currentUser = null;

const setUser = (user) => {
  currentUser = user;
  if (!user) return;
  const identity = user.github_login || user.email;
  authStatus.textContent = `Signed in as @${identity} · ${user.role}`;
  loginButtons.forEach((button) => {
    button.textContent = `@${identity}`;
    button.classList.add("hidden");
  });
  if (user.role === "admin") document.querySelector("#admin-panel").classList.remove("hidden");
};

const refreshUser = async () => {
  try {
    setUser(await api("/api/v1/auth/me"));
  } catch {
    authStatus.textContent = "GitHub sign-in is required before submitting a proposal.";
  }
};

const login = async () => {
  try {
    const config = await api("/api/v1/auth/config");
    if (!config.github_login_enabled) {
      throw new Error("GitHub sign-in has not been configured for this control panel yet.");
    }
    const { authorization_url: authorizationUrl } = await api("/auth/github/authorize");
    const popup = window.open(authorizationUrl, "ai4sbench-github", "popup,width=700,height=780");
    if (!popup) throw new Error("Your browser blocked the GitHub sign-in window.");
    authStatus.textContent = "Finish GitHub sign-in in the opened window…";
    const timer = window.setInterval(async () => {
      await refreshUser();
      if (currentUser) {
        window.clearInterval(timer);
        popup.close();
      }
    }, 1000);
  } catch (error) {
    authStatus.textContent = error.message;
  }
};

loginButtons.forEach((button) => button.addEventListener("click", login));

proposalForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentUser) return login();
  const data = Object.fromEntries(new FormData(proposalForm));
  proposalResult.textContent = "Opening your GitHub Discussion…";
  try {
    const proposal = await api("/api/v1/proposals", { method: "POST", body: JSON.stringify(data) });
    const link = document.createElement("a");
    link.href = proposal.discussion_url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "view proposal →";
    proposalResult.replaceChildren("Discussion opened: ", link);
  } catch (error) {
    proposalResult.textContent = error.message;
  }
});

document.querySelector("#cloud-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const output = document.querySelector("#cloud-result");
  try {
    const allocation = JSON.parse(form.get("allocation"));
    const profile = await api("/api/v1/cloud-profiles", {
      method: "POST",
      body: JSON.stringify({ name: form.get("name"), provider: "aws", allocation, enabled: true }),
    });
    output.textContent = `Saved ${profile.name}.`;
  } catch (error) {
    output.textContent = error.message;
  }
});

refreshUser();
