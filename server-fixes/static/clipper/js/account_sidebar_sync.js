(() => {
  "use strict";

  function setHidden(el, hide) {
    if (!el) return;
    const shouldHide = Boolean(hide);
    el.hidden = shouldHide;
    if (shouldHide) {
      el.setAttribute("hidden", "");
      el.setAttribute("aria-hidden", "true");
    } else {
      el.removeAttribute("hidden");
      el.removeAttribute("aria-hidden");
    }
  }

  async function syncSidebarAccountButtons() {
    const linkYt = document.getElementById("linkYoutubeBtn");
    const unlinkYt = document.getElementById("unlinkYoutubeBtn");
    const linkTt = document.getElementById("linkTiktokBtn");
    const unlinkTt = document.getElementById("unlinkTiktokBtn");
    if (!linkYt && !unlinkYt && !linkTt && !unlinkTt) return;

    let youtubeOn = unlinkYt ? !unlinkYt.hidden : false;
    let tiktokOn = unlinkTt ? !unlinkTt.hidden : false;

    try {
      const [ytRes, ttRes] = await Promise.all([
        fetch("/api/youtube/status/"),
        fetch("/api/tiktok/status/"),
      ]);
      if (ytRes.ok) {
        const yt = await ytRes.json();
        youtubeOn = Boolean(yt.connected);
      }
      if (ttRes.ok) {
        const tt = await ttRes.json();
        tiktokOn = Boolean(tt.connected);
      }
    } catch (_err) {
      /* keep SSR defaults */
    }

    setHidden(linkYt, youtubeOn);
    setHidden(unlinkYt, !youtubeOn);
    setHidden(linkTt, tiktokOn);
    setHidden(unlinkTt, !tiktokOn);
  }

  window.syncSidebarAccountButtons = syncSidebarAccountButtons;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncSidebarAccountButtons);
  } else {
    syncSidebarAccountButtons();
  }
})();
