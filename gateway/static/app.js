const queryForm = document.getElementById("queryForm");
const queryInput = document.getElementById("queryInput");
const terminalResponse = document.getElementById("terminalResponse");
const terminalMeta = document.getElementById("terminalMeta");
const uploadPlaceholder = document.getElementById("uploadPlaceholder");
const uploadHintDisplay = document.getElementById("uploadHintDisplay");
const activeHintChip = document.getElementById("activeHintChip");
const mockUploadDialog = document.getElementById("mockUploadDialog");
const uploadImageUrl = document.getElementById("uploadImageUrl");
const uploadImagePath = document.getElementById("uploadImagePath");
const saveUploadHints = document.getElementById("saveUploadHints");
const clearUploadHints = document.getElementById("clearUploadHints");
const closeMockUpload = document.getElementById("closeMockUpload");
const sideTail = document.getElementById("sideTail");
const submitButton = queryForm?.querySelector('button[type="submit"]');
const memoryNeedle = document.getElementById("memoryNeedle");
const gpuNeedle = document.getElementById("gpuNeedle");
const memoryValue = document.getElementById("memoryValue");
const gpuValue = document.getElementById("gpuValue");
const memoryStatInline = document.getElementById("memoryStatInline");
const scopeMemoryRedlineLabel = document.getElementById("scopeMemoryRedlineLabel");
const scopeGpuRedlineLabel = document.getElementById("scopeGpuRedlineLabel");
const scopeLineMemoryBox = document.getElementById("scopeLineMemoryBox");
const scopeLineGpuBox = document.getElementById("scopeLineGpuBox");
const scopePulseMemoryBox = document.getElementById("scopePulseMemoryBox");
const scopePulseGpuBox = document.getElementById("scopePulseGpuBox");

const imageHints = {
  imageUrl: "",
  imagePath: ""
};

const telemetryState = {
  scopeHistoryGpu: [],
  scopeHistoryMemory: [],
  seenSuccess: false
};

function appendTail(line) {
  if (!sideTail) {
    return;
  }

  const now = new Date().toLocaleTimeString();
  sideTail.textContent += `${now} ${line}\n`;

  const lines = sideTail.textContent.split("\n");
  if (lines.length > 18) {
    sideTail.textContent = `${lines.slice(lines.length - 18).join("\n")}`;
  }
}

function inferResponseText(routeResponse) {
  const workerResponse = routeResponse?.worker_response;
  const workerText = workerResponse?.details?.result?.text;
  if (typeof workerText === "string" && workerText.trim()) {
    return workerText;
  }

  const confidence = Number(routeResponse?.confidence ?? 0).toFixed(3);
  const topK = Array.isArray(routeResponse?.top_k_models) ? routeResponse.top_k_models.join(", ") : "n/a";
  const source = routeResponse?.source || "unknown";
  return `source: ${source}\nconfidence: ${confidence}\ntop_k: ${topK}`;
}

async function submitRouteQuery(text) {
  const imageUrl = imageHints.imageUrl || null;
  const imagePath = imageHints.imagePath || null;
  const hasImage = Boolean(imageUrl || imagePath);

  const response = await fetch("/route", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      text,
      has_image: hasImage,
      image_url: imageUrl,
      image_path: imagePath
    })
  });

  if (!response.ok) {
    throw new Error(`route request failed: http_${response.status}`);
  }

  return response.json();
}

function setGauge(needleEl, valueEl, percent, label) {
  const safePercent = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null;

  if (safePercent === null) {
    if (needleEl) {
      needleEl.style.transform = "translateX(-50%) rotate(-90deg)";
    }
    if (valueEl) {
      valueEl.textContent = "N/A";
    }
    return;
  }

  const degrees = -90 + (safePercent / 100) * 180;
  if (needleEl) {
    needleEl.style.transform = `translateX(-50%) rotate(${degrees}deg)`;
  }
  if (valueEl) {
    valueEl.textContent = `${label}: ${safePercent.toFixed(1)}%`;
  }
}

function setClockGauge(needleEl, valueEl, currentMhz, maxMhz, label) {
  const hasCurrent = Number.isFinite(currentMhz);
  const hasMax = Number.isFinite(maxMhz) && maxMhz > 0;

  if (!hasCurrent || !hasMax) {
    if (needleEl) {
      needleEl.style.transform = "translateX(-50%) rotate(-90deg)";
      needleEl.classList.remove("overclock");
    }
    if (valueEl) {
      valueEl.textContent = `${label}: N/A`;
    }
    return null;
  }

  const ratio = (currentMhz / maxMhz) * 100;
  const clampedForNeedle = Math.max(0, Math.min(120, ratio));
  const degrees = -90 + (clampedForNeedle / 100) * 180;

  if (needleEl) {
    needleEl.style.transform = `translateX(-50%) rotate(${degrees}deg)`;
    needleEl.classList.toggle("overclock", ratio > 100);
  }

  if (valueEl) {
    valueEl.textContent = `${label}: ${(currentMhz / 1000).toFixed(2)} GHz`;
    valueEl.style.color = ratio > 100 ? "#ff5555" : "";
  }

  return {
    ratio,
    currentMhz,
    maxMhz,
  };
}

