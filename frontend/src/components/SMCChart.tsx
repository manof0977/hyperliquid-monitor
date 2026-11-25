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
import { CoinPayload } from '../api/client';

interface Props {
  data: CoinPayload;
  showEMA: boolean;
  showOB: boolean;
  showFVG: boolean;
  showSR: boolean;
  showPlan: boolean;
}

export const SMCChart: React.FC<Props> = ({ data, showEMA, showOB, showFVG, showSR, showPlan }) => {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 520,
      layout: {
        background: { type: ColorType.Solid, color: '#0b0f17' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: 'rgba(148,163,184,0.06)' },
        horzLines: { color: 'rgba(148,163,184,0.06)' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false },
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

    // Volume histogram on bottom 18%
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volSeries.setData(
      data.candles.map((c) => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(16,185,129,0.28)' : 'rgba(239,68,68,0.28)',
      }))
    );

    // EMA 20 / 50 / 100 / 200 overlays
    if (showEMA) {
      const emaSpecs: [keyof CoinPayload['candles'][number], string][] = [
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

    // Markers: Swings (HH/HL/LH/LL), BOS/CHoCH & Liquidity Sweeps
    const markers: SeriesMarker<Time>[] = [];
    if (data.overlays) {
      for (const s of data.overlays.structure.swings.slice(-24)) {
        markers.push({
          time: Math.floor(s.time / 1000) as UTCTimestamp,
          position: s.kind === 'high' ? 'aboveBar' : 'belowBar',
          color: s.label === 'HH' || s.label === 'HL' ? '#22c55e' : '#f87171',
          shape: s.kind === 'high' ? 'arrowDown' : 'arrowUp',
          text: s.label,
        });
      }
      for (const b of data.overlays.structure.breaks.slice(-14)) {
        markers.push({
          time: Math.floor(b.time / 1000) as UTCTimestamp,
          position: b.direction === 'bullish' ? 'belowBar' : 'aboveBar',
          color: b.kind === 'CHoCH' ? '#fbbf24' : b.direction === 'bullish' ? '#34d399' : '#fb7185',
          shape: 'circle',
          text: `${b.direction === 'bullish' ? '▲' : '▼'}${b.kind}`,
        });
      }
      for (const sw of data.overlays.smc.sweeps.slice(-10)) {
        markers.push({
          time: Math.floor(sw.time / 1000) as UTCTimestamp,
          position: sw.kind === 'bullish' ? 'belowBar' : 'aboveBar',
          color: '#38bdf8',
          shape: 'square',
          text: 'SWEEP',
        });
      }
    }
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    candleSeries.setMarkers(markers);

    // Support / Resistance & PDH/PDL/PWH/PWL horizontal price lines
    if (showSR && data.overlays) {
      for (const sup of data.overlays.sr.supports.slice(0, 3)) {
        candleSeries.createPriceLine({
          price: sup.price,
          color: 'rgba(34,197,94,0.65)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `SUP (${sup.source})`,
        });
      }
      for (const res of data.overlays.sr.resistances.slice(0, 3)) {
        candleSeries.createPriceLine({
          price: res.price,
          color: 'rgba(248,113,113,0.65)',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `RES (${res.source})`,
        });
      }
    }

    // Active trade plan price lines: Entry, Invalidation, T1/T2/T3
    if (showPlan && data.summary) {
      const s = data.summary;
      if (s.entry) {
        candleSeries.createPriceLine({
          price: s.entry,
          color: '#38bdf8',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: `ENTRY (${s.active_side})`,
        });
      }
      if (s.invalidation) {
        candleSeries.createPriceLine({
          price: s.invalidation,
          color: '#ef4444',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: 'INVALIDATION',
        });
      }
      (s.targets || []).forEach((t, i) => {
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

    // Synchronized 2D canvas overlay for Order Block & FVG rectangles + BOS horizontal segments + Entry Zone band
    const drawZones = () => {
      const cvs = canvasRef.current;
      if (!cvs || !el) return;
      const w = el.clientWidth;
      const h = 520;
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
      const rightEdgeX = ts.timeToCoordinate(lastTime) ?? w - 65;

      const drawBox = (t1Sec: number, t2Sec: number, top: number, bottom: number, fill: string, border: string, tag: string) => {
        const x1 = ts.timeToCoordinate(t1Sec as UTCTimestamp) ?? 0;
        const x2 = Math.max(x1 + 18, ts.timeToCoordinate(t2Sec as UTCTimestamp) ?? rightEdgeX);
        const yTop = candleSeries.priceToCoordinate(top);
        const yBot = candleSeries.priceToCoordinate(bottom);
        if (yTop === null || yBot === null) return;
        const y = Math.min(yTop, yBot);
        const bh = Math.max(2, Math.abs(yBot - yTop));
        ctx.fillStyle = fill;
        ctx.fillRect(x1, y, x2 - x1, bh);
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.strokeRect(x1, y, x2 - x1, bh);
        ctx.fillStyle = border;
        ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
        ctx.fillText(tag, x1 + 4, y + 11);
      };

      if (showOB) {
        for (const ob of data.overlays.smc.order_blocks) {
          const bull = ob.kind === 'bullish';
          drawBox(
            Math.floor(ob.time / 1000),
            Math.floor(ob.end_time / 1000),
            ob.top,
            ob.bottom,
            bull ? 'rgba(16,185,129,0.14)' : 'rgba(239,68,68,0.14)',
            bull ? 'rgba(16,185,129,0.7)' : 'rgba(239,68,68,0.7)',
            `${bull ? 'Bull' : 'Bear'} OB`
          );
        }
      }

      if (showFVG) {
        for (const g of data.overlays.smc.fvgs) {
          const bull = g.kind === 'bullish';
          drawBox(
            Math.floor(g.time / 1000),
            Math.floor(g.end_time / 1000),
            g.top,
            g.bottom,
            bull ? 'rgba(56,189,248,0.13)' : 'rgba(245,158,11,0.13)',
            bull ? 'rgba(56,189,248,0.65)' : 'rgba(245,158,11,0.65)',
            'FVG'
          );
        }
      }

      // BOS / CHoCH structural break horizontal line from broken swing to break candle
      for (const b of data.overlays.structure.breaks.slice(-12)) {
        const x1 = ts.timeToCoordinate(Math.floor(b.swing_time / 1000) as UTCTimestamp);
        const x2 = ts.timeToCoordinate(Math.floor(b.time / 1000) as UTCTimestamp);
        const y = candleSeries.priceToCoordinate(b.level);
        if (x1 !== null && x2 !== null && y !== null) {
          ctx.save();
          ctx.strokeStyle = b.kind === 'CHoCH' ? '#fbbf24' : b.direction === 'bullish' ? '#22c55e' : '#f87171';
          ctx.setLineDash([4, 3]);
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(x2, y);
          ctx.stroke();
          ctx.restore();
        }
      }

      if (showPlan && data.summary?.entry_zone) {
        const [zl, zh] = data.summary.entry_zone;
        const firstT = data.candles[Math.max(0, data.candles.length - 35)].time;
        drawBox(firstT, lastTime, zh, zl, 'rgba(56,189,248,0.10)', 'rgba(56,189,248,0.8)', 'ENTRY ZONE');
      }
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(drawZones);
    chart.subscribeCrosshairMove(drawZones);
    setTimeout(drawZones, 50);

    const onResize = () => {
      if (!el) return;
      chart.applyOptions({ width: el.clientWidth });
      drawZones();
    };
    window.addEventListener('resize', onResize);

    return () => {
      window.removeEventListener('resize', onResize);
      chart.remove();
    };
  }, [data, showEMA, showOB, showFVG, showSR, showPlan]);

  return (
    <div className="chart-stage" ref={wrapRef}>
      <canvas ref={canvasRef} className="chart-overlay-canvas" />
    </div>
  );
};
