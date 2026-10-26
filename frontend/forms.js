(function () {
  "use strict";
  var B = window.BF, seq = 0;
  B.field = function (key, label, type, extra) {
    var f = { key: key, label: label, type: type || "text" };
    Object.keys(extra || {}).forEach(function (k) { f[k] = extra[k]; }); return f;
  };
  B.form = function (fields, context, values) {
    var form = B.el("form", { novalidate: true, autocomplete: "off" });
    var errors = B.el("div", { class: "errors", role: "alert", tabindex: "-1" });
    form.appendChild(errors);
    var readers = [], controls = {}, submittedControls = {}, repeatGroups = [];
    function build(f, parent, supplied, path) {
      var id = "field-" + (++seq), v = supplied;
      if (v === undefined) v = typeof f.value === "function" ? f.value(context) : f.value;
      var wrap, input, read;
      if (f.type === "hidden") return function () { return v === undefined ? null : v; };
      if (f.type === "object") {
        wrap = B.el("fieldset", {}, [B.text("legend", f.label)]);
        if (f.hint) wrap.appendChild(B.text("p", f.hint, "hint"));
        var sub = f.fields.map(function (child) { return [child.key, build(child, wrap, v ? v[child.key] : undefined, path + "." + child.key)]; });
        read = function () { var o = {}; sub.forEach(function (p) { o[p[0]] = p[1](); }); return o; };
      } else if (f.type === "repeat") {
        wrap = B.el("fieldset", {}, [B.text("legend", f.label)]);
        if (f.hint) wrap.appendChild(B.text("p", f.hint, "hint"));
        var rows = [], list = B.el("div", {}), rowSerial = 0;
        repeatGroups.push({ path: path, rows: rows });
        function add(value) {
          var row = B.el("div", { class: "repeat-row" });
          var prefix = path + "." + rowSerial++;
          var rowReads = f.fields.map(function (child) { return [child.key, build(child, row, value ? value[child.key] : undefined, prefix + "." + child.key)]; });
          var item = { row: row, prefix: prefix, read: function () { var o = {}; rowReads.forEach(function (p) { o[p[0]] = p[1](); }); return o; } };
          row.appendChild(B.button("Remove " + (f.itemLabel || "item"), function () {
            Object.keys(controls).forEach(function (key) { if (key.indexOf(prefix + ".") === 0) delete controls[key]; });
            rows.splice(rows.indexOf(item), 1); row.remove();
            var next = wrap.querySelector("button"); if (next) next.focus();
          }, "small"));
          rows.push(item); list.appendChild(row);
        }
        wrap.appendChild(list); (v || (f.empty ? [] : [{}])).forEach(add);
        wrap.appendChild(B.button("Add " + (f.itemLabel || "item"), function () { add({}); var last = list.lastChild.querySelector("input,select,textarea"); if (last) last.focus(); }, "small"));
        read = function () { return rows.map(function (r) { return r.read(); }); };
      } else if (f.type === "known") {
        wrap = B.el("div", { class: "field" }, [B.el("label", { for: id, text: f.label })]);
        input = B.el("input", { id: id, type: "text", value: v && v.known ? v.value || "" : "", maxlength: 2000 });
        var state = B.el("select", { "aria-label": f.label + " knowledge" }, [
          B.el("option", { value: "unknown", text: "Unknown" }), B.el("option", { value: "known", text: "Known value" }), B.el("option", { value: "none", text: "Known absent" })
        ]);
        state.value = v ? (v.known ? (v.value === null ? "none" : "known") : "unknown") : "unknown";
        function updateKnown() { input.disabled = state.value !== "known"; input.required = state.value === "known"; }
        state.addEventListener("change", updateKnown); updateKnown();
        wrap.appendChild(B.el("span", { class: "known" }, [input, state]));
        read = function () { return { known: state.value !== "unknown", value: state.value === "known" ? input.value : null }; };
      } else if (f.type === "multi") {
        wrap = B.el("fieldset", {}, [B.text("legend", f.label)]);
        var checks = (typeof f.options === "function" ? f.options(context) : f.options || []).map(function (o) {
          var pair = Array.isArray(o) ? o : [o, B.human(o)], cb = B.el("input", { type: "checkbox", value: pair[0], checked: (v || []).indexOf(pair[0]) !== -1 });
          wrap.appendChild(B.el("label", { class: "check" }, [cb, B.text("span", pair[1])])); return cb;
        });
        read = function () {
          var selected = checks.filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });
          if (!f.optional && !selected.length) throw { field: path, message: "Select at least one " + f.label.toLowerCase() + "." };
          return selected;
        };
      } else {
        wrap = B.el("label", { class: f.type === "bool" ? "check" : "field", for: id });
        if (f.type !== "bool") wrap.appendChild(B.text("span", f.label + (f.optional ? " (optional)" : "")));
        if (f.type === "select" || f.type === "ref" || f.type === "scope") {
          input = B.el("select", { id: id });
          input.appendChild(B.el("option", { value: "", text: f.optional ? "None / not applicable" : "Choose…" }));
          var opts = typeof f.options === "function" ? f.options(context) : f.options || [];
          if (f.type === "scope") {
            var freshScope = B.uuid();
            opts = [[freshScope, "NEW work scope · " + freshScope.slice(0, 8)]].concat(opts);
            if (v === undefined || v === null) v = freshScope;
          }
          opts.forEach(function (o) { var p = Array.isArray(o) ? o : [o, B.human(o)]; input.appendChild(B.el("option", { value: p[0], text: p[1] })); });
          // Restored drafts cannot manufacture a disappeared/unauthorized option.
          if (v !== undefined && v !== null && !opts.some(function (o) { return String(Array.isArray(o) ? o[0] : o) === String(v); })) v = "";
        } else if (f.type === "textarea" || f.type === "lines") input = B.el("textarea", { id: id, maxlength: 2000, rows: 3 });
        else input = B.el("input", { id: id, type: f.type === "bool" ? "checkbox" : f.type === "password" ? "password" : f.type === "date" ? "datetime-local" : f.type === "integer" || f.type === "money" ? "number" : "text" });
        if (f.type === "integer") input.step = "1";
        if (f.type === "money") { input.step = "0.01"; input.inputMode = "decimal"; }
        if (f.min !== undefined) input.min = f.min;
        if (f.max !== undefined) input.max = f.max;
        if (f.type === "password") { input.autocomplete = f.autocomplete || "new-password"; input.maxLength = 256; input.minLength = f.minLength || 1; }
        if (f.type === "code") { input.autocomplete = "off"; input.spellcheck = false; input.minLength = 43; input.maxLength = 43; input.pattern = "[A-Za-z0-9_\\-]{43}"; }
        if (f.type === "text" || f.type === "lines") input.maxLength = 2000;
        if (f.type === "bool") input.checked = !!v;
        else if (v !== null && v !== undefined) {
          if (f.type === "money") input.value = (v / 100).toFixed(2);
          else if (f.type === "lines") input.value = v.join("\n");
          else if (f.type === "date") { var d = new Date(v); input.value = isNaN(d.getTime()) ? "" : new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
          else input.value = v;
        }
        if (!f.optional && f.type !== "bool") input.required = true;
        if (f.readonly) input.disabled = true;
        wrap.appendChild(input);
        if (f.type === "bool") wrap.appendChild(B.text("span", f.label));
        read = function () {
          if (f.type === "bool") return input.checked;
          var raw = input.value;
          if (f.type === "lines") return raw ? raw.split("\n").filter(function (s) { return s.trim(); }) : [];
          if (!raw && f.optional) return null;
          if (f.type === "money") {
            if (!/^-?\d+(?:\.\d{1,2})?$/.test(raw)) throw { field: path, message: "Enter an amount with at most two decimal places." };
            return Math.round(Number(raw) * 100);
          }
          if (f.type === "integer") return raw ? Number(raw) : null;
          if (f.type === "date") return raw ? new Date(raw).toISOString() : null;
          return raw;
        };
      }
      if (input) { controls[path] = input; input.name = path; }
      if (f.hint) {
        var hint = B.text("small", f.hint, "hint"); hint.id = id + "-hint"; wrap.appendChild(hint);
        if (input) input.setAttribute("aria-describedby", hint.id);
      }
      parent.appendChild(wrap); return read;
    }
    fields.forEach(function (f) { readers.push([f.key, build(f, form, values ? values[f.key] : undefined, "data." + f.key)]); });
    form.read = function () {
      var invalid = [];
      submittedControls = Object.assign({}, controls);
      repeatGroups.forEach(function (group) {
        Object.keys(submittedControls).forEach(function (key) { if (key.indexOf(group.path + ".") === 0) delete submittedControls[key]; });
        group.rows.forEach(function (item, index) {
          Object.keys(controls).forEach(function (key) {
            if (key.indexOf(item.prefix + ".") === 0) submittedControls[group.path + "." + index + key.slice(item.prefix.length)] = controls[key];
          });
        });
      });
      Object.keys(controls).forEach(function (k) { var n = controls[k]; n.removeAttribute("aria-invalid"); if (!n.disabled && !n.checkValidity()) invalid.push({ field: k, message: n.validationMessage }); });
      if (invalid.length) { form.showErrors(invalid, "Please correct the fields below. Nothing was recorded.", true); return null; }
      try { var o = {}; readers.forEach(function (p) { o[p[0]] = p[1](); }); return o; }
      catch (e) { form.showErrors([e], "Please check this value.", true); return null; }
    };
    form.showErrors = function (items, message, local) {
      Object.keys(controls).forEach(function (key) {
        var input = controls[key], oldError = input.getAttribute("data-error-description");
        if (oldError) {
          var remaining = (input.getAttribute("aria-describedby") || "").split(" ").filter(function (id) { return id && id !== oldError; }).join(" ");
          if (remaining) input.setAttribute("aria-describedby", remaining); else input.removeAttribute("aria-describedby");
          input.removeAttribute("data-error-description");
        }
      });
      B.clear(errors); errors.className = "errors callout error"; errors.appendChild(B.text("strong", message || "Not saved. Your entries remain here."));
      var ul = B.el("ul", {});
      (items || []).forEach(function (e) {
        var n = (local ? controls : submittedControls)[e.field], a = n ? B.link(e.message || "Check this value", "#" + n.id) : B.text("span", (e.field ? e.field + " · " : "") + (e.message || "Check this value"));
        if (n) { n.setAttribute("aria-invalid", "true"); a.addEventListener("click", function (event) { event.preventDefault(); n.focus(); }); }
        var item = B.el("li", { id: "validation-" + (++seq) }, a);
        if (n) { n.setAttribute("data-error-description", item.id); n.setAttribute("aria-describedby", ((n.getAttribute("aria-describedby") || "") + " " + item.id).trim()); }
        ul.appendChild(item);
      });
      errors.appendChild(ul); errors.focus();
    };
    form.clearSecrets = function () { form.querySelectorAll('input[type="password"],input[name="data.code"]').forEach(function (n) { n.value = ""; }); };
    form.safeSnapshot = function () {
      // Only categorical/numeric draft values survive reauthentication in memory.
      // Free text, contact values, notes, serials, passwords and codes never do.
      var safe = {}, safeTypes = ["integer", "money", "select", "multi", "bool", "date", "ref"];
      fields.forEach(function (field, i) {
        if (safeTypes.indexOf(field.type) === -1) return;
        try { safe[field.key] = readers[i][1](); } catch (_) { /* Invalid drafts are not carried over. */ }
      });
      return safe;
    };
    return form;
  };
  B.reviewValues = function (fields, data, context) {
    var pairs = [];
    fields.forEach(function (f) {
      var v = data[f.key]; if (f.type === "hidden" || f.type === "password" || f.type === "code") return;
      if (f.type === "object") pairs.push([f.label, B.reviewValues(f.fields, v || {}, context)]);
      else if (f.type === "repeat") pairs.push([f.label, B.el("div", {}, (v || []).map(function (r) { return B.el("div", { class: "panel inset" }, B.reviewValues(f.fields, r, context)); }))]);
      else if (f.type === "money") pairs.push([f.label, B.money(v, data.currency || "USD")]);
      else if (f.type === "date") pairs.push([f.label, B.date(v)]);
      else if (f.type === "known") pairs.push([f.label, B.known(v)]);
      else if (f.type === "bool") pairs.push([f.label, v ? "Yes" : "No"]);
      else if (Array.isArray(v)) {
        var choices = typeof f.options === "function" ? f.options(context || {}) : f.options || [];
        pairs.push([f.label, v.map(function (value) { var match = choices.find(function (o) { return Array.isArray(o) && o[0] === value; }); return match ? match[1] : B.human(value); }).join(", ") || "None"]);
      }
      else {
        var opts = typeof f.options === "function" ? f.options(context || {}) : f.options || [], found = opts.filter(function (p) { return Array.isArray(p) && String(p[0]) === String(v); })[0];
        pairs.push([f.label, found ? found[1] : v === null ? "None" : String(v)]);
      }
    }); return B.facts(pairs);
  };
}());
