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

var PENDING_COLOR = "3";

ready(function () {
  var api = window.pywebview.api;
  var vaultPath = null;
  var currentCardId = null;

  var views = {
    picker: document.getElementById("view-picker"),
    overview: document.getElementById("view-overview"),
    detail: document.getElementById("view-detail"),
  };

  function showView(name) {
    Object.keys(views).forEach(function (key) {
      views[key].hidden = key !== name;
    });
    document.getElementById("header-actions").hidden = name === "picker";
  }

  function statusDot(color) {
    var dot = document.createElement("span");
    dot.className = "status-dot";
    dot.style.backgroundColor = STATUS_COLORS[color] || "#999";
    return dot;
  }

  function renderPendingGroup(group) {
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
      var btn = document.createElement("button");
      btn.className = "card-btn";

      var title = document.createElement("strong");
      title.textContent = card.id;
      btn.appendChild(title);

      if (card.description) {
        var desc = document.createElement("span");
        desc.textContent = card.description;
        btn.appendChild(desc);
      }

      btn.addEventListener("click", function () {
        openDetail(card.id);
      });
      li.appendChild(btn);
      list.appendChild(li);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderReadOnlyGroup(group) {
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
      row.className = "card-row";

      var title = document.createElement("strong");
      title.textContent = card.id;
      row.appendChild(title);

      if (card.description) {
        var desc = document.createElement("span");
        desc.textContent = card.description;
        row.appendChild(desc);
      }

      li.appendChild(row);
      list.appendChild(li);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function loadOverview() {
    api.get_status(vaultPath).then(function (res) {
      var groupsEl = document.getElementById("overview-groups");
      var errorEl = document.getElementById("overview-error");
      groupsEl.innerHTML = "";

      if (!res.ok) {
        errorEl.textContent = res.error;
        showView("overview");
        return;
      }
      errorEl.textContent = "";

      // The pending-review group always shows first and always renders, even empty --
      // it's the one section that needs the user's decision, everything else is FYI.
      var pending = res.data.groups.filter(function (g) { return g.color === PENDING_COLOR; })[0];
      var rest = res.data.groups.filter(function (g) { return g.color !== PENDING_COLOR; });

      groupsEl.appendChild(renderPendingGroup(pending));
      rest.forEach(function (group) {
        if (group.cards.length > 0) {
          groupsEl.appendChild(renderReadOnlyGroup(group));
        }
      });

      showView("overview");
    });
  }

  function openDetail(cardId) {
    currentCardId = cardId;
    document.getElementById("reject-form").hidden = true;
    document.getElementById("detail-actions").hidden = false;
    document.getElementById("reject-feedback").value = "";
    document.getElementById("detail-status").textContent = "";

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
