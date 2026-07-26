/**
 * Background: Import page, graph page, and admin page all use a "tab-switched subview" structure.
 * Design intent: Controlled Tabs header component that only renders the tab row; content switching
 * is managed by the parent, so each tab can lazily load data on demand (graph d3 init is expensive).
 * Key constraint: Labels are passed in pre-translated by the caller.
 */
export function Tabs({ items, active, onChange }: {
  items: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={active === item.key}
          className={active === item.key ? "active" : ""}
          onClick={() => onChange(item.key)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
