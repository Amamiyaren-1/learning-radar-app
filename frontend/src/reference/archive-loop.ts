import { archiveColumns, columnFiles, fileLocation, logicalRow } from "./data.ts";

export type ArchiveCell = { lane: number; row: number };
export type ArchiveNavigation =
  { axis: "row" | "lane"; direction: number } | { cell: ArchiveCell };

export const LOOP_COLUMNS = 9;
// A fixed 9×64 pool covers the measured floor/top frustum plus transition margin.
export const LOOP_ROWS = 64;
export const COLUMN_SPACING = 5.2;
export const ROW_SPACING = 0.62;
const POOL_LANES = [0, 1, 2, 3, 4, -2, -1, 5, 6];

export function wrap(value: number, count: number) {
  return count > 0 ? ((value % count) + count) % count : 0;
}

// Choose an occurrence of an item in an unbounded sequence. Directional moves
// use adjacent cells instead, so the last-to-first transition never reverses.
export function nearestOccurrence(
  value: number,
  center: number,
  period: number,
) {
  return period > 0 ? value + Math.floor((center - value + period / 2) / period) * period : value;
}

export function fileAtCell({ lane, row }: ArchiveCell) {
  const files = columnFiles(wrap(lane, archiveColumns.length));
  return files.length ? files[wrap(logicalRow(row) - 12, files.length)] : undefined;
}

export function selectionCell(
  index: number,
  current: ArchiveCell,
  navigation?: ArchiveNavigation,
): ArchiveCell {
  if (navigation && "cell" in navigation) return { ...navigation.cell };
  const next = fileLocation(index, wrap(current.lane, archiveColumns.length));
  const row = nearestOccurrence(
    next.row,
    current.row,
    columnFiles(next.lane).length,
  );
  if (navigation?.axis === "row") {
    return { lane: current.lane, row: current.row + navigation.direction };
  }
  return {
    lane:
      navigation?.axis === "lane"
        ? current.lane + navigation.direction
        : nearestOccurrence(next.lane, current.lane, archiveColumns.length),
    row,
  };
}

// Preserve the reference animation's original first 160 instances. The four
// extra columns form a hidden margin on either side during interactive use.
export function poolCell(index: number): ArchiveCell {
  return {
    lane: POOL_LANES[Math.floor(index / LOOP_ROWS)],
    row: index % LOOP_ROWS,
  };
}

export function referenceCell(index:number):ArchiveCell{return {lane:Math.floor(index/32),row:index%32};}

// Do not immediately replace a cell merely because selection changed. Recycle
// one whole period only after the old model's bounds are beyond the guard band.
export function guardedCell(cell:ArchiveCell,center:ArchiveCell):ArchiveCell{
  return {lane:nearestOccurrence(cell.lane,center.lane,LOOP_COLUMNS),row:nearestOccurrence(cell.row,center.row,LOOP_ROWS)};
}

export function visibleCell(index: number, center: ArchiveCell): ArchiveCell {
  return {
    lane: nearestOccurrence(
      POOL_LANES[Math.floor(index / LOOP_ROWS)],
      center.lane,
      LOOP_COLUMNS,
    ),
    row: nearestOccurrence(index % LOOP_ROWS, center.row, LOOP_ROWS),
  };
}

export function cellKey(cell: ArchiveCell) {
  return `${cell.lane}:${cell.row}`;
}

export function sameCell(a: ArchiveCell, b: ArchiveCell) {
  return a.lane === b.lane && a.row === b.row;
}
