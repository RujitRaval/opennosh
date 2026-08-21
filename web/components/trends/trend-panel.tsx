import type { TrendPoint } from "./transform";

function readableDate(value: string): string {
  const includesTime = value.includes("T");
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    ...(includesTime ? { hour: "numeric", minute: "2-digit", timeZoneName: "short" } : {}),
    timeZone: "UTC",
  }).format(new Date(includesTime ? value : `${value}T00:00:00Z`));
}

export function TrendPanel({
  title,
  description,
  points,
  unit,
  emptyMessage,
  children,
}: {
  title: string;
  description: string;
  points: TrendPoint[];
  unit: string;
  emptyMessage: string;
  children: React.ReactNode;
}) {
  const visiblePoints = points.filter((point) => point.count === undefined || point.count > 0);
  const maximum = Math.max(...points.map((point) => point.value), 0);

  return (
    <section className="trend-card" aria-labelledby={`${title.toLowerCase().replaceAll(" ", "-")}-title`}>
      <div className="trend-heading">
        <div>
          <p className="section-kicker">Recorded history</p>
          <h2 id={`${title.toLowerCase().replaceAll(" ", "-")}-title`}>{title}</h2>
          <p>{description}</p>
        </div>
        {children}
      </div>
      {visiblePoints.length === 0 ? (
        <div className="empty-state empty-compact">
          <h3>No data in this range</h3>
          <p>{emptyMessage}</p>
        </div>
      ) : (
        <>
          {visiblePoints.length === 1 ? (
            <p className="notice notice-neutral">One recorded point is available. Add more over time to see a longer pattern.</p>
          ) : null}
          <div className="trend-chart" aria-hidden="true">
            {points.map((point) => (
              <span
                className={point.count === 0 ? "trend-bar trend-bar-empty" : "trend-bar"}
                key={point.key ?? point.date}
                style={{ height: `${maximum === 0 ? 2 : Math.max((point.value / maximum) * 100, 2)}%` }}
              />
            ))}
          </div>
          <div className="table-scroll trend-table" tabIndex={0} aria-label={`Scrollable ${title} data`}>
            <table aria-label={`${title} data table`}>
              <thead><tr><th scope="col">Recorded at</th><th scope="col">Recorded value</th></tr></thead>
              <tbody>
                {points.map((point) => (
                  <tr key={point.key ?? point.date}>
                    <th scope="row">{readableDate(point.date)}</th>
                    <td>{point.count === 0 ? "No entries" : `${point.value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
