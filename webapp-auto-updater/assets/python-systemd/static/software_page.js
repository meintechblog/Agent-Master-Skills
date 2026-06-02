/* software_page.js —
 *
 * Controller for the #system/software page.
 *
 * Responsibilities:
 *   - Build version / release-notes / progress-checklist / rollback /
 *     update-config cards inside #software-root
 *   - Drive the client state machine: idle -> confirming -> starting ->
 * running -> success|failed
 *   - Disable update buttons during active work via body.app-update-busy
 * plus CSS selector `.app-update-busy.app-update-action`
 *   - Render install-confirm + rollback-confirm dialogs via native
 * <dialog>.showModal with Cancel autofocus + ESC close (..)
 * - Deduplicate WS update_progress messages by monotonic sequence
 *   - On WS reconnect, re-fetch /api/version and /api/update/status to
 * detect a post-update stale tab and replay missed
 * history entries
 *   - Attach X-CSRF-Token header read from app_csrf cookie to all
 * mutating fetch calls (consumer)
 *   - Render release notes via window.renderSoftwareMarkdown (NEVER
 *     write HTML strings into the DOM)
 * - Reuse window.showToast for all user notifications
 *   - Show rollback button for 3_600_000 ms after a successful update
 * via sessionStorage.lastUpdateSuccessAt
 *
 * Public surface:
 *   window.softwarePage = {
 *     init(rootEl),
 *     onRouteEnter(),
 *     onRouteLeave(),
 *     handleWsMessage(data),
 *     onWsReconnect(),
 *     setState(next),
 *     getState()
 *   }
 */
