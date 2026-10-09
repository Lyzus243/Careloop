/*
 * Careloop session: keeps the user signed in between visits.
 *
 * Login returns a short-lived access token (authToken, 60 min) and a refresh
 * token (refreshToken, 30 days). The refresh token is swapped for a new pair
 * whenever the access token runs out, and each swap restarts the 30 days, so
 * an active user is never asked to log in again.
 *
 *   clAuth.saveSession(data)       -> store tokens from a login/refresh response
 *   clAuth.clearSession()          -> forget the user on this device
 *   clAuth.installFetchRefresh(u)  -> dashboard: retry API calls that hit 401
 *                                     with a renewed token, else send the user to u
 *   await clAuth.resumeSession()   -> true if a saved session is still good
 */
(function () {
  if (window.clAuth) return;

  var ACCESS = 'authToken';
  var REFRESH = 'refreshToken';
  var nativeFetch = window.fetch.bind(window);
  var pending = null;

  function saveSession(data) {
    localStorage.setItem(ACCESS, data.access_token);
    if (data.refresh_token) localStorage.setItem(REFRESH, data.refresh_token);
  }

  function clearSession() {
    [ACCESS, REFRESH, 'cl_user', 'cl_customers'].forEach(function (k) { localStorage.removeItem(k); });
  }

  // Resolves 'ok', 'invalid' (the server refused the refresh token) or 'error'
  // (network or server trouble; the session may still be fine).
  function refresh() {
    if (pending) return pending;
    var token = localStorage.getItem(REFRESH);
    if (!token) return Promise.resolve('invalid');
    pending = nativeFetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token })
    }).then(function (r) {
      if (r.ok) return r.json().then(function (d) { saveSession(d); return 'ok'; });
      // Another tab may have used this refresh token first and stored the new pair.
      if (localStorage.getItem(REFRESH) !== token) return 'ok';
      return r.status === 401 ? 'invalid' : 'error';
    }, function () {
      return 'error';
    }).finally(function () {
      pending = null;
    });
    return pending;
  }

  function withToken(init, token) {
    var headers = new Headers(init && init.headers);
    headers.set('Authorization', 'Bearer ' + token);
    return Object.assign({}, init, { headers: headers });
  }

  function installFetchRefresh(loginUrl) {
    function signOut() {
      clearSession();
      window.location.href = loginUrl;
    }

    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : String((input && input.url) || input);
      var sent = new Headers(init && init.headers).get('Authorization');
      if (!sent || /\/api\/auth\/(login|refresh|logout)\b/.test(url)) return nativeFetch(input, init);

      return nativeFetch(input, init).then(function (res) {
        if (res.status !== 401) return res;
        var stored = localStorage.getItem(ACCESS);
        // A token newer than the one sent means another call or tab already refreshed.
        var renewed = stored && sent !== 'Bearer ' + stored ? Promise.resolve('ok') : refresh();
        return renewed.then(function (outcome) {
          if (outcome === 'invalid') signOut();
          if (outcome !== 'ok') return res;
          return nativeFetch(input, withToken(init, localStorage.getItem(ACCESS))).then(function (again) {
            if (again.status === 401) signOut();
            return again;
          });
        });
      });
    };
  }

  function resumeSession() {
    if (!localStorage.getItem(REFRESH)) return Promise.resolve(false);
    return refresh().then(function (outcome) {
      if (outcome === 'invalid') clearSession();
      return outcome === 'ok';
    });
  }

  window.clAuth = {
    saveSession: saveSession,
    clearSession: clearSession,
    installFetchRefresh: installFetchRefresh,
    resumeSession: resumeSession
  };
})();
