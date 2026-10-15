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

  // 開催場所の入力: 名称から学内施設・学外の場所の候補を出し、選んだ場所の位置を保存する
  function setupPlaceFields() {
    document.querySelectorAll(".js-place-field").forEach(function (field) {
      var input = field.querySelector("input[name=location]");
      var lat = field.querySelector("input[name=location_lat]");
      var lng = field.querySelector("input[name=location_lng]");
      var list = field.querySelector(".place-options");
      var selectedNote = field.querySelector(".js-place-selected");
      var results = [];
      var active = -1;
      var timer = null;
      var requestId = 0;
      // 候補から選んだ場所の名前。教室名などを書き足しても、この名前で始まる間は位置を保持する
      var chosenName = lat.value ? input.value : "";

      function showSelected(text) {
        selectedNote.textContent = text || "";
        selectedNote.hidden = !text;
      }

      function close() {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
        active = -1;
      }

      function highlight(index) {
        active = index;
        Array.from(list.children).forEach(function (li, i) {
          li.classList.toggle("is-active", i === index);
          li.setAttribute("aria-selected", i === index ? "true" : "false");
        });
        if (index >= 0) {
          input.setAttribute("aria-activedescendant", "place-option-" + index);
          list.children[index].scrollIntoView({ block: "nearest" });
        }
      }

      function render(data) {
        results = data.results || [];
        list.innerHTML = "";
        results.forEach(function (r, i) {
          var li = document.createElement("li");
          li.id = "place-option-" + i;
          li.setAttribute("role", "option");
          li.className = "place-option" + (r.campus ? " is-campus" : "");
          var tag = document.createElement("span");
          tag.className = "place-tag";
          tag.textContent = r.campus ? "学内" : "地図";
          var name = document.createElement("strong");
          name.textContent = r.name;
          var address = document.createElement("small");
          address.textContent = r.address || "";
          li.append(tag, name, address);
          li.addEventListener("mousedown", function (e) {
            e.preventDefault(); // 入力欄のフォーカスを外さずに選択する
            choose(i);
          });
          list.appendChild(li);
        });
        if (data.error) {
          var note = document.createElement("li");
          note.className = "place-note";
          note.textContent = "学外の場所の検索に失敗しました。名称を入力すれば、そのまま登録できます。";
          list.appendChild(note);
        } else if (results.length === 0) {
          var empty = document.createElement("li");
          empty.className = "place-note";
          empty.textContent = "候補が見つかりません。入力した名称のまま登録できます(地図は名称で検索して表示します)。";
          list.appendChild(empty);
        }
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        active = -1;
      }

      function choose(i) {
        var r = results[i];
        if (!r) return;
        input.value = r.name;
        chosenName = r.name;
        // 学内施設は施設の位置を使うので、緯度経度は送らない
        lat.value = r.campus ? "" : r.lat;
        lng.value = r.campus ? "" : r.lng;
        showSelected(r.campus
          ? "✓ 学内施設を選択しました。教室名などを書き足せます。"
          : "✓ 地図の位置: " + r.name + (r.address ? "(" + r.address + ")" : ""));
        close();
      }

      function search() {
        var q = input.value.trim();
        var id = ++requestId;
        fetch(field.dataset.searchUrl + "?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
          .then(function (res) { return res.ok ? res.json() : { results: [], error: true }; })
          .then(function (data) { if (id === requestId) render(data); })
          .catch(function () { if (id === requestId) render({ results: [], error: true }); });
      }

      input.addEventListener("input", function () {
        if (chosenName && input.value.indexOf(chosenName) !== 0) {
          chosenName = "";
          lat.value = "";
          lng.value = "";
          showSelected("");
        }
        clearTimeout(timer);
        timer = setTimeout(search, 300);
      });
      input.addEventListener("focus", function () {
        if (!input.value.trim()) search();
      });
      input.addEventListener("blur", close);
      input.addEventListener("keydown", function (e) {
        if (list.hidden) {
          if (e.key === "ArrowDown") search();
          return;
        }
        if (e.key === "ArrowDown") {
          e.preventDefault();
          highlight(Math.min(active + 1, results.length - 1));
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          highlight(Math.max(active - 1, 0));
        } else if (e.key === "Enter" && active >= 0) {
          e.preventDefault(); // フォームを送信せずに候補を選ぶ
          choose(active);
        } else if (e.key === "Escape") {
          close();
        }
      });
      if (lat.value) showSelected("✓ 候補から選んだ場所の位置を地図に表示します。");
    });
  }

  // 管理画面: キャンパスマップをクリックして施設のピン位置を設定する
  function setupPinEditor() {
    var editor = document.querySelector(".js-pin-editor");
    if (!editor) return;
    var map = editor.querySelector(".js-pin-map");
    var form = editor.querySelector(".js-pin-form");
    var hint = editor.querySelector(".js-pin-hint");
    var width = Number(editor.dataset.mapWidth);
    var height = Number(editor.dataset.mapHeight);
    var selected = form.elements.name.value;

    function pinFor(name) {
      return Array.from(map.querySelectorAll(".campus-pin")).find(function (p) { return p.dataset.name === name; });
    }

    function select(name) {
      selected = name;
      form.elements.name.value = name;
      form.elements.x.value = "";
      form.elements.y.value = "";
      form.querySelector("button:not([name])").disabled = true;
      form.elements.reset.disabled = !name;
      editor.querySelectorAll(".js-pin-choice").forEach(function (b) {
        b.classList.toggle("is-selected", b.dataset.name === name);
      });
      map.querySelectorAll(".campus-pin").forEach(function (p) {
        p.classList.toggle("is-active", p.dataset.name === name);
      });
      hint.textContent = name ? "「" + name + "」の建物を地図上でクリックしてください。" : "左の一覧から施設を選んでください。";
    }

    editor.querySelectorAll(".js-pin-choice").forEach(function (b) {
      b.addEventListener("click", function () { select(b.dataset.name); });
    });

    map.addEventListener("click", function (e) {
      if (!selected) return;
      var rect = map.getBoundingClientRect();
      var px = (e.clientX - rect.left) / rect.width;
      var py = (e.clientY - rect.top) / rect.height;
      var pin = pinFor(selected);
      if (!pin) {
        pin = document.createElement("span");
        pin.className = "campus-pin is-active";
        pin.dataset.name = selected;
        var label = document.createElement("span");
        label.className = "campus-pin-label";
        label.textContent = selected;
        pin.appendChild(label);
        map.appendChild(pin);
      }
      pin.style.left = (px * 100) + "%";
      pin.style.top = (py * 100) + "%";
      form.elements.x.value = Math.round(px * width);
      form.elements.y.value = Math.round(py * height);
      form.querySelector("button:not([name])").disabled = false;
      hint.textContent = "位置を確認して「この位置で保存」を押してください。";
    });

    select(selected);
  }

  pollCounts();
  setupRegistrationFields();
  setupPlaceFields();
  setupPinEditor();
})();