(function () {
  'use strict';

  // ===== Constants =====

  // 19 canonical phases from updater_root/status_writer.py PHASES frozenset.
  // MUST match byte-for-byte (sorted) — acceptance test compares against
  // the Python source of truth.
  var PHASE_ORDER = [
    'trigger_received',
    'backup',
    'extract',
    'pip_install_dryrun',
    'pip_install',
    'compileall',
    'smoke_import',
    'config_dryrun',
    'pending_marker_written',
    'symlink_flipped',
    'restarting',
    'healthcheck',
    'done',
    'rollback_starting',
    'rollback_symlink_flipped',
    'rollback_restarting',
    'rollback_healthcheck',
    'rollback_done',
    'rollback_failed'
  ];

  // Mirrors __PKG_NAME__.updater.progress.IDLE_PHASES (reuse).
  var IDLE_PHASES = {
    'idle': 1,
    'done': 1,
    'rollback_done': 1,
    'rollback_failed': 1
  };

  var TERMINAL_DONE_PHASES = {
    'done': 1,
    'rollback_done': 1
  };

  // Rollback window: 1 hour fixed in ms.
  var ROLLBACK_WINDOW_MS = 3600000;

  // Human-readable German labels for the 19 phases in the progress checklist.
  var PHASE_LABELS = {
    'trigger_received': 'Trigger empfangen',
    'backup': 'Backup erstellt',
    'extract': 'Release entpackt',
    'pip_install_dryrun': 'pip install (dry run)',
    'pip_install': 'pip install',
    'compileall': 'Python compileall',
    'smoke_import': 'Smoke import',
    'config_dryrun': 'Config dry run',
    'pending_marker_written': 'Pending marker geschrieben',
    'symlink_flipped': 'Symlink umgelegt',
    'restarting': 'Service neu gestartet',
    'healthcheck': 'Healthcheck',
    'done': 'Fertig',
    'rollback_starting': 'Rollback gestartet',
    'rollback_symlink_flipped': 'Rollback: Symlink umgelegt',
    'rollback_restarting': 'Rollback: Service neu gestartet',
    'rollback_healthcheck': 'Rollback: Healthcheck',
    'rollback_done': 'Rollback abgeschlossen',
    'rollback_failed': 'Rollback fehlgeschlagen'
  };

  // ===== Module state =====

  var state = {
    phase: 'idle',                // 'idle' | 'confirming' | 'starting' | 'running' | 'success' | 'failed'
    version: null,
    commit: null,
    bootVersion: null,            // set on first /api/version; used to detect post-update stale tab
    bootCommit: null,
    latestVersion: null,
    latestCommit: null,
    releaseNotes: '',
    releaseUrl: '',
    releaseTag: '',
    lastUpdateSuccessAt: null, // ms since epoch; sessionStorage-backed
    lastSequenceSeen: -1, // dedupe on WS reconnect
    phaseElements: {},            // phase_name -> <li> element in the checklist
    rollbackTimerId: null
  };

  // Cached DOM references built lazily in init().
  var els = {
    root: null,
    versionLine: null,
    currentVersion: null,
    latestVersion: null,
    releaseNotes: null,
    checklist: null,
    installBtn: null,
    checkBtn: null,
    rollbackCard: null,
    rollbackBtn: null,
    cfgRepoInput: null,
    cfgIntervalInput: null,
    cfgAutoInstall: null,
    cfgSaveBtn: null,
    cfgCancelBtn: null,
    dlg: null,                    // install confirm dialog
    rollbackDlg: null             // rollback confirm dialog
  };

  //: originals snapshot for the dirty-tracking
  // Save/Cancel pair on the update-config card. Populated by
  // loadUpdateConfig() after the skeleton is mounted.
  var _cfgOriginals = {
    github_repo: '',
    check_interval_hours: 24,
    auto_install: false
  };

  var initialized = false;

  // ===== CSRF helper (consumer) =====

  function readCsrfCookie() {
    var match = document.cookie.match(/(?:^|;\s*)app_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function csrfHeaders() {
    var tok = readCsrfCookie();
    var h = { 'Content-Type': 'application/json' };
    if (tok) h['X-CSRF-Token'] = tok;
    return h;
  }

  // ===== State machine + body class toggling =====

  function setState(next) {
    state.phase = next;
    var busy = (next === 'starting' || next === 'running');
    if (document.body && document.body.classList) {
      document.body.classList.toggle('app-update-busy', busy);
    }
    renderStateDependentUI();
  }

  function renderStateDependentUI() {
    if (!initialized) return;
    // Install button label reflects state.
    if (els.installBtn) {
      if (state.phase === 'starting' || state.phase === 'running') {
        els.installBtn.textContent = 'Läuft...';
      } else {
        els.installBtn.textContent = 'Installieren';
      }
    }
  }

  // ===== Install confirm dialog =====

  function buildDialog() {
    var dlg = document.createElement('dialog');
    dlg.className = 'app-dialog';
    dlg.id = 'app-update-dialog';

    var title = document.createElement('h2');
    title.className = 'app-dialog-title';
    title.textContent = 'Update installieren?';
    dlg.appendChild(title);

    var versionLine = document.createElement('p');
    versionLine.className = 'app-dialog-version';
    dlg.appendChild(versionLine);

    var notesBox = document.createElement('div');
    notesBox.className = 'app-dialog-notes';
    dlg.appendChild(notesBox);

    var warn = document.createElement('p');
    warn.className = 'app-dialog-warn';
    warn.textContent = 'Der Update-Prozess startet den Service neu.';
    dlg.appendChild(warn);

    var actions = document.createElement('div');
    actions.className = 'app-dialog-actions';

    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'app-btn app-btn--cancel';
    cancelBtn.textContent = 'Abbrechen';
    cancelBtn.setAttribute('autofocus', 'autofocus'); // default focus
    cancelBtn.addEventListener('click', function () {
      dlg.close('cancel');
    });

    var okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'app-btn app-btn--primary';
    okBtn.textContent = 'Installieren';
    okBtn.addEventListener('click', function () {
      dlg.close('confirm');
    });

    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    dlg.appendChild(actions);

    dlg.addEventListener('close', function () {
      if (dlg.returnValue === 'confirm') {
        startInstall();
      }
    });

    document.body.appendChild(dlg);
    return {
      dlg: dlg,
      title: title,
      versionLine: versionLine,
      notesBox: notesBox
    };
  }

  function openInstallDialog() {
    if (!els.dlg) els.dlg = buildDialog();
    els.dlg.versionLine.textContent =
      'Version ' + (state.version || '?') + ' → ' + (state.latestVersion || '?');
    if (typeof window.renderSoftwareMarkdown === 'function') {
      window.renderSoftwareMarkdown(state.releaseNotes || '', els.dlg.notesBox);
    } else {
      els.dlg.notesBox.textContent = state.releaseNotes || '';
    }
    if (typeof els.dlg.dlg.showModal === 'function') {
      els.dlg.dlg.showModal();
    }
  }

  // ===== Rollback confirm dialog (also apply to destructive rollback) =====

  function buildRollbackDialog() {
    var dlg = document.createElement('dialog');
    dlg.className = 'app-dialog';
    dlg.id = 'app-rollback-dialog';

    var title = document.createElement('h2');
    title.className = 'app-dialog-title';
    title.textContent = 'Rollback zur vorherigen Version?';
    dlg.appendChild(title);

    var body = document.createElement('p');
    body.className = 'app-dialog-warn';
    body.textContent =
      'Der Service wird neu gestartet. Aktuelle Version wird durch die vorherige ersetzt.';
    dlg.appendChild(body);

    var actions = document.createElement('div');
    actions.className = 'app-dialog-actions';

    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'app-btn app-btn--cancel';
    cancelBtn.textContent = 'Abbrechen';
    cancelBtn.setAttribute('autofocus', 'autofocus'); // default focus
    cancelBtn.addEventListener('click', function () {
      dlg.close('cancel');
    });

    var okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'app-btn app-btn--danger';
    okBtn.textContent = 'Rollback';
    okBtn.addEventListener('click', function () {
      dlg.close('confirm');
    });

    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    dlg.appendChild(actions);

    document.body.appendChild(dlg);
    return dlg;
  }

  function confirmRollback() {
    return new Promise(function (resolve) {
      if (!els.rollbackDlg) els.rollbackDlg = buildRollbackDialog();
      var dlg = els.rollbackDlg;
      function onClose() {
        dlg.removeEventListener('close', onClose);
        resolve(dlg.returnValue === 'confirm');
      }
      dlg.addEventListener('close', onClose);
      if (typeof dlg.showModal === 'function') {
        dlg.showModal();
      } else {
        resolve(false);
      }
    });
  }

  // ===== Install flow — POST /api/update/start (consumer) =====

  function startInstall() {
    // Need a concrete commit to install. If the server could not resolve
    // origin/main (no git/network at last check), re-check instead of
    // POSTing a null target_sha (which the server rejects with 400).
    if (!state.latestCommit) {
      window.showToast('Ziel-Commit nicht aufgelöst — bitte erneut nach Updates suchen', 'warning');
      checkNow();
      return;
    }
    setState('starting');
    fetch('/api/update/start', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ target_sha: state.latestCommit }),
      credentials: 'same-origin'
    })
      .then(function (res) {
        if (res.status === 202) {
          setState('running');
          window.showToast('Update gestartet', 'success');
        } else if (res.status === 409) {
          setState('idle');
          window.showToast('Update läuft bereits', 'warning');
        } else if (res.status === 429) {
          var retry = res.headers.get('Retry-After') || '60';
          setState('idle');
          window.showToast('Bitte ' + retry + 's warten', 'warning');
        } else if (res.status === 422) {
          setState('idle');
          window.showToast(
            'Sicherheitstoken abgelaufen — Seite wird neu geladen',
            'error'
          );
          // Stale CSRF cookie triggers a reload.
          setTimeout(function () {
            location.reload();
          }, 1500);
        } else {
          setState('failed');
          window.showToast(
            'Update-Start fehlgeschlagen: HTTP ' + res.status,
            'error'
          );
        }
      })
      .catch(function (e) {
        setState('failed');
        window.showToast('Netzwerkfehler: ' + (e && e.message), 'error');
      });
  }

  // ===== WS update_progress handler =====

  function handleWsMessage(msg) {
    if (!msg || msg.type !== 'update_progress') return;
    var data = msg.data || {};

    if (typeof data.sequence === 'number') {
      if (data.sequence <= state.lastSequenceSeen) return; // dedupe
      state.lastSequenceSeen = data.sequence;
    }

    if (data.phase) {
      markPhase(
        data.phase,
        data.error
          ? 'failed'
          : IDLE_PHASES[data.phase]
          ? 'done'
          : 'running'
      );
    }

    if (data.phase === 'done') {
      state.lastUpdateSuccessAt = Date.now();
      try {
        sessionStorage.setItem(
          'lastUpdateSuccessAt',
          String(state.lastUpdateSuccessAt)
        );
      } catch (e) {
        /* storage disabled — non-fatal */
      }
      setState('success');
      window.showToast('Update erfolgreich abgeschlossen', 'success');
      showRollbackButton();
      scheduleRollbackWindowCheck();
    } else if (data.phase === 'rollback_done') {
      setState('success');
      window.showToast('Rollback erfolgreich', 'success');
      hideRollbackButton();
    } else if (data.phase === 'rollback_failed' || data.error) {
      setState('failed');
      window.showToast(
        'Update fehlgeschlagen: ' + (data.error || 'unbekannt'),
        'error'
      );
    } else if (state.phase !== 'running') {
      // First live phase message during a run -> make sure body is busy.
      setState('running');
    }
  }

  // ===== Progress checklist rendering =====

  function buildChecklist(parent) {
    var ul = document.createElement('ul');
    ul.className = 'app-update-progress';
    for (var i = 0; i < PHASE_ORDER.length; i++) {
      var name = PHASE_ORDER[i];
      var li = document.createElement('li');
      li.className = 'app-update-progress-item';
      li.setAttribute('data-phase', name);
      li.textContent = PHASE_LABELS[name] || name;
      ul.appendChild(li);
      state.phaseElements[name] = li;
    }
    parent.appendChild(ul);
    return ul;
  }

  function markPhase(name, classSuffix) {
    var li = state.phaseElements[name];
    if (!li) return;
    // Clear prior state classes on this row.
    li.classList.remove(
      'app-progress--running',
      'app-progress--done',
      'app-progress--failed'
    );
    li.classList.add('app-progress--' + classSuffix);
    // Any earlier phase that wasn't yet marked "done" gets promoted to done
    // when a later phase becomes running/done — the update engine only
    // advances forward.
    if (classSuffix === 'running' || classSuffix === 'done') {
      var idx = PHASE_ORDER.indexOf(name);
      for (var j = 0; j < idx; j++) {
        var prior = state.phaseElements[PHASE_ORDER[j]];
        if (
          prior &&
          !prior.classList.contains('app-progress--done') &&
          !prior.classList.contains('app-progress--failed')
        ) {
          prior.classList.remove('app-progress--running');
          prior.classList.add('app-progress--done');
        }
      }
    }
  }

  function resetChecklist() {
    for (var name in state.phaseElements) {
      if (Object.prototype.hasOwnProperty.call(state.phaseElements, name)) {
        var li = state.phaseElements[name];
        if (li) {
          li.classList.remove(
            'app-progress--running',
            'app-progress--done',
            'app-progress--failed'
          );
        }
      }
    }
  }

  // ===== WS reconnect + /api/version probe =====

  function onWsReconnect() {
    // 1. Fetch /api/version — reload if mismatch vs boot snapshot.
    fetch('/api/version', { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (v) {
        if (!v) return;
        if (state.bootVersion === null) {
          state.bootVersion = v.version || null;
          state.bootCommit = v.commit || null;
          state.version = v.version || null;
          state.commit = v.commit || null;
          renderVersionCard();
          return;
        }
        if (
          v.version !== state.bootVersion ||
          v.commit !== state.bootCommit
        ) {
          // Post-update stale-tab detection (mitigation).
          location.reload();
          return;
        }
      })
      .catch(function () {
        /* silent — /api/version may not exist yet during early boot */
      });

    // 2. Fetch /api/update/status — replay missed history entries.
    fetch('/api/update/status', { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (s) {
        if (!s) return;
        var history = s.history || [];
        for (var i = 0; i < history.length; i++) {
          var entry = history[i];
          var seq = typeof entry.sequence === 'number' ? entry.sequence : i;
          if (seq > state.lastSequenceSeen) {
            // Re-dispatch into our own handler so dedupe cursor advances.
            handleWsMessage({
              type: 'update_progress',
              data: {
                phase: entry.phase,
                at: entry.at,
                sequence: seq,
                error: entry.error || null
              }
            });
          }
        }
      })
      .catch(function () {
        /* silent */
      });
  }

  // ===== Rollback flow =====

  function showRollbackButton() {
    if (els.rollbackCard) {
      els.rollbackCard.classList.add('app-rollback-card--visible');
    }
  }

  function hideRollbackButton() {
    if (els.rollbackCard) {
      els.rollbackCard.classList.remove('app-rollback-card--visible');
    }
    if (state.rollbackTimerId) {
      clearTimeout(state.rollbackTimerId);
      state.rollbackTimerId = null;
    }
  }

  function scheduleRollbackWindowCheck() {
    if (!state.lastUpdateSuccessAt) return;
    var remaining =
      state.lastUpdateSuccessAt + ROLLBACK_WINDOW_MS - Date.now();
    if (remaining <= 0) {
      hideRollbackButton();
      return;
    }
    if (state.rollbackTimerId) clearTimeout(state.rollbackTimerId);
    state.rollbackTimerId = setTimeout(hideRollbackButton, remaining);
  }

  function restoreRollbackWindowFromStorage() {
    try {
      var raw = sessionStorage.getItem('lastUpdateSuccessAt');
      if (!raw) return;
      var ts = parseInt(raw, 10);
      if (!isFinite(ts)) return;
      state.lastUpdateSuccessAt = ts;
      if (Date.now() - ts <= ROLLBACK_WINDOW_MS) {
        showRollbackButton();
        scheduleRollbackWindowCheck();
      }
    } catch (e) {
      /* storage disabled */
    }
  }

  function rollback() {
    //: destructive action uses app-dialog, NOT native prompt.
    confirmRollback().then(function (ok) {
      if (!ok) return;
      fetch('/api/update/rollback', {
        method: 'POST',
        headers: csrfHeaders(),
        body: JSON.stringify({ target_sha: 'previous' }), // sentinel
        credentials: 'same-origin'
      })
        .then(function (res) {
          if (res.status === 202) {
            setState('running');
            window.showToast('Rollback gestartet', 'success');
          } else if (res.status === 409) {
            window.showToast('Update läuft bereits', 'warning');
          } else if (res.status === 429) {
            var retry = res.headers.get('Retry-After') || '60';
            window.showToast('Bitte ' + retry + 's warten', 'warning');
          } else if (res.status === 422) {
            window.showToast(
              'Sicherheitstoken abgelaufen — Seite wird neu geladen',
              'error'
            );
            setTimeout(function () {
              location.reload();
            }, 1500);
          } else {
            window.showToast(
              'Rollback fehlgeschlagen: HTTP ' + res.status,
              'error'
            );
          }
        })
        .catch(function (e) {
          window.showToast('Netzwerkfehler: ' + (e && e.message), 'error');
        });
    });
  }

  // ===== Check-now button =====

  function checkNow() {
    fetch('/api/update/check', {
      method: 'POST',
      headers: csrfHeaders(),
      credentials: 'same-origin'
    })
      .then(function (res) {
        if (!res.ok) {
          window.showToast('Check fehlgeschlagen: HTTP ' + res.status, 'error');
          return null;
        }
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        if (data.available) {
          window.showToast(
            'Neue Version verfügbar: ' + (data.latest_version || '?'),
            'success'
          );
        } else {
          window.showToast('Keine neue Version', 'info');
        }
        loadAvailableUpdate();
      })
      .catch(function (e) {
        window.showToast('Netzwerkfehler: ' + (e && e.message), 'error');
      });
  }

  // ===== /api/update/available fetch =====

  function loadAvailableUpdate() {
    fetch('/api/update/available', { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        // /api/update/available nests the release under `available_update`
        // (null when up to date). Fall back to flat keys defensively.
        var av = data.available_update || data;
        if (av && (av.latest_version || av.tag_name)) {
          state.latestVersion = av.latest_version || av.tag_name;
          state.latestCommit = av.latest_commit || null;
          state.releaseNotes = av.release_notes || '';
          state.releaseUrl = av.html_url || '';
          state.releaseTag = av.tag_name || '';
        } else {
          // Up to date — clear any stale "update available" state.
          state.latestVersion = null;
          state.latestCommit = null;
        }
        renderVersionCard();
        renderReleaseNotes();
      })
      .catch(function () {
        /* silent */
      });
  }

  // ===== DOM build: version / release-notes / progress / rollback / config cards =====

  function buildCard(titleText, className) {
    var card = document.createElement('div');
    card.className = 'app-software-card' + (className ? ' ' + className : '');
    var h = document.createElement('h2');
    h.className = 'app-software-card-title';
    h.textContent = titleText;
    card.appendChild(h);
    return card;
  }

  function buildVersionCard() {
    var card = buildCard('Aktuelle Version', 'app-software-version-card');
    var line = document.createElement('p');
    line.className = 'app-software-version-line';
    card.appendChild(line);
    els.versionLine = line;

    var btnRow = document.createElement('div');
    btnRow.className = 'app-btn-pair';

    var installBtn = document.createElement('button');
    installBtn.type = 'button';
    installBtn.className = 'app-btn app-btn--primary app-update-action';
    installBtn.textContent = 'Installieren';
    installBtn.addEventListener('click', function () {
      if (!state.latestVersion) {
        window.showToast('Keine neue Version verfügbar', 'info');
        return;
      }
      openInstallDialog();
    });
    els.installBtn = installBtn;

    var checkBtn = document.createElement('button');
    checkBtn.type = 'button';
    checkBtn.className = 'app-btn app-btn--sm app-update-action';
    checkBtn.textContent = 'Jetzt prüfen';
    checkBtn.addEventListener('click', checkNow);
    els.checkBtn = checkBtn;

    btnRow.appendChild(installBtn);
    btnRow.appendChild(checkBtn);
    card.appendChild(btnRow);
    return card;
  }

  function buildReleaseNotesCard() {
    var card = buildCard('Release Notes', 'app-software-notes-card');
    var box = document.createElement('div');
    box.className = 'app-software-notes';
    card.appendChild(box);
    els.releaseNotes = box;
    return card;
  }

  function buildProgressCard() {
    var card = buildCard('Update-Fortschritt', 'app-software-progress-card');
    buildChecklist(card);
    els.checklist = card;
    return card;
  }

  function buildRollbackCard() {
    var card = buildCard('Rollback', 'app-software-rollback-card app-rollback-card');
    var hint = document.createElement('p');
    hint.className = 'app-software-version-line';
    hint.textContent =
      'Rollback auf die vorherige Version. Sichtbar 1 Stunde nach erfolgreichem Update.';
    card.appendChild(hint);
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'app-btn app-btn--cancel app-update-action';
    btn.textContent = 'Rollback auf vorherige Version';
    btn.addEventListener('click', rollback);
    card.appendChild(btn);
    els.rollbackBtn = btn;
    els.rollbackCard = card;
    return card;
  }

  function buildUpdateConfigCard() {
    //: update-config card with dirty-tracking
    // Save/Cancel pair reusing the existing app-cfg-save-pair pattern.
    // Three inputs wired to PATCH /api/update/config .
    var card = buildCard('Update-Konfiguration', 'app-software-config-card');
    var grid = document.createElement('div');
    grid.className = 'app-software-config-grid';

    grid.appendChild(buildFormGroup('GitHub Repo', 'text', 'cfgRepoInput', 'github_repo'));
    grid.appendChild(
      buildFormGroup('Check-Intervall (Stunden)', 'number', 'cfgIntervalInput', 'check_interval_hours')
    );

    // auto_install toggle
    var autoGroup = document.createElement('div');
    autoGroup.className = 'app-form-group';
    var autoLabel = document.createElement('label');
    autoLabel.textContent = 'Nächtliches Auto-Update (04:00 Uhr)';
    autoLabel.title = 'Prüft nachts um 04:00 Uhr auf Updates und installiert sie automatisch.';
    var autoInput = document.createElement('input');
    autoInput.type = 'checkbox';
    autoInput.className = 'app-input';
    autoInput.setAttribute('data-cfg-field', 'auto_install');
    autoGroup.appendChild(autoLabel);
    autoGroup.appendChild(autoInput);
    grid.appendChild(autoGroup);
    els.cfgAutoInstall = autoInput;

    card.appendChild(grid);

    // Dirty-tracking Save/Cancel pair (matches app.js app-cfg-save-pair
    // convention: span with app-btn-pair app-cfg-save-pair, hidden until
    // any tracked input is marked dirty).
    var pair = document.createElement('span');
    pair.className = 'app-btn-pair app-cfg-save-pair';
    pair.style.display = 'none';
    var saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'app-btn app-btn--sm app-btn--save app-update-action';
    saveBtn.textContent = 'Speichern';
    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'app-btn app-btn--sm app-btn--cancel';
    cancelBtn.textContent = 'Abbrechen';
    pair.appendChild(saveBtn);
    pair.appendChild(cancelBtn);
    card.appendChild(pair);
    els.cfgSaveBtn = saveBtn;
    els.cfgCancelBtn = cancelBtn;
    els.cfgSavePair = pair;

    // Bind dirty-tracking on all three inputs.
    els.cfgRepoInput.addEventListener('input', _cfgCheckDirty);
    els.cfgIntervalInput.addEventListener('input', _cfgCheckDirty);
    els.cfgAutoInstall.addEventListener('change', _cfgCheckDirty);

    cancelBtn.addEventListener('click', _cfgCancel);
    saveBtn.addEventListener('click', _cfgSave);

    return card;
  }

  // ===== Update-config dirty tracking =====

  function _cfgReadInputs() {
    var intervalRaw = els.cfgIntervalInput ? els.cfgIntervalInput.value : '';
    var intervalParsed = parseInt(intervalRaw, 10);
    return {
      github_repo: els.cfgRepoInput ? els.cfgRepoInput.value : '',
      check_interval_hours: isNaN(intervalParsed) ? 0 : intervalParsed,
      auto_install: !!(els.cfgAutoInstall && els.cfgAutoInstall.checked)
    };
  }

  function _cfgSetInputs(values) {
    if (els.cfgRepoInput) {
      els.cfgRepoInput.value = values.github_repo || '';
    }
    if (els.cfgIntervalInput) {
      els.cfgIntervalInput.value =
        typeof values.check_interval_hours === 'number'
          ? values.check_interval_hours
          : 24;
    }
    if (els.cfgAutoInstall) {
      els.cfgAutoInstall.checked = !!values.auto_install;
    }
  }

  function _cfgCheckDirty() {
    var cur = _cfgReadInputs();
    var dirty = false;

    var repoDirty = cur.github_repo !== _cfgOriginals.github_repo;
    if (els.cfgRepoInput) {
      els.cfgRepoInput.classList.toggle('app-input--dirty', repoDirty);
    }
    if (repoDirty) dirty = true;

    var intervalDirty =
      cur.check_interval_hours !== _cfgOriginals.check_interval_hours;
    if (els.cfgIntervalInput) {
      els.cfgIntervalInput.classList.toggle('app-input--dirty', intervalDirty);
    }
    if (intervalDirty) dirty = true;

    var autoDirty = cur.auto_install !== _cfgOriginals.auto_install;
    if (els.cfgAutoInstall) {
      els.cfgAutoInstall.classList.toggle('app-input--dirty', autoDirty);
    }
    if (autoDirty) dirty = true;

    if (els.cfgSavePair) {
      els.cfgSavePair.style.display = dirty ? '' : 'none';
    }
  }

  function _cfgCancel() {
    _cfgSetInputs(_cfgOriginals);
    _cfgCheckDirty();
  }

  function _cfgSave() {
    var cur = _cfgReadInputs();
    // Client-side guard: non-positive interval is rejected server-side with
    // 422 anyway, but fail fast with a clearer message.
    if (
      !cur.github_repo ||
      !cur.check_interval_hours ||
      cur.check_interval_hours <= 0
    ) {
      if (window.showToast) {
        window.showToast('Bitte gültige Werte eingeben', 'error');
      }
      return;
    }
    fetch('/api/update/config', {
      method: 'PATCH',
      headers: csrfHeaders(),
      credentials: 'same-origin',
      body: JSON.stringify(cur)
    })
      .then(function (res) {
        if (res.status === 200) {
          return res.json().then(function (data) {
            _cfgOriginals.github_repo = data.github_repo;
            _cfgOriginals.check_interval_hours = data.check_interval_hours;
            _cfgOriginals.auto_install = !!data.auto_install;
            _cfgSetInputs(_cfgOriginals);
            _cfgCheckDirty();
            if (window.showToast) {
              window.showToast('Einstellungen gespeichert', 'success');
            }
          });
        }
        if (res.status === 422) {
          return res.json().then(function (data) {
            var detail = (data && data.detail) || 'unbekannt';
            if (window.showToast) {
              window.showToast('Ungültige Eingabe: ' + detail, 'error');
            }
          });
        }
        if (window.showToast) {
          window.showToast(
            'Speichern fehlgeschlagen: HTTP ' + res.status,
            'error'
          );
        }
        return null;
      })
      .catch(function (e) {
        if (window.showToast) {
          window.showToast('Netzwerkfehler: ' + (e && e.message), 'error');
        }
      });
  }

  function loadUpdateConfig() {
    fetch('/api/update/config', { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        _cfgOriginals.github_repo = data.github_repo || '';
        _cfgOriginals.check_interval_hours =
          typeof data.check_interval_hours === 'number'
            ? data.check_interval_hours
            : 24;
        _cfgOriginals.auto_install = !!data.auto_install;
        _cfgSetInputs(_cfgOriginals);
        _cfgCheckDirty();
      })
      .catch(function () {
        /* silent — the page can still render with empty defaults */
      });
  }

  function buildFormGroup(labelText, inputType, elsKey, cfgField) {
    var group = document.createElement('div');
    group.className = 'app-form-group';
    var label = document.createElement('label');
    label.textContent = labelText;
    var input = document.createElement('input');
    input.type = inputType;
    input.className = 'app-input';
    input.setAttribute('data-cfg-field', cfgField);
    group.appendChild(label);
    group.appendChild(input);
    els[elsKey] = input;
    return group;
  }

  // ===== Renderers =====

  function renderVersionCard() {
    if (!els.versionLine) return;
    var cur = state.version || '?';
    var latest = state.latestVersion || cur;
    if (state.latestVersion && state.latestVersion !== state.version) {
      els.versionLine.textContent =
        'Version ' + cur + ' → ' + latest + ' verfügbar';
    } else {
      els.versionLine.textContent = 'Version ' + cur + ' (aktuell)';
    }
  }

  function renderReleaseNotes() {
    if (!els.releaseNotes) return;
    if (typeof window.renderSoftwareMarkdown === 'function') {
      window.renderSoftwareMarkdown(state.releaseNotes || '', els.releaseNotes);
    } else {
      els.releaseNotes.textContent = state.releaseNotes || '';
    }
  }

  // ===== Route hooks =====

  function init(rootEl) {
    if (initialized) return;
    if (!rootEl) rootEl = document.getElementById('software-root');
    if (!rootEl) return;
    els.root = rootEl;

    // Clear any pre-existing children via textContent (no string-HTML).
    els.root.textContent = '';

    els.root.appendChild(buildVersionCard());
    els.root.appendChild(buildReleaseNotesCard());
    els.root.appendChild(buildProgressCard());
    els.root.appendChild(buildRollbackCard());
    els.root.appendChild(buildUpdateConfigCard());

    // Build both dialogs eagerly so autofocus attribute is in the DOM
    // before the first showModal() call.
    els.dlg = buildDialog();
    els.rollbackDlg = buildRollbackDialog();

    initialized = true;

    restoreRollbackWindowFromStorage();
    renderVersionCard();
    loadAvailableUpdate();
    //: populate the update-config card with the
    // current server values and arm dirty tracking.
    loadUpdateConfig();
  }

  function onRouteEnter() {
    // Ensure the page is mounted; lazily init on first entry.
    var rootEl = document.getElementById('software-root');
    if (rootEl) {
      rootEl.style.display = '';
    }
    if (!initialized) {
      init(rootEl);
    } else {
      // Refresh available-update snapshot on every entry.
      loadAvailableUpdate();
    }
  }

  function onRouteLeave() {
    var rootEl = document.getElementById('software-root');
    if (rootEl) {
      rootEl.style.display = 'none';
    }
  }

  function getState() {
    return state;
  }

  // ===== Public surface =====

  window.softwarePage = {
    init: init,
    onRouteEnter: onRouteEnter,
    onRouteLeave: onRouteLeave,
    handleWsMessage: handleWsMessage,
    onWsReconnect: onWsReconnect,
    setState: setState,
    getState: getState
  };

  // Auto-init on DOMContentLoaded if the root is already in the DOM.
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('DOMContentLoaded', function () {
      var rootEl = document.getElementById('software-root');
      if (rootEl && !initialized) {
        init(rootEl);
        // Keep the page hidden until the hash points to it.
        if (
          (window.location.hash || '').replace('#', '') !== 'system/software'
        ) {
          rootEl.style.display = 'none';
        }
      }
    });
  }
})();
