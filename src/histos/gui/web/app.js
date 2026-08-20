function ready(fn) {
  if (window.pywebview) {
    fn();
  } else {
    window.addEventListener("pywebviewready", fn);
  }
}

ready(function () {
  var pickBtn = document.getElementById("pick-btn");
  var pathEl = document.getElementById("path");
  var outputEl = document.getElementById("output");

  pickBtn.addEventListener("click", function () {
    window.pywebview.api.pick_vault_folder().then(function (picked) {
      if (!picked.ok) {
        pathEl.textContent = "";
        outputEl.textContent = picked.error;
        return;
      }
      pathEl.textContent = picked.path;
      outputEl.textContent = "loading...";
      return window.pywebview.api.get_status(picked.path).then(function (status) {
        outputEl.textContent = JSON.stringify(status, null, 2);
      });
    });
  });
});