function updateMemorySummary(usedGb, totalGb) {
  if (!memoryStatInline) {
    return;
  }

  if (Number.isFinite(usedGb) && Number.isFinite(totalGb) && totalGb > 0) {
    memoryStatInline.textContent = `MEM ${usedGb.toFixed(1)} / ${totalGb.toFixed(1)} GB`;
  } else {
    memoryStatInline.textContent = "MEM N/A";
  }
}

function updateRedlineLabels(cpuGauge, gpuGauge) {
  if (scopeMemoryRedlineLabel) {
    if (cpuGauge) {
      const over = cpuGauge.ratio > 100 ? " REDLINE" : "";
      scopeMemoryRedlineLabel.textContent = `CPU MAX ${(cpuGauge.maxMhz / 1000).toFixed(2)} GHZ${over}`;
    } else {
      scopeMemoryRedlineLabel.textContent = "CPU MAX CLOCK";
    }
  }

  if (scopeGpuRedlineLabel) {
    if (gpuGauge) {
      const over = gpuGauge.ratio > 100 ? " REDLINE" : "";
      scopeGpuRedlineLabel.textContent = `GPU MAX ${(gpuGauge.maxMhz / 1000).toFixed(2)} GHZ${over}`;
    } else {
      scopeGpuRedlineLabel.textContent = "GPU MAX CLOCK";
    }
  }
}

function pushHistory(history, sample) {
  const bounded = Math.max(0, Math.min(100, sample));
  history.push(bounded);
  if (history.length > 56) {
    history.shift();
  }
}

