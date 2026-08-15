(() => {
  "use strict";

  const cfg = window.TIKTOK_CLIPER || {};
  let videos = [];
  let selectedIds = new Set();
  let currentUsername = "";
  let currentSource = "tiktok";
  let activeJobId = null;
  let previewJobId = null;
  let previewClips = [];
  let selectedPreviewIds = new Set();
  let pollTimer = null;
  let pendingFetchSource = "auto";
  const WORKSPACE_STATE_KEY = "tiktok_cliper_workspace_v1";
  const ACTIVE_JOB_KEY = "tiktok_cliper_active_job";
  let lastFetchSummary = "";
  let workspaceSaveTimer = null;
  let workspaceHydrated = false;

  const ACTIVITY_STAGES = [
    { id: "idle", emoji: "✨", label: "Ready to clip" },
    { id: "fetching", emoji: "🔍", label: "Fetching videos" },
    { id: "downloading", emoji: "⬇️", label: "Downloading clip" },
    { id: "analyzing", emoji: "🤖", label: "AI analyzing" },
    { id: "retrying", emoji: "🔄", label: "AI retrying fix" },
    { id: "trimming", emoji: "✂️", label: "Cutting segments" },
    { id: "preview_ready", emoji: "🎬", label: "Clips ready" },
    { id: "audio_fix", emoji: "🎵", label: "Fixing audio" },
    { id: "enhancing", emoji: "✨", label: "Enhancing video" },
    { id: "uploading", emoji: "🚀", label: "Publishing clip" },
    { id: "tiktok_extension", emoji: "📱", label: "Posting via TikTok extension" },
    { id: "complete", emoji: "✅", label: "Upload complete" },
    { id: "cancelled", emoji: "🛑", label: "Cancelled" },
    { id: "failed", emoji: "⚠️", label: "Something went wrong" },
    { id: "connected", emoji: "🔗", label: "Account linked" },
  ];

  const STAGE_SLIDE_HEIGHT = 64;

  const $ = (sel) => document.querySelector(sel);

  const els = {
    tiktokInput: $("#tiktokInput"),
    fetchSourceSelect: $("#fetchSourceSelect"),
    fetchBtn: $("#fetchBtn"),
    videoGrid: $("#videoGrid"),
    emptyState: $("#emptyState"),
    loadingState: $("#loadingState"),
    selectedCount: $("#selectedCount"),
    totalCount: $("#totalCount"),
    videoCountBadge: $("#videoCountBadge"),
    clipUploadBtn: $("#clipUploadBtn"),
    selectAllBtn: $("#selectAllBtn"),
    clearSelectionBtn: $("#clearSelectionBtn"),
    profileChip: null,
    videoSourceGroup: null,
    noProfileHint: null,
    jobStatusText: $("#jobStatusText"),
    progressCard: $("#progressCard"),
    progressFill: $("#progressFill"),
    progressPercent: $("#progressPercent"),
    progressDetail: $("#progressDetail"),
    cancelJobBtn: $("#cancelJobBtn"),
    fixProgressActions: $("#aiFixProgressActions"),
    activityStagePanel: $("#activityStagePanel"),
    activityStageTrack: $("#activityStageTrack"),
    activityStageDetail: $("#activityStageDetail"),
    aiFeaturesEnabled: $("#aiFeaturesEnabled"),
    uploadResults: $("#uploadResults"),
    resultsList: $("#resultsList"),
    connectionBadge: $("#connectionBadge"),
    refreshStatusBtn: $("#refreshStatusBtn"),
    unlinkYoutubeBtn: $("#unlinkYoutubeBtn"),
    unlinkTiktokBtn: $("#unlinkTiktokBtn"),
    linkedAccountsGroup: $("#linkedAccountsGroup"),
    linkedYoutubeCard: $("#linkedYoutubeCard"),
    linkedTiktokCard: $("#linkedTiktokCard"),
    linkedYoutubeName: $("#linkedYoutubeName"),
    linkedTiktokName: $("#linkedTiktokName"),
    linkedTiktokHandle: $("#linkedTiktokHandle"),
    linkedYoutubeAvatar: $("#linkedYoutubeAvatar"),
    linkedTiktokAvatar: $("#linkedTiktokAvatar"),
    linkYoutubeBtn: $("#linkYoutubeBtn"),
    linkTiktokBtn: $("#linkTiktokBtn"),
    postToYoutube: $("#postToYoutube"),
    postToTiktok: $("#postToTiktok"),
    sidebar: $("#sidebar"),
    sidebarBackdrop: $("#sidebarBackdrop"),
    sidebarToggle: $("#sidebarToggle"),
    navClipPanel: $("#navClipPanel"),
    navPinnedComment: $("#navPinnedComment"),
    navVideoFx: $("#navVideoFx"),
    navAiCheck: $("#navAiCheck"),
    navChannelHelp: $("#navChannelHelp"),
    studioWorkspace: $("#studioWorkspace"),
    commentSettingsWorkspace: $("#commentSettingsWorkspace"),
    videoFxWorkspace: $("#videoFxWorkspace"),
    aiCheckWorkspace: $("#aiCheckWorkspace"),
    channelHelpWorkspace: $("#channelHelpWorkspace"),
    topBarTitle: $("#topBarTitle"),
    pinnedCommentEnabled: $("#pinnedCommentEnabled"),
    pinnedCommentType: $("#pinnedCommentType"),
    pinnedCommentCustom: $("#pinnedCommentCustom"),
    pinnedCommentPreview: $("#pinnedCommentPreview"),
    savePinnedCommentBtn: $("#savePinnedCommentBtn"),
    videoProcessingEnabled: $("#videoProcessingEnabled"),
    subscribeOverlayEnabled: $("#subscribeOverlayEnabled"),
    audioReplaceEnabled: $("#audioReplaceEnabled"),
    contentSafetyEnabled: $("#contentSafetyEnabled"),
    audioMoodSelect: $("#audioMoodSelect"),
    saveVideoFxBtn: $("#saveVideoFxBtn"),
    fetchSourceModal: $("#fetchSourceModal"),
    fetchSourceModalBackdrop: $("#fetchSourceModalBackdrop"),
    fetchSourceModalClose: $("#fetchSourceModalClose"),
    fetchSourceModalCancel: $("#fetchSourceModalCancel"),
    fetchSourceModalConfirm: $("#fetchSourceModalConfirm"),
    fetchSourceModalQuery: $("#fetchSourceModalQuery"),
    fetchSourceOptions: $("#fetchSourceOptions"),
  };

  function getSelectedDestinations() {
    const destinations = [];
    if (els.postToYoutube?.checked && cfg.youtubeConnected) destinations.push("youtube");
    if (els.postToTiktok?.checked && cfg.tiktokConnected) destinations.push("tiktok");
    return destinations;
  }

  function canPostToAnyDestination() {
    return getSelectedDestinations().length > 0;
  }

  function updateConnectionBadge() {
    if (!els.connectionBadge) return;
    if (cfg.youtubeConnected && cfg.tiktokConnected) {
      els.connectionBadge.textContent = "YouTube + TikTok Ready";
    } else if (cfg.youtubeConnected) {
      els.connectionBadge.textContent = "YouTube Ready";
    } else if (cfg.tiktokConnected) {
      els.connectionBadge.textContent = "TikTok Ready";
    } else {
      els.connectionBadge.textContent = "No Accounts Linked";
    }
  }

  function setLinkedAccountAvatar(container, imageUrl, fallbackIconClass) {
    if (!container) return;
    container.innerHTML = "";
    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = "";
      container.appendChild(img);
      return;
    }
    const icon = document.createElement("i");
    icon.className = fallbackIconClass;
    icon.setAttribute("aria-hidden", "true");
    container.appendChild(icon);
  }

  function updateLinkedAccountsUI() {
    const youtubeOn = Boolean(cfg.youtubeConnected);
    const tiktokOn = Boolean(cfg.tiktokConnected);

    if (els.linkedAccountsGroup) {
      els.linkedAccountsGroup.hidden = !(youtubeOn || tiktokOn);
    }
    if (els.linkedYoutubeCard) {
      els.linkedYoutubeCard.hidden = !youtubeOn;
    }
    if (els.linkedTiktokCard) {
      els.linkedTiktokCard.hidden = !tiktokOn;
    }

    const youtube = cfg.linkedAccounts?.youtube;
    if (youtubeOn && youtube && els.linkedYoutubeName) {
      els.linkedYoutubeName.textContent = youtube.name || "YouTube Channel";
      setLinkedAccountAvatar(els.linkedYoutubeAvatar, youtube.thumbnail, "bi bi-youtube");
    }

    const tiktok = cfg.linkedAccounts?.tiktok;
    if (tiktokOn && tiktok) {
      if (els.linkedTiktokName) {
        els.linkedTiktokName.textContent = tiktok.displayName || "TikTok Account";
      }
      if (els.linkedTiktokHandle) {
        els.linkedTiktokHandle.textContent = tiktok.username ? `@${tiktok.username}` : "TikTok";
      }
      setLinkedAccountAvatar(els.linkedTiktokAvatar, tiktok.avatarUrl, "bi bi-tiktok");
    }
  }

  let postingPrefsTimer = null;
  function schedulePostingPreferencesSave() {
    clearTimeout(postingPrefsTimer);
    postingPrefsTimer = setTimeout(savePostingPreferences, 400);
  }

  async function savePostingPreferences() {
    const postToYoutube = Boolean(els.postToYoutube?.checked && cfg.youtubeConnected);
    const postToTiktok = Boolean(els.postToTiktok?.checked && cfg.tiktokConnected);
    const aiOn = Boolean(els.aiFeaturesEnabled?.checked);
    cfg.postingPreferences = { postToYoutube, postToTiktok, aiFeaturesEnabled: aiOn };
    cfg.aiFeaturesEnabled = aiOn;
    applyAiFeaturesUI();
    try {
      await fetch("/api/posting-preferences/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          post_to_youtube: postToYoutube,
          post_to_tiktok: postToTiktok,
          ai_features_enabled: aiOn,
        }),
      });
    } catch (_err) {
      // Non-blocking — defaults still work for this session.
    }
  }

  function applyAiFeaturesUI() {
    const aiOn = cfg.aiFeaturesEnabled !== false;
    if (els.aiFeaturesEnabled) els.aiFeaturesEnabled.checked = aiOn;
    if (els.navAiCheck) {
      els.navAiCheck.hidden = !aiOn;
    }
    if (els.clipUploadBtn) {
      els.clipUploadBtn.innerHTML = aiOn
        ? '<i class="bi bi-stars"></i> Clip with AI'
        : '<i class="bi bi-cloud-upload"></i> Clip &amp; Upload';
      els.clipUploadBtn.title = aiOn
        ? "Send selected videos to remote AI for clipping and upload"
        : "Clip and upload without AI";
    }
    updateSelectionUI();
  }

  window.TIKTOK_CLIPER = window.TIKTOK_CLIPER || {};
  window.TIKTOK_CLIPER.getSelectedDestinations = getSelectedDestinations;
  window.TIKTOK_CLIPER.canPostToAnyDestination = canPostToAnyDestination;

  function csrfHeaders() {
    return {
      "Content-Type": "application/json",
      "X-CSRFToken": cfg.csrfToken,
    };
  }

  function showToast(message, type = "info") {
    const container = $("#toastContainer");
    const id = "toast-" + Date.now();
    const bgClass = type === "error" ? "text-danger" : type === "success" ? "text-success" : "";
    container.insertAdjacentHTML(
      "beforeend",
      `<div id="${id}" class="toast show" role="alert">
        <div class="toast-body ${bgClass}">${escapeHtml(message)}</div>
      </div>`
    );
    setTimeout(() => document.getElementById(id)?.remove(), 4000);
  }
  window.showPanelToast = showToast;

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function getRemainingQuota() {
    if (!cfg.uploadQuota?.limited) return null;
    return Math.max(0, cfg.uploadQuota.remaining ?? 0);
  }

  function updateQuotaUI() {
    if (!cfg.uploadQuota?.limited) return;

    const remaining = getRemainingQuota();
    const used = cfg.uploadQuota.used ?? 0;
    const limit = cfg.uploadQuota.limit ?? 0;

    const quotaUsed = document.getElementById("quotaUsed");
    const quotaRemaining = document.getElementById("quotaRemaining");
    if (quotaUsed) quotaUsed.textContent = used;
    if (quotaRemaining) quotaRemaining.textContent = remaining;

    const quotaBadge = document.getElementById("quotaBadge");
    if (quotaBadge) {
      quotaBadge.classList.toggle("quota-exhausted", remaining === 0);
    }
  }

  function applyQuotaToSelection() {
    const remaining = getRemainingQuota();
    if (remaining === null) return;

    if (remaining === 0) {
      selectedIds.clear();
      return;
    }

    if (selectedIds.size > remaining) {
      const keep = [...selectedIds].slice(0, remaining);
      selectedIds.clear();
      keep.forEach((id) => selectedIds.add(id));
      showToast(`Daily limit: only ${remaining} upload(s) left today.`, "error");
    }
  }

  function consumeQuota(count) {
    if (!cfg.uploadQuota?.limited || count <= 0) return;
    cfg.uploadQuota.used += count;
    cfg.uploadQuota.remaining = Math.max(0, (cfg.uploadQuota.limit ?? 0) - cfg.uploadQuota.used);
    updateQuotaUI();
  }

  function buildActivityStages() {
    if (!els.activityStageTrack) return;
    els.activityStageTrack.innerHTML = ACTIVITY_STAGES.map(
      (stage) => `
        <div class="activity-stage-slide" data-stage="${stage.id}">
          <div class="activity-stage-emoji" aria-hidden="true">${stage.emoji}</div>
          <span>${escapeHtml(stage.label)}</span>
        </div>
      `
    ).join("");
  }

  function updateActivityStage(stageId, detail = "") {
    const stageIndex = Math.max(
      0,
      ACTIVITY_STAGES.findIndex((stage) => stage.id === stageId)
    );
    const resolvedId = ACTIVITY_STAGES[stageIndex]?.id || "idle";

    if (els.activityStageTrack) {
      els.activityStageTrack.style.transform = `translateY(-${stageIndex * STAGE_SLIDE_HEIGHT}px)`;
      els.activityStageTrack.querySelectorAll(".activity-stage-slide").forEach((slide, index) => {
        slide.classList.toggle("is-active", index === stageIndex);
        slide.classList.toggle("is-past", index < stageIndex);
      });
    }
    if (els.activityStageDetail) {
      els.activityStageDetail.textContent =
        detail || ACTIVITY_STAGES[stageIndex]?.label || "Working...";
    }
    return resolvedId;
  }

  function statusLabelForStage(stageId) {
    if (stageId === "failed") return "Failed";
    if (stageId === "cancelled") return "Cancelled";
    if (stageId === "complete") return "Complete";
    if (stageId === "idle") return "Idle";
    return "Running";
  }

  function updateFixProgressActions(stageId, detail) {
    if (!els.fixProgressActions) return;
    const showExtract =
      stageId === "failed" &&
      !window.youtubeCookies?.areCookiesConfigured?.() &&
      window.youtubeCookies?.isAgeRestrictedMessage?.(detail);
    els.fixProgressActions.hidden = !showExtract;
    els.fixProgressActions.innerHTML = "";
    if (showExtract) {
      window.youtubeCookies?.ensureExtractButton?.(els.fixProgressActions);
    }
  }

  function setProgressCancelVisible(show) {
    if (!els.cancelJobBtn) return;
    els.cancelJobBtn.hidden = !show;
    if (!show) {
      els.cancelJobBtn.disabled = false;
    }
  }

  async function cancelActiveJob() {
    if (!activeJobId) return;

    const jobId = activeJobId;
    if (els.cancelJobBtn) els.cancelJobBtn.disabled = true;

    try {
      const res = await fetch(`/api/job/${jobId}/cancel/`, {
        method: "POST",
        headers: csrfHeaders(),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not cancel job.");

      stopPolling();
      window.AiFixUpload?.stopFixPolling?.();
      clearActiveJob();
      updateFixProgressPanel("cancelled", "Cancelled by you.", 0);
      if (els.jobStatusText) els.jobStatusText.textContent = "Cancelled";
      showToast(data.message || "Job cancelled.", "info");
      addLog("Job cancelled.", "info");
      if (els.clipUploadBtn) els.clipUploadBtn.disabled = false;
      updateSelectionUI();
      setTimeout(hideFixProgressPanel, 1200);
    } catch (err) {
      showToast(err.message, "error");
      if (els.cancelJobBtn) els.cancelJobBtn.disabled = false;
    }
  }

  function updateFixProgressPanel(stageId, detail = "", percent = 0) {
    const pct = Math.max(0, Math.min(100, Number(percent) || 0));
    if (els.jobStatusText) {
      els.jobStatusText.textContent = statusLabelForStage(stageId);
    }
    if (els.progressFill) els.progressFill.style.width = `${pct}%`;
    if (els.progressPercent) els.progressPercent.textContent = `${pct}%`;
    if (els.progressDetail) els.progressDetail.textContent = detail || "";
    updateActivityStage(stageId, detail);
    updateFixProgressActions(stageId, detail);
    setProgressCancelVisible(Boolean(activeJobId) && !["complete", "cancelled", "idle", "failed"].includes(stageId));
  }

  function showFixProgressPanel() {
    els.progressCard?.classList.add("is-active");
    els.activityStagePanel?.classList.add("is-running");
    setProgressCancelVisible(Boolean(activeJobId));
    updateFixProgressPanel("downloading", "Starting AI fix pipeline...", 5);
  }

  function hideFixProgressPanel() {
    els.progressCard?.classList.remove("is-active");
    els.activityStagePanel?.classList.remove("is-running");
    setProgressCancelVisible(false);
  }

  function addLog(message, type = "info") {
    const stageByType = {
      success: "complete",
      error: "failed",
      info: "idle",
    };
    const stage =
      type === "success" && /connect|linked/i.test(message)
        ? "connected"
        : stageByType[type] || "idle";
    updateActivityStage(stage, message);
  }

  function formatDuration(seconds) {
    if (!seconds) return "";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  function getSelectedVideos() {
    return videos.filter((v) => selectedIds.has(videoKey(v)));
  }

  function isTwitchAiClipEligible(item) {
    const kind = item.twitch_kind || "vod";
    if (kind === "clip" || kind === "live") return false;
    const duration = Number(item.duration || 0);
    return duration >= 120;
  }

  function isSingleTwitchVodSelected() {
    const selected = getSelectedVideos();
    return (
      selected.length === 1 &&
      (selected[0].source || currentSource) === "twitch" &&
      isTwitchAiClipEligible(selected[0])
    );
  }

  function twitchBadgeMeta(video) {
    const kind = video.twitch_kind || "vod";
    if (kind === "clip") return { label: "Clip", className: "source-twitch-clip" };
    if (kind === "live") return { label: "Live", className: "source-twitch-live" };
    if (kind === "highlight") return { label: "Highlight", className: "source-twitch" };
    return { label: "VOD", className: "source-twitch" };
  }

  function sourceBadgeMeta(source) {
    if (source === "youtube") return { label: "YouTube", className: "source-youtube" };
    if (source === "twitch") return { label: "Twitch", className: "source-twitch" };
    return { label: "TikTok", className: "source-tiktok" };
  }

  function twitchKindOrder(kind) {
    const order = { live: 0, vod: 1, upload: 2, highlight: 3, clip: 4 };
    return order[kind] ?? 9;
  }

  function sortTwitchVideos(list) {
    return [...list].sort(
      (a, b) =>
        twitchKindOrder(a.twitch_kind || "vod") - twitchKindOrder(b.twitch_kind || "vod")
    );
  }

  function updateSelectionUI() {
    if (isPreviewMode()) {
      updatePreviewSelectionUI();
      return;
    }
    applyQuotaToSelection();
    els.selectedCount.textContent = selectedIds.size;
    els.totalCount.textContent = videos.length;
    els.videoCountBadge.textContent = `${videos.length} videos`;

    const remaining = getRemainingQuota();
    const quotaBlocked = remaining !== null && remaining === 0;
    const aiOn = cfg.aiFeaturesEnabled !== false;
    const canPost = canPostToAnyDestination();
    const selected = getSelectedVideos();
    const singleTwitchVod =
      selected.length === 1 &&
      (selected[0].source || currentSource) === "twitch" &&
      isTwitchAiClipEligible(selected[0]);

    if (els.clipUploadBtn) {
      els.clipUploadBtn.disabled = selectedIds.size === 0 || !canPost || quotaBlocked;
      if (aiOn) {
        els.clipUploadBtn.innerHTML = singleTwitchVod
          ? '<i class="bi bi-stars"></i> Clip with AI'
          : `<i class="bi bi-stars"></i> Clip with AI${selectedIds.size ? ` (${selectedIds.size})` : ""}`;
        els.clipUploadBtn.title = singleTwitchVod
          ? "AI will find clip moments in this VOD and prepare uploads"
          : "Send selected videos to remote AI for clipping and upload";
      }
    }

    document.querySelectorAll(".video-card").forEach((card) => {
      const id = card.dataset.id;
      card.classList.toggle("selected", selectedIds.has(id));
    });
    saveWorkspaceState();
  }

  function videoKey(video) {
    const source = video.source || currentSource || "tiktok";
    return `${source}:${video.id}`;
  }

  function compactVideosForStorage(videoList) {
    return (videoList || []).map((video) => ({
      id: video.id,
      title: video.title,
      duration: video.duration,
      source: video.source,
      url: video.url,
      thumbnail: video.thumbnail,
      twitch_kind: video.twitch_kind,
      user_login: video.user_login,
      user_name: video.user_name,
    }));
  }

  function getWorkspaceSnapshot() {
    const selected = isPreviewMode() ? [] : getSelectedVideos();
    return {
      mode: isPreviewMode() ? "preview" : "grid",
      videos: isPreviewMode() ? [] : compactVideosForStorage(videos),
      selectedIds: [...selectedIds],
      activeVideos: compactVideosForStorage(selected),
      activeVodId: selected[0]?.id ? String(selected[0].id).replace(/^v/i, "") : "",
      jobVideoTitle: selected[0]?.title || "",
      activeVodUrl: selected[0]?.url || "",
      previewClips: isPreviewMode() ? previewClips : [],
      selectedPreviewIds: [...selectedPreviewIds],
      previewJobId: previewJobId || "",
      currentUsername,
      currentSource,
      fetchInput: els.tiktokInput?.value || "",
      lastFetchSummary,
    };
  }

  function normalizeVideoId(value) {
    return String(value || "")
      .replace(/^v/i, "")
      .trim();
  }

  function reconcileSelectionFromSnapshot(snapshot, jobMeta = {}) {
    if (!videos.length || !snapshot) return false;

    const matched = new Set();
    const tryAddKey = (key) => {
      if (videos.some((video) => videoKey(video) === key)) {
        matched.add(key);
      }
    };

    const savedIds = [
      ...(snapshot.selectedIds || snapshot.selected_ids || []),
      ...(snapshot.activeVideoIds || snapshot.active_video_ids || []),
    ].map(String);

    savedIds.forEach(tryAddKey);
    savedIds.forEach((id) => {
      const bare = normalizeVideoId(id.includes(":") ? id.split(":").pop() : id);
      videos.forEach((video) => {
        if (normalizeVideoId(video.id) === bare) {
          matched.add(videoKey(video));
        }
      });
    });

    const activeVodId = snapshot.activeVodId || snapshot.active_vod_id;
    if (activeVodId) {
      const bare = normalizeVideoId(activeVodId);
      videos.forEach((video) => {
        if (normalizeVideoId(video.id) === bare) {
          matched.add(videoKey(video));
        }
      });
    }

    const activeUrl = snapshot.activeVodUrl || snapshot.active_vod_url;
    if (activeUrl) {
      videos.forEach((video) => {
        if (video.url && video.url === activeUrl) {
          matched.add(videoKey(video));
        }
      });
    }

    const storedVideos = snapshot.activeVideos || snapshot.active_videos || [];
    storedVideos.forEach((stored) => {
      const bare = normalizeVideoId(stored.id);
      videos.forEach((video) => {
        if (normalizeVideoId(video.id) === bare) {
          matched.add(videoKey(video));
        }
        if (stored.url && video.url === stored.url) {
          matched.add(videoKey(video));
        }
        if (stored.title && video.title === stored.title) {
          matched.add(videoKey(video));
        }
      });
    });

    const title =
      jobMeta.current_video_title ||
      snapshot.jobVideoTitle ||
      snapshot.job_video_title ||
      "";
    if (title) {
      const normalizedTitle = title.trim().toLowerCase();
      let best = videos.find(
        (video) => (video.title || "").trim().toLowerCase() === normalizedTitle
      );
      if (!best) {
        best = videos.find((video) => {
          const videoTitle = (video.title || "").trim().toLowerCase();
          return (
            videoTitle &&
            (normalizedTitle.includes(videoTitle) || videoTitle.includes(normalizedTitle))
          );
        });
      }
      if (best) {
        matched.add(videoKey(best));
      }
    }

    if (!matched.size) return false;

    selectedIds.clear();
    matched.forEach((key) => selectedIds.add(key));
    return true;
  }

  async function restoreWorkspaceFromJob(snapshot, jobMeta = {}) {
    if (!snapshot || typeof snapshot !== "object") return false;

    if (applyWorkspaceSnapshot(snapshot, { jobMeta })) {
      return true;
    }

    const fetchInput =
      snapshot.fetchInput ||
      snapshot.fetch_input ||
      snapshot.currentUsername ||
      snapshot.current_username ||
      "";
    if (!fetchInput) return false;

    const source = snapshot.currentSource || snapshot.current_source || "auto";

    if (els.tiktokInput) {
      els.tiktokInput.value = fetchInput;
    }
    currentUsername = snapshot.currentUsername || snapshot.current_username || "";
    currentSource = source === "auto" ? "twitch" : source;

    try {
      const res = await fetch("/api/fetch-videos/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          input: fetchInput,
          source: source === "twitch" ? "twitch" : source === "youtube" ? "youtube" : "auto",
        }),
      });
      const data = await res.json();
      if (!res.ok) return false;

      renderVideos(data, { silent: true });
      reconcileSelectionFromSnapshot(snapshot, jobMeta);
      rebuildVideoGrid();
      updateSelectionUI();
      saveWorkspaceState();
      if (lastFetchSummary) {
        updateActivityStage("idle", lastFetchSummary);
      }
      return true;
    } catch (_err) {
      return false;
    }
  }

  function applyWorkspaceSnapshot(snapshot, { onlyIfEmpty = false, jobMeta = {} } = {}) {
    if (!snapshot || typeof snapshot !== "object") return false;
    if (onlyIfEmpty && (videos.length > 0 || previewClips.length > 0)) return false;

    const fetchInput = snapshot.fetchInput || snapshot.fetch_input || "";
    if (els.tiktokInput && fetchInput) {
      els.tiktokInput.value = fetchInput;
    }
    currentUsername = snapshot.currentUsername || snapshot.current_username || "";
    currentSource = snapshot.currentSource || snapshot.current_source || "tiktok";
    lastFetchSummary = snapshot.lastFetchSummary || snapshot.last_fetch_summary || "";

    if (snapshot.mode === "preview") {
      const clips = snapshot.previewClips || snapshot.preview_clips || [];
      if (!Array.isArray(clips) || !clips.length) {
        return Boolean(fetchInput);
      }
      renderPreviewClips(clips, snapshot.previewJobId || snapshot.preview_job_id || null, {
        selectAll: false,
      });
      selectedPreviewIds.clear();
      (snapshot.selectedPreviewIds || snapshot.selected_preview_ids || []).forEach((id) =>
        selectedPreviewIds.add(String(id))
      );
      updatePreviewSelectionUI();
      if (lastFetchSummary) {
        updateActivityStage("preview_ready", lastFetchSummary);
      }
      saveWorkspaceState();
      return true;
    }

    const storedVideos = snapshot.videos || [];
    if (!Array.isArray(storedVideos) || !storedVideos.length) {
      // Still restore the last searched channel/link even with no cached cards.
      if (fetchInput) {
        if (lastFetchSummary) {
          updateActivityStage("idle", lastFetchSummary);
        }
        saveWorkspaceState({ syncServer: false });
        return true;
      }
      return false;
    }
    videos = storedVideos;
    selectedIds.clear();
    reconcileSelectionFromSnapshot(snapshot, jobMeta);
    rebuildVideoGrid();
    updateSelectionUI();
    if (lastFetchSummary) {
      updateActivityStage("idle", lastFetchSummary);
    }
    saveWorkspaceState({ syncServer: false });
    return true;
  }

  function buildPersistedWorkspacePayload() {
    if (isPreviewMode()) {
      return {
        mode: "preview",
        previewClips,
        previewJobId,
        selectedPreviewIds: [...selectedPreviewIds],
        currentUsername,
        currentSource,
        fetchInput: els.tiktokInput?.value || "",
        lastFetchSummary,
        savedAt: Date.now(),
      };
    }
    return {
      mode: "grid",
      videos: compactVideosForStorage(videos),
      selectedIds: [...selectedIds],
      currentUsername,
      currentSource,
      fetchInput: els.tiktokInput?.value || "",
      lastFetchSummary,
      savedAt: Date.now(),
    };
  }

  function writeWorkspaceToBrowser(payload) {
    const raw = JSON.stringify(payload);
    try {
      localStorage.setItem(WORKSPACE_STATE_KEY, raw);
    } catch (_err) {
      // Quota exceeded — try without thumbnails.
      try {
        const slim = {
          ...payload,
          videos: (payload.videos || []).map((video) => ({ ...video, thumbnail: "" })),
        };
        localStorage.setItem(WORKSPACE_STATE_KEY, JSON.stringify(slim));
      } catch (_err2) {
        // ignore
      }
    }
    try {
      sessionStorage.setItem(WORKSPACE_STATE_KEY, raw);
    } catch (_err) {
      // ignore
    }
  }

  function readWorkspaceFromBrowser() {
    for (const store of [localStorage, sessionStorage]) {
      try {
        const raw = store.getItem(WORKSPACE_STATE_KEY);
        if (!raw) continue;
        const state = JSON.parse(raw);
        if (state && typeof state === "object") return state;
      } catch (_err) {
        // try next store
      }
    }
    return null;
  }

  function syncWorkspaceToServer(payload) {
    // Debounce account-level persistence so selection clicks stay snappy.
    if (workspaceSaveTimer) clearTimeout(workspaceSaveTimer);
    workspaceSaveTimer = setTimeout(async () => {
      try {
        await fetch("/api/panel-workspace/", {
          method: "POST",
          headers: csrfHeaders(),
          body: JSON.stringify({ workspace: payload }),
        });
      } catch (_err) {
        // Browser storage is already the primary restore path.
      }
    }, 400);
  }

  function saveWorkspaceState({ syncServer = true } = {}) {
    try {
      const payload = buildPersistedWorkspacePayload();
      const hasSearch = Boolean((payload.fetchInput || "").trim());
      const hasVideos = Boolean((payload.videos && payload.videos.length) || (payload.previewClips && payload.previewClips.length));

      if (!hasSearch && !hasVideos && !activeJobId) {
        try {
          localStorage.removeItem(WORKSPACE_STATE_KEY);
          sessionStorage.removeItem(WORKSPACE_STATE_KEY);
        } catch (_err) {
          // ignore
        }
        return;
      }

      writeWorkspaceToBrowser(payload);
      if (syncServer && workspaceHydrated) {
        syncWorkspaceToServer(payload);
      }
    } catch (_err) {
      // Storage may be unavailable.
    }
  }

  function restoreWorkspaceState() {
    const state = readWorkspaceFromBrowser();
    if (!state) return false;
    return applyWorkspaceSnapshot(state);
  }

  async function restoreWorkspaceFromServer() {
    try {
      const res = await fetch("/api/panel-workspace/");
      const data = await res.json();
      if (!res.ok || !data.workspace) return false;
      const server = data.workspace;
      if (!(server.fetchInput || (server.videos && server.videos.length))) return false;

      const local = readWorkspaceFromBrowser();
      const serverSaved = Number(server.savedAt) || Date.parse(server.savedAt || "") || 0;
      const localSaved = Number(local?.savedAt) || 0;
      if (local && localSaved >= serverSaved && (local.fetchInput || local.videos?.length)) {
        return false;
      }
      return applyWorkspaceSnapshot(server);
    } catch (_err) {
      return false;
    }
  }

  function persistActiveJob(jobId) {
    if (!jobId) return;
    try {
      localStorage.setItem(ACTIVE_JOB_KEY, String(jobId));
    } catch (_err) {
      // ignore
    }
  }

  function clearActiveJob() {
    activeJobId = null;
    try {
      localStorage.removeItem(ACTIVE_JOB_KEY);
    } catch (_err) {
      // ignore
    }
  }

  function isJobInProgress(status) {
    return ["pending", "downloading", "uploading"].includes(status);
  }

  async function resumeActiveJob() {
    let jobId = null;
    try {
      const res = await fetch("/api/job/active/");
      const data = await res.json();
      if (res.ok && data.active && data.job_id) {
        await applyJobStatus(data, { resumed: true });
        return;
      }
    } catch (_err) {
      // fall through to localStorage
    }

    try {
      jobId = localStorage.getItem(ACTIVE_JOB_KEY);
    } catch (_err) {
      jobId = null;
    }
    if (!jobId) return;

    try {
      const res = await fetch(`/api/job/${jobId}/status/`);
      const data = await res.json();
      if (!res.ok) {
        clearActiveJob();
        return;
      }
      await applyJobStatus(data, { resumed: true });
    } catch (_err) {
      // Job may still be running server-side; keep polling.
      activeJobId = jobId;
      els.progressCard?.classList.add("is-active");
      els.activityStagePanel?.classList.add("is-running");
      setProgressCancelVisible(true);
      startPolling(jobId, { skipPersist: true });
    }
  }

  async function applyJobStatus(job, { resumed = false } = {}) {
    if (!job?.job_id) return;

    const progressText = `${job.progress_message || ""} ${job.error_message || ""}`;
    const rateLimited = /rate-limited|\b429\b/i.test(progressText);
    const effectivelyFailed =
      job.status === "failed" || (job.status === "uploading" && rateLimited);

    // Never replace the user's last searched channel with a finished/stuck job.
    if (resumed && isJobInProgress(job.status) && !effectivelyFailed) {
      if (job.workspace_snapshot) {
        const jobMeta = { current_video_title: job.current_video_title || "" };
        const restored = applyWorkspaceSnapshot(job.workspace_snapshot, {
          onlyIfEmpty: true,
          jobMeta,
        });
        if (!restored && !videos.length && !previewClips.length) {
          await restoreWorkspaceFromJob(job.workspace_snapshot, jobMeta);
        } else if (restored && selectedIds.size === 0) {
          reconcileSelectionFromSnapshot(job.workspace_snapshot, jobMeta);
          rebuildVideoGrid();
          updateSelectionUI();
        }
      } else if (!videos.length && !previewClips.length && job.tiktok_username) {
        await restoreWorkspaceFromJob(
          {
            fetchInput: job.tiktok_username,
            currentUsername: job.tiktok_username,
            currentSource: "twitch",
            selectedIds: [],
            videos: [],
            mode: "grid",
            jobVideoTitle: job.current_video_title || "",
          },
          { current_video_title: job.current_video_title || "" }
        );
      }
    }

    if (effectivelyFailed) {
      updateProgress({ ...job, status: "failed" });
      clearActiveJob();
      return;
    }

    activeJobId = job.job_id;
    persistActiveJob(job.job_id);

    if (isJobInProgress(job.status)) {
      els.progressCard?.classList.add("is-active");
      els.activityStagePanel?.classList.add("is-running");
      setProgressCancelVisible(true);
      updateProgress(job);
      startPolling(job.job_id, { skipPersist: true });
      if (resumed) {
        showToast("Resuming job in progress on the server…", "info");
        addLog(job.progress_message || "Job still running on remote server.", "info");
      }
      return;
    }

    if (job.progress_stage === "preview_ready" && job.preview_clips?.length) {
      if (!isPreviewMode() || previewJobId !== job.job_id) {
        renderPreviewClips(job.preview_clips, job.job_id);
      }
      updateProgress(job);
      if (resumed) {
        showToast("AI clips are ready — your selection was restored.", "info");
      }
      return;
    }

    if (job.status === "completed" || job.status === "failed") {
      updateProgress(job);
      clearActiveJob();
    }
  }

  function formatFetchSummary(data) {
    const tiktokCount = data.tiktok_count ?? 0;
    const youtubeCount = data.youtube_count ?? 0;
    const twitchCount = data.twitch_count ?? 0;
    const handle = data.username ? `@${data.username}` : "this source";

    if (data.source === "mixed") {
      return `Loaded ${tiktokCount} TikTok + ${youtubeCount} YouTube videos for ${handle}`;
    }
    if (data.source === "youtube") {
      return `Loaded ${data.count} YouTube videos from ${data.profile_title || handle}`;
    }
    if (data.source === "twitch") {
      const vodCount = data.twitch_vod_count ?? 0;
      const highlightCount = data.twitch_highlight_count ?? 0;
      const clipCount = data.twitch_clip_count ?? 0;
      const liveCount = data.twitch_live_count ?? 0;
      const parts = [];
      if (liveCount) parts.push(`${liveCount} live`);
      if (vodCount) parts.push(`${vodCount} VODs`);
      if (highlightCount) parts.push(`${highlightCount} highlights`);
      if (clipCount) parts.push(`${clipCount} clips`);
      const detail = parts.length ? parts.join(", ") : `${data.count} items`;
      return `Loaded ${detail} from ${data.profile_title || handle}`;
    }
    return `Loaded ${data.count} TikTok videos from ${handle}`;
  }

  function isPreviewMode() {
    return previewClips.length > 0;
  }

  function getSelectedPreviewClips() {
    return previewClips.filter((clip) => selectedPreviewIds.has(String(clip.id)));
  }

  function updatePreviewSelectionUI() {
    const selected = getSelectedPreviewClips();
    els.selectedCount.textContent = selected.length;
    els.totalCount.textContent = previewClips.length;
    els.videoCountBadge.textContent = `${previewClips.length} clips`;

    const remaining = getRemainingQuota();
    const quotaBlocked = remaining !== null && remaining === 0;
    const canPost = canPostToAnyDestination();
    const tooMany = remaining !== null && selected.length > remaining;

    if (els.clipUploadBtn) {
      els.clipUploadBtn.disabled =
        selected.length === 0 || !canPost || quotaBlocked || tooMany;
      els.clipUploadBtn.innerHTML =
        selected.length > 0
          ? `<i class="bi bi-cloud-upload"></i> Upload ${selected.length} Clip${selected.length === 1 ? "" : "s"}`
          : '<i class="bi bi-stars"></i> Clip with AI';
    }

    document.querySelectorAll(".video-card.preview-clip-card").forEach((card) => {
      const id = card.dataset.clipId;
      card.classList.toggle("selected", selectedPreviewIds.has(id));
    });
    saveWorkspaceState();
  }

  function buildVideoCardElement(video) {
    const thumb =
      video.thumbnail ||
      "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='350' fill='%23222'%3E%3Crect width='200' height='350'/%3E%3C/svg%3E";
    const source = video.source || currentSource;
    const badge = source === "twitch" ? twitchBadgeMeta(video) : sourceBadgeMeta(source);
    const score = Number(video.ai_score);
    const hasScore = Number.isFinite(score);
    const isDup = Boolean(video.ai_duplicate);
    const isLong = Number(video.duration || 0) >= 60;
    const scoreClass = isDup
      ? "ai-score-badge dup"
      : score >= 70
        ? "ai-score-badge high"
        : score >= 50
          ? "ai-score-badge mid"
          : "ai-score-badge low";

    const card = document.createElement("div");
    card.className = "video-card";
    if (selectedIds.has(videoKey(video))) {
      card.classList.add("selected");
    }
    if (isDup) card.classList.add("is-duplicate");
    if (video.ai_select) card.classList.add("ai-recommended");
    card.dataset.id = videoKey(video);
    card.innerHTML = `
      <img src="${escapeHtml(thumb)}" alt="" loading="lazy">
      <span class="source-badge ${badge.className}">${badge.label}</span>
      <span class="check-badge"><i class="bi bi-check-lg"></i></span>
      ${video.duration ? `<span class="duration-badge">${formatDuration(video.duration)}</span>` : ""}
      ${hasScore ? `<span class="${scoreClass}" title="${escapeHtml(video.ai_reason || "")}">${isDup ? "DUP" : Math.round(score)}</span>` : ""}
      ${isLong ? `<button type="button" class="moment-find-btn" title="Find 15–30s moments"><i class="bi bi-scissors"></i></button>` : ""}
      <div class="video-overlay">
        <div class="video-title">${escapeHtml(video.title)}</div>
        ${video.ai_reason ? `<div class="video-ai-reason">${escapeHtml(video.ai_reason)}</div>` : ""}
      </div>
    `;
    card.addEventListener("click", (event) => {
      if (event.target.closest(".moment-find-btn")) return;
      const key = videoKey(video);
      if (selectedIds.has(key)) {
        selectedIds.delete(key);
      } else {
        const remaining = getRemainingQuota();
        if (remaining !== null && selectedIds.size >= remaining) {
          showToast(`Daily limit: only ${remaining} upload(s) left today.`, "error");
          return;
        }
        selectedIds.add(key);
      }
      updateSelectionUI();
    });
    const momentBtn = card.querySelector(".moment-find-btn");
    if (momentBtn) {
      momentBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        findMomentsForVideo(video);
      });
    }
    return card;
  }

  function rebuildVideoGrid() {
    els.videoGrid.innerHTML = "";
    videos.forEach((video) => {
      els.videoGrid.appendChild(buildVideoCardElement(video));
    });
    els.emptyState.hidden = videos.length > 0;
    els.videoGrid.hidden = videos.length === 0;
  }

  function clearPreviewClips() {
    previewClips = [];
    previewJobId = null;
    selectedPreviewIds.clear();
    try {
      const raw = sessionStorage.getItem(WORKSPACE_STATE_KEY);
      if (raw) {
        const state = JSON.parse(raw);
        if (state?.mode === "preview") {
          sessionStorage.removeItem(WORKSPACE_STATE_KEY);
        }
      }
    } catch (_err) {
      // ignore
    }
  }

  function renderPreviewClips(clips, jobId, { selectAll = true } = {}) {
    previewClips = clips || [];
    previewJobId = jobId || previewJobId;
    videos = [];
    selectedIds.clear();
    selectedPreviewIds.clear();
    if (selectAll) {
      previewClips.forEach((clip) => selectedPreviewIds.add(String(clip.id)));
    }

    els.videoGrid.innerHTML = "";
    previewClips.forEach((clip) => {
      const clipId = String(clip.id);
      const card = document.createElement("div");
      card.className = "video-card preview-clip-card selected";
      card.dataset.id = `preview:${clipId}`;
      card.dataset.clipId = clipId;
      const category = (clip.category || "hype").replace(/_/g, " ");
      card.innerHTML = `
        <video src="${escapeHtml(clip.preview_url)}" muted playsinline preload="metadata"></video>
        <button type="button" class="preview-play-btn" aria-label="Play preview">
          <i class="bi bi-play-fill"></i>
        </button>
        <span class="category-badge">${escapeHtml(category)}</span>
        <span class="source-badge source-twitch">Clip</span>
        <span class="check-badge"><i class="bi bi-check-lg"></i></span>
        ${clip.duration ? `<span class="duration-badge">${formatDuration(clip.duration)}</span>` : ""}
        <div class="video-overlay">
          <div class="video-title">${escapeHtml(clip.title || "Untitled clip")}</div>
        </div>
      `;

      card.addEventListener("click", () => {
        if (selectedPreviewIds.has(clipId)) {
          selectedPreviewIds.delete(clipId);
        } else {
          const remaining = getRemainingQuota();
          if (remaining !== null && selectedPreviewIds.size >= remaining) {
            showToast(`Daily limit: only ${remaining} upload(s) left today.`, "error");
            return;
          }
          selectedPreviewIds.add(clipId);
        }
        updatePreviewSelectionUI();
      });

      const playBtn = card.querySelector(".preview-play-btn");
      playBtn?.addEventListener("click", (event) => {
        event.stopPropagation();
        const video = card.querySelector("video");
        if (!video) return;
        document.querySelectorAll(".preview-clip-card video").forEach((el) => {
          if (el !== video) el.pause();
        });
        if (video.paused) {
          video.play().catch(() => {});
        } else {
          video.pause();
        }
      });

      els.videoGrid.appendChild(card);
    });

    els.emptyState.hidden = previewClips.length > 0;
    els.videoGrid.hidden = previewClips.length === 0;
    updatePreviewSelectionUI();
    updateActivityStage(
      "preview_ready",
      `${previewClips.length} AI clips ready — select clips to upload.`
    );
    saveWorkspaceState();
  }

  function renderVideos(data, { selectedIdsToRestore = null, silent = false } = {}) {
    clearPreviewClips();
    videos = data.videos || [];
    if ((data.source || "") === "twitch") {
      videos = sortTwitchVideos(videos);
    }
    // Keep AI ranking when present.
    if (videos.some((v) => Number.isFinite(Number(v.ai_score)))) {
      videos = [...videos].sort((a, b) => {
        if (Boolean(a.ai_duplicate) !== Boolean(b.ai_duplicate)) {
          return a.ai_duplicate ? 1 : -1;
        }
        return Number(b.ai_score || 0) - Number(a.ai_score || 0);
      });
    }
    currentUsername = data.username || "";
    currentSource = data.source || "tiktok";
    selectedIds.clear();
    if (Array.isArray(selectedIdsToRestore) && selectedIdsToRestore.length) {
      selectedIdsToRestore.forEach((id) => selectedIds.add(String(id)));
    } else if (!selectedIdsToRestore && videos.some((v) => v.ai_select)) {
      videos
        .filter((v) => v.ai_select && !v.ai_duplicate)
        .slice(0, 8)
        .forEach((v) => selectedIds.add(videoKey(v)));
    }
    lastFetchSummary = formatFetchSummary(data);
    if (data.ai_score) {
      const dup = data.ai_score.duplicates_found || 0;
      const rec = data.ai_score.recommended_count || 0;
      lastFetchSummary = `${lastFetchSummary} · AI scored (top ${rec}${dup ? `, ${dup} dup` : ""})`;
    }

    rebuildVideoGrid();
    updateSelectionUI();
    if (!silent) {
      updateActivityStage("idle", lastFetchSummary);
    }
    saveWorkspaceState();
  }

  async function findMomentsForVideo(video) {
    const url = video.url || video.webpage_url || "";
    if (!url) {
      showToast("No URL available for moment finding.", "error");
      return;
    }
    updateActivityStage("fetching", "Finding 15–30s moments with free vision model...");
    showToast("Finding clip moments… this can take a minute.", "info");
    try {
      const res = await fetch("/api/ai/find-moments/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          url,
          source: video.source || currentSource || "auto",
          title: video.title || "",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Moment finder failed");
      const moments = data.moments || [];
      if (!moments.length) {
        showToast("No strong moments found.", "error");
        return;
      }
      // Show moments as selectable synthetic clips in the grid overlay via toast + activity.
      const lines = moments
        .slice(0, 5)
        .map(
          (m, idx) =>
            `${idx + 1}. ${Math.round(m.start_sec)}s–${Math.round(m.end_sec)}s · ${m.title || "Moment"}`
        )
        .join("\n");
      addLog(`Moments for ${video.title || url}:\n${lines}`, "info");
      updateActivityStage(
        "idle",
        `Found ${moments.length} moment(s) · vision ${data.vision_model || "moondream"}`
      );
      showToast(`Found ${moments.length} moment window(s). Check Activity log.`, "success");
      window.__lastMoments = { video, moments, meta: data };
    } catch (err) {
      showToast(err.message || "Moment finder failed", "error");
      addLog(err.message || "Moment finder failed", "error");
      updateActivityStage("idle", lastFetchSummary || "Ready");
    }
  }

  function setFetchSourceSelection(source) {
    pendingFetchSource = source || "auto";
    if (els.fetchSourceSelect) {
      els.fetchSourceSelect.value = pendingFetchSource;
    }
    els.fetchSourceOptions?.querySelectorAll(".fetch-source-option").forEach((option) => {
      const selected = option.dataset.source === pendingFetchSource;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-selected", selected ? "true" : "false");
    });
  }

  function openFetchSourceModal(input) {
    if (!els.fetchSourceModal || !els.fetchSourceModalBackdrop) {
      return fetchVideos();
    }

    setFetchSourceSelection(els.fetchSourceSelect?.value || "auto");
    if (els.fetchSourceModalQuery) {
      els.fetchSourceModalQuery.textContent = input;
    }

    els.fetchSourceModalBackdrop.hidden = false;
    els.fetchSourceModal.hidden = false;
    document.body.classList.add("fetch-source-modal-open");
    els.fetchSourceModalConfirm?.focus();
    return undefined;
  }

  function closeFetchSourceModal() {
    if (els.fetchSourceModalBackdrop) els.fetchSourceModalBackdrop.hidden = true;
    if (els.fetchSourceModal) els.fetchSourceModal.hidden = true;
    document.body.classList.remove("fetch-source-modal-open");
  }

  function promptFetchVideos() {
    const input = els.tiktokInput.value.trim();
    if (!input) {
      showToast("Enter a TikTok username, YouTube link, or Twitch channel/VOD URL.", "error");
      return;
    }
    openFetchSourceModal(input);
  }

  async function fetchVideos(sourceOverride = null) {
    updateActivityStage("fetching", "Searching for videos...");
    const input = els.tiktokInput.value.trim();
    const source = sourceOverride || els.fetchSourceSelect?.value || "auto";
    if (!input) {
      showToast("Enter a TikTok username, YouTube link, or Twitch channel/VOD URL.", "error");
      return false;
    }

    if (els.fetchSourceSelect && sourceOverride) {
      els.fetchSourceSelect.value = source;
    }

    els.loadingState.hidden = false;
    els.emptyState.hidden = true;
    els.videoGrid.hidden = true;
    if (els.fetchBtn) els.fetchBtn.disabled = true;

    try {
      const res = await fetch("/api/fetch-videos/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({ input, source }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Fetch failed");
      renderVideos(data);
      showToast(`Found ${data.count} videos`, "success");
      return true;
    } catch (err) {
      showToast(err.message, "error");
      addLog(err.message, "error");
      els.emptyState.hidden = false;
      return false;
    } finally {
      els.loadingState.hidden = true;
      if (els.fetchBtn) els.fetchBtn.disabled = false;
    }
  }

  async function openClipPanelWithFetch({ input = "", source = "auto", autoSelectId = "" } = {}) {
    const query = (input || "").trim();
    if (!query) {
      showToast("No video URL to load.", "error");
      return false;
    }

    switchDashboardView("studio");
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    if (els.tiktokInput) els.tiktokInput.value = query;
    if (els.fetchSourceSelect && source && source !== "auto") {
      els.fetchSourceSelect.value = source;
    }

    els.studioWorkspace?.scrollIntoView({ behavior: "smooth", block: "start" });

    try {
      const ok = await fetchVideos(source && source !== "auto" ? source : undefined);
      if (!ok) return false;

      selectFetchedVideo(autoSelectId);
      return true;
    } catch (err) {
      showToast(err.message || "Could not load video.", "error");
      return false;
    }
  }

  function selectFetchedVideo(autoSelectId = "") {
    if (!videos.length) return false;

    let match = null;
    if (autoSelectId) {
      match = videos.find(
        (video) =>
          video.id === autoSelectId ||
          videoKey(video) === autoSelectId ||
          videoKey(video).endsWith(`:${autoSelectId}`)
      );
    }
    if (!match && videos.length === 1) {
      match = videos[0];
    }

    if (match) {
      selectedIds.clear();
      selectedIds.add(videoKey(match));
      updateSelectionUI();
      showToast("Video loaded and selected — click Clip & Upload.", "success");
      return true;
    }

    showToast(`Found ${videos.length} videos — select the one you want.`, "info");
    return false;
  }

  async function importFetchResults(data, { autoSelectId = "" } = {}) {
    switchDashboardView("studio");
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    renderVideos(data);
    els.studioWorkspace?.scrollIntoView({ behavior: "smooth", block: "start" });
    selectFetchedVideo(autoSelectId);
    return true;
  }

  async function uploadTwitchClips() {
    const selected = getSelectedPreviewClips();
    if (!previewJobId || !selected.length) {
      showToast("Select at least one clip to upload.", "error");
      return;
    }
    if (!canPostToAnyDestination()) {
      showToast("Link YouTube or TikTok and select at least one destination.", "error");
      return;
    }

    const remaining = getRemainingQuota();
    if (remaining !== null && selected.length > remaining) {
      showToast(`You can only upload ${remaining} more video(s) today.`, "error");
      return;
    }

    if (els.clipUploadBtn) els.clipUploadBtn.disabled = true;
    updateActivityStage("uploading", `Uploading ${selected.length} clip(s)...`);

    try {
      const res = await fetch("/api/twitch/upload-clips/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          job_id: previewJobId,
          clip_ids: selected.map((clip) => clip.id),
          destinations: getSelectedDestinations(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");

      activeJobId = data.job_id;
      showToast(data.message, "success");
      addLog(data.message, "success");
      startPolling(activeJobId);
    } catch (err) {
      showToast(err.message, "error");
      addLog(err.message, "error");
      if (els.clipUploadBtn) els.clipUploadBtn.disabled = false;
      updatePreviewSelectionUI();
    }
  }

  async function makeTwitchClipsWithAi() {
    if (cfg.aiFeaturesEnabled === false) {
      showToast("AI features are turned off. Enable them in Controls.", "error");
      return;
    }
    if (!isSingleTwitchVodSelected()) {
      showToast("Select exactly one Twitch VOD.", "error");
      return;
    }
    if (!canPostToAnyDestination()) {
      showToast("Link YouTube or TikTok and select at least one destination.", "error");
      return;
    }

    const vod = getSelectedVideos()[0];
    saveWorkspaceState();
    if (els.clipUploadBtn) els.clipUploadBtn.disabled = true;
    updateActivityStage("analyzing", "Planning clip moments with AI...");

    try {
      const res = await fetch("/api/twitch/vod-clips/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          vod,
          username: currentUsername,
          destinations: getSelectedDestinations(),
          workspace: getWorkspaceSnapshot(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Twitch clip job failed");

      activeJobId = data.job_id;
      previewJobId = data.job_id;
      showToast(data.message, "success");
      addLog(data.message, "success");
      startPolling(activeJobId);
    } catch (err) {
      showToast(err.message, "error");
      addLog(err.message, "error");
      updateSelectionUI();
    }
  }

  async function clipWithAi() {
    if (isPreviewMode()) {
      return uploadTwitchClips();
    }
    if (selectedIds.size === 0) return;

    const aiOn = cfg.aiFeaturesEnabled !== false;
    if (!aiOn) {
      return clipAndUpload();
    }

    const selected = getSelectedVideos();
    if (
      selected.length === 1 &&
      (selected[0].source || currentSource) === "twitch" &&
      isTwitchAiClipEligible(selected[0])
    ) {
      return makeTwitchClipsWithAi();
    }

    return clipAndUpload();
  }

  async function clipAndUpload() {
    if (isPreviewMode()) {
      return uploadTwitchClips();
    }
    if (selectedIds.size === 0) return;
    if (!canPostToAnyDestination()) {
      showToast("Link YouTube or TikTok and select at least one destination.", "error");
      return;
    }

    const selected = videos.filter((v) => selectedIds.has(videoKey(v)));
    const remaining = getRemainingQuota();
    if (remaining !== null && selected.length > remaining) {
      showToast(`You can only upload ${remaining} more video(s) today.`, "error");
      return;
    }

    const aiOn = cfg.aiFeaturesEnabled !== false;

    if (aiOn && window.AiFixUpload?.startFixUploadJob) {
      updateActivityStage("downloading", `Starting clip & upload for ${selected.length} video(s)...`);
      saveWorkspaceState();
      const jobId = await window.AiFixUpload.startFixUploadJob(selected, currentUsername, {
        buttons: [els.clipUploadBtn],
        endpoint: "/api/clip-upload/",
        onJobUpdate: updateProgress,
        destinations: getSelectedDestinations(),
        workspace: getWorkspaceSnapshot(),
      });
      if (jobId) activeJobId = jobId;
      return;
    }

    els.clipUploadBtn.disabled = true;
    updateActivityStage("downloading", `Starting clip & upload for ${selected.length} video(s)...`);
    saveWorkspaceState();

    try {
      const res = await fetch("/api/clip-upload/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          videos: selected,
          username: currentUsername,
          source: currentSource,
          destinations: getSelectedDestinations(),
          workspace: getWorkspaceSnapshot(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");

      activeJobId = data.job_id;
      showToast(data.message, "success");
      addLog(data.message, "success");
      startPolling(activeJobId);
    } catch (err) {
      showToast(err.message, "error");
      addLog(err.message, "error");
      els.clipUploadBtn.disabled = false;
    }
  }

  function updateProgress(job) {
    const percent = job.fix_progress ?? job.progress_percent ?? 0;
    const detail =
      job.status === "failed" && job.error_message
        ? job.error_message
        : job.progress_message || "Processing...";

    const stageId =
      job.status === "failed"
        ? "failed"
        : job.status === "completed"
          ? "complete"
          : job.progress_stage || "downloading";

    if (isJobInProgress(job.status)) {
      els.progressCard?.classList.add("is-active");
      els.activityStagePanel?.classList.add("is-running");
      setProgressCancelVisible(true);
      if (job.job_id) {
        activeJobId = job.job_id;
        persistActiveJob(job.job_id);
      }
    }

    if (job.progress_stage === "cancelled") {
      stopPolling();
      window.AiFixUpload?.stopFixPolling?.();
      clearActiveJob();
      updateFixProgressPanel("cancelled", job.progress_message || "Cancelled by user.", percent);
      if (els.jobStatusText) els.jobStatusText.textContent = "Cancelled";
      if (els.clipUploadBtn) els.clipUploadBtn.disabled = false;
      setTimeout(hideFixProgressPanel, 1200);
      return;
    }

    updateFixProgressPanel(stageId, detail, percent);
    if (els.jobStatusText) {
      els.jobStatusText.textContent =
        job.status.charAt(0).toUpperCase() + job.status.slice(1);
    }

    if (job.status === "completed" || job.status === "failed") {
      const isPreviewReady =
        job.progress_stage === "preview_ready" && job.preview_clips?.length;

      if (!isPreviewReady) {
        stopPolling();
        window.AiFixUpload?.stopFixPolling?.();
        setTimeout(hideFixProgressPanel, job.status === "completed" ? 1800 : 3500);
        clearActiveJob();
      }

      if (isPreviewReady) {
        if (!isPreviewMode() || previewJobId !== job.job_id) {
          renderPreviewClips(job.preview_clips, job.job_id);
        }
        addLog(job.progress_message || "AI clips are ready to review.", "success");
        updatePreviewSelectionUI();
        els.progressCard?.classList.remove("is-active");
        els.activityStagePanel?.classList.remove("is-running");
        return;
      }

      if (job.progress_stage === "complete" && job.status === "completed") {
        clearPreviewClips();
        els.videoGrid.innerHTML = "";
        els.emptyState.hidden = false;
        els.videoGrid.hidden = true;
        applyAiFeaturesUI();
        try {
          sessionStorage.removeItem(WORKSPACE_STATE_KEY);
        } catch (_err) {
          // ignore
        }
      }

      if (job.status === "completed" && job.completed_videos) {
        consumeQuota(job.completed_videos);
      }

      updateSelectionUI();

      if (job.videos && job.videos.length) {
        els.uploadResults.hidden = false;
        els.resultsList.innerHTML = job.videos
          .map((v) => {
            const statusIcon =
              v.status === "completed"
                ? '<i class="bi bi-check-circle text-success"></i>'
                : '<i class="bi bi-x-circle text-danger"></i>';
            const links = [];
            if (v.youtube_url) {
              links.push(
                `<a href="${escapeHtml(v.youtube_url)}" target="_blank" rel="noopener">YouTube</a>`
              );
            }
            if (v.tiktok_url) {
              links.push(
                `<a href="${escapeHtml(v.tiktok_url)}" target="_blank" rel="noopener">TikTok</a>`
              );
            }
            const link = links.length
              ? links.join(" · ")
              : escapeHtml(v.error_message || v.status);
            return `<div class="result-item">${statusIcon} ${escapeHtml(v.title)} — ${link}</div>`;
          })
          .join("");
      }

      addLog(
        job.status === "completed" ? "Job completed!" : `Job ended: ${job.error_message || job.status}`,
        job.status === "completed" ? "success" : "error"
      );
    }
  }

  let pollFailCount = 0;
  let lastSuccessfulPollAt = 0;
  let tiktokExtensionPostKey = "";

  async function maybeTriggerTiktokExtensionUpload(job) {
    // Browser-extension posting disabled — TikTok uses Developer API uploads.
    return;
  }

  async function pollJob(jobId) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    try {
      const res = await fetch(`/api/job/${jobId}/status/`, { signal: controller.signal });
      let data = {};
      try {
        data = await res.json();
      } catch {
        data = {};
      }
      if (res.ok) {
        pollFailCount = 0;
        lastSuccessfulPollAt = Date.now();
        updateProgress(data);
        maybeTriggerTiktokExtensionUpload(data);
        return;
      }
      pollFailCount += 1;
      if (pollFailCount >= 8) {
        const msg = data.error || `Job status check failed (${res.status}).`;
        els.progressDetail.textContent = msg;
        updateActivityStage("failed", msg);
      }
    } catch (err) {
      pollFailCount += 1;
      const uploadingRecently =
        lastSuccessfulPollAt && Date.now() - lastSuccessfulPollAt < 10 * 60 * 1000;
      if (pollFailCount >= 8) {
        updateActivityStage(
          "failed",
          uploadingRecently
            ? "Lost connection while checking job status. TikTok uploads can take several minutes — refresh the page to check again."
            : "Lost connection while checking job status. The job may still be running on the remote server."
        );
      } else if (pollFailCount >= 3) {
        updateActivityStage(
          "uploading",
          "Still uploading… TikTok browser posts can take a few minutes per clip."
        );
      }
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function startPolling(jobId, { skipPersist = false } = {}) {
    stopPolling();
    pollFailCount = 0;
    activeJobId = jobId;
    if (!skipPersist) {
      persistActiveJob(jobId);
    }
    pollJob(jobId);
    pollTimer = setInterval(() => pollJob(jobId), 2000);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refreshConnectionStatus() {
    await Promise.all([refreshYoutubeStatus(), refreshTiktokStatus()]);
    updateConnectionBadge();
    updateSelectionUI();
  }

  async function refreshYoutubeStatus() {
    try {
      const res = await fetch("/api/youtube/status/");
      const data = await res.json();
      cfg.youtubeConnected = data.connected;
      if (data.channel) {
        cfg.linkedAccounts = cfg.linkedAccounts || {};
        cfg.linkedAccounts.youtube = {
          name: data.channel.title || "YouTube Channel",
          thumbnail: data.channel.thumbnail || "",
        };
      } else if (!data.connected) {
        if (cfg.linkedAccounts) cfg.linkedAccounts.youtube = null;
      }
      updateLinkedAccountsUI();
      if (els.postToYoutube) {
        els.postToYoutube.disabled = !data.connected;
        if (!data.connected) els.postToYoutube.checked = false;
      }
      updateConnectionBadge();
      if (els.unlinkYoutubeBtn) {
        els.unlinkYoutubeBtn.hidden = !data.connected;
      }
      updateSelectionUI();
      if (data.connected && data.channel) {
        addLog(`YouTube connected: ${data.channel.title}`, "success");
      }
      cfg.commentsReady = data.comments_ready;
      cfg.needsCommentRelink = data.needs_comment_relink;
      if (data.needs_comment_relink) {
        addLog(
          "Pinned comments need extra permission — open Pinned Comment and click Link YouTube again.",
          "error"
        );
      }
    } catch (err) {
      addLog("Could not refresh YouTube status.", "error");
    }
  }

  async function refreshTiktokStatus() {
    try {
      const res = await fetch("/api/tiktok/status/");
      const data = await res.json();
      cfg.tiktokConnected = data.connected;
      if (data.account) {
        cfg.linkedAccounts = cfg.linkedAccounts || {};
        cfg.linkedAccounts.tiktok = {
          displayName: data.account.display_name || "TikTok Account",
          username: data.account.username || "",
          avatarUrl: data.account.avatar_url || "",
        };
      } else if (!data.connected) {
        if (cfg.linkedAccounts) cfg.linkedAccounts.tiktok = null;
      }
      updateLinkedAccountsUI();
      if (els.postToTiktok) {
        els.postToTiktok.disabled = !data.connected;
        if (!data.connected) els.postToTiktok.checked = false;
      }
      if (els.unlinkTiktokBtn) {
        els.unlinkTiktokBtn.hidden = !data.connected;
      }
      const banner = document.getElementById("tiktokPostingBanner");
      if (banner) banner.hidden = Boolean(data.connected);
      if (data.connected && data.account?.display_name) {
        addLog(`TikTok connected (Developer API): ${data.account.display_name}`, "success");
      }
    } catch (_err) {
      addLog("Could not refresh TikTok status.", "error");
    }
  }

  async function unlinkTiktok() {
    try {
      const res = await fetch("/api/tiktok/disconnect/", {
        method: "POST",
        headers: csrfHeaders(),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Unlink failed");
      cfg.tiktokConnected = false;
      if (els.unlinkTiktokBtn) els.unlinkTiktokBtn.hidden = true;
      updateConnectionBadge();
      updateSelectionUI();
      showToast("TikTok account unlinked.", "success");
      addLog("TikTok account unlinked.", "info");
      location.reload();
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function unlinkYoutube() {
    try {
      const res = await fetch("/api/youtube/disconnect/", {
        method: "POST",
        headers: csrfHeaders(),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Unlink failed");
      cfg.youtubeConnected = false;
      els.connectionBadge.textContent = "YouTube Not Linked";
      if (els.unlinkYoutubeBtn) els.unlinkYoutubeBtn.hidden = true;
      updateSelectionUI();
      showToast("YouTube channel unlinked.", "success");
      addLog("YouTube channel unlinked. Link a different account anytime.", "info");
      location.reload();
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  const COMMENT_TEMPLATES = {
    subscribe:
      "🔔 Plz Subscribe,  to help me  get 500 subs on YouTube  🙏\nIf you already Subscribe , Thank you so much i really appreciate it ❤️",
    emoji_hype: "🔥🔥🔥 Who else loved this? Drop a comment below! 👇",
    follow: "Turn on notifications for {channel} so you never miss a upload! 🔔",
    streamer: "Best moments from the stream — follow {channel} for daily clips! 🎮",
    custom: "",
  };

  function getChannelTitle() {
    return cfg.channelTitle || "Your Channel";
  }

  function buildCommentPreview() {
    const channel = getChannelTitle();
    const text = (els.pinnedCommentCustom?.value || "").trim();
    if (!text) {
      return "Enter your comment text above.";
    }
    return text.replaceAll("{channel}", channel);
  }

  function applyPresetToEditor() {
    const type = els.pinnedCommentType?.value || "subscribe";
    const channel = getChannelTitle();
    const template = COMMENT_TEMPLATES[type] ?? "";
    if (els.pinnedCommentCustom) {
      els.pinnedCommentCustom.value = template.replaceAll("{channel}", channel);
    }
    updateCommentPreview();
  }

  function updateCommentPreview() {
    if (els.pinnedCommentPreview) {
      els.pinnedCommentPreview.textContent = buildCommentPreview();
    }
  }

  function switchDashboardView(view) {
    const isComments = view === "comments";
    const isVideoFx = view === "videofx";
    const isAiCheck = view === "aicheck";
    const isChannelHelp = view === "channelhelp";
    const isStudio = !isComments && !isVideoFx && !isAiCheck && !isChannelHelp;
    els.studioWorkspace.hidden = !isStudio;
    els.commentSettingsWorkspace.hidden = !isComments;
    els.videoFxWorkspace.hidden = !isVideoFx;
    els.aiCheckWorkspace.hidden = !isAiCheck;
    els.channelHelpWorkspace.hidden = !isChannelHelp;
    els.navClipPanel?.classList.toggle("active", isStudio);
    els.navPinnedComment?.classList.toggle("active", isComments);
    els.navVideoFx?.classList.toggle("active", isVideoFx);
    els.navAiCheck?.classList.toggle("active", isAiCheck);
    els.navChannelHelp?.classList.toggle("active", isChannelHelp);
    if (els.topBarTitle) {
      if (isComments) {
        els.topBarTitle.textContent = "Pinned Comment Settings";
      } else if (isVideoFx) {
        els.topBarTitle.textContent = "Video Enhancements";
      } else if (isAiCheck) {
        els.topBarTitle.textContent = "AI Check Clip";
      } else if (isChannelHelp) {
        els.topBarTitle.textContent = "☠️ AI Help Channel⚡";
      } else {
        els.topBarTitle.textContent = "Clip & Upload Studio";
      }
    }
    if (isAiCheck && window.aiCheckPanel?.onShow) {
      window.aiCheckPanel.onShow();
    }
    if (isChannelHelp && window.channelHelpPanel?.onShow) {
      window.channelHelpPanel.onShow();
    }
    window.AppSidebar?.close?.();
  }
  window.switchDashboardView = switchDashboardView;

  async function savePinnedCommentSettings() {
    if (!cfg.youtubeConnected) {
      showToast("Link your YouTube channel first.", "error");
      return;
    }

    els.savePinnedCommentBtn.disabled = true;
    try {
      const res = await fetch("/api/upload-settings/save/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          enabled: Boolean(els.pinnedCommentEnabled?.checked),
          comment_type: els.pinnedCommentType?.value || "subscribe",
          comment_text: els.pinnedCommentCustom?.value || "",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Save failed");
      if (data.settings?.preview && els.pinnedCommentPreview) {
        els.pinnedCommentPreview.textContent = data.settings.preview;
      }
      showToast("Pinned comment settings saved.", "success");
      addLog("Pinned comment settings updated.", "success");
    } catch (err) {
      showToast(err.message, "error");
      addLog(err.message, "error");
    } finally {
      els.savePinnedCommentBtn.disabled = false;
    }
  }

  async function saveVideoFxSettings() {
    if (!cfg.youtubeConnected) {
      showToast("Link your YouTube channel first.", "error");
      return;
    }

    els.saveVideoFxBtn.disabled = true;
    try {
      const res = await fetch("/api/video-processing/save/", {
        method: "POST",
        headers: csrfHeaders(),
        body: JSON.stringify({
          enabled: Boolean(els.videoProcessingEnabled?.checked),
          subscribe_overlay: Boolean(els.subscribeOverlayEnabled?.checked),
          audio_replace: Boolean(els.audioReplaceEnabled?.checked),
          content_safety: Boolean(els.contentSafetyEnabled?.checked),
          audio_mood: els.audioMoodSelect?.value || "upbeat",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Save failed");
      showToast("Video enhancement settings saved.", "success");
      addLog("Video enhancement settings updated.", "success");
    } catch (err) {
      showToast(err.message, "error");
      addLog(err.message, "error");
    } finally {
      els.saveVideoFxBtn.disabled = false;
    }
  }

  function initCommentSettings() {
    els.navClipPanel?.addEventListener("click", () => switchDashboardView("studio"));
    els.navPinnedComment?.addEventListener("click", () => switchDashboardView("comments"));
    els.navVideoFx?.addEventListener("click", () => switchDashboardView("videofx"));
    els.navAiCheck?.addEventListener("click", () => switchDashboardView("aicheck"));
    els.navChannelHelp?.addEventListener("click", () => switchDashboardView("channelhelp"));
    els.pinnedCommentType?.addEventListener("change", applyPresetToEditor);
    els.pinnedCommentCustom?.addEventListener("input", updateCommentPreview);
    els.savePinnedCommentBtn?.addEventListener("click", savePinnedCommentSettings);
    els.saveVideoFxBtn?.addEventListener("click", saveVideoFxSettings);
    updateCommentPreview();
  }

  function init() {
    cfg.aiFeaturesEnabled = cfg.postingPreferences?.aiFeaturesEnabled !== false;
    cfg.linkedAccounts = cfg.linkedAccounts || { youtube: null, tiktok: null };
    buildActivityStages();
    updateLinkedAccountsUI();
    updateActivityStage(
      "idle",
      cfg.youtubeConnected || cfg.tiktokConnected
        ? "Select videos and choose a destination to begin."
        : "Link YouTube or TikTok, then fetch videos to begin."
    );
    applyAiFeaturesUI();

    els.aiFeaturesEnabled?.addEventListener("change", schedulePostingPreferencesSave);
    els.fetchBtn?.addEventListener("click", promptFetchVideos);
    els.tiktokInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") promptFetchVideos();
    });
    els.tiktokInput?.addEventListener("change", () => saveWorkspaceState({ syncServer: true }));
    els.tiktokInput?.addEventListener("blur", () => saveWorkspaceState({ syncServer: true }));

    els.fetchSourceOptions?.addEventListener("click", (event) => {
      const option = event.target.closest(".fetch-source-option");
      if (!option?.dataset.source) return;
      setFetchSourceSelection(option.dataset.source);
    });

    els.fetchSourceModalClose?.addEventListener("click", closeFetchSourceModal);
    els.fetchSourceModalCancel?.addEventListener("click", closeFetchSourceModal);
    els.fetchSourceModalBackdrop?.addEventListener("click", closeFetchSourceModal);
    els.fetchSourceModalConfirm?.addEventListener("click", async () => {
      const source = pendingFetchSource || "auto";
      closeFetchSourceModal();
      await fetchVideos(source);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || els.fetchSourceModal?.hidden) return;
      closeFetchSourceModal();
    });

    els.clipUploadBtn?.addEventListener("click", clipWithAi);
    els.cancelJobBtn?.addEventListener("click", cancelActiveJob);
    els.selectAllBtn?.addEventListener("click", () => {
      if (isPreviewMode()) {
        selectedPreviewIds.clear();
        const remaining = getRemainingQuota();
        const toSelect =
          remaining === null ? previewClips : previewClips.slice(0, remaining);
        toSelect.forEach((clip) => selectedPreviewIds.add(String(clip.id)));
        if (remaining !== null && previewClips.length > remaining) {
          showToast(`Selected ${remaining} clip(s) — your daily limit.`, "info");
        }
        updatePreviewSelectionUI();
        return;
      }
      selectedIds.clear();
      const remaining = getRemainingQuota();
      const toSelect = remaining === null ? videos : videos.slice(0, remaining);
      toSelect.forEach((v) => selectedIds.add(videoKey(v)));
      if (remaining !== null && videos.length > remaining) {
        showToast(`Selected ${remaining} video(s) — your daily limit.`, "info");
      }
      updateSelectionUI();
    });
    els.clearSelectionBtn?.addEventListener("click", () => {
      if (isPreviewMode()) {
        selectedPreviewIds.clear();
        updatePreviewSelectionUI();
        return;
      }
      selectedIds.clear();
      updateSelectionUI();
    });
    els.refreshStatusBtn?.addEventListener("click", refreshConnectionStatus);
    if (els.unlinkYoutubeBtn) {
      els.unlinkYoutubeBtn.addEventListener("click", unlinkYoutube);
    }
    if (els.unlinkTiktokBtn) {
      els.unlinkTiktokBtn.addEventListener("click", unlinkTiktok);
    }
    els.postToYoutube?.addEventListener("change", () => {
      schedulePostingPreferencesSave();
      updateSelectionUI();
    });
    els.postToTiktok?.addEventListener("change", () => {
      schedulePostingPreferencesSave();
      updateSelectionUI();
    });

    const params = new URLSearchParams(window.location.search);
    const requestedView = (params.get("view") || "").trim().toLowerCase();
    if (["studio", "aicheck", "channelhelp", "comments", "videofx"].includes(requestedView)) {
      switchDashboardView(requestedView === "studio" ? "studio" : requestedView);
      window.history.replaceState({}, "", "/dashboard/");
    }
    if (params.get("youtube_connected")) {
      showToast("YouTube channel linked successfully!", "success");
      addLog("YouTube channel linked.", "success");
      refreshConnectionStatus();
      window.history.replaceState({}, "", "/dashboard/");
    }
    if (params.get("tiktok_connected")) {
      showToast("TikTok account linked successfully!", "success");
      addLog("TikTok account linked.", "success");
      refreshConnectionStatus();
      window.history.replaceState({}, "", "/dashboard/");
    }
    if (params.get("tiktok_error")) {
      const err = decodeURIComponent(params.get("tiktok_error"));
      showToast("TikTok auth error: " + err, "error");
      addLog("TikTok auth error: " + err, "error");
      window.history.replaceState({}, "", "/dashboard/");
    }
    if (params.get("youtube_error")) {
      const err = decodeURIComponent(params.get("youtube_error"));
      if (err === "access_denied") {
        showToast(
          "Google blocked sign-in. Add your Gmail under OAuth consent screen → Test users.",
          "error"
        );
        addLog(
          "Access denied — your Google email must be listed as a Test user while the app is in Testing mode.",
          "error"
        );
      } else if (err.includes("code verifier") || err.includes("invalid_grant")) {
        showToast("YouTube link failed. Please click Link YouTube and try again.", "error");
        addLog("OAuth error: " + err, "error");
      } else {
        showToast("YouTube auth error: " + err, "error");
        addLog("YouTube auth error: " + err, "error");
      }
      window.history.replaceState({}, "", "/dashboard/");
    }

    updateQuotaUI();
    restoreWorkspaceState();
    workspaceHydrated = true;
    void (async () => {
      if (!videos.length && !previewClips.length) {
        await restoreWorkspaceFromServer();
      } else {
        // Keep account copy in sync with what we restored from this browser.
        saveWorkspaceState({ syncServer: true });
      }
      await resumeActiveJob();
      if (!videos.length && !previewClips.length) {
        updateSelectionUI();
      }
    })();
    initCommentSettings();

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && activeJobId) {
        pollJob(activeJobId);
      }
    });

    window.clipPanel = {
      openWithFetch: openClipPanelWithFetch,
      importResults: importFetchResults,
      showStudio: () => switchDashboardView("studio"),
      clipAndUploadSelected: clipAndUpload,
    };
    window.refreshTiktokStatus = refreshTiktokStatus;

    window.TIKTOK_CLIPER.updateActivityStage = updateActivityStage;
    window.TIKTOK_CLIPER.updateFixProgressPanel = updateFixProgressPanel;
    window.TIKTOK_CLIPER.showFixProgressPanel = showFixProgressPanel;
    window.TIKTOK_CLIPER.hideFixProgressPanel = hideFixProgressPanel;

    window.addEventListener("clip-panel:open", async (event) => {
      try {
        await openClipPanelWithFetch(event.detail || {});
      } catch (err) {
        showToast(err.message || "Could not open Clip Panel.", "error");
      }
    });
  }

  init();
})();
