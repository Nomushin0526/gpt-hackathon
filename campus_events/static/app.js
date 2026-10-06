// 申込人数のリアルタイム更新と、イベント登録フォームの表示切り替え
(function () {
  "use strict";

  var POLL_INTERVAL_MS = 10000;

  function updateCapacity(id, info) {
    document.querySelectorAll('.js-capacity[data-event-id="' + id + '"]').forEach(function (el) {
      var registered = el.querySelector(".js-registered");
      if (registered) registered.textContent = info.registered;
      if (info.capacity) {
        var pct = Math.min(100, Math.floor((info.registered * 100) / info.capacity));
        var bar = el.querySelector(".js-bar");
        if (bar) bar.style.width = pct + "%";
        var left = el.querySelector(".js-left");
        if (left) left.textContent = info.full ? "満員" : "残り " + (info.capacity - info.registered) + " 名";
        el.classList.toggle("is-few", pct >= 80);
      }
      el.classList.toggle("is-full", info.full);
    });

    document.querySelectorAll('.js-full-badge[data-event-id="' + id + '"]').forEach(function (badge) {
      badge.textContent = info.full ? "満員" : "✍ 要申込";
      badge.classList.toggle("reg-full", info.full);
      badge.classList.toggle("reg-required", !info.full);
    });

    // 閲覧中に定員に達したら申込フォームを閉じる
    if (info.full) {
      document.querySelectorAll('.js-register-form[data-event-id="' + id + '"]').forEach(function (form) {
        var msg = document.createElement("p");
        msg.className = "full-message";
        msg.textContent = "定員に達したため、申込を締め切りました。";
        form.replaceWith(msg);
      });
    }
  }

  function pollCounts() {
    var ids = Array.from(new Set(
      Array.from(document.querySelectorAll(".js-capacity")).map(function (el) { return el.dataset.eventId; })
    ));
    if (ids.length === 0) return;

    function refresh() {
      if (document.hidden) return;
      fetch("/api/registration-counts?ids=" + ids.join(","), { headers: { Accept: "application/json" } })
        .then(function (res) { return res.ok ? res.json() : {}; })
        .then(function (data) {
          Object.keys(data).forEach(function (id) { updateCapacity(id, data[id]); });
        })
        .catch(function () { /* 通信エラー時は次回の更新を待つ */ });
    }

    setInterval(refresh, POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", refresh);
  }

  function setupRegistrationFields() {
    document.querySelectorAll(".js-registration-fields").forEach(function (fieldset) {
      var info = fieldset.querySelector("textarea[name=registration_info]");
      var mark = fieldset.querySelector(".js-info-required");
      function sync() {
        var checked = fieldset.querySelector("input[name=registration_mode]:checked");
        var external = checked && checked.value === "external";
        info.required = external;
        if (mark) mark.hidden = !external;
      }
      fieldset.addEventListener("change", sync);
      sync();
    });
  }

  pollCounts();
  setupRegistrationFields();
})();
