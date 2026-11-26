function ready(fn) {
  if (window.pywebview) {
    fn();
  } else {
    window.addEventListener("pywebviewready", fn);
  }
}

// Canvas color code -> CSS color, matching the legend Obsidian shows on the board itself
// (see docs/canvas-schema.md) so the two stay visually consistent.
var STATUS_COLORS = {
  "1": "#c62828", // Blocked -- red
  "2": "#e67700", // In progress -- orange
  "3": "#d4a300", // Proposal pending review -- yellow
  "4": "#2e7d32", // Approved -- green
  "5": "#0097a7", // Dependency change request -- cyan
  "6": "#7c4dff", // Backlog -- purple
};

var IN_PROGRESS_COLOR = "2";
var PENDING_COLOR = "3";
var APPROVED_COLOR = "4";

ready(function () {
  var api = window.pywebview.api;
  var vaultPath = null;
  var currentCardId = null;
  var currentGuideName = null;
  var cardEvalCache = {};

  var views = {
    picker: document.getElementById("view-picker"),
    overview: document.getElementById("view-overview"),
    detail: document.getElementById("view-detail"),
    guideDetail: document.getElementById("view-guide-detail"),
  };

  function showView(name) {
    Object.keys(views).forEach(function (key) {
      views[key].hidden = key !== name;
    });
    document.getElementById("header-actions").hidden = name === "picker";
  }

  function makeBadge(text, className) {
    var span = document.createElement("span");
    span.className = "badge " + className;
    span.textContent = text;
    return span;
  }

  function statusDot(color) {
    var dot = document.createElement("span");
    dot.className = "status-dot";
    dot.style.backgroundColor = STATUS_COLORS[color] || "#999";
    return dot;
  }

  function plainNumber(n) {
    return n === Math.trunc(n) ? String(Math.trunc(n)) : String(n);
  }

  function formatHours(hours) {
    var minutes = Math.max(5, Math.round(hours * 12) * 5);
    if (hours < 1 && minutes < 60) {
      return "~" + minutes + " min";
    }
    var rounded = hours < 10 ? Math.round(hours * 10) / 10 : Math.round(hours);
    if (rounded < 24) {
      return "~" + plainNumber(rounded) + " h";
    }
    return "~" + plainNumber(Math.round((hours / 24) * 10) / 10) + " d";
  }

  function formatEstimatePart(label, part) {
    if (!part) {
      return null;
    }
    if (typeof part.median === "number" && typeof part.p80 === "number") {
      return label + " " + formatHours(part.median) + " (\u2264" + formatHours(part.p80).slice(1) + ")";
    }
    if (typeof part.needed === "number") {
      return label + " no data (" + part.needed + " more needed)";
    }
    return null;
  }

  function formatCardEstimate(est) {
    if (!est) {
      return "";
    }
    var parts = [];
    var agentStr = formatEstimatePart("agent", est.agent);
    var reviewStr = formatEstimatePart("review", est.review);
    if (agentStr) {
      parts.push(agentStr);
    }
    if (reviewStr) {
      parts.push(reviewStr);
    }
    return parts.join(" \u00b7 ");
  }

  function renderEvalSummaryBox(evalResults, compact) {
    if (!evalResults || !Array.isArray(evalResults.checks)) {
      return null;
    }
    var box = document.createElement("div");
    var passed = evalResults.passed === true;
    box.className = (compact ? "card-evals " : "evals-box ") + (passed ? "card-evals-pass" : "card-evals-fail");

    var okCount = evalResults.checks.filter(function (c) { return c && c.passed; }).length;
    var total = evalResults.checks.length;
    var header = document.createElement("strong");
    var summary = "Evals: " + (passed ? "passed" : "failed") + " (" + okCount + "/" + total + ")";
    if (typeof evalResults.attempt === "number") {
      summary += ", attempt " + evalResults.attempt;
    }
    header.textContent = summary;
    box.appendChild(header);

    var failedChecks = evalResults.checks.filter(function (c) { return c && !c.passed; });
    if (!compact || failedChecks.length > 0) {
      var list = document.createElement("ul");
      var itemsToShow = compact ? failedChecks : evalResults.checks;
      itemsToShow.forEach(function (c) {
        var li = document.createElement("li");
        var text = (c.passed ? "PASS " : "FAIL ") + (c.name || "check");
        if (!c.passed && c.error) {
          text += ": " + c.error;
        } else if (c.detail) {
          text += " (" + c.detail + ")";
        }
        li.textContent = text;
        list.appendChild(li);
      });
      box.appendChild(list);
    }
    return box;
  }

  function appendCardBadges(container, card, criticalSet) {
    if (criticalSet[card.id]) {
      container.appendChild(makeBadge("Critical path", "badge-critical"));
    }
    if (card.tier) {
      var tierClass = (card.tier === "fast" || card.tier === "exec" || card.tier === "reasoning")
        ? "badge-tier-" + card.tier
        : "badge-worker";
      container.appendChild(makeBadge(card.tier, tierClass));
    }
    if (card.approved_model) {
      container.appendChild(makeBadge("model: " + card.approved_model, "badge-model"));
    } else if (card.proposed_model) {
      container.appendChild(makeBadge("proposed: " + card.proposed_model + "?", "badge-model-proposed"));
    }
    if (card.worker) {
      container.appendChild(makeBadge("worker: " + card.worker, "badge-worker"));
    }
    if (card.content_tampered) {
      container.appendChild(makeBadge("Edited outside Histos", "badge-tampered"));
    }
  }

  function showOverviewError(message) {
    document.getElementById("overview-error").textContent = message || "";
  }

  function makeCardActionButtons(card, groupColor) {
    var actions = document.createElement("div");
    actions.className = "card-actions";

    if (groupColor === IN_PROGRESS_COLOR && card.eval_results && card.eval_results.passed === false) {
      var retryBtn = document.createElement("button");
      retryBtn.className = "small-btn";
      retryBtn.textContent = "Retry";
      retryBtn.addEventListener("click", function () {
        api.retry(vaultPath, card.id).then(function (res) {
          if (!res.ok) {
            showOverviewError(res.error);
            return;
          }
          loadOverview();
        });
      });
      actions.appendChild(retryBtn);
    }

    if (card.content_tampered) {
      var acceptBtn = document.createElement("button");
      acceptBtn.className = "warn-btn";
      acceptBtn.textContent = "Accept edit";
      acceptBtn.addEventListener("click", function () {
        api.accept_edit(vaultPath, [card.id]).then(function (res) {
          if (!res.ok) {
            showOverviewError(res.error);
            return;
          }
          loadOverview();
        });
      });
      actions.appendChild(acceptBtn);
    }

    return actions;
  }

  function renderCardBody(container, card, groupColor, criticalSet) {
    var top = document.createElement("div");
    top.className = "card-top";

    var titleGroup = document.createElement("div");
    titleGroup.className = "card-title-group";
    var title = document.createElement("strong");
    title.textContent = card.id;
    titleGroup.appendChild(title);
    appendCardBadges(titleGroup, card, criticalSet);
    top.appendChild(titleGroup);
    container.appendChild(top);

    if (card.description) {
      var desc = document.createElement("span");
      desc.className = "card-desc";
      desc.textContent = card.description;
      container.appendChild(desc);
    }

    if (groupColor !== APPROVED_COLOR && card.estimate) {
      var estText = formatCardEstimate(card.estimate);
      if (estText) {
        var estEl = document.createElement("span");
        estEl.className = "card-meta";
        estEl.textContent = estText;
        container.appendChild(estEl);
      }
    }

    var evalBox = renderEvalSummaryBox(card.eval_results, true);
    if (evalBox) {
      container.appendChild(evalBox);
    }
  }

  function renderPendingGroup(group, criticalSet) {
    var wrap = document.createElement("div");
    wrap.className = "group group-pending";

    var heading = document.createElement("h3");
    heading.appendChild(statusDot(group.color));
    heading.appendChild(document.createTextNode(group.label + " (" + group.cards.length + ")"));
    wrap.appendChild(heading);

    if (group.cards.length === 0) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Nothing to review right now.";
      wrap.appendChild(empty);
      return wrap;
    }

    var list = document.createElement("ul");
    list.className = "card-list";
    group.cards.forEach(function (card) {
      var li = document.createElement("li");
      li.className = "pending-card-wrap";

      var btn = document.createElement("button");
      btn.className = "card-btn" + (criticalSet[card.id] ? " card-critical" : "");
      renderCardBody(btn, card, group.color, criticalSet);
      btn.addEventListener("click", function () {
        openDetail(card.id);
      });
      li.appendChild(btn);

      var actions = makeCardActionButtons(card, group.color);
      if (actions.childNodes.length > 0) {
        li.appendChild(actions);
      }
      list.appendChild(li);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderReadOnlyGroup(group, criticalSet) {
    var wrap = document.createElement("div");
    wrap.className = "group";

    var heading = document.createElement("h3");
    heading.appendChild(statusDot(group.color));
    heading.appendChild(document.createTextNode(group.label + " (" + group.cards.length + ")"));
    wrap.appendChild(heading);

    var list = document.createElement("ul");
    list.className = "card-list";
    group.cards.forEach(function (card) {
      var li = document.createElement("li");
      var row = document.createElement("div");
      row.className = "card-row" + (criticalSet[card.id] ? " card-critical" : "");
      renderCardBody(row, card, group.color, criticalSet);

      var actions = makeCardActionButtons(card, group.color);
      if (actions.childNodes.length > 0) {
        row.appendChild(actions);
      }

      li.appendChild(row);
      list.appendChild(li);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderCriticalPathStrip(criticalPath) {
    var strip = document.getElementById("critical-path-strip");
    strip.textContent = "";
    if (!criticalPath || !Array.isArray(criticalPath.cards) || criticalPath.cards.length === 0) {
      strip.hidden = true;
      return;
    }
    strip.hidden = false;

    var header = document.createElement("div");
    header.className = "critical-header";
    var label = document.createElement("strong");
    label.textContent = "Critical path";
    header.appendChild(label);

    var n = criticalPath.cards.length;
    var sizeText = n + " card" + (n === 1 ? "" : "s");
    var summary = document.createElement("span");
    summary.className = "hint-inline";
    if (typeof criticalPath.hours === "number") {
      summary.textContent = "(" + formatHours(criticalPath.hours) + " \u00b7 " + sizeText + ")";
    } else {
      summary.textContent = "(" + sizeText + ", no estimates yet)";
    }
    header.appendChild(summary);
    strip.appendChild(header);

    var chain = document.createElement("div");
    chain.className = "critical-chain";
    criticalPath.cards.forEach(function (cid, idx) {
      if (idx > 0) {
        var arrow = document.createElement("span");
        arrow.className = "critical-arrow";
        arrow.textContent = "\u2192";
        chain.appendChild(arrow);
      }
      var item = document.createElement("span");
      item.textContent = cid;
      chain.appendChild(item);
    });
    strip.appendChild(chain);
  }

  function renderModelsPanel(groups) {
    var panel = document.getElementById("models-panel");
    panel.textContent = "";

    var awaiting = [];
    groups.forEach(function (group) {
      if (group.color === APPROVED_COLOR) {
        return;
      }
      group.cards.forEach(function (card) {
        if (card.proposed_model && !card.approved_model) {
          awaiting.push(card);
        }
      });
    });

    if (awaiting.length === 0) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    var header = document.createElement("div");
    header.className = "panel-header";
    var h3 = document.createElement("h3");
    h3.textContent = "Approve proposed models (" + awaiting.length + ")";
    header.appendChild(h3);

    var inputsById = {};
    var batchBtn = document.createElement("button");
    batchBtn.className = "small-approve-btn";
    batchBtn.textContent = "Approve all (" + awaiting.length + ")";
    batchBtn.addEventListener("click", function () {
      var items = awaiting.map(function (c) {
        return { card_id: c.id, model: inputsById[c.id].value };
      });
      api.approve_models_batch(vaultPath, items).then(function (res) {
        if (!res.ok) {
          showOverviewError(res.error);
          return;
        }
        loadOverview();
      });
    });
    header.appendChild(batchBtn);
    panel.appendChild(header);

    var list = document.createElement("ul");
    list.className = "model-queue-list";
    awaiting.forEach(function (card) {
      var li = document.createElement("li");
      li.className = "model-queue-row";

      var info = document.createElement("div");
      info.className = "card-title-group";
      var idEl = document.createElement("strong");
      idEl.textContent = card.id;
      info.appendChild(idEl);
      if (card.tier) {
        info.appendChild(makeBadge(card.tier, "badge-tier-" + card.tier));
      }
      li.appendChild(info);

      var controls = document.createElement("div");
      controls.className = "model-queue-controls";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "model-input";
      input.value = card.proposed_model || "";
      input.setAttribute("aria-label", "Approved model for " + card.id);
      inputsById[card.id] = input;
      controls.appendChild(input);

      var approveBtn = document.createElement("button");
      approveBtn.className = "small-approve-btn";
      approveBtn.textContent = "Approve model";
      approveBtn.addEventListener("click", function () {
        api.approve_model(vaultPath, card.id, input.value).then(function (res) {
          if (!res.ok) {
            showOverviewError(res.error);
            return;
          }
          loadOverview();
        });
      });
      controls.appendChild(approveBtn);
      li.appendChild(controls);
      list.appendChild(li);
    });
    panel.appendChild(list);
  }

  function renderGuidesPanel(res) {
    var panel = document.getElementById("guides-panel");
    panel.textContent = "";
    if (!res) {
      panel.hidden = true;
      return;
    }
    if (!res.ok) {
      panel.hidden = false;
      var err = document.createElement("p");
      err.className = "error";
      err.textContent = "Could not load pending guides: " + res.error;
      panel.appendChild(err);
      return;
    }
    var guidesList = (res.data && res.data.guides) || [];
    if (guidesList.length === 0) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    var header = document.createElement("div");
    header.className = "panel-header";
    var h3 = document.createElement("h3");
    h3.textContent = "Guides pending review (" + guidesList.length + ")";
    header.appendChild(h3);
    panel.appendChild(header);

    var list = document.createElement("ul");
    list.className = "card-list";
    guidesList.forEach(function (guide) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.className = "card-btn";

      var top = document.createElement("div");
      top.className = "card-top";
      var title = document.createElement("strong");
      title.textContent = guide.name;
      top.appendChild(title);
      btn.appendChild(top);

      var evalBox = renderEvalSummaryBox(guide.eval_results, true);
      if (evalBox) {
        btn.appendChild(evalBox);
      }

      btn.addEventListener("click", function () {
        openGuideDetail(guide);
      });
      li.appendChild(btn);
      list.appendChild(li);
    });
    panel.appendChild(list);
  }

  function renderPlanPanel(res) {
    var panel = document.getElementById("plan-panel");
    panel.textContent = "";
    if (!res) {
      panel.hidden = true;
      return;
    }
    if (!res.ok) {
      panel.hidden = false;
      var err = document.createElement("p");
      err.className = "error";
      err.textContent = "Pending plan error: " + res.error;
      panel.appendChild(err);
      return;
    }
    if (!res.data || !res.data.plan) {
      panel.hidden = true;
      return;
    }
    var plan = res.data.plan;
    panel.hidden = false;

    var header = document.createElement("div");
    header.className = "panel-header";
    var h3 = document.createElement("h3");
    h3.textContent = "Pending plan (plan.yaml) \u2014 " + plan.cards.length + " cards";
    header.appendChild(h3);
    panel.appendChild(header);

    var summary = document.createElement("p");
    summary.textContent = plan.summary;
    panel.appendChild(summary);

    if (Array.isArray(plan.errors) && plan.errors.length > 0) {
      var errList = document.createElement("ul");
      errList.className = "error-list";
      plan.errors.forEach(function (err) {
        var li = document.createElement("li");
        li.textContent = err;
        errList.appendChild(li);
      });
      panel.appendChild(errList);
    }

    if (Array.isArray(plan.warnings) && plan.warnings.length > 0) {
      var warnList = document.createElement("ul");
      warnList.className = "warning-list";
      plan.warnings.forEach(function (w) {
        var li = document.createElement("li");
        li.textContent = w;
        warnList.appendChild(li);
      });
      panel.appendChild(warnList);
    }

    var table = document.createElement("table");
    table.className = "plan-table";
    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    ["ID", "Title", "Depends on", "Done when"].forEach(function (col) {
      var th = document.createElement("th");
      th.textContent = col;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    plan.cards.forEach(function (c) {
      var tr = document.createElement("tr");
      var deps = Array.isArray(c.depends_on) && c.depends_on.length > 0 ? c.depends_on.join(", ") : "-";
      [c.id, c.title, deps, c.done_when].forEach(function (val) {
        var td = document.createElement("td");
        td.textContent = val || "";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    panel.appendChild(table);

    if (Array.isArray(plan.levels) && plan.levels.length > 0) {
      var levelsDiv = document.createElement("div");
      levelsDiv.className = "plan-levels";
      plan.levels.forEach(function (lvl, idx) {
        var row = document.createElement("div");
        row.textContent = "Level " + (idx + 1) + ": " + lvl.join(", ");
        levelsDiv.appendChild(row);
      });
      panel.appendChild(levelsDiv);
    }

    if (plan.raw_yaml) {
      var details = document.createElement("details");
      details.className = "plan-raw-toggle";
      var sumEl = document.createElement("summary");
      sumEl.textContent = "Show raw plan.yaml";
      details.appendChild(sumEl);
      var pre = document.createElement("pre");
      pre.className = "text-block";
      pre.textContent = plan.raw_yaml;
      details.appendChild(pre);
      panel.appendChild(details);
    }

    var actions = document.createElement("div");
    actions.className = "actions";
    var approveBtn = document.createElement("button");
    approveBtn.className = "small-approve-btn";
    approveBtn.textContent = "Approve plan";
    approveBtn.disabled = Array.isArray(plan.errors) && plan.errors.length > 0;
    approveBtn.addEventListener("click", function () {
      api.approve_plan(vaultPath).then(function (r) {
        if (!r.ok) {
          showOverviewError(r.error);
          return;
        }
        loadOverview();
      });
    });

    var rejectBtn = document.createElement("button");
    rejectBtn.className = "small-reject-btn";
    rejectBtn.textContent = "Reject plan";
    actions.appendChild(approveBtn);
    actions.appendChild(rejectBtn);
    panel.appendChild(actions);

    var rejectForm = document.createElement("div");
    rejectForm.id = "plan-reject-form";
    rejectForm.hidden = true;
    var fbLabel = document.createElement("label");
    fbLabel.setAttribute("for", "plan-reject-feedback");
    fbLabel.textContent = "Why are you rejecting this plan? (optional)";
    var fbInput = document.createElement("textarea");
    fbInput.id = "plan-reject-feedback";
    fbInput.rows = 3;
    var formActions = document.createElement("div");
    formActions.className = "actions";
    var confirmBtn = document.createElement("button");
    confirmBtn.className = "small-reject-btn";
    confirmBtn.textContent = "Confirm reject";
    var cancelBtn = document.createElement("button");
    cancelBtn.className = "link-btn";
    cancelBtn.textContent = "Cancel";
    formActions.appendChild(confirmBtn);
    formActions.appendChild(cancelBtn);
    rejectForm.appendChild(fbLabel);
    rejectForm.appendChild(fbInput);
    rejectForm.appendChild(formActions);
    panel.appendChild(rejectForm);

    rejectBtn.addEventListener("click", function () {
      rejectForm.hidden = false;
      actions.hidden = true;
    });
    cancelBtn.addEventListener("click", function () {
      rejectForm.hidden = true;
      actions.hidden = false;
    });
    confirmBtn.addEventListener("click", function () {
      api.reject_plan(vaultPath, fbInput.value).then(function (r) {
        if (!r.ok) {
          showOverviewError(r.error);
          return;
        }
        loadOverview();
      });
    });
  }

  function updatePauseState(paused) {
    document.getElementById("pause-banner").hidden = !paused;
    var btn = document.getElementById("pause-btn");
    btn.disabled = !!paused;
    btn.textContent = paused ? "Paused" : "Pause reviewing";
  }

  function loadOverview() {
    document.getElementById("overview-path").textContent = vaultPath;

    api.is_vault(vaultPath).then(function (checkRes) {
      var newProjectEl = document.getElementById("overview-new-project");
      var contentEl = document.getElementById("overview-content");

      if (checkRes.ok && !checkRes.is_vault) {
        newProjectEl.hidden = false;
        contentEl.hidden = true;
        document.getElementById("init-error").textContent = "";
        showView("overview");
        return;
      }

      newProjectEl.hidden = true;
      contentEl.hidden = false;

      Promise.all([
        api.get_status(vaultPath),
        api.get_pending_guides(vaultPath),
        api.get_pending_plan(vaultPath),
      ]).then(function (results) {
        var res = results[0];
        var guidesRes = results[1];
        var planRes = results[2];

        var groupsEl = document.getElementById("overview-groups");
        var errorEl = document.getElementById("overview-error");
        var hintEl = document.getElementById("getting-started-hint");
        groupsEl.textContent = "";
        cardEvalCache = {};

        if (!res.ok) {
          errorEl.textContent = res.error;
          hintEl.hidden = true;
          updatePauseState(false);
          renderCriticalPathStrip(null);
          renderModelsPanel([]);
          renderGuidesPanel(null);
          renderPlanPanel(null);
          showView("overview");
          return;
        }
        errorEl.textContent = "";

        updatePauseState(res.data.paused);
        renderCriticalPathStrip(res.data.critical_path);

        var criticalSet = {};
        if (res.data.critical_path && Array.isArray(res.data.critical_path.cards)) {
          res.data.critical_path.cards.forEach(function (cid) {
            criticalSet[cid] = true;
          });
        }

        res.data.groups.forEach(function (group) {
          group.cards.forEach(function (card) {
            cardEvalCache[card.id] = card.eval_results || null;
          });
        });

        renderPlanPanel(planRes);
        renderGuidesPanel(guidesRes);
        renderModelsPanel(res.data.groups);

        // The pending-review group always shows first and always renders, even empty --
        // it's the one section that needs the user's decision, everything else is FYI.
        var pending = res.data.groups.filter(function (g) { return g.color === PENDING_COLOR; })[0];
        var rest = res.data.groups.filter(function (g) { return g.color !== PENDING_COLOR; });

        var totalCards = res.data.groups.reduce(function (sum, g) { return sum + g.cards.length; }, 0);
        hintEl.hidden = totalCards > 0;

        groupsEl.appendChild(renderPendingGroup(pending, criticalSet));
        rest.forEach(function (group) {
          if (group.cards.length > 0) {
            groupsEl.appendChild(renderReadOnlyGroup(group, criticalSet));
          }
        });

        showView("overview");
      });
    });
  }

  function openDetail(cardId) {
    currentCardId = cardId;
    document.getElementById("reject-form").hidden = true;
    document.getElementById("detail-actions").hidden = false;
    document.getElementById("reject-feedback").value = "";
    document.getElementById("detail-status").textContent = "";

    var evalsEl = document.getElementById("detail-evals");
    evalsEl.textContent = "";
    var renderedEvals = renderEvalSummaryBox(cardEvalCache[cardId], false);
    if (renderedEvals) {
      evalsEl.className = renderedEvals.className;
      while (renderedEvals.firstChild) {
        evalsEl.appendChild(renderedEvals.firstChild);
      }
      evalsEl.hidden = false;
    } else {
      evalsEl.hidden = true;
    }

    api.get_diff(vaultPath, cardId).then(function (res) {
      if (!res.ok) {
        document.getElementById("detail-status").textContent = res.error;
        return;
      }
      document.getElementById("detail-title").textContent = cardId;
      document.getElementById("detail-current").textContent =
        res.data.current_body || "This is a new page -- nothing has been saved for it yet.";
      document.getElementById("detail-proposed").textContent = res.data.proposed_body;
      showView("detail");
    });
  }

  function openGuideDetail(guide) {
    currentGuideName = guide.name;
    document.getElementById("guide-reject-form").hidden = true;
    document.getElementById("guide-detail-actions").hidden = false;
    document.getElementById("guide-reject-feedback").value = "";
    document.getElementById("guide-detail-status").textContent = "";
    document.getElementById("guide-detail-title").textContent = guide.name;
    document.getElementById("guide-detail-current").textContent =
      guide.current || "This guide doesn't exist yet -- approving will create it.";
    document.getElementById("guide-detail-proposed").textContent = guide.proposed;

    var evalsEl = document.getElementById("guide-detail-evals");
    evalsEl.textContent = "";
    var renderedEvals = renderEvalSummaryBox(guide.eval_results, false);
    if (renderedEvals) {
      evalsEl.className = renderedEvals.className;
      while (renderedEvals.firstChild) {
        evalsEl.appendChild(renderedEvals.firstChild);
      }
      evalsEl.hidden = false;
    } else {
      evalsEl.hidden = true;
    }
    showView("guideDetail");
  }

  document.getElementById("pick-btn").addEventListener("click", function () {
    api.pick_vault_folder().then(function (res) {
      if (!res.ok) {
        document.getElementById("picker-error").textContent = res.error;
        return;
      }
      document.getElementById("picker-error").textContent = "";
      vaultPath = res.path;
      loadOverview();
    });
  });

  document.getElementById("change-folder-btn").addEventListener("click", function () {
    showView("picker");
  });

  document.getElementById("pause-btn").addEventListener("click", function () {
    api.pause(vaultPath).then(function (res) {
      if (!res.ok) {
        showOverviewError(res.error);
        return;
      }
      loadOverview();
    });
  });

  document.getElementById("init-vault-btn").addEventListener("click", function () {
    api.init_vault(vaultPath).then(function (res) {
      if (!res.ok) {
        document.getElementById("init-error").textContent = res.error;
        return;
      }
      loadOverview();
    });
  });

  document.getElementById("open-obsidian-btn").addEventListener("click", function () {
    api.open_in_obsidian(vaultPath);
  });

  document.getElementById("open-folder-btn").addEventListener("click", function () {
    api.open_folder(vaultPath);
  });

  document.getElementById("back-btn").addEventListener("click", function () {
    loadOverview();
  });

  document.getElementById("approve-btn").addEventListener("click", function () {
    api.approve(vaultPath, currentCardId).then(function (res) {
      if (!res.ok) {
        document.getElementById("detail-status").textContent = res.error;
        return;
      }
      loadOverview();
    });
  });

  document.getElementById("reject-btn").addEventListener("click", function () {
    document.getElementById("reject-form").hidden = false;
    document.getElementById("detail-actions").hidden = true;
  });

  document.getElementById("reject-cancel-btn").addEventListener("click", function () {
    document.getElementById("reject-form").hidden = true;
    document.getElementById("detail-actions").hidden = false;
  });

  document.getElementById("reject-confirm-btn").addEventListener("click", function () {
    var feedback = document.getElementById("reject-feedback").value;
    api.reject(vaultPath, currentCardId, feedback).then(function (res) {
      if (!res.ok) {
        document.getElementById("detail-status").textContent = res.error;
        return;
      }
      loadOverview();
    });
  });

  document.getElementById("guide-back-btn").addEventListener("click", function () {
    loadOverview();
  });

  document.getElementById("guide-approve-btn").addEventListener("click", function () {
    api.approve_guide(vaultPath, currentGuideName).then(function (res) {
      if (!res.ok) {
        document.getElementById("guide-detail-status").textContent = res.error;
        return;
      }
      loadOverview();
    });
  });

  document.getElementById("guide-reject-btn").addEventListener("click", function () {
    document.getElementById("guide-reject-form").hidden = false;
    document.getElementById("guide-detail-actions").hidden = true;
  });

  document.getElementById("guide-reject-cancel-btn").addEventListener("click", function () {
    document.getElementById("guide-reject-form").hidden = true;
    document.getElementById("guide-detail-actions").hidden = false;
  });

  document.getElementById("guide-reject-confirm-btn").addEventListener("click", function () {
    var feedback = document.getElementById("guide-reject-feedback").value;
    api.reject_guide(vaultPath, currentGuideName, feedback).then(function (res) {
      if (!res.ok) {
        document.getElementById("guide-detail-status").textContent = res.error;
        return;
      }
      loadOverview();
    });
  });

  // Startup: resume the last vault if we have one, otherwise ask for a folder.
  api.get_last_vault().then(function (res) {
    if (res.ok && res.path) {
      vaultPath = res.path;
      loadOverview();
    } else {
      showView("picker");
    }
  });
});
