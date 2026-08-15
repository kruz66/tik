(() => {
  "use strict";

  const cfg = window.TIKTOK_CLIPER || {};
  if (cfg.subscription?.active) return;

  const els = {
    subscribeBtn: document.getElementById("subscribeBtn"),
    backdrop: document.getElementById("subscribeModalBackdrop"),
    modal: document.getElementById("subscribeModal"),
    closeBtn: document.getElementById("subscribeModalClose"),
    stepSelect: document.getElementById("subscribeStepSelect"),
    stepPay: document.getElementById("subscribeStepPay"),
    coinSelect: document.getElementById("subscribeCoinSelect"),
    payBtn: document.getElementById("subscribePayBtn"),
    backBtn: document.getElementById("subscribeBackBtn"),
    qrImage: document.getElementById("subscribeQrImage"),
    payAmount: document.getElementById("subscribePayAmount"),
    payAddress: document.getElementById("subscribePayAddress"),
    payMemo: document.getElementById("subscribePayMemo"),
    memoRow: document.getElementById("subscribeMemoRow"),
    status: document.getElementById("subscribePaymentStatus"),
    copyAddressBtn: document.getElementById("copyAddressBtn"),
    copyMemoBtn: document.getElementById("copyMemoBtn"),
  };

  let currencies = [];
  let activePaymentId = null;
  let pollTimer = null;

  function csrfHeaders() {
    return {
      "Content-Type": "application/json",
      "X-CSRFToken": cfg.csrfToken,
    };
  }

  function showToast(message, type = "info") {
    if (typeof window.showPanelToast === "function") {
      window.showPanelToast(message, type);
      return;
    }
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast show`;
    toast.innerHTML = `<div class="toast-body ${type === "error" ? "text-danger" : type === "success" ? "text-success" : ""}">${message}</div>`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4500);
  }

  function openModal() {
    els.backdrop.hidden = false;
    els.modal.hidden = false;
    document.body.classList.add("subscribe-modal-open");
    loadCurrencies();
  }

  function closeModal() {
    els.backdrop.hidden = true;
    els.modal.hidden = true;
    document.body.classList.remove("subscribe-modal-open");
    stopPolling();
    showSelectStep();
  }

  function showSelectStep() {
    els.stepSelect.hidden = false;
    els.stepPay.hidden = true;
    activePaymentId = null;
    if (els.qrImage) els.qrImage.removeAttribute("src");
  }

  function showPayStep() {
    els.stepSelect.hidden = true;
    els.stepPay.hidden = false;
  }

  async function loadCurrencies() {
    els.coinSelect.innerHTML = '<option value="">Loading coins...</option>';
    try {
      const res = await fetch("/api/subscription/currencies/");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load coins.");

      currencies = data.currencies || [];
      if (!currencies.length) {
        els.coinSelect.innerHTML = '<option value="">No coins available</option>';
        return;
      }

      els.coinSelect.innerHTML = currencies
        .map(
          (coin) =>
            `<option value="${coin.code}">${coin.name} (${coin.code.toUpperCase()})</option>`
        )
        .join("");

      const defaultCoin = currencies.find((coin) => coin.code === "usdttrc20");
      els.coinSelect.value = defaultCoin ? defaultCoin.code : currencies[0].code;
    } catch (err) {
      els.coinSelect.innerHTML = '<option value="">Failed to load coins</option>';
      showToast(err.message, "error");
    }
  }

  async function createPayment() {
    const payCurrency = els.coinSelect.value;
    if (!payCurrency) {
      showToast("Select a cryptocurrency.", "error");
      return;
    }

    els.payBtn.disabled = true;
    const original = els.payBtn.innerHTML;
    els.payBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Creating payment...';

    try {
      const res = await fetch("/api/subscription/create-payment/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({ pay_currency: payCurrency }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Payment creation failed.");

      const payment = data.payment;
      activePaymentId = payment.payment_id;

      els.payAmount.textContent = `${payment.pay_amount} ${payment.pay_currency.toUpperCase()} (~$${payment.price_amount})`;
      els.payAddress.textContent = payment.pay_address;

      if (payment.payin_extra_id) {
        els.memoRow.hidden = false;
        els.payMemo.textContent = payment.payin_extra_id;
      } else {
        els.memoRow.hidden = true;
      }

      els.qrImage.src = `${data.qr_url}?t=${Date.now()}`;
      setPaymentStatus("waiting", "Waiting for payment...");
      showPayStep();
      startPolling();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      els.payBtn.disabled = false;
      els.payBtn.innerHTML = original;
    }
  }

  function setPaymentStatus(status, message) {
    const iconMap = {
      waiting: "bi-hourglass-split",
      confirming: "bi-arrow-repeat",
      confirmed: "bi-check-circle-fill",
      finished: "bi-check-circle-fill",
      failed: "bi-x-circle-fill",
      expired: "bi-x-circle-fill",
    };
    const icon = iconMap[status] || "bi-hourglass-split";
    const success = status === "confirmed" || status === "finished";
    const failed = status === "failed" || status === "expired";
    els.status.className = `subscribe-status${success ? " is-success" : failed ? " is-error" : ""}`;
    els.status.innerHTML = `<i class="bi ${icon}"></i><span>${message}</span>`;
  }

  async function pollPayment() {
    if (!activePaymentId) return;

    try {
      const res = await fetch(`/api/subscription/payment/${activePaymentId}/status/`);
      const data = await res.json();
      if (!res.ok) return;

      const payment = data.payment;
      const status = payment.status;

      if (status === "waiting") {
        setPaymentStatus(status, "Waiting for payment...");
      } else if (status === "confirming") {
        setPaymentStatus(status, "Payment detected — confirming on blockchain...");
      } else if (status === "confirmed" || status === "finished") {
        setPaymentStatus(status, "Payment confirmed! Pro plan activated.");
        stopPolling();
        showToast("Subscription activated — unlimited uploads unlocked!", "success");
        setTimeout(() => window.location.reload(), 1800);
      } else if (status === "failed" || status === "expired") {
        setPaymentStatus(status, `Payment ${status}. Choose another coin to try again.`);
        stopPolling();
      } else {
        setPaymentStatus(status, `Status: ${status}`);
      }
    } catch {
      /* ignore transient poll errors */
    }
  }

  function startPolling() {
    stopPolling();
    pollPayment();
    pollTimer = setInterval(pollPayment, 5000);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function copyText(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      showToast(`${label} copied.`, "success");
    } catch {
      showToast("Copy failed.", "error");
    }
  }

  els.subscribeBtn?.addEventListener("click", openModal);
  els.closeBtn?.addEventListener("click", closeModal);
  els.backdrop?.addEventListener("click", closeModal);
  els.payBtn?.addEventListener("click", createPayment);
  els.backBtn?.addEventListener("click", () => {
    stopPolling();
    showSelectStep();
  });
  els.copyAddressBtn?.addEventListener("click", () =>
    copyText(els.payAddress.textContent, "Address")
  );
  els.copyMemoBtn?.addEventListener("click", () =>
    copyText(els.payMemo.textContent, "Memo")
  );

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.modal.hidden) closeModal();
  });
})();
