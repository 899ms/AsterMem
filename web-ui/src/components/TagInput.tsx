/**
 * Background: Both the editor and smart import need an "Enter to add tag chip" input control.
 * Design intent: Controlled tags array + internal draft state; Enter/comma commits, Backspace removes the last tag.
 * Key constraint: CJK IMEs also fire Enter keydown when confirming candidates—
 * must use e.nativeEvent.isComposing to distinguish, otherwise half-composed text gets committed as a tag.
 * This is a mandatory project IME convention and cannot be simplified to only checking e.key.
 */
import { useState } from "react";
import { IconX } from "@tabler/icons-react";

export function TagInput({ tags, onChange, placeholder }: {
  tags: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");

  const commit = () => {
    const value = draft.trim().replace(/,+$/, "");
    if (value && !tags.includes(value)) onChange([...tags, value]);
    setDraft("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === "Enter" || e.key === ",") && !e.nativeEvent.isComposing) {
      e.preventDefault();
      commit();
    } else if (e.key === "Backspace" && draft === "" && tags.length > 0) {
      onChange(tags.slice(0, -1));
    }
  };

  return (
    <div className="input tag-input">
      {tags.map((tag) => (
        <span key={tag} className="tag-input-token">
          {tag}
          <button type="button" onClick={() => onChange(tags.filter((x) => x !== tag))} aria-label={`remove ${tag}`}>
            <IconX />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commit}
        placeholder={tags.length === 0 ? placeholder : undefined}
      />
    </div>
  );
}
