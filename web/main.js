/**
 * STONKBOT site behaviour.
 *
 * Deliberately tiny and dependency-free: this is a static marketing page, so
 * it makes no network calls, reads no storage, and handles no secrets. The
 * only JS is progressive enhancement — every word on the page is readable
 * with JS disabled.
 */
(function () {
  'use strict';

  // Signals to CSS that JS is running, so the reveal animation may start from
  // hidden. Without this class the content renders visible by default.
  document.documentElement.classList.remove('no-js');

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------- mobile nav ---------------- */

  var toggle = document.querySelector('.nav-toggle');
  var mobileNav = document.getElementById('mobile-nav');

  function setNav(open) {
    if (!toggle || !mobileNav) return;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    mobileNav.hidden = !open;
  }

  if (toggle && mobileNav) {
    toggle.addEventListener('click', function () {
      setNav(toggle.getAttribute('aria-expanded') !== 'true');
    });

    // Close after following a link, so the menu doesn't cover the target.
    mobileNav.addEventListener('click', function (event) {
      if (event.target.closest('a')) setNav(false);
    });

    // Escape closes the menu and returns focus to the button that opened it.
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
        setNav(false);
        toggle.focus();
      }
    });

    // If the viewport grows past the mobile breakpoint while the menu is open,
    // hide it — the desktop nav is visible again and both would show at once.
    window.matchMedia('(min-width: 901px)').addEventListener('change', function (event) {
      if (event.matches) setNav(false);
    });
  }

  /* ---------------- scroll reveal ---------------- */

  var revealables = document.querySelectorAll('.reveal');

  if (reduceMotion || !('IntersectionObserver' in window)) {
    // No animation wanted, or no support: show everything immediately.
    revealables.forEach(function (el) { el.classList.add('in'); });
  } else {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('in');
        observer.unobserve(entry.target); // one-shot; keeps scrolling cheap
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });

    revealables.forEach(function (el, index) {
      // A small stagger within each group reads as one motion rather than a
      // scatter, but is capped so later items never feel delayed.
      el.style.transitionDelay = Math.min(index % 6, 5) * 45 + 'ms';
      observer.observe(el);
    });
  }

  /* ---------------- FAQ: one open at a time ---------------- */

  var faqItems = document.querySelectorAll('.faq > details');
  faqItems.forEach(function (item) {
    item.addEventListener('toggle', function () {
      if (!item.open) return;
      faqItems.forEach(function (other) {
        if (other !== item) other.open = false;
      });
    });
  });

  /* ---------------- live market board ----------------
   *
   * Data comes from our own /api/live, which proxies StonkFun server-side.
   * The browser never contacts StonkFun directly.
   *
   * SECURITY: token names and symbols are chosen by whoever launched them, so
   * every value from the API is written with textContent and never with
   * innerHTML. There is no path here that turns API data into markup.
   */

  var rowsEl = document.getElementById('token-rows');
  var stampEl = document.getElementById('live-stamp');
  var statsEl = document.getElementById('live-stats');
  var panelEl = document.getElementById('panel-tokens');
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.board-tab'));

  if (rowsEl) {
    var cache = null;
    var activeView = 'newest';
    var REFRESH_MS = 60000;

    function compactUsd(value) {
      var n = Number(value) || 0;
      if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
      if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
      if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
      return '$' + n.toFixed(0);
    }

    function compactNum(value) {
      var n = Number(value) || 0;
      return n >= 1000 ? n.toLocaleString('en-US') : String(n);
    }

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    }

    function monogram(symbol) {
      var text = (symbol || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase();
      return el('span', 'tk-mono', text || '?');
    }

    function renderRow(token) {
      var row = el('div', 'board-row');

      var identity = el('div', 'tk col-tk');
      identity.appendChild(monogram(token.symbol));
      var id = el('span', 'tk-id');
      id.appendChild(el('span', 'tk-sym', token.symbol || '—'));
      id.appendChild(el('span', 'tk-name', token.name || ''));
      identity.appendChild(id);
      row.appendChild(identity);

      row.appendChild(el('span', 'pair col-pair', token.quoteSymbol || '—'));
      row.appendChild(el('span', 'num col-mcap', compactUsd(token.marketCapUsd)));
      row.appendChild(el('span', 'num col-vol', compactUsd(token.volume24hUsd)));

      var change = token.priceChange24h;
      var changeEl = el('span', 'chg col-chg');
      if (typeof change === 'number') {
        changeEl.classList.add(change > 0 ? 'up' : change < 0 ? 'down' : 'flat');
        changeEl.textContent = (change > 0 ? '+' : '') + change.toFixed(1) + '%';
      } else {
        changeEl.classList.add('flat');
        changeEl.textContent = '—';
      }
      row.appendChild(changeEl);

      var bonded = token.status === 'graduated' || token.progress >= 1;
      var pct = Math.max(0, Math.min(1, Number(token.progress) || 0)) * 100;
      var bond = el('div', 'bond col-bond');
      var bar = el('div', 'bond-bar');
      var fill = el('div', 'bond-fill' + (bonded ? ' done' : ''));
      fill.style.width = pct.toFixed(1) + '%';
      bar.appendChild(fill);
      bond.appendChild(bar);
      bond.appendChild(el('span', 'bond-text', bonded ? 'Bonded' : pct.toFixed(0) + '%'));
      row.appendChild(bond);

      return row;
    }

    function renderTokens(list) {
      rowsEl.textContent = '';
      if (!list || !list.length) {
        rowsEl.appendChild(el('p', 'board-msg', 'Nothing to show here yet.'));
        return;
      }
      var frag = document.createDocumentFragment();
      list.forEach(function (token) { frag.appendChild(renderRow(token)); });
      rowsEl.appendChild(frag);
    }

    function renderStats(stats) {
      if (!statsEl || !stats) return;
      var formatted = {
        totalTokens: compactNum(stats.totalTokens),
        graduated: compactNum(stats.graduated),
        volume24hUsd: compactUsd(stats.volume24hUsd),
        marketCapUsd: compactUsd(stats.marketCapUsd)
      };
      Object.keys(formatted).forEach(function (key) {
        var node = statsEl.querySelector('[data-stat="' + key + '"]');
        if (node) node.textContent = formatted[key];
      });
    }

    function setStamp(text) {
      if (stampEl) stampEl.textContent = text;
    }

    function showError() {
      rowsEl.textContent = '';
      rowsEl.appendChild(
        el('p', 'board-msg err', "Live data is unavailable right now. StonkFun's API isn't responding.")
      );
      setStamp('Offline');
    }

    function load() {
      if (panelEl) panelEl.setAttribute('aria-busy', 'true');
      return fetch('/api/live', { headers: { Accept: 'application/json' } })
        .then(function (response) {
          if (!response.ok) throw new Error('http ' + response.status);
          return response.json();
        })
        .then(function (data) {
          cache = data;
          renderStats(data.stats);
          renderTokens(data[activeView]);
          setStamp(
            'Updated ' +
              new Date(data.fetchedAt || Date.now()).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
              })
          );
        })
        .catch(showError)
        .then(function () {
          if (panelEl) panelEl.setAttribute('aria-busy', 'false');
        });
    }

    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        activeView = tab.getAttribute('data-view');
        tabs.forEach(function (other) {
          var on = other === tab;
          other.classList.toggle('is-active', on);
          other.setAttribute('aria-selected', String(on));
        });
        if (panelEl) panelEl.setAttribute('aria-labelledby', tab.id);
        if (cache) renderTokens(cache[activeView]);
      });
    });

    load();

    // Refresh on a timer, but never while the tab is hidden — a backgrounded
    // page should not keep polling.
    setInterval(function () {
      if (!document.hidden) load();
    }, REFRESH_MS);

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && cache === null) load();
    });
  }
})();
