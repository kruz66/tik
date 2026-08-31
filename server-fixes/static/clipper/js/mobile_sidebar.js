(() => {
  "use strict";

  const MOBILE_QUERY = "(max-width: 1100px)";

  function getElements() {
    return {
      sidebar: document.getElementById("sidebar"),
      backdrop: document.getElementById("sidebarBackdrop"),
      toggle: document.getElementById("sidebarToggle"),
    };
  }

  function isMobileNav() {
    return window.matchMedia(MOBILE_QUERY).matches;
  }

  function setSidebarOpen(open) {
    const { sidebar, backdrop, toggle } = getElements();
    if (!sidebar) return;

    const shouldOpen = Boolean(open) && isMobileNav();
    sidebar.classList.toggle("is-open", shouldOpen);
    backdrop?.classList.toggle("is-visible", shouldOpen);
    if (toggle) {
      toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      toggle.setAttribute("aria-label", shouldOpen ? "Close menu" : "Open menu");
    }
    document.documentElement.classList.toggle("sidebar-open", shouldOpen);
    document.body.classList.toggle("sidebar-open", shouldOpen);

    // When opening the drawer, re-sync Link/Unlink visibility from live status.
    if (shouldOpen && typeof window.syncSidebarAccountButtons === "function") {
      try {
        window.syncSidebarAccountButtons();
      } catch (_err) {
        /* ignore */
      }
    }
  }

  function toggleSidebar() {
    const { sidebar } = getElements();
    if (!sidebar || !isMobileNav()) return;
    setSidebarOpen(!sidebar.classList.contains("is-open"));
  }

  function bindSidebarNav() {
    const { sidebar } = getElements();
    if (!sidebar || sidebar.dataset.navBound === "1") return;
    sidebar.dataset.navBound = "1";

    sidebar.querySelectorAll(".sidebar-item, .sidebar-nav a, .sidebar-logout-form button").forEach((el) => {
      el.addEventListener("click", () => {
        if (isMobileNav()) setSidebarOpen(false);
      });
    });
  }

  function initMobileSidebar() {
    const { sidebar, backdrop, toggle } = getElements();
    if (!sidebar || !toggle) return;

    bindSidebarNav();

    if (toggle.dataset.sidebarBound !== "1") {
      toggle.dataset.sidebarBound = "1";
      toggle.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggleSidebar();
      });
    }

    if (backdrop && backdrop.dataset.sidebarBound !== "1") {
      backdrop.dataset.sidebarBound = "1";
      backdrop.addEventListener("click", () => setSidebarOpen(false));
    }

    if (document.documentElement.dataset.sidebarGlobalBound !== "1") {
      document.documentElement.dataset.sidebarGlobalBound = "1";

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") setSidebarOpen(false);
      });

      window.addEventListener("resize", () => {
        if (!isMobileNav()) setSidebarOpen(false);
      });
    }
  }

  window.AppSidebar = {
    open: () => setSidebarOpen(true),
    close: () => setSidebarOpen(false),
    toggle: toggleSidebar,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initMobileSidebar);
  } else {
    initMobileSidebar();
  }
  window.addEventListener("load", initMobileSidebar);
})();
