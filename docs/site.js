const citation = document.querySelector(".citation");
const source = document.getElementById("source-e42");
const intro = document.querySelector(".source-intro");
const closeSource = document.querySelector("[data-close-source]");

function syncSource() {
  const open = location.hash === citation.hash;
  source.hidden = !open;
  intro.hidden = open;
  citation.setAttribute("aria-expanded", String(open));
  if (open) citation.setAttribute("aria-current", "true");
  else citation.removeAttribute("aria-current");
}

citation.addEventListener("click", event => {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  history.replaceState(null, "", citation.hash);
  syncSource();
  source.focus({preventScroll: true});
  source.scrollIntoView({block: "nearest"});
});

closeSource.hidden = false;
closeSource.addEventListener("click", () => {
  history.replaceState(null, "", "#demo");
  syncSource();
  citation.focus();
});
window.addEventListener("hashchange", syncSource);
syncSource();

if ("IntersectionObserver" in window && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("entered");
      observer.unobserve(entry.target);
    }
  }, {threshold: .12});
  document.querySelectorAll("[data-reveal]").forEach(element => observer.observe(element));
}

