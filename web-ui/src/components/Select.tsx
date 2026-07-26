/**
 * Background: The native <select> dropdown is rendered by the OS and cannot be themed
 * (macOS blue highlight clashes with the paper-style design), while the settings page model
 * picker already has a custom dropdown visual.
 * Design intent: Provide a site-wide themed dropdown. Closed state reuses .select styles
 * (including chevron background image), open panel reuses model-picker-dropdown class series,
 * visually identical to the model picker.
 * Key constraint: Uses onBlur + relatedTarget to determine if focus left the component to collapse;
 * option clicks use onMouseDown + preventDefault to avoid blur closing the panel first.
 */
import { useRef, useState } from "react";
import type { CSSProperties, FocusEvent } from "react";

export interface SelectOption {
  value: string;
  label: string;
}

export function Select({
  value,
  options,
  onChange,
  ariaLabel,
  mono,
  small,
  style,
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  ariaLabel?: string;
  mono?: boolean;
  /** Small variant, same height as .btn.small, for toolbars */
  small?: boolean;
  style?: CSSProperties;
}) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const current = options.find((o) => o.value === value);

  const handleBlur = (e: FocusEvent) => {
    if (wrapperRef.current?.contains(e.relatedTarget as Node)) return;
    setOpen(false);
  };

  return (
    <div className={`themed-select${small ? " small" : ""}`} ref={wrapperRef} onBlur={handleBlur} style={style}>
      <button
        type="button"
        className={`select themed-select-trigger${mono ? " mono" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
      >
        {current?.label ?? value}
      </button>
      {open && (
        <div className="model-picker-dropdown themed-select-dropdown" role="listbox">
          <ul className="model-picker-list">
            {options.map((option) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                className={`model-picker-option ${option.value === value ? "active" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onChange(option.value);
                  setOpen(false);
                }}
              >
                {option.value === value && <span style={{ marginRight: 4 }}>&#10003;</span>}
                {option.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
