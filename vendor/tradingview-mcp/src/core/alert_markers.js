/**
 * Chart markers for multi-condition alerts (price + volume).
 *
 * TradingView hides the native price-line for alerts that have more than one
 * condition, so we render a companion horizontal_line on the chart. The label
 * carries the ticker, trigger price and the volume filter so the user can read
 * the full condition without opening the alerts panel.
 *
 * Colors track the trade direction:
 *   LONG  → green (#26A69A)
 *   SHORT → red   (#EF5350)
 *   unknown → blue (#2962FF), kept as a safe fallback.
 */
import { evaluate, getChartApi } from '../connection.js';
import * as drawing from './drawing.js';

const COLOR_LONG = '#26A69A';
const COLOR_SHORT = '#EF5350';
const COLOR_DEFAULT = '#2962FF';

export function buildChartMarkerLabel(price, message) {
  const tickerMatch = String(message || '').match(/^([A-Z][A-Z0-9.\-]*)\s*:/);
  const ticker = tickerMatch ? tickerMatch[1] : '';
  const volMatch = String(message || '').match(/\+\s*vol\s*([<>≤≥]=?)\s*([\d.]+\s*[KMB]?)/i);
  const volSuffix = volMatch ? ` (vol ${volMatch[1]} ${volMatch[2].trim()})` : '';
  const tickerPart = ticker ? `${ticker} ` : '';
  return `🔔 ${tickerPart}Trigger $${Number(price).toFixed(2)}${volSuffix}`;
}

export function markerNeedle(ticker, price) {
  return `${ticker} Trigger $${Number(price).toFixed(2)}`;
}

export function colorFor(direction) {
  const d = String(direction || '').toUpperCase();
  if (d === 'LONG') return COLOR_LONG;
  if (d === 'SHORT') return COLOR_SHORT;
  return COLOR_DEFAULT;
}

export async function getLastBarTime() {
  try {
    const apiPath = await getChartApi();
    const t = await evaluate(`
      (function() {
        try {
          var bars = ${apiPath}._chartWidget.model().mainSeries().bars();
          var last = bars && bars.last();
          if (last && last.value && last.value[0]) return last.value[0];
        } catch(e) {}
        return null;
      })()
    `);
    if (typeof t === 'number' && t > 0) return t;
  } catch {}
  return Math.floor(Date.now() / 1000);
}

export async function drawMultiConditionMarker({ price, message, direction }) {
  try {
    const time = await getLastBarTime();
    const text = buildChartMarkerLabel(price, message);
    const color = colorFor(direction);
    const res = await drawing.drawShape({
      shape: 'horizontal_line',
      point: { time, price: Number(price) },
      text,
      overrides: {
        linecolor: color,
        linewidth: 2,
        linestyle: 2,
        showLabel: true,
        textcolor: color,
        horzLabelsAlign: 'right',
        vertLabelsAlign: 'top',
        bold: true,
      },
    });
    return { ok: !!res?.success, entity_id: res?.entity_id, text };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
}

export async function reconcileMarker({ ticker, price, message, direction }) {
  const needle = markerNeedle(ticker, price);
  try {
    const found = await drawing.findShapesByText({ substring: needle });
    if (found?.shapes?.length) {
      return { ok: true, action: 'skipped', reason: 'already exists', count: found.shapes.length, needle };
    }
    const drawn = await drawMultiConditionMarker({ price, message, direction });
    if (drawn?.ok) {
      return { ok: true, action: 'created', entity_id: drawn.entity_id, text: drawn.text };
    }
    return { ok: false, action: 'error', error: drawn?.error || 'draw failed', needle };
  } catch (e) {
    return { ok: false, action: 'error', error: String(e?.message || e), needle };
  }
}
