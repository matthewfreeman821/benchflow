(function () {
  "use strict";
  var B = window.BF;
  B.session = null;
  B.request = async function (path, body, binary) {
    var write = body !== undefined, id = B.uuid();
    var headers = { Accept: "application/json", "X-Request-ID": id };
    if (write) {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = B.session ? B.session.csrf_token : "";
    }
    var response, controller = new AbortController(), timeout = setTimeout(function () { controller.abort(); }, 20000);
    try {
      response = await fetch("/api/v1" + path, {
        method: write ? "POST" : "GET", headers: headers, credentials: "same-origin",
        cache: "no-store", redirect: "error", signal: controller.signal, body: write ? JSON.stringify(body) : undefined
      });
    } catch (_) {
      var network = new Error(write ? "We could not confirm whether this was recorded. Check current history before retrying with the same operation ID." : "BenchFlow could not be reached. Check that the local host is running, then retry.");
      network.unknown = write; network.code = "NETWORK_UNAVAILABLE"; throw network;
    } finally { clearTimeout(timeout); }
    if (response.status === 204) return null;
    if (binary && response.ok) {
      if ((response.headers.get("Content-Type") || "").split(";")[0] !== "application/octet-stream") throw new Error("The server did not return a verified database download.");
      return response.blob();
    }
    var data;
    try { data = await response.json(); } catch (_) {
      var malformed = new Error("The server returned an unreadable result. Review current history before another action.");
      malformed.unknown = write; throw malformed;
    }
    if (!response.ok) {
      var info = data.error || {}, e = new Error(info.message || "Request could not be completed.");
      e.status = response.status; e.code = info.code; e.fields = info.field_errors || [];
      e.requestId = info.request_id || id; e.retryable = info.retryable;
      e.unknown = write && response.status >= 500 && !info.retryable;
      if (response.status === 401 && info.code !== "AUTH_INVALID") {
        B.session = null;
        if (B.expired) B.expired();
      }
      throw e;
    }
    return data;
  };
  B.getSession = async function () { B.session = await B.request("/session"); return B.session; };
  B.command = function (envelope) { return B.request("/commands", envelope); };
  B.downloadBackup = async function (id) {
    var blob = await B.request("/staff/backups/" + encodeURIComponent(id) + "/download", undefined, true);
    var url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = "benchflow.sqlite3"; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  };
  // Wire-shape checks only; the backend alone verifies files, authorization and integrity.
  B.backupSummary = function (view) {
    if (!view || typeof view.backup_id !== "string" || typeof view.created_at !== "string" ||
        !Number.isInteger(view.schema_version) || !Number.isInteger(view.size_bytes) ||
        !/^[0-9a-f]{64}$/.test(view.sha256) || view.integrity !== "verified_at_creation") throw new Error("Backup metadata is unavailable in the contracted shape. No availability is inferred.");
    return view;
  };
  B.backupDetail = function (view) {
    var m = view && view.manifest;
    if (!m || view.integrity !== "verified_now" || m.integrity !== "ok" ||
        m.format_version !== 1 || m.database_file !== "benchflow.sqlite3") throw new Error("A currently verified backup manifest was not supplied. Download and restore instructions are unavailable.");
    B.backupSummary(Object.assign({}, m, { integrity: "verified_at_creation" }));
    return m;
  };
  B.backupReceipt = function (view, operation) {
    if (!view || view.command_id !== operation || !view.backup || view.backup.backup_id !== operation || ["applied","replayed"].indexOf(view.status) === -1) throw new Error("Backup creation was not confirmed in the contracted response. Check the backup list and retry the same operation ID.");
    return B.backupSummary(view.backup);
  };
  B.query = function (path, args) {
    var q = Object.keys(args || {}).filter(function (k) { return args[k] !== null && args[k] !== undefined && args[k] !== ""; }).map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(args[k]); }).join("&");
    return path + (q ? "?" + q : "");
  };
}());
