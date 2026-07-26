/**
 * Background: AsterMem is often deployed on LAN IPs via http, which is not a secure context—
 * navigator.clipboard will be undefined and all copy buttons on the page become non-functional.
 * Design intent: Prefer the async Clipboard API; when unavailable, fall back to a hidden
 * textarea + execCommand, ensuring "Copy for AI" works across all self-hosted access methods.
 * Key constraint: The fallback textarea must be inserted into the DOM before select, then removed immediately.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (err) {
    console.error("[AsterMem] clipboard API failed", err);
  }

  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(area);
    return copied;
  } catch (err) {
    console.error("[AsterMem] clipboard fallback failed", err);
    return false;
  }
}
