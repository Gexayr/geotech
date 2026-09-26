interface RowEntry {
  row_id: string;
  length_m: number;
}

interface Props {
  rows: RowEntry[] | null;
  activeRowId: string | null;
  onHoverRow: (rowId: string | null) => void;
}

export function RowTable({ rows, activeRowId, onHoverRow }: Props) {
  if (!rows || rows.length === 0) return null;

  return (
    <table className="row-table">
      <thead>
        <tr>
          <th>Row ID</th>
          <th>Length</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.row_id}
            className={activeRowId === r.row_id ? "row-table__row--active" : undefined}
            onMouseEnter={() => onHoverRow(r.row_id)}
            onMouseLeave={() => onHoverRow(null)}
          >
            <td>{r.row_id}</td>
            <td>{r.length_m.toFixed(1)} m</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
