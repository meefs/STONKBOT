/**
 * Server-side proxy for StonkFun's public read API.
 *
 * The browser never talks to StonkFun directly. That matters for three
 * reasons:
 *
 *  1. Privacy — a direct call would expose every visitor's IP to a third
 *     party just for loading our homepage.
 *  2. Rate limits — StonkFun limits reads to 300/min *per IP*. Proxied and
 *     CDN-cached, the whole site costs a handful of upstream calls per minute
 *     instead of one per visitor.
 *  3. CSP — with the fetch staying same-origin, the page keeps
 *     `connect-src 'self'` rather than opening up to an external host.
 *
 * This endpoint is read-only and takes no user input beyond a fixed view
 * name. There are no secrets here: StonkFun's public API needs no key.
 */

const STONKFUN = 'https://www.stonkfun.xyz/api/public/v1';

// Allowlist. The client picks a view by name; it can never supply a URL, so
// this cannot be turned into an open proxy / SSRF vector.
const VIEWS = {
  stats: '/stats',
  newest: '/tokens?sort=newest&pageSize=8',
  graduated: '/tokens?status=graduated&sort=volume&pageSize=6',
  volume: '/tokens?sort=volume&pageSize=6',
};

const UPSTREAM_TIMEOUT_MS = 6000;

async function fetchJson(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const response = await fetch(STONKFUN + path, {
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        'User-Agent': 'stonkbot-site/1.0 (+https://stonkfunbot.vercel.app)',
      },
    });
    if (!response.ok) throw new Error(`upstream ${response.status}`);
    const body = await response.json();
    return body.data ?? body;
  } finally {
    clearTimeout(timer);
  }
}

/** Keep only the fields the page renders. Anything else — creator wallets,
 *  metadata URIs, remote image URLs — is dropped here rather than shipped to
 *  the browser. */
function slimToken(token) {
  if (!token || typeof token !== 'object') return null;
  const market = token.market || {};
  return {
    mint: String(token.mint || ''),
    name: String(token.name || ''),
    symbol: String(token.symbol || ''),
    quoteSymbol: String(token.quote?.symbol || ''),
    quoteLabel: String(token.quote?.categoryLabel || ''),
    mode: token.mode === 'reward' ? 'reward' : 'standard',
    status: String(token.status || ''),
    progress: Number(token.graduationProgress) || 0,
    marketCapUsd: Number(market.marketCapUsd) || 0,
    volume24hUsd: Number(market.volume24hUsd) || 0,
    priceChange24h:
      typeof market.priceChange24h === 'number' ? market.priceChange24h : null,
    createdAt: String(token.createdAt || ''),
  };
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ error: 'method_not_allowed' });
  }

  try {
    const [stats, newest, graduated, volume] = await Promise.allSettled([
      fetchJson(VIEWS.stats),
      fetchJson(VIEWS.newest),
      fetchJson(VIEWS.graduated),
      fetchJson(VIEWS.volume),
    ]);

    const statsData = stats.status === 'fulfilled' ? stats.value : null;
    const tokensOf = (settled) =>
      settled.status === 'fulfilled'
        ? (settled.value?.tokens || []).map(slimToken).filter(Boolean)
        : [];

    const payload = {
      ok: Boolean(statsData) || stats.status === 'fulfilled',
      stats: statsData
        ? {
            totalTokens: Number(statsData.tokens?.total) || 0,
            graduated: Number(statsData.tokens?.graduated) || 0,
            aboutToGraduate: Number(statsData.tokens?.aboutToGraduate) || 0,
            marketCapUsd: Number(statsData.tokens?.totalMarketCapUsd) || 0,
            volume24hUsd: Number(statsData.tokens?.totalVolume24hUsd) || 0,
            graduationMarketCapUsd:
              Number(statsData.config?.graduationMarketCapUsd) || 0,
            apiLaunchesEnabled: statsData.config?.apiLaunchesEnabled !== false,
          }
        : null,
      newest: tokensOf(newest),
      graduated: tokensOf(graduated),
      volume: tokensOf(volume),
      fetchedAt: new Date().toISOString(),
    };

    // If every upstream call failed, say so with a 503 rather than serving an
    // empty page that looks like "no tokens exist".
    if (!payload.stats && !payload.newest.length && !payload.graduated.length) {
      res.setHeader('Cache-Control', 'no-store');
      return res.status(503).json({ ok: false, error: 'upstream_unavailable' });
    }

    // Cached at the edge: visitors share one upstream fetch per 30s, and a
    // stale copy is served while it refreshes rather than blocking.
    res.setHeader(
      'Cache-Control',
      'public, s-maxage=30, stale-while-revalidate=300'
    );
    return res.status(200).json(payload);
  } catch (error) {
    res.setHeader('Cache-Control', 'no-store');
    return res.status(503).json({ ok: false, error: 'upstream_unavailable' });
  }
}