function historyToPoints(history) {
  return history.map((value, index, arr) => {
    const x = arr.length <= 1 ? 0 : (index / (arr.length - 1)) * 1000;
    const y = 142 - (value / 100) * 98;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
}

function animatePulse(pulseEl, endpoint) {
  if (!pulseEl || !endpoint) {
    return;
  }

  const [x, y] = endpoint.split(",");
  pulseEl.setAttribute("cx", x);
  pulseEl.setAttribute("cy", y);
  pulseEl.classList.remove("active");
  void pulseEl.getBoundingClientRect();
  pulseEl.classList.add("active");
}

function renderScopeTrace(cpuClockRatio, gpuClockRatio) {
  if (!scopeLineMemoryBox || !scopeLineGpuBox) {
    return;
  }

  if (Number.isFinite(gpuClockRatio)) {
    pushHistory(telemetryState.scopeHistoryGpu, gpuClockRatio);
  }
  if (Number.isFinite(cpuClockRatio)) {
    pushHistory(telemetryState.scopeHistoryMemory, cpuClockRatio);
  }

  const gpuPoints = historyToPoints(telemetryState.scopeHistoryGpu);
  const memoryPoints = historyToPoints(telemetryState.scopeHistoryMemory);

  if (memoryPoints.length > 0) {
    scopeLineMemoryBox.setAttribute("points", memoryPoints.join(" "));
  }
  if (gpuPoints.length > 0) {
    scopeLineGpuBox.setAttribute("points", gpuPoints.join(" "));
  }

  animatePulse(scopePulseMemoryBox, memoryPoints[memoryPoints.length - 1]);
  animatePulse(scopePulseGpuBox, gpuPoints[gpuPoints.length - 1]);
}

async function pollTelemetrySnapshot() {
  try {
    const response = await fetch("/telemetry/snapshot");
    if (!response.ok) {
      throw new Error(`telemetry http_${response.status}`);
    }

    const payload = await response.json();
    const memoryUsedGb = Number(payload?.memory_used_gb);
    const memoryTotalGb = Number(payload?.memory_total_gb);
    const cpuClockMhz = Number(payload?.cpu_clock_mhz);
    const cpuClockMaxMhz = Number(payload?.cpu_clock_max_mhz);
    const gpuClockMhz = Number(payload?.gpu_clock_mhz);
    const gpuClockMaxMhz = Number(payload?.gpu_clock_max_mhz);
    const cpuGauge = setClockGauge(memoryNeedle, memoryValue, cpuClockMhz, cpuClockMaxMhz, "CPU");
    const gpuGauge = setClockGauge(gpuNeedle, gpuValue, gpuClockMhz, gpuClockMaxMhz, "GPU");
    updateMemorySummary(Number.isFinite(memoryUsedGb) ? memoryUsedGb : null, Number.isFinite(memoryTotalGb) ? memoryTotalGb : null);
    updateRedlineLabels(cpuGauge, gpuGauge);

    const hasUpdate = Boolean(payload?.ok) && (Boolean(cpuGauge) || Boolean(gpuGauge));

    if (hasUpdate) {
      renderScopeTrace(cpuGauge ? cpuGauge.ratio : NaN, gpuGauge ? gpuGauge.ratio : NaN);
    }

    if (!telemetryState.seenSuccess && payload?.ok) {
      appendTail(`[telemetry] source=${payload.source || "unknown"}`);
      telemetryState.seenSuccess = true;
    }
  } catch (error) {
    setGauge(memoryNeedle, memoryValue, null, "MEM");
    setGauge(gpuNeedle, gpuValue, null, "GPU");
  }
}

function updateUploadHintDisplay() {
  if (!uploadHintDisplay) {
    return;
  }

  if (imageHints.imageUrl) {
    uploadHintDisplay.textContent = `image_url: ${imageHints.imageUrl}`;
    if (activeHintChip) {
      activeHintChip.textContent = "hint: image_url";
    }
    return;
  }

  if (imageHints.imagePath) {
    uploadHintDisplay.textContent = `image_path: ${imageHints.imagePath}`;
    if (activeHintChip) {
      activeHintChip.textContent = "hint: image_path";
    }
    return;
  }

  uploadHintDisplay.textContent = "no image hint set";
  if (activeHintChip) {
    activeHintChip.textContent = "no hint";
  }
}

queryForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = (queryInput?.value || "").trim();
  if (!text) {
    return;
  }

  const startedAt = performance.now();
  const previousLabel = submitButton?.textContent || "Send It!";

  if (submitButton) {
    submitButton.disabled = true;
    submitButton.textContent = "sending...";
  }

  terminalResponse.textContent = "routing query...";
  terminalMeta.textContent = ">>model name returned query in<<";
  appendTail(`[query] query> ${text}`);
  appendTail("[router] POST /route");
  if (imageHints.imageUrl || imageHints.imagePath) {
    appendTail(`[query] image_hint=${imageHints.imageUrl || imageHints.imagePath}`);
  }

  try {
    const routeResponse = await submitRouteQuery(text);
    const elapsedMs = Math.round(performance.now() - startedAt);

    terminalResponse.textContent = inferResponseText(routeResponse);

    const model = routeResponse?.model || "unknown";
    const dispatchTarget = routeResponse?.dispatch_target || "none";
    terminalMeta.textContent = `>>${model} returned query in<< ${dispatchTarget}`;

    appendTail(`[router] selected=${model} confidence=${Number(routeResponse?.confidence ?? 0).toFixed(3)}`);
    appendTail(`[dispatch] target=${dispatchTarget} status=${routeResponse?.worker_status || "n/a"}`);
    appendTail(`[return] ${elapsedMs}ms`);

    if (routeResponse?.worker_response?.details?.result?.used_precision) {
      appendTail(`[worker] precision=${routeResponse.worker_response.details.result.used_precision}`);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown error";
    terminalResponse.textContent = `request failed\n${message}`;
    terminalMeta.textContent = ">>model name returned query in<<";
    appendTail(`[error] ${message}`);
  } finally {
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.textContent = previousLabel;
    }
    queryInput.value = "";
    queryInput.focus();
  }
});

uploadPlaceholder?.addEventListener("click", () => {
  if (uploadImageUrl) {
    uploadImageUrl.value = imageHints.imageUrl;
  }
  if (uploadImagePath) {
    uploadImagePath.value = imageHints.imagePath;
  }
  mockUploadDialog?.showModal();
});

saveUploadHints?.addEventListener("click", () => {
  imageHints.imageUrl = (uploadImageUrl?.value || "").trim();
  imageHints.imagePath = (uploadImagePath?.value || "").trim();
  updateUploadHintDisplay();
  appendTail("[upload] hint saved");
  mockUploadDialog?.close();
});

clearUploadHints?.addEventListener("click", () => {
  imageHints.imageUrl = "";
  imageHints.imagePath = "";
  if (uploadImageUrl) {
    uploadImageUrl.value = "";
  }
  if (uploadImagePath) {
    uploadImagePath.value = "";
  }
  updateUploadHintDisplay();
  appendTail("[upload] hint cleared");
});

closeMockUpload?.addEventListener("click", () => {
  mockUploadDialog?.close();
});

appendTail("[ui] terminal online");
updateUploadHintDisplay();
pollTelemetrySnapshot();
setInterval(pollTelemetrySnapshot, 3000);
