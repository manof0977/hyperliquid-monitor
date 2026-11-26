import React, { useEffect, useState } from 'react';
import { apiGet, CoinPayload, fmtPrice, fmtVol, TradePlan } from '../api/client';
import { binanceBridge } from '../api/binanceBridge';
import { BiasPill, ScoreCell, StatusBadge } from '../components/Badges';
import { SMCChart } from '../components/SMCChart';
import { PlainEnglishReport } from '../components/PlainEnglishReport';

interface Props {
  symbol: string;
  preferredSide?: 'LONG' | 'SHORT' | null;
  symbols: string[];
  onChangeSymbol: (s: string) => void;
  watchlist: string[];
  onToggleWatch: (s: string, add: boolean) => void;
}

const TFS = ['5m', '15m', '1h', '4h', '1d', '1w'];
const GRID_4_TFS = ['1d', '4h', '1h', '15m'];

export const CoinAnalysisPage: React.FC<Props> = ({
  symbol,
  preferredSide,
  symbols,
  onChangeSymbol,
  watchlist,
  onToggleWatch,
}) => {
  const [layout, setLayout] = useState<'1x' | '2x' | '4x'>('1x');
  const [tf, setTf] = useState('1h');
  const [splitTfs, setSplitTfs] = useState<[string, string]>(['4h', '15m']);
  const [detailMode, setDetailMode] = useState<'clean' | 'full'>('clean');

  const [data, setData] = useState<CoinPayload | null>(null);
  const [multiData, setMultiData] = useState<Record<string, CoinPayload>>({});
  const [loading, setLoading] = useState(true);

  const [showEMA, setShowEMA] = useState(true);
  const [showOB, setShowOB] = useState(true);
  const [showFVG, setShowFVG] = useState(true);
  const [showSR, setShowSR] = useState(false); // Default OFF so horizontal lines never clutter Entry/SL/TP
  const [showPlan, setShowPlan] = useState(true);
  const [manualSide, setManualSide] = useState<'LONG' | 'SHORT' | null>(preferredSide ?? null);
  const [showRulesHelp, setShowRulesHelp] = useState(false);
  const [coinQuery, setCoinQuery] = useState('');
  const [reloadNonce, setReloadNonce] = useState(0);

  // Sync manualSide & clear stale coin chart whenever symbol changes
  useEffect(() => {
    setManualSide(preferredSide ?? null);
    setCoinQuery('');
    setData((prev) => (prev && prev.symbol === symbol ? prev : null));
    setMultiData({});
  }, [symbol, preferredSide]);

  const splitLeft = splitTfs[0];
  const splitRight = splitTfs[1];

  // Load primary chart + any visible multi-timeframe charts in parallel
  useEffect(() => {
    let alive = true;
    setLoading(true);

    const neededTfs =
      layout === '4x'
        ? Array.from(new Set([tf, ...GRID_4_TFS]))
        : layout === '2x'
        ? Array.from(new Set([tf, splitLeft, splitRight]))
        : [tf];

    const load = async () => {
      try {
        let first = await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${tf}`);
        if (alive && first.candles.length === 0) {
          const ok = await binanceBridge.syncSingleSymbolNow(symbol, tf);
          if (ok && alive) {
            first = await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${tf}`);
          }
        }
        if (!alive) return;
        // Render candles immediately without waiting for background sentiment calls
        setData(first);
        setLoading(false);

        if (alive && first.candles.length > 0 && !first.sentiment) {
          binanceBridge
            .syncSymbolSentiment(symbol, tf)
            .then(async (ok) => {
              if (ok && alive) {
                const refreshed = await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${tf}`);
                if (alive && refreshed.symbol === symbol) {
                  setData(refreshed);
                }
              }
            })
            .catch(() => {});
        }

        const extraTfs = neededTfs.filter((k) => k !== tf);
        if (extraTfs.length > 0) {
          let results = await Promise.all(
            extraTfs.map(async (k) => {
              try {
                return [k, await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${k}`)] as const;
              } catch {
                return null;
              }
            })
          );
          // If any secondary timeframe had 0 candles, sync all 6 TFs once and re-read
          if (alive && results.some((it) => it && it[1].candles.length === 0)) {
            const ok = await binanceBridge.syncSingleSymbolNow(symbol, tf);
            if (ok && alive) {
              results = await Promise.all(
                extraTfs.map(async (k) => {
                  try {
                    return [k, await apiGet<CoinPayload>(`/api/coin/${encodeURIComponent(symbol)}?tf=${k}`)] as const;
                  } catch {
                    return null;
                  }
                })
              );
            }
          }
          if (!alive) return;
          const nextMap: Record<string, CoinPayload> = { [tf]: first };
          for (const item of results) {
            if (item) nextMap[item[0]] = item[1];
          }
          setMultiData(nextMap);
        } else {
          setMultiData({ [tf]: first });
        }
      } catch {
        // Keep UI responsive even if a transient poll fails
      } finally {
        if (alive) setLoading(false);
      }
    };

    load();
    const id = setInterval(load, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [symbol, tf, layout, splitLeft, splitRight, reloadNonce]);

  const sum = data?.summary;
  const ov = data?.overlays;
  const starred = watchlist.includes(symbol);

  // Strict side resolution: explicit user selection -> status prefix -> active_side
  const autoSide: 'LONG' | 'SHORT' = sum?.status?.startsWith('SHORT')
    ? 'SHORT'
    : sum?.status?.startsWith('LONG')
    ? 'LONG'
    : sum?.active_side === 'SHORT'
    ? 'SHORT'
    : 'LONG';
  const effectiveSide: 'LONG' | 'SHORT' = manualSide ?? autoSide;
  const displayedPlan: TradePlan | undefined =
    effectiveSide === 'SHORT' ? sum?.short_plan : sum?.long_plan;

  const planEntryZone = displayedPlan?.entry_zone ?? (sum?.active_side === effectiveSide ? sum?.entry_zone : null);
  const planEntry = displayedPlan?.entry ?? (sum?.active_side === effectiveSide ? sum?.entry : null);
  const planInv = displayedPlan?.invalidation ?? (sum?.active_side === effectiveSide ? sum?.invalidation : null);
  const planTargets = displayedPlan?.targets ?? (sum?.active_side === effectiveSide ? sum?.targets ?? [] : []);
  const planTargetDetails = displayedPlan?.target_details ?? [];
  const planRR = displayedPlan?.rr ?? (sum?.active_side === effectiveSide ? sum?.rr : 0);
  const sideStatus =
    effectiveSide === 'SHORT'
      ? sum?.short_status || (sum?.status?.startsWith('SHORT') ? sum.status : 'NO SETUP')
      : sum?.long_status || (sum?.status?.startsWith('LONG') ? sum.status : 'NO SETUP');

  const orderedSymbols = React.useMemo(() => {
    const base = symbols.includes(symbol) ? symbols : [...symbols, symbol];
    return [...base].sort();
  }, [symbols, symbol]);

  const curSymIdx = Math.max(0, orderedSymbols.indexOf(symbol));
  const jumpByOffset = (delta: number) => {
    if (orderedSymbols.length <= 1) return;
    const nextIdx = (curSymIdx + delta + orderedSymbols.length) % orderedSymbols.length;
    onChangeSymbol(orderedSymbols[nextIdx]);
  };

  const handleQuickJump = (raw: string) => {
    const q = raw.trim().toUpperCase();
    if (!q) return;
    const exact = orderedSymbols.find((s) => s === q) || orderedSymbols.find((s) => s === `${q}USDT`);
    if (exact) {
      onChangeSymbol(exact);
      setCoinQuery('');
      return;
    }
    const partial = orderedSymbols.find((s) => s.startsWith(q)) || orderedSymbols.find((s) => s.includes(q));
    if (partial) {
      onChangeSymbol(partial);
      setCoinQuery('');
      return;
    }
    onChangeSymbol(q.endsWith('USDT') ? q : `${q}USDT`);
    setCoinQuery('');
  };

  return (
    <div className="page">
      <div className="page-header">
        <div className="coin-title-row" style={{ flexWrap: 'wrap', gap: 8 }}>
          <button
            className="btn ghost"
            style={{ padding: '5px 9px' }}
            title="Previous Coin"
            onClick={() => jumpByOffset(-1)}
          >
            ◀
          </button>
          <select className="input sym-select" value={symbol} onChange={(e) => onChangeSymbol(e.target.value)}>
            {orderedSymbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button
            className="btn ghost"
            style={{ padding: '5px 9px' }}
            title="Next Coin"
            onClick={() => jumpByOffset(1)}
          >
            ▶
          </button>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleQuickJump(coinQuery);
            }}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
          >
            <input
              className="input"
              list="coin-jump-list"
              placeholder="Jump coin (e.g. ETH, SOL)…"
              value={coinQuery}
              style={{ width: 175 }}
              onChange={(e) => {
                const v = e.target.value;
                setCoinQuery(v);
                const up = v.trim().toUpperCase();
                if (orderedSymbols.includes(up)) {
                  onChangeSymbol(up);
                  setCoinQuery('');
                }
              }}
            />
            <datalist id="coin-jump-list">
              {orderedSymbols.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </form>
          <span className="coin-price mono">{fmtPrice(sum?.price ?? data?.candles.slice(-1)[0]?.close)}</span>
          {sum && (
            <span className={sum.change_24h >= 0 ? 'bull-txt mono' : 'bear-txt mono'}>
              {sum.change_24h >= 0 ? '+' : ''}{sum.change_24h.toFixed(2)}%
            </span>
          )}
          {sum && <StatusBadge status={sum.status} early={sum.early_watch} />}
          <button className="btn ghost" onClick={() => onToggleWatch(symbol, !starred)}>
            {starred ? '★ In Watchlist' : '☆ Add Watchlist'}
          </button>
        </div>

        <div className="tf-bar" style={{ flexWrap: 'wrap', gap: 10 }}>
          {/* Multi-Timeframe Layout Switcher */}
          <div className="seg-group" title="Switch between 1 Single Chart, 2 Split Charts (HTF+LTF), or 4 Multi-Timeframe Charts">
            <button className={`tf-btn ${layout === '1x' ? 'active' : ''}`} onClick={() => setLayout('1x')}>
              1x Chart
            </button>
            <button className={`tf-btn ${layout === '2x' ? 'active' : ''}`} onClick={() => setLayout('2x')}>
              2x Split
            </button>
            <button className={`tf-btn ${layout === '4x' ? 'active' : ''}`} onClick={() => setLayout('4x')}>
              4x Multi-TF
            </button>
          </div>

          {/* Timeframe Selector — Always visible! */}
          <div className="seg-group">
            {TFS.map((k) => (
              <button
                key={k}
                className={`tf-btn ${tf === k ? 'active' : ''}`}
                onClick={() => {
                  setTf(k);
                  if (layout === '4x' && !GRID_4_TFS.includes(k)) {
                    setLayout('1x');
                  } else if (layout === '2x' && !splitTfs.includes(k)) {
                    setSplitTfs([splitTfs[0], k]);
                  }
                }}
              >
                {k.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Clean vs Full Label Density Switcher */}
          <div className="seg-group" title="Clean mode filters out historical box/label clutter so candles stay readable">
            <button
              className={`tf-btn ${detailMode === 'clean' ? 'active' : ''}`}
              onClick={() => setDetailMode('clean')}
            >
              Clean View
            </button>
            <button
              className={`tf-btn ${detailMode === 'full' ? 'active' : ''}`}
              onClick={() => setDetailMode('full')}
            >
              Full SMC
            </button>
          </div>

          <div className="overlay-toggles">
            <label><input type="checkbox" checked={showEMA} onChange={(e) => setShowEMA(e.target.checked)} /> EMA</label>
            <label><input type="checkbox" checked={showOB} onChange={(e) => setShowOB(e.target.checked)} /> OB</label>
            <label><input type="checkbox" checked={showFVG} onChange={(e) => setShowFVG(e.target.checked)} /> FVG</label>
            <label><input type="checkbox" checked={showSR} onChange={(e) => setShowSR(e.target.checked)} /> S/R</label>
            <label><input type="checkbox" checked={showPlan} onChange={(e) => setShowPlan(e.target.checked)} /> Plan</label>
          </div>
        </div>
      </div>

      <div className="analysis-layout">
        <div className="analysis-main-col">
          {/* 1x SINGLE CHART */}
          {layout === '1x' && (
            <div className="panel chart-panel">
              {data && data.symbol === symbol && data.candles.length > 0 ? (
                <SMCChart
                  key={`${symbol}-${tf}`}
                  data={data}
                  showEMA={showEMA}
                  showOB={showOB}
                  showFVG={showFVG}
                  showSR={showSR}
                  showPlan={showPlan}
                  detailMode={detailMode}
                  planSide={effectiveSide}
                  height={500}
                />
              ) : (
                <div className="empty" style={{ height: 500, display: 'grid', placeItems: 'center' }}>
                  {loading ? (
                    `Loading ${symbol} ${tf.toUpperCase()} klines…`
                  ) : (
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ marginBottom: 10 }}>No candle data available yet for {symbol} ({tf.toUpperCase()}).</div>
                      <button className="btn" onClick={() => setReloadNonce((n) => n + 1)}>
                        Retry Loading {symbol} Chart
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 2x SPLIT (HTF Structure + LTF Trigger) */}
          {layout === '2x' && (
            <div className="mtf-chart-grid cols-2">
              {splitTfs.map((paneTf, idx) => {
                const pData = multiData[paneTf];
                return (
                  <div key={`${idx}-${paneTf}`} className={`panel chart-panel ${tf === paneTf ? 'focused-pane' : ''}`}>
                    <div className="pane-bar">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <select
                          className="input pane-tf-select"
                          value={paneTf}
                          onChange={(e) => {
                            const next: [string, string] = [...splitTfs] as [string, string];
                            next[idx] = e.target.value;
                            setSplitTfs(next);
                            setTf(e.target.value);
                          }}
                        >
                          {TFS.map((tOpt) => (
                            <option key={tOpt} value={tOpt}>{tOpt.toUpperCase()}</option>
                          ))}
                        </select>
                        <span className="muted small">{idx === 0 ? 'HTF Structure' : 'LTF Trigger'}</span>
                        <BiasPill bias={sum?.biases?.[paneTf]} />
                      </div>
                      <div className="muted small mono">
                        {pData?.overlays ? (
                          <>
                            Trend: <strong>{pData.overlays.structure.trend.toUpperCase()}</strong> • RSI:{' '}
                            <strong>{Number(pData.overlays.ind.rsi14 || 0).toFixed(1)}</strong>
                          </>
                        ) : null}
                        <button
                          className="btn ghost"
                          style={{ marginLeft: 8, padding: '2px 7px', fontSize: 11 }}
                          onClick={() => setTf(paneTf)}
                        >
                          {tf === paneTf ? '● Report TF' : 'Inspect Report'}
                        </button>
                      </div>
                    </div>
                    {pData && pData.symbol === symbol && pData.candles.length > 0 ? (
                      <SMCChart
                        key={`${symbol}-2x-${idx}-${paneTf}`}
                        data={pData}
                        showEMA={showEMA}
                        showOB={showOB}
                        showFVG={showFVG}
                        showSR={showSR}
                        showPlan={showPlan}
                        detailMode={detailMode}
                        planSide={effectiveSide}
                        height={390}
                      />
                    ) : (
                      <div className="empty" style={{ height: 390, display: 'grid', placeItems: 'center' }}>
                        Loading {symbol} {paneTf.toUpperCase()}…
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* 4x MULTI-TIMEFRAME GRID (1D • 4H • 1H • 15M) */}
          {layout === '4x' && (
            <div className="mtf-chart-grid cols-2">
              {GRID_4_TFS.map((paneTf) => {
                const pData = multiData[paneTf];
                const roleMap: Record<string, string> = {
                  '1d': 'Macro Bias',
                  '4h': 'Swing Structure',
                  '1h': 'Primary Execution',
                  '15m': 'Entry Trigger',
                };
                return (
                  <div
                    key={paneTf}
                    className={`panel chart-panel ${tf === paneTf ? 'focused-pane' : ''}`}
                    onClick={() => setTf(paneTf)}
                  >
                    <div className="pane-bar">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <strong className="accent-txt">{paneTf.toUpperCase()}</strong>
                        <span className="muted small">{roleMap[paneTf]}</span>
                        <BiasPill bias={sum?.biases?.[paneTf]} />
                      </div>
                      <div className="muted small mono">
                        {pData?.overlays ? (
                          <>
                            {pData.overlays.structure.trend.toUpperCase()} • RSI{' '}
                            {Number(pData.overlays.ind.rsi14 || 0).toFixed(0)}
                          </>
                        ) : null}
                      </div>
                    </div>
                    {pData && pData.symbol === symbol && pData.candles.length > 0 ? (
                      <SMCChart
                        key={`${symbol}-4x-${paneTf}`}
                        data={pData}
                        showEMA={showEMA}
                        showOB={showOB}
                        showFVG={showFVG}
                        showSR={showSR}
                        showPlan={showPlan}
                        detailMode={detailMode}
                        planSide={effectiveSide}
                        height={285}
                        compact
                      />
                    ) : (
                      <div className="empty" style={{ height: 285, display: 'grid', placeItems: 'center' }}>
                        Loading {paneTf.toUpperCase()}…
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <PlainEnglishReport report={data?.explanation} symbol={symbol} tf={tf} />
        </div>

        <div className="side-panels">
          <div className="panel">
            <div className="panel-head">
              <h3>Multi-Timeframe Bias</h3>
              <span className="muted small">Click any TF to view</span>
            </div>
            <div className="mtf-grid">
              {['1w', '1d', '4h', '1h', '15m', '5m'].map((k) => (
                <div
                  key={k}
                  className={`mtf-cell clickable ${tf === k ? 'active-tf-cell' : ''}`}
                  onClick={() => setTf(k)}
                >
                  <span className="muted small">{k.toUpperCase()}</span>
                  <BiasPill bias={sum?.biases?.[k]} />
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <h3>Dual Scoring Engine</h3>
            <div className="score-grid-2">
              <div>
                <div className="muted small">LONG Tech ({sum?.long_tier ?? '—'})</div>
                <ScoreCell value={sum?.long_tech_score ?? 0} side="long" />
                <div className="muted small" style={{ marginTop: 8 }}>LONG Opportunity</div>
                <ScoreCell value={sum?.long_opp_score ?? 0} side="long" />
              </div>
              <div>
                <div className="muted small">SHORT Tech ({sum?.short_tier ?? '—'})</div>
                <ScoreCell value={sum?.short_tech_score ?? 0} side="short" />
                <div className="muted small" style={{ marginTop: 8 }}>SHORT Opportunity</div>
                <ScoreCell value={sum?.short_opp_score ?? 0} side="short" />
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head" style={{ marginBottom: 8 }}>
              <h3>
                Structural Trade Levels (
                <span className={effectiveSide === 'SHORT' ? 'bear-txt' : 'bull-txt'}>{effectiveSide}</span>)
              </h3>
              <div className="seg-group">
                <button
                  className={`tf-btn ${effectiveSide === 'LONG' ? 'active' : ''}`}
                  onClick={() => setManualSide('LONG')}
                >
                  LONG
                </button>
                <button
                  className={`tf-btn ${effectiveSide === 'SHORT' ? 'active' : ''}`}
                  onClick={() => setManualSide('SHORT')}
                >
                  SHORT
                </button>
              </div>
            </div>

            <div style={{ marginBottom: 10, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <StatusBadge status={sideStatus} early={false} />
              <span className="muted small">Based on {(sum?.primary_tf || '1h').toUpperCase()} Structure</span>
            </div>

            {displayedPlan?.order_type && (
              <div className="order-type-chip">
                {displayedPlan.order_type}
              </div>
            )}

            <div className="kv-list">
              <div><span>Current Live Price</span><strong className="mono">{fmtPrice(sum?.price)}</strong></div>
              <div>
                <span>{effectiveSide} Entry Zone</span>
                <strong className="mono">
                  {planEntryZone ? `${fmtPrice(planEntryZone[0])} – ${fmtPrice(planEntryZone[1])}` : 'No valid zone'}
                </strong>
              </div>
              {displayedPlan?.entry_reason && (
                <div className="reason-subrow">
                  <span>Zone Basis: {displayedPlan.entry_reason}</span>
                </div>
              )}
              <div>
                <span>Suggested {effectiveSide} Entry</span>
                <strong className="mono accent-txt">{fmtPrice(planEntry)}</strong>
              </div>
              <div>
                <span>Stop-Loss ({effectiveSide === 'SHORT' ? 'Above Entry' : 'Below Entry'})</span>
                <strong className="mono bear-txt">{fmtPrice(planInv)}</strong>
              </div>
              {displayedPlan?.invalidation_reason && (
                <div className="reason-subrow">
                  <span>SL Basis: {displayedPlan.invalidation_reason}</span>
                </div>
              )}
              {[0, 1, 2].map((i) => {
                const td = planTargetDetails[i];
                const tVal = planTargets[i];
                return (
                  <React.Fragment key={i}>
                    <div>
                      <span>Target {i + 1} ({effectiveSide === 'SHORT' ? '▼ Below' : '▲ Above'})</span>
                      <strong className="mono bull-txt">
                        {fmtPrice(tVal)}{td?.rr ? ` (${td.rr}R)` : ''}
                      </strong>
                    </div>
                    {td?.source && (
                      <div className="reason-subrow">
                        <span>TP{i + 1} Magnet: {td.source}</span>
                      </div>
                    )}
                  </React.Fragment>
                );
              })}
              <div><span>Estimated Risk / Reward</span><strong className="mono">{planRR ? `${planRR} R` : '—'}</strong></div>
              <div><span>Dealing Range Position</span><strong className="mono">{ov?.smc.zone?.toUpperCase() ?? '—'}</strong></div>
            </div>

            <button
              className="btn ghost"
              style={{ width: '100%', marginTop: 10, fontSize: 11.5 }}
              onClick={() => setShowRulesHelp((v) => !v)}
            >
              {showRulesHelp ? '▲ Hide Entry / Stop / Target Formula' : '▼ How Entry, Stop & Targets Are Calculated'}
            </button>

            {showRulesHelp && (
              <div className="formula-help-box">
                <p><strong>1. Entry Zone &amp; Suggested Entry:</strong> Derived from the nearest unviolated 1H Order Block (`OB`), Fair Value Gap (`FVG`), or multi-touch S/R cluster on the pullback side of price (below price for <b>LONG</b>, above price for <b>SHORT</b>). If price is already inside the zone, entry = live price; otherwise entry = zone edge (`OB/FVG top` for Long limit buy, `OB/FVG base` for Short limit sell).</p>
                <p><strong>2. Hard Invalidation (Stop-Loss):</strong> Placed <b>0.25 × ATR(14)</b> beyond BOTH the Entry Zone outer edge and the protecting structural Swing Low (for Long) or Swing High (for Short) — never a fixed arbitrary %.</p>
                <p><strong>3. Profit Targets (TP1–TP3):</strong> Ranked opposing structural liquidity pools spaced at least <b>0.4 × ATR</b> apart: Horizontal S/R clusters, Previous Day/Week Highs &amp; Lows (`PDH`/`PDL`, `PWH`/`PWL`), Dealing Range extremes, and opposing Order Blocks.</p>
              </div>
            )}
          </div>

          {data?.sentiment && (
            <div className="panel">
              <h3>Futures Positioning &amp; Squeeze Data</h3>
              <div className="kv-list">
                {data.sentiment.funding_rate_pct !== undefined && (
                  <div>
                    <span>Funding Rate (8H)</span>
                    <strong className={`mono ${data.sentiment.funding_rate_pct > 0.025 ? 'bear-txt' : data.sentiment.funding_rate_pct < -0.01 ? 'bull-txt' : ''}`}>
                      {data.sentiment.funding_rate_pct >= 0 ? '+' : ''}
                      {Number(data.sentiment.funding_rate_pct).toFixed(4)}%
                    </strong>
                  </div>
                )}
                {data.sentiment.basis_pct !== undefined && (
                  <div>
                    <span>Perp vs Spot Basis</span>
                    <strong className="mono">
                      {data.sentiment.basis_pct >= 0 ? '+' : ''}
                      {Number(data.sentiment.basis_pct).toFixed(3)}%
                    </strong>
                  </div>
                )}
                {data.sentiment.open_interest !== undefined && sum?.price ? (
                  <div>
                    <span>Open Interest</span>
                    <strong className="mono">{fmtVol(Number(data.sentiment.open_interest) * sum.price)}</strong>
                  </div>
                ) : null}
                {data.sentiment.retail_long_pct !== undefined && (
                  <div>
                    <span>Retail Accounts (L / S)</span>
                    <strong className="mono">
                      <span className="bull-txt">{data.sentiment.retail_long_pct}%</span> /{' '}
                      <span className="bear-txt">{(100 - Number(data.sentiment.retail_long_pct)).toFixed(1)}%</span>
                    </strong>
                  </div>
                )}
                {data.sentiment.top_trader_long_pct !== undefined && (
                  <div>
                    <span>Top Whales (L / S)</span>
                    <strong className="mono">
                      <span className="bull-txt">{data.sentiment.top_trader_long_pct}%</span> /{' '}
                      <span className="bear-txt">{(100 - Number(data.sentiment.top_trader_long_pct)).toFixed(1)}%</span>
                    </strong>
                  </div>
                )}
              </div>
            </div>
          )}

          {ov && (
            <div className="panel">
              <h3>{tf.toUpperCase()} Indicators &amp; Key Levels</h3>
              <div className="kv-list">
                <div><span>Structure Trend</span><strong>{ov.structure.trend.toUpperCase()}</strong></div>
                <div><span>EMA Stack</span><strong>{String(ov.ind.ema_stack || '').toUpperCase()}</strong></div>
                <div><span>RSI (14)</span><strong className="mono">{Number(ov.ind.rsi14 || 0).toFixed(1)}</strong></div>
                <div><span>MACD Hist</span><strong className="mono">{Number(ov.ind.macd_hist || 0).toPrecision(3)}</strong></div>
                <div><span>Volume / SMA20</span><strong className="mono">{Number(ov.ind.vol_ratio || 0).toFixed(2)}x</strong></div>
                <div><span>PDH / PDL</span><strong className="mono">{fmtPrice(ov.sr.pdh)} / {fmtPrice(ov.sr.pdl)}</strong></div>
                <div><span>PWH / PWL</span><strong className="mono">{fmtPrice(ov.sr.pwh)} / {fmtPrice(ov.sr.pwl)}</strong></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
