import { Link } from "react-router-dom";
import { formatEnum } from "../utils/format";

export function DetailHeader({ eyebrow, title, backTo, backLabel = "返回列表" }) {
  return (
    <header className="page-header">
      {backTo ? <Link className="text-link" to={backTo}>{backLabel}</Link> : null}
      {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
      <h1>{title}</h1>
    </header>
  );
}

export function StatusBadge({ value }) {
  if (!value) return <span>-</span>;
  return <span className="status-badge">{formatEnum(value)}</span>;
}

export function JsonPanel({ title, value }) {
  return (
    <section className="exchange-panel table-panel">
      <div className="panel-title"><h2>{title}</h2></div>
      <pre className="json-panel">{JSON.stringify(value ?? {}, null, 2)}</pre>
    </section>
  );
}

export function ActionMessage({ message }) {
  if (!message) return null;
  return <div className="action-line">{message}</div>;
}
