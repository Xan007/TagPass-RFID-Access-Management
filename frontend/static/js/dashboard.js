document.addEventListener("DOMContentLoaded", () => {
  const banner = document.querySelector(".banner--error");
  if (banner) {
    setTimeout(() => {
      banner.classList.add("is-hidden");
    }, 8000);
  }
});
