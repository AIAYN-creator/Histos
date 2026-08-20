function ready(fn) {
  if (window.pywebview) {
    fn();
  } else {
    window.addEventListener("pywebviewready", fn);
  }
}

ready(function () {
  var api = window.pywebview.api;
  var vaultPath = null;
  var currentCardId = null;

  var views = {
    picker: document.getElementById("view-picker"),
    list: document.getElementById("view-list"),
    detail: document.getElementById("view-detail"),
  };

  function showView(name) {
    Object.keys(views).forEach(function (key) {
      views[key].hidden = key !== name;
    });
    document.getElementById("change-folder-btn").hidden = name === "picker";
  }

  function loadReviewList() {
    api.get_pending_reviews(vaultPath).then(function (res) {
      var listEl = document.getElementById("list-items");
      var emptyEl = document.getElementById("list-empty");
      listEl.innerHTML = "";

      if (!res.ok) {
        emptyEl.hidden = false;
        emptyEl.textContent = res.error;
        showView("list");
        return;
      }

      var cards = res.data.cards;
      emptyEl.hidden = cards.length > 0;
      cards.forEach(function (card) {
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
        listEl.appendChild(li);
      });

      showView("list");
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
      loadReviewList();
    });
  });

  document.getElementById("change-folder-btn").addEventListener("click", function () {
    showView("picker");
  });

  document.getElementById("back-btn").addEventListener("click", function () {
    loadReviewList();
  });

  document.getElementById("approve-btn").addEventListener("click", function () {
    api.approve(vaultPath, currentCardId).then(function (res) {
      if (!res.ok) {
        document.getElementById("detail-status").textContent = res.error;
        return;
      }
      loadReviewList();
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
      loadReviewList();
    });
  });

  // Startup: resume the last vault if we have one, otherwise ask for a folder.
  api.get_last_vault().then(function (res) {
    if (res.ok && res.path) {
      vaultPath = res.path;
      loadReviewList();
    } else {
      showView("picker");
    }
  });
});
