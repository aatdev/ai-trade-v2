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
  legs: IbOrder[]; // display order: entry, then per-tranche stop→target (T1, T2, …)
  entry: IbOrder | null; // parent leg (null once filled & dropped by IB)
  stop: IbOrder | null; // first tranche's protective stop (summary cell)
  target: IbOrder | null; // first tranche's take-profit limit (summary cell)
  entryPrice: number | null; // parent pivot (STP auxPrice or LMT price)
  quantity: number | null; // largest leg quantity
  tif: string | null; // entry TIF (children are GTC)
  status: string | null; // entry status, else first child's
  roleByLeg: Map<IbOrder, string>; // per-leg label incl. tranche (Стоп T2, Цель T3, …)
}

export type OrderRow = SingleRow | BracketRow;

/** Non-empty linkage/identity tokens for one order. */
function tokensOf(o: IbOrder): string[] {
  return [o.order_id, o.client_order_id, o.order_ref, o.parent_id, o.oca_group].filter(
    (t): t is string => typeof t === 'string' && t.length > 0,
  );
}

// Leg-type classifiers, robust to BOTH the submit form ("STP"/"LMT") and the
// snapshot's human labels ("Stop"/"Limit") — `/iserver/account/orders` echoes
// the readable name, so a bare `.includes('STP')` misses "Stop". Exported so
// callers (MemoryOps) classify takes/stops identically.
export const isStop = (o: IbOrder | null): boolean => {
  const t = (o?.order_type ?? '').toUpperCase();
  return !!o && (t.includes('STP') || t.includes('STOP'));
};
export const isLimit = (o: IbOrder | null): boolean => {
  const t = (o?.order_type ?? '').toUpperCase();
  return !!o && (t.includes('LMT') || t.includes('LIMIT'));
};

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

  // Entry side; if the parent already filled and dropped off, the working
  // children are exit-side, so the entry side is their opposite.
  let side = entry?.side ?? null;
  if (!side) {
    const exitSide = children.find((l) => isStop(l) || isLimit(l))?.side ?? null;
    if (exitSide) side = exitSide.toUpperCase() === 'SELL' ? 'BUY' : 'SELL';
  }

  // Pair children into scale-out tranches. `oca_group` is only populated once a
  // leg is armed (post entry-fill), so we pair a take-profit with its protective
  // stop by matching QUANTITY (a tranche's stop and take share the same size) —
  // robust before arming and when two tranches happen to be equal size (the
  // stops are interchangeable). Tranche order follows the take price: ascending
  // for a long (T1 < T2 < T3), descending for a short.
  const long = (side ?? 'BUY').toUpperCase() === 'BUY';
  const stops = children.filter(isStop);
  const takes = children
    .filter((l) => isLimit(l) && !isStop(l))
    .sort((a, b) => {
      const pa = a.limit_price ?? Infinity;
      const pb = b.limit_price ?? Infinity;
      return long ? pa - pb : pb - pa;
    });
  const usedStops = new Set<IbOrder>();
  const pairs = takes.map((take) => {
    const match =
      stops.find((s) => !usedStops.has(s) && s.total_quantity === take.total_quantity) ??
      stops.find((s) => !usedStops.has(s)) ??
      null;
    if (match) usedStops.add(match);
    return { stop: match, take };
  });
  const looseStops = stops.filter((s) => !usedStops.has(s));

  const ordered: IbOrder[] = [];
  const roleByLeg = new Map<IbOrder, string>();
  if (entry) {
    ordered.push(entry);
    roleByLeg.set(entry, 'Вход');
  }
  const multi = pairs.length > 1;
  pairs.forEach((p, i) => {
    const tag = multi ? ` T${i + 1}` : '';
    if (p.stop) {
      ordered.push(p.stop);
      roleByLeg.set(p.stop, `Стоп${tag}`);
    }
    ordered.push(p.take);
    roleByLeg.set(p.take, `Цель${tag}`);
  });
  for (const s of looseStops) {
    ordered.push(s);
    roleByLeg.set(s, 'Стоп');
  }
  for (const c of children) {
    if (!roleByLeg.has(c)) {
      ordered.push(c);
      roleByLeg.set(c, 'Нога');
    }
  }

  const stop = pairs[0]?.stop ?? looseStops[0] ?? null;
  const target = pairs[0]?.take ?? null;

  const qtys = legs
    .map((l) => l.total_quantity)
    .filter((q): q is number => typeof q === 'number');
  const quantity = qtys.length ? Math.max(...qtys) : null;

  const entryPrice = entry ? (isStop(entry) ? entry.stop_price : entry.limit_price) : null;

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
    roleByLeg,
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

/** Order statuses that will never execute again — cancelled / rejected / inactive.
 * These linger in the IB snapshot after a cancel but are not a live order. */
export const DEAD_ORDER_STATUSES = new Set(['cancelled', 'canceled', 'inactive', 'rejected']);
export function isDeadOrder(status: string | null | undefined): boolean {
  return DEAD_ORDER_STATUSES.has((status ?? '').toLowerCase());
}

/**
 * Display rows that belong to a thesis. The thesis id is embedded only in the
 * bracket PARENT's cOID (`wl-<id>-<date>-t{i}` → client_order_id / order_ref);
 * the child stop/take legs link to the parent by its NUMERIC order_id, so they
 * don't carry the thesis id themselves. Grouping the FULL order list first lets
 * those children ride along with their parent, then we keep any row where some
 * leg carries the id. `liveOnly` drops rows with no working leg (every leg
 * cancelled / rejected / inactive).
 */
export function rowsForThesis(orders: IbOrder[], thesisId: string, liveOnly = true): OrderRow[] {
  const belongs = (o: IbOrder) =>
    [o.client_order_id, o.order_ref, o.parent_id].some(
      (t) => typeof t === 'string' && t.includes(thesisId),
    );
  return groupIbOrders(orders).filter((r) => {
    const legs = r.kind === 'bracket' ? r.legs : [r.order];
    if (!legs.some(belongs)) return false;
    if (liveOnly && !legs.some((l) => !isDeadOrder(l.status))) return false;
    return true;
  });
}

/** Role label for a leg within its bracket, incl. tranche (Стоп T2, Цель T3, …). */
export function legRole(leg: IbOrder, row: BracketRow): string {
  const label = row.roleByLeg?.get(leg);
  if (label) return label;
  if (leg === row.entry) return 'Вход';
  if (isStop(leg)) return 'Стоп';
  if (isLimit(leg)) return 'Цель';
  return 'Нога';
}
