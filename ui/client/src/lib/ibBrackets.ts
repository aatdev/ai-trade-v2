import type { IbOrder } from '@shared/types';

/**
 * Collapse native IB bracket legs into single rows for the IB orders table.
 *
 * A native bracket is one parent (entry) carrying a `client_order_id` (cOID),
 * plus child stop/target legs that carry `parent_id == cOID` and, once armed,
 * a shared `oca_group`. The Client Portal returns the three legs as separate
 * order rows; we group any legs that share a linkage token so the bracket
 * reads as one line (entry / stop / target) instead of three.
 */

export interface SingleRow {
  kind: 'single';
  key: string;
  order: IbOrder;
}

export interface BracketRow {
  kind: 'bracket';
  key: string;
  symbol: string;
  side: string | null; // entry side (BUY = long bracket, SELL = short)
  legs: IbOrder[]; // display order: entry, stop, target, then any extras
  entry: IbOrder | null; // parent leg (null once filled & dropped by IB)
  stop: IbOrder | null; // protective stop child
  target: IbOrder | null; // take-profit limit child
  entryPrice: number | null; // parent pivot (STP auxPrice or LMT price)
  quantity: number | null; // largest leg quantity
  tif: string | null; // entry TIF (children are GTC)
  status: string | null; // entry status, else first child's
}

export type OrderRow = SingleRow | BracketRow;

/** Non-empty linkage/identity tokens for one order. */
function tokensOf(o: IbOrder): string[] {
  return [o.order_id, o.client_order_id, o.order_ref, o.parent_id, o.oca_group].filter(
    (t): t is string => typeof t === 'string' && t.length > 0,
  );
}

const isStop = (o: IbOrder | null): boolean =>
  !!o && (o.order_type ?? '').toUpperCase().includes('STP');
const isLimit = (o: IbOrder | null): boolean =>
  !!o && (o.order_type ?? '').toUpperCase().includes('LMT');

function makeBracketRow(legs: IbOrder[]): BracketRow {
  // The entry/parent is the leg the children point at (their parent_id matches
  // its id/cOID), or failing that one carrying a cOID. If neither exists the
  // parent already filled and dropped off — the survivors are all exit legs.
  const parentIds = new Set(legs.map((l) => l.parent_id).filter((x): x is string => !!x));
  const isReferenced = (l: IbOrder) =>
    (!!l.order_id && parentIds.has(l.order_id)) ||
    (!!l.client_order_id && parentIds.has(l.client_order_id)) ||
    (!!l.order_ref && parentIds.has(l.order_ref));
  const entry =
    legs.find(isReferenced) ?? legs.find((l) => !!l.client_order_id && !l.parent_id) ?? null;
  const children = legs.filter((l) => l !== entry);
  const stop = children.find(isStop) ?? null;
  const target = children.find((l) => l !== stop && isLimit(l)) ?? null;

  // Entry side; if the parent already filled and dropped off, the working
  // children are exit-side, so the entry side is their opposite.
  let side = entry?.side ?? null;
  if (!side) {
    const exitSide = (stop ?? target)?.side ?? null;
    if (exitSide) side = exitSide.toUpperCase() === 'SELL' ? 'BUY' : 'SELL';
  }

  const qtys = legs
    .map((l) => l.total_quantity)
    .filter((q): q is number => typeof q === 'number');
  const quantity = qtys.length ? Math.max(...qtys) : null;

  const entryPrice = entry ? (isStop(entry) ? entry.stop_price : entry.limit_price) : null;

  const extras = children.filter((l) => l !== stop && l !== target);
  const ordered = [entry, stop, target, ...extras].filter((l): l is IbOrder => !!l);

  const key =
    entry?.client_order_id ??
    entry?.order_ref ??
    stop?.parent_id ??
    target?.parent_id ??
    legs.map((l) => l.order_id ?? '?').join('+');

  return {
    kind: 'bracket',
    key,
    symbol: entry?.symbol ?? legs[0].symbol,
    side,
    legs: ordered,
    entry,
    stop,
    target,
    entryPrice: entryPrice ?? null,
    quantity,
    tif: entry?.tif ?? null,
    status: entry?.status ?? children[0]?.status ?? null,
  };
}

/**
 * Group a flat order list into display rows. Legs sharing any linkage token
 * (parent_id / cOID / order_ref / oca_group, or a child's parent_id matching a
 * parent's order_id) collapse into one bracket row; everything else stays a
 * single row. Input order is otherwise preserved.
 */
export function groupIbOrders(orders: IbOrder[]): OrderRow[] {
  const n = orders.length;
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x: number): number => {
    let r = x;
    while (parent[r] !== r) r = parent[r];
    while (parent[x] !== r) {
      const next = parent[x];
      parent[x] = r;
      x = next;
    }
    return r;
  };
  const union = (a: number, b: number) => {
    parent[find(a)] = find(b);
  };

  // Union any two orders that share a linkage/identity token.
  const seen = new Map<string, number>();
  orders.forEach((o, i) => {
    for (const tok of tokensOf(o)) {
      const prev = seen.get(tok);
      if (prev === undefined) seen.set(tok, i);
      else union(prev, i);
    }
  });

  // Collect members per root, preserving first-appearance order of groups.
  const groups = new Map<number, number[]>();
  const rootsInOrder: number[] = [];
  orders.forEach((_, i) => {
    const r = find(i);
    let members = groups.get(r);
    if (!members) {
      members = [];
      groups.set(r, members);
      rootsInOrder.push(r);
    }
    members.push(i);
  });

  return rootsInOrder.map((root) => {
    const idxs = groups.get(root)!;
    if (idxs.length === 1) {
      const o = orders[idxs[0]];
      return { kind: 'single', key: o.order_id ?? `${o.symbol}-${idxs[0]}`, order: o };
    }
    return makeBracketRow(idxs.map((i) => orders[i]));
  });
}

/** Role label for a leg within its bracket (для подписи раскрытой ноги). */
export function legRole(leg: IbOrder, row: BracketRow): string {
  if (leg === row.entry) return 'Вход';
  if (leg === row.stop) return 'Стоп';
  if (leg === row.target) return 'Цель';
  return 'Нога';
}
