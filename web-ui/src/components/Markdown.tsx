/**
 * Background: Memory content is Markdown; both the editor live preview and detail page need rendering,
 * and content may come from imported arbitrary text, posing XSS risks.
 * Design intent: marked parses + DOMPurify whitelist-sanitizes before injecting into DOM;
 * useMemo caches the parse result to avoid repeated parsing jank during streaming/input.
 * Key constraint: Never dangerouslySetInnerHTML with unsanitized content;
 * marked is configured in sync mode (async:false) to guarantee a string return.
 */
import { useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ gfm: true, breaks: true, async: false });

export function Markdown({ source }: { source: string }) {
  const html = useMemo(() => {
    try {
      const raw = marked.parse(source || "") as string;
      return DOMPurify.sanitize(raw);
    } catch (err) {
      // On parse failure, fall back to escaped plain text display, ensuring no input causes a blank screen.
      console.error("[AsterMem] markdown parse failed", err);
      const div = document.createElement("div");
      div.textContent = source;
      return div.innerHTML;
    }
  }, [source]);

  return <div className="md-body" dangerouslySetInnerHTML={{ __html: html }} />;
}
