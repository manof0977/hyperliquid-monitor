import React, { useEffect, useRef } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  IChartApi,
  ISeriesApi,
  LineStyle,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts';
import { CoinPayload, TradePlan } from '../api/client';

interface Props {
  data: CoinPayload;
  showEMA: boolean;
  showOB: boolean;
  showFVG: boolean;
  showSR: boolean;
  showPlan: boolean;
  detailMode?: 'clean' | 'full';
  planSide?: 'LONG' | 'SHORT';
  height?: number;
  compact?: boolean;
}

export const SMCChart: React.FC<Props> = ({
  data,
  showEMA,
  showOB,
  showFVG,
  showSR,
  showPlan,
  detailMode = 'clean',
  planSide,
  height = 500,
  compact = false,
}) => {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const isClean = detailMode === 'clean' || compact;

    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: '#0b0f17' },
        textColor: '#94a3b8',
        fontSize: compact ? 10 : 11,
      },
      grid: {
        vertLines: { color: 'rgba(148,163,184,0.045)' },
        horzLines: { color: 'rgba(148,163,184,0.045)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1e293b', scaleMargins: { top: 0.08, bottom: 0.2 } },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
    });
    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });
    seriesRef.current = candleSeries;

    const candles = data.candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    candleSeries.setData(candles);

    // Zoom to recent ~90 bars (65 in compact grid) so candles have clean spacing instead of 300 squished bars
    if (candles.length > 20) {
      const visibleBars = compact ? 65 : 95;
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, candles.length - visibleBars),
        to: candles.length + 5,
      });
    }

    // Subtle volume histogram on bottom 15%
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });
    volSeries.setData(
      data.candles.map((c) => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(16,185,129,0.22)' : 'rgba(239,68,68,0.22)',
      }))
    );

    // EMA overlays (in Clean mode: EMA 20 + EMA 50 + EMA 200; in Full mode: all 4)
    if (showEMA) {
      const emaSpecs: [keyof CoinPayload['candles'][number], string][] = isClean
        ? [
            ['ema20', 'rgba(56,189,248,0.75)'],
            ['ema50', 'rgba(245,158,11,0.75)'],
            ['ema200', 'rgba(226,232,240,0.65)'],
          ]
        : [
            ['ema20', '#38bdf8'],
            ['ema50', '#f59e0b'],
            ['ema100', '#a855f7'],
            ['ema200', '#e2e8f0'],
          ];
      for (const [key, color] of emaSpecs) {
        const ls = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        ls.setData(
          data.candles
            .filter((c) => typeof c[key] === 'number')
            .map((c) => ({ time: c.time as UTCTimestamp, value: c[key] as number }))
        );
      }
    }

    // De-congested Markers: deduplicated per bar, spaced apart so labels NEVER stack on top of each other
    const byTime = new Map<number, SeriesMarker<Time>>();
    if (data.overlays) {
      const swingCount = compact ? 3 : isClean ? 5 : 16;
      const breakCount = compact ? 1 : isClean ? 2 : 10;

      // 1. Major Swings (spaced at least 5 bars apart in Clean mode)
      let lastSwingIdx = -999;
      for (const s of data.overlays.structure.swings.slice(-swingCount * 2)) {
        if (isClean && Math.abs(s.index - lastSwingIdx) < 5) continue;
        lastSwingIdx = s.index;
        const ts = Math.floor(s.time / 1000);
        byTime.set(ts, {
          time: ts as UTCTimestamp,
          position: s.kind === 'high' ? 'aboveBar' : 'belowBar',
          color: s.label === 'HH' || s.label === 'HL' ? '#22c55e' : '#f87171',
          shape: s.kind === 'high' ? 'arrowDown' : 'arrowUp',
          text: s.label,
        });
      }
      // Keep only last `swingCount` swings
      if (byTime.size > swingCount) {
        const keys = Array.from(byTime.keys()).sort((a, b) => a - b);
        for (const k of keys.slice(0, keys.length - swingCount)) byTime.delete(k);
      }

      // 2. Sweeps ONLY in Full SMC mode (removes "SWEEP" clutter in Clean mode)
      if (!isClean) {
        for (const sw of data.overlays.smc.sweeps.slice(-6)) {
          const ts = Math.floor(sw.time / 1000);
          if (!byTime.has(ts)) {
            byTime.set(ts, {
              time: ts as UTCTimestamp,
              position: sw.kind === 'bullish' ? 'belowBar' : 'aboveBar',
              color: '#38bdf8',
              shape: 'square',
              text: 'LIQ',
            });
          }
        }
      }

      // 3. Latest BOS / CHoCH breaks (highest priority)
      for (const b of data.overlays.structure.breaks.slice(-breakCount)) {
        const ts = Math.floor(b.time / 1000);
        byTime.set(ts, {
          time: ts as UTCTimestamp,
          position: b.direction === 'bullish' ? 'belowBar' : 'aboveBar',
          color: b.kind === 'CHoCH' ? '#fbbf24' : b.direction === 'bullish' ? '#34d399' : '#fb7185',
          shape: 'circle',
          text: b.kind,
        });
      }
    }
    const markers = Array.from(byTime.values()).sort((a, b) => Number(a.time) - Number(b.time));
    candleSeries.setMarkers(markers);

    // Support / Resistance horizontal lines (in Clean mode: ONLY 1 nearest SUP + 1 nearest RES; in Full: up to 3)
    if (showSR && data.overlays) {
      const srCount = isClean ? 1 : 3;
      for (const sup of data.overlays.sr.supports.slice(0, srCount)) {
        candleSeries.createPriceLine({
          price: sup.price,
          color: 'rgba(34,197,94,0.55)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: !compact,
          title: compact ? '' : 'SUP',
        });
      }
      for (const res of data.overlays.sr.resistances.slice(0, srCount)) {
        candleSeries.createPriceLine({
          price: res.price,
          color: 'rgba(248,113,113,0.55)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: !compact,
          title: compact ? '' : 'RES',
        });
      }
    }

    // Resolve the exact TradePlan for `planSide` (strictly matches SHORT or LONG!)
    const s = data.summary;
    const effSide: 'LONG' | 'SHORT' =
      planSide || (s?.active_side === 'SHORT' ? 'SHORT' : 'LONG');
    const activePlan: TradePlan | undefined =
      effSide === 'SHORT' ? s?.short_plan : s?.long_plan;
    const planEntry = activePlan?.entry ?? (s?.active_side === effSide ? s?.entry : null);
    const planInv = activePlan?.invalidation ?? (s?.active_side === effSide ? s?.invalidation : null);
    const planTargets = activePlan?.targets ?? (s?.active_side === effSide ? s?.targets ?? [] : []);
    const planZone = activePlan?.entry_zone ?? (s?.active_side === effSide ? s?.entry_zone : null);

    // Active trade plan price lines: Entry, Stop-Loss, TP1/TP2/(TP3)
    if (showPlan && planEntry && planInv) {
      candleSeries.createPriceLine({
        price: planEntry,
        color: '#38bdf8',
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: compact ? effSide[0] : `${effSide} ENTRY`,
      });
      candleSeries.createPriceLine({
        price: planInv,
        color: '#ef4444',
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: compact ? 'SL' : 'STOP (SL)',
      });
      const maxTps = compact ? 1 : isClean ? 2 : 3;
      planTargets.slice(0, maxTps).forEach((t, i) => {
        candleSeries.createPriceLine({
          price: t,
          color: '#10b981',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: `TP${i + 1}`,
        });
      });
    }

    const curPrice = data.candles.length ? data.candles[data.candles.length - 1].close : 0;
    const atrEst = Number(data.overlays?.ind?.atr14 || curPrice * 0.01 || 1);

    // Synchronized 2D canvas overlay for nearest Order Blocks, FVGs, recent BOS line & Entry Zone band
    const drawZones = () => {
      const cvs = canvasRef.current;
      if (!cvs || !el) return;
      const w = el.clientWidth;
      const h = height;
      if (cvs.width !== w || cvs.height !== h) {
        cvs.width = w;
        cvs.height = h;
      }
      const ctx = cvs.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, w, h);
      if (!data.overlays || data.candles.length === 0) return;

      const ts = chart.timeScale();
      const lastTime = data.candles[data.candles.length - 1].time as UTCTimestamp;
      const rightEdgeX = Math.min(w - 66, (ts.timeToCoordinate(lastTime) ?? w - 90) + 22);

      const drawBox = (
        t1Sec: number,
        t2Sec: number,
        top: number,
        bottom: number,
        fill: string,
        border: string,
        tag: string
      ) => {
        const rawX1 = ts.timeToCoordinate(t1Sec as UTCTimestamp) ?? 0;
        // Clamp left edge in Clean mode so ancient zones don't paint across the whole screen
        const x1 = isClean ? Math.max(rawX1, w * 0.28) : Math.max(0, rawX1);
        const x2 = Math.max(x1 + 24, Math.min(rightEdgeX, ts.timeToCoordinate(t2Sec as UTCTimestamp) ?? rightEdgeX));
        const yTop = candleSeries.priceToCoordinate(top);
        const yBot = candleSeries.priceToCoordinate(bottom);
        if (yTop === null || yBot === null) return;
        const y = Math.min(yTop, yBot);
        const bh = Math.max(3, Math.abs(yBot - yTop));
        if (y > h || y + bh < 0) return;

        ctx.fillStyle = fill;
        ctx.fillRect(x1, y, x2 - x1, bh);
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.strokeRect(x1, y, x2 - x1, bh);
        if (!compact && x2 - x1 >= 42) {
          ctx.fillStyle = border;
          ctx.font = '600 9.5px ui-monospace, SFMono-Regular, Menlo, monospace';
          ctx.textAlign = 'right';
          ctx.fillText(tag, x2 - 4, y + Math.min(bh - 2, 11));
          ctx.textAlign = 'left';
        }
      };

      if (showOB) {
        // Sort by proximity to current price; in Clean mode keep ONLY the 2 closest unmitigated OBs within 7 ATR
        const obs = [...data.overlays.smc.order_blocks]
          .filter((ob) => !isClean || (!ob.mitigated && Math.abs((ob.top + ob.bottom) / 2 - curPrice) <= 7 * atrEst))
          .sort((a, b) => Math.abs((a.top + a.bottom) / 2 - curPrice) - Math.abs((b.top + b.bottom) / 2 - curPrice))
          .slice(0, compact ? 2 : isClean ? 3 : 8);

        for (const ob of obs) {
          const bull = ob.kind === 'bullish';
          drawBox(
            Math.floor(ob.time / 1000),
            Math.floor(ob.end_time / 1000),
            ob.top,
            ob.bottom,
            bull ? 'rgba(16,185,129,0.09)' : 'rgba(239,68,68,0.09)',
            bull ? 'rgba(16,185,129,0.55)' : 'rgba(239,68,68,0.55)',
            bull ? 'Demand OB' : 'Supply OB'
          );
        }
      }

      if (showFVG) {
        const fvgs = [...data.overlays.smc.fvgs]
          .filter((g) => !isClean || Math.abs((g.top + g.bottom) / 2 - curPrice) <= 6 * atrEst)
          .sort((a, b) => Math.abs((a.top + a.bottom) / 2 - curPrice) - Math.abs((b.top + b.bottom) / 2 - curPrice))
          .slice(0, compact ? 1 : isClean ? 2 : 7);

        for (const g of fvgs) {
          const bull = g.kind === 'bullish';
          drawBox(
            Math.floor(g.time / 1000),
            Math.floor(g.end_time / 1000),
            g.top,
            g.bottom,
            bull ? 'rgba(56,189,248,0.08)' : 'rgba(245,158,11,0.08)',
            bull ? 'rgba(56,189,248,0.5)' : 'rgba(245,158,11,0.5)',
            'FVG'
          );
        }
      }

      // BOS / CHoCH structural break horizontal segment (last 2 in Clean mode, last 8 in Full mode)
      const brSlice = compact ? 1 : isClean ? 2 : 8;
      for (const b of data.overlays.structure.breaks.slice(-brSlice)) {
        const x1 = ts.timeToCoordinate(Math.floor(b.swing_time / 1000) as UTCTimestamp);
        const x2 = ts.timeToCoordinate(Math.floor(b.time / 1000) as UTCTimestamp);
        const y = candleSeries.priceToCoordinate(b.level);
        if (x1 !== null && x2 !== null && y !== null) {
          ctx.save();
          ctx.strokeStyle = b.kind === 'CHoCH' ? 'rgba(251,191,36,0.75)' : b.direction === 'bullish' ? 'rgba(34,197,94,0.7)' : 'rgba(248,113,113,0.7)';
          ctx.setLineDash([4, 3]);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(x2, y);
          ctx.stroke();
          ctx.restore();
        }
      }

      if (showPlan && planZone) {
        const [zl, zh] = planZone;
        const y1 = candleSeries.priceToCoordinate(zh);
        const y2 = candleSeries.priceToCoordinate(zl);
        if (y1 !== null && y2 !== null) {
          const topY = Math.min(y1, y2);
          const bh = Math.max(3, Math.abs(y2 - y1));
          ctx.fillStyle = effSide === 'SHORT' ? 'rgba(244,63,94,0.12)' : 'rgba(56,189,248,0.12)';
          ctx.fillRect(Math.max(0, rightEdgeX - 130), topY, 130, bh);
        }
      }
    };

    const subRange = () => requestAnimationFrame(drawZones);
    chart.timeScale().subscribeVisibleLogicalRangeChange(subRange);
    chart.timeScale().subscribeVisibleTimeRangeChange(subRange);
    setTimeout(drawZones, 40);

    const ro = new ResizeObserver(() => {
      if (!el) return;
      chart.applyOptions({ width: el.clientWidth, height });
      drawZones();
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [data, showEMA, showOB, showFVG, showSR, showPlan, detailMode, planSide, height, compact]);

  return (
    <div className="chart-stage" style={{ height }} ref={wrapRef}>
      <canvas ref={canvasRef} className="chart-overlay-canvas" />
    </div>
  );
};
